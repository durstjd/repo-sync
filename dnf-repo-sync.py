#!/usr/bin/env python3
"""
DNF/YUM Repository Synchronization Script
Syncs Rocky Linux, EPEL, and other RPM repositories to local filesystem using rsync
"""

import os
import sys
import yaml
import subprocess
import logging
import shutil
import time
import random
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class DNFRepoSync:
    def __init__(self, config_path: Path):
        """Initialize the DNF repository synchronizer."""
        self.config_path = config_path
        self.config = self.load_config()
        self.base_dir = Path(self.config['dnf']['local_base_dir']).expanduser()
        self.rsync_opts = self.config['dnf'].get('rsync_options', [])
        self.rsync_delete_opts = self.config['dnf'].get('rsync_delete_options', [])
        self.package_files = set()
        
        # Check dependencies
        if not self.check_dependencies():
            sys.exit(1)
    
    def check_dependencies(self) -> bool:
        """Check that required external tools are available."""
        dependencies = {
            'rsync': 'rsync is required. Install with: dnf install rsync',
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
            'dnf': {
                'local_base_dir': '~/repos',
                'rsync_options': [
                    '--archive',
                    '--hard-links',  # Preserve hard links for space savings
                    '--copy-links',  # Transform symlinks
                    '--verbose',
                    '--partial',
                    '--append-verify',
                    '--timeout=300',
                    '--contimeout=60'
                ],
                'rsync_delete_options': [
                    '--delete-after'
                ],
                'retry_settings': {
                    'max_retries': 3,
                    'base_delay': 2.0,
                    'max_delay': 30.0,
                    'connection_delay': 1.0
                },
                'repositories': [
                    {
                        'name': 'rocky',
                        'rsync_url': 'msync.rockylinux.org::rocky-linux',
                        'versions': ['8', '9', '10'],
                        'repos': {
                            '8': ['BaseOS', 'AppStream', 'extras', 'PowerTools'],
                            '9': ['BaseOS', 'AppStream', 'extras', 'CRB'],
                            '10': ['BaseOS', 'AppStream', 'extras', 'CRB']
                        },
                        'architectures': ['x86_64'],
                        'sync_source': False,
                        'path_suffix': '/os'  # Rocky uses /os suffix
                    },
                    {
                        'name': 'epel',
                        'rsync_url': 'archive.linux.duke.edu::fedora-epel',
                        'versions': ['8', '9', '10'],
                        'repos': {
                            '8': ['Everything', 'Modular'],
                            '9': ['Everything'],
                            '10': ['Everything']
                        },
                        'architectures': ['x86_64'],
                        'sync_source': False,
                        'path_suffix': ''  # EPEL doesn't use /os suffix
                    }
                ]
            }
        }
        
        # Create config directory if it doesn't exist
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(self.config_path, 'w') as f:
            yaml.dump(default_config, f, default_flow_style=False, sort_keys=False)
    
    def rsync_execute(self, source: str, dest: Path, additional_opts: List[str] = None, use_delete: bool = False) -> bool:
        """Execute rsync command with error handling and retry logic."""
        dest.mkdir(parents=True, exist_ok=True)
        
        retry_settings = self.config['dnf'].get('retry_settings', {})
        max_retries = retry_settings.get('max_retries', 3)
        base_delay = retry_settings.get('base_delay', 2.0)
        max_delay = retry_settings.get('max_delay', 30.0)
        connection_delay = retry_settings.get('connection_delay', 1.0)
        
        for attempt in range(max_retries):
            # Add random delay to avoid connection limit issues
            if attempt > 0:
                delay = min(base_delay * (2 ** attempt), max_delay) + random.uniform(0, 2)
                logger.info(f"Retrying in {delay:.1f} seconds (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            elif attempt == 0:
                # Small random delay even on first attempt to spread out connections
                time.sleep(random.uniform(0, connection_delay))
            
            cmd = ['rsync'] + self.rsync_opts
            
            # Only add delete options if requested
            if use_delete and '--files-from' not in (additional_opts or []):
                cmd.extend(self.rsync_delete_opts)
            
            if additional_opts:
                cmd.extend(additional_opts)
            cmd.extend([source, str(dest) + '/'])
            
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
                
                # Check if it's a connection limit error
                if "max connections" in (e.stderr or "").lower():
                    logger.warning("Connection limit reached, will retry with delay")
                    continue
                elif attempt == max_retries - 1:
                    return False
            except FileNotFoundError:
                logger.error("rsync command not found. Please install rsync.")
                return False
        
        return False
    
    def sync_repository_data(self, repo: Dict) -> bool:
        """Synchronize repodata and Packages for each repository."""
        logger.info(f"Syncing repository data for {repo['name']}")
        
        repo_dir = self.base_dir / repo['name']
        base_url = repo['rsync_url']
        
        # Calculate total operations for progress reporting
        total_ops = 0
        for version in repo['versions']:
            repo_list = repo['repos'].get(version, [])
            for repo_name in repo_list:
                for arch in repo['architectures']:
                    total_ops += 1
        
        current_op = 0
        
        for version in repo['versions']:
            repo_list = repo['repos'].get(version, [])
            for repo_name in repo_list:
                for arch in repo['architectures']:
                    current_op += 1
                    # Use path_suffix from config (Rocky uses /os, EPEL doesn't)
                    path_suffix = repo.get('path_suffix', '/os')
                    repo_path = f"{version}/{repo_name}/{arch}{path_suffix}"
                    
                    logger.info(f"  [{current_op}/{total_ops}] Syncing {repo_path}")
                    
                    # Sync repodata directory
                    logger.info(f"    Syncing repodata for {repo_path}")
                    include_opts = [
                        '--include=/repodata/',
                        '--include=/repodata/**',
                        '--exclude=*'
                    ]
                    
                    source = f"{base_url}/{repo_path}/"
                    dest = repo_dir / repo_path
                    
                    if not self.rsync_execute(source, dest, include_opts, use_delete=True):
                        logger.error(f"Failed to sync repodata for {repo_path}")
                        return False
                    
                    # Sync Packages directory
                    logger.info(f"    Syncing Packages for {repo_path}")
                    include_opts = [
                        '--include=/Packages/',
                        '--include=/Packages/**',
                        '--exclude=*'
                    ]
                    
                    if not self.rsync_execute(source, dest, include_opts, use_delete=True):
                        logger.error(f"Failed to sync Packages for {repo_path}")
                        return False
                    
                    # Also sync the GPG keys and other important files
                    logger.info(f"    Syncing metadata files for {repo_path}")
                    include_opts = [
                        '--include=/RPM-GPG-KEY-*',
                        '--include=/EULA',
                        '--include=/LICENSE',
                        '--include=/media.repo',
                        '--exclude=*'
                    ]
                    
                    self.rsync_execute(source, dest, include_opts)
        
        return True
    
    def sync_repository(self, repo: Dict) -> bool:
        """Synchronize a complete repository."""
        logger.info(f"Starting sync for repository: {repo['name']}")
        logger.info(f"  URL: {repo['rsync_url']}")
        logger.info(f"  Versions: {', '.join(repo['versions'])}")
        
        # Log repos per version for clarity
        for version in repo['versions']:
            repo_list = repo['repos'].get(version, [])
            logger.info(f"  Version {version} repos: {', '.join(repo_list)}")
        
        logger.info(f"  Architectures: {', '.join(repo['architectures'])}")
        
        # Sync repodata and Packages directories
        if not self.sync_repository_data(repo):
            logger.error(f"Failed to sync repository data for {repo['name']}")
            return False
        
        logger.info(f"Successfully completed sync for {repo['name']}")
        return True
    
    def run(self):
        """Run the synchronization for all configured repositories."""
        logger.info("Starting DNF/YUM repository synchronization")
        logger.info(f"Local base directory: {self.base_dir}")
        
        # Ensure base directory exists
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
        success_count = 0
        fail_count = 0
        
        for repo in self.config['dnf']['repositories']:
            if self.sync_repository(repo):
                success_count += 1
            else:
                fail_count += 1
        
        logger.info(f"Synchronization complete: {success_count} succeeded, {fail_count} failed")
        
        return fail_count == 0

def main():
    """Main entry point."""
    # Parse command line arguments
    if '--debug' in sys.argv or '-v' in sys.argv:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")
    
    # Determine config path
    config_dir = Path.home() / '.config' / 'repo-sync'
    config_path = config_dir / 'dnf-repo-sync.yaml'
    
    logger.info("DNF/YUM Repository Sync Script")
    logger.info(f"Config file: {config_path}")
    
    # Create sync object and run
    try:
        syncer = DNFRepoSync(config_path)
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