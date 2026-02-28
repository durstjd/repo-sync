#!/usr/bin/env python3
"""
APT Repository Synchronization Script
Syncs Debian/Ubuntu repositories to local filesystem using rsync
"""

import os
import sys
import yaml
import gzip
import lzma
import subprocess
import tempfile
import logging
import shutil
from pathlib import Path
from typing import List, Dict, Set, Optional
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class APTRepoSync:
    def __init__(self, config_path: Path):
        """Initialize the APT repository synchronizer."""
        self.config_path = config_path
        self.config = self.load_config()
        self.base_dir = Path(self.config['apt']['local_base_dir']).expanduser()
        self.rsync_opts = self.config['apt'].get('rsync_options', [])
        self.rsync_delete_opts = self.config['apt'].get('rsync_delete_options', [])
        self.package_files = set()
        
        # Check dependencies
        if not self.check_dependencies():
            sys.exit(1)
    
    def check_dependencies(self) -> bool:
        """Check that required external tools are available."""
        dependencies = {
            'rsync': 'rsync is required. Install with: apt install rsync',
        }
        
        all_found = True
        for cmd, msg in dependencies.items():
            if not shutil.which(cmd):
                logger.error(f"Missing dependency: {cmd}")
                logger.error(f"  {msg}")
                all_found = False
            else:
                logger.debug(f"Found dependency: {cmd}")
        
        return all_found
        
    def load_config(self) -> dict:
        """Load configuration from YAML file."""
        if not self.config_path.exists():
            self.create_default_config()
            logger.info(f"Created default configuration at {self.config_path}")
            logger.info("Please edit the configuration and run again.")
            sys.exit(0)
            
        with open(self.config_path, 'r') as f:
            return yaml.safe_load(f)
    
    def create_default_config(self):
        """Create a default configuration file."""
        default_config = {
            'apt': {
                'local_base_dir': '~/mirror/apt',
                'rsync_options': [
                    '--archive',
                    '--copy-links',  # Transform symlinks into referent file/dir
                    '--verbose',
                    '--partial',
                    '--append-verify',
                    '--timeout=300',
                    '--contimeout=60'
                ],
                'rsync_delete_options': [
                    '--delete-after'  # Only use for dists, not pool
                ],
                'repositories': [
                    {
                        'name': 'debian',
                        'rsync_url': 'rsync://ftp.debian.org/debian',
                        'suites': ['bookworm', 'bookworm-updates', 'bookworm-backports'],
                        'components': ['main', 'contrib', 'non-free', 'non-free-firmware'],
                        'architectures': ['amd64', 'i386', 'all']
                    },
                    {
                        'name': 'debian-security',
                        'rsync_url': 'rsync://rsync.security.debian.org/debian-security',
                        'suites': ['bookworm-security'],
                        'components': ['main', 'contrib', 'non-free', 'non-free-firmware'],
                        'architectures': ['amd64', 'i386', 'all']
                    },
                    {
                        'name': 'ubuntu',
                        'rsync_url': 'rsync://archive.ubuntu.com/ubuntu',
                        'suites': ['jammy', 'jammy-updates', 'jammy-security', 'jammy-backports'],
                        'components': ['main', 'restricted', 'universe', 'multiverse'],
                        'architectures': ['amd64', 'i386', 'all']
                    }
                ]
            }
        }
        
        # Create config directory if it doesn't exist
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(self.config_path, 'w') as f:
            yaml.dump(default_config, f, default_flow_style=False, sort_keys=False)
    
    def rsync_execute(self, source: str, dest: Path, additional_opts: List[str] = None, use_delete: bool = False) -> bool:
        """Execute rsync command with error handling.
        
        Args:
            source: Source URL or path
            dest: Destination path
            additional_opts: Additional rsync options
            use_delete: Whether to include delete options (only for recursive operations)
        """
        dest.mkdir(parents=True, exist_ok=True)
        
        cmd = ['rsync'] + self.rsync_opts
        
        # Only add delete options if requested and we're doing recursive sync
        if use_delete and '--no-dirs' not in (additional_opts or []):
            cmd.extend(self.rsync_delete_opts)
        
        if additional_opts:
            cmd.extend(additional_opts)
        cmd.extend([source, str(dest) + '/'])  # Add trailing slash to destination
        
        logger.debug(f"Executing: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            if result.stdout:
                logger.debug(result.stdout)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Rsync failed with exit code {e.returncode}")
            logger.error(f"Command: {' '.join(cmd)}")
            if e.stderr:
                logger.error(f"Error output: {e.stderr}")
            if e.stdout:
                logger.debug(f"Standard output: {e.stdout}")
            return False
        except FileNotFoundError:
            logger.error("rsync command not found. Please install rsync.")
            return False
    
    def sync_dists(self, repo: Dict) -> bool:
        """Synchronize the dists directory structure for a repository."""
        logger.info(f"Syncing dists for {repo['name']}")
        
        repo_dir = self.base_dir / repo['name']
        
        for suite in repo['suites']:
            # First, sync the suite-level Release files
            logger.info(f"  Syncing Release files for {suite}")
            
            # Build include patterns for Release files
            # Be specific about which directories to include to avoid empty dirs
            include_opts = [
                f"--include=/{suite}/",  # Include this specific suite directory
                f"--include=/{suite}/Release",
                f"--include=/{suite}/Release.gpg",
                f"--include=/{suite}/InRelease",
                "--exclude=*"      # Exclude everything else
            ]
            
            source = f"{repo['rsync_url']}/dists/"
            dest = repo_dir / 'dists'
            
            if not self.rsync_execute(source, dest, include_opts, use_delete=True):
                logger.error(f"Failed to sync Release files for {suite}")
                return False
            
            # Now sync each component/architecture combination
            for component in repo['components']:
                for arch in repo['architectures']:
                    if arch == 'all':
                        # 'all' packages are typically included in binary-* directories
                        continue
                    
                    logger.info(f"  Syncing {suite}/{component}/binary-{arch}")
                    
                    # Be explicit about the directory structure we want
                    include_opts = [
                        f"--include=/{suite}/",
                        f"--include=/{suite}/{component}/",
                        f"--include=/{suite}/{component}/binary-{arch}/",
                        f"--include=/{suite}/{component}/binary-{arch}/**",
                        "--exclude=*"     # Exclude everything else
                    ]
                    
                    if not self.rsync_execute(source, dest, include_opts, use_delete=True):
                        logger.error(f"Failed to sync {suite}/{component}/binary-{arch}")
                        return False
                
                # Also sync Contents and i18n if needed
                logger.info(f"  Syncing {suite}/{component} metadata")
                include_opts = [
                    f"--include=/{suite}/",
                    f"--include=/{suite}/{component}/",
                    f"--include=/{suite}/{component}/Contents-*.gz",
                    f"--include=/{suite}/{component}/i18n/",
                    f"--include=/{suite}/{component}/i18n/**",
                    f"--include=/{suite}/{component}/dep11/",
                    f"--include=/{suite}/{component}/dep11/**",
                    "--exclude=*"
                ]
                
                self.rsync_execute(source, dest, include_opts, use_delete=True)
        
        return True
    
    def extract_package_files(self, repo: Dict) -> Set[str]:
        """Extract all package filenames from Packages.gz or Packages.xz files."""
        import lzma
        
        logger.info(f"Extracting package list for {repo['name']}")
        
        package_files = set()
        repo_dir = self.base_dir / repo['name']
        
        for suite in repo['suites']:
            for component in repo['components']:
                for arch in repo['architectures']:
                    if arch == 'all':
                        continue
                    
                    # Try multiple compression formats
                    packages_base = repo_dir / 'dists' / suite / component / f'binary-{arch}' / 'Packages'
                    packages_gz = packages_base.with_suffix('.gz')
                    packages_xz = packages_base.with_suffix('.xz')
                    packages_uncompressed = packages_base
                    
                    packages_file = None
                    open_func = None
                    
                    # Check which format exists
                    if packages_gz.exists():
                        packages_file = packages_gz
                        open_func = gzip.open
                        logger.debug(f"  Found Packages.gz: {packages_gz}")
                    elif packages_xz.exists():
                        packages_file = packages_xz
                        open_func = lzma.open
                        logger.debug(f"  Found Packages.xz: {packages_xz}")
                    elif packages_uncompressed.exists():
                        packages_file = packages_uncompressed
                        open_func = open
                        logger.debug(f"  Found uncompressed Packages: {packages_uncompressed}")
                    else:
                        logger.warning(f"No Packages file found in {packages_base.parent}")
                        continue
                    
                    logger.debug(f"  Parsing {packages_file}")
                    
                    try:
                        with open_func(packages_file, 'rt') as f:
                            for line in f:
                                if line.startswith('Filename: '):
                                    # Remove 'Filename: ' prefix and strip whitespace
                                    filename = line[10:].strip()
                                    # Store relative path from repo root (remove leading /)
                                    if filename.startswith('/'):
                                        filename = filename[1:]
                                    package_files.add(filename)
                    except Exception as e:
                        logger.error(f"Failed to parse {packages_file}: {e}")
        
        logger.info(f"  Found {len(package_files)} unique packages for {repo['name']}")
        return package_files
    
    def sync_pool(self, repo: Dict, package_files: Set[str]) -> bool:
        """Synchronize the pool directory with only required packages."""
        logger.info(f"Syncing pool for {repo['name']} ({len(package_files)} packages)")
        
        repo_dir = self.base_dir / repo['name']
        
        # Create a temporary file with the list of files to sync
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            for pkg_file in sorted(package_files):
                # Remove 'pool/' prefix if present for the file list
                if pkg_file.startswith('pool/'):
                    f.write(pkg_file[5:] + '\n')
                else:
                    f.write(pkg_file + '\n')
            temp_file = f.name
        
        try:
            # Use --files-from to sync only the packages we need
            # IMPORTANT: --archive does NOT imply --recursive with --files-from
            # We need to explicitly add --recursive to create directory structure
            additional_opts = [
                f"--files-from={temp_file}",
                "--recursive",    # Explicitly needed with --files-from
                "--relative"      # Preserve directory structure
            ]
            
            source = f"{repo['rsync_url']}/pool/"
            dest = repo_dir / 'pool'
            
            success = self.rsync_execute(source, dest, additional_opts)
            
            if success:
                logger.info(f"Successfully synced pool for {repo['name']}")
            else:
                logger.error(f"Failed to sync pool for {repo['name']}")
            
            return success
            
        finally:
            # Clean up temporary file
            os.unlink(temp_file)
    
    def sync_repository(self, repo: Dict) -> bool:
        """Synchronize a complete repository."""
        logger.info(f"Starting sync for repository: {repo['name']}")
        logger.info(f"  URL: {repo['rsync_url']}")
        logger.info(f"  Suites: {', '.join(repo['suites'])}")
        logger.info(f"  Components: {', '.join(repo['components'])}")
        logger.info(f"  Architectures: {', '.join(repo['architectures'])}")
        
        # Step 1: Sync dists structure
        if not self.sync_dists(repo):
            logger.error(f"Failed to sync dists for {repo['name']}")
            return False
        
        # Step 2: Extract package filenames
        package_files = self.extract_package_files(repo)
        
        if not package_files:
            logger.warning(f"No packages found for {repo['name']}")
            return True
        
        # Step 3: Sync pool with required packages
        if not self.sync_pool(repo, package_files):
            logger.error(f"Failed to sync pool for {repo['name']}")
            return False
        
        logger.info(f"Successfully completed sync for {repo['name']}")
        return True
    
    def run(self):
        """Run the synchronization for all configured repositories."""
        logger.info("Starting APT repository synchronization")
        logger.info(f"Local base directory: {self.base_dir}")
        
        # Ensure base directory exists
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
        success_count = 0
        fail_count = 0
        
        for repo in self.config['apt']['repositories']:
            if self.sync_repository(repo):
                success_count += 1
            else:
                fail_count += 1
        
        logger.info(f"Synchronization complete: {success_count} succeeded, {fail_count} failed")
        
        return fail_count == 0

def main():
    """Main entry point."""
    # Parse command line arguments for verbosity
    if '--debug' in sys.argv or '-v' in sys.argv:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")
    
    # Determine config path
    config_dir = Path.home() / '.config' / 'repo-sync'
    config_path = config_dir / 'repo-sync.yaml'
    
    logger.info("APT Repository Sync Script")
    logger.info(f"Config file: {config_path}")
    
    # Create sync object and run
    try:
        syncer = APTRepoSync(config_path)
        # Exit with appropriate code
        sys.exit(0 if syncer.run() else 1)
    except KeyboardInterrupt:
        logger.info("\nSync interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        logger.debug("Stack trace:", exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    main()
