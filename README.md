# repo-sync
Linux repository syncs

# Repository Sync (APT & DNF/YUM)

Sync Debian/Ubuntu (APT) and Rocky Linux / EPEL (DNF/YUM) repositories to a local directory using rsync. Two scripts share the same config directory but use separate config files and layouts.

| Script | Config file | Purpose |
|--------|-------------|---------|
| **apt-repo-sync.py** | `~/.config/repo-sync/repo-sync.yaml` | Debian/Ubuntu mirrors (dists + pool) |
| **dnf-repo-sync.py** | `~/.config/repo-sync/dnf-repo-sync.yaml` | Rocky, EPEL, and other DNF/YUM repos (repodata + Packages) |

## Requirements

- **Python 3**
- **rsync**
- **PyYAML** (`pip install pyyaml` or `apt install python3-yaml` / `dnf install python3-pyyaml`)

APT script only (for parsing `Packages`): standard library `gzip` and `lzma` (no extra install).

---

## apt-repo-sync — Debian/Ubuntu

Syncs APT repositories: **dists** (Release files, binary indices, Contents, i18n, dep11) and **pool** (only the `.deb` files referenced in the Packages indices).

### Configuration

On first run, a default config is created at **`~/.config/repo-sync/repo-sync.yaml`**. Edit it, then run again.

**Main options (under `apt:`):**

| Option | Description |
|--------|-------------|
| `local_base_dir` | Local mirror root (e.g. `~/mirror/apt`) |
| `rsync_options` | Default rsync flags (archive, timeout, etc.) |
| `rsync_delete_options` | Options for removing obsolete files (e.g. `--delete-after`) |

**Per-repository options:**

| Option | Description |
|--------|-------------|
| `name` | Short name (e.g. `debian`, `ubuntu`, `debian-security`) |
| `rsync_url` | Rsync URL (e.g. `rsync://ftp.debian.org/debian`) |
| `suites` | Suites to sync (e.g. `bookworm`, `jammy-updates`) |
| `components` | Components (e.g. `main`, `contrib`, `universe`) |
| `architectures` | Architectures (e.g. `amd64`, `i386`, `all`) |

Default config includes examples for Debian (bookworm), Debian Security, and Ubuntu (jammy).

### Usage

```bash
./apt-repo-sync.py
```

Use **`--debug`** or **`-v`** for debug logging.

### Behavior

1. **dists** — Syncs Release/Release.gpg/InRelease and, per suite/component/arch, `binary-{arch}/` (Packages.gz/xz) and metadata (Contents-*.gz, i18n/, dep11/). Uses `--delete-after` so local dists match the server.
2. **Package list** — Reads Packages.gz or Packages.xz to get every `Filename:` path.
3. **pool** — Rsyncs only those filenames from the pool (via `--files-from`), so the pool contains just the packages referenced in the synced dists.

### Example local layout

With `local_base_dir: ~/mirror/apt`:

```
~/mirror/apt/
├── debian/
│   ├── dists/bookworm/...   (Release, main/..., contrib/..., etc.)
│   └── pool/...
├── debian-security/
│   ├── dists/bookworm-security/...
│   └── pool/...
└── ubuntu/
    ├── dists/jammy/...
    └── pool/...
```

---

## dnf-repo-sync — Rocky / EPEL / DNF-YUM

Syncs DNF/YUM repositories: **repodata** and **Packages** (and optional metadata files). No parsing of primary.xml or other repodata; DNF uses the repodata as-is, and every package in a component’s Packages directory is described there.

### Configuration

On first run, a default config is created at **`~/.config/repo-sync/dnf-repo-sync.yaml`**. Edit it, then run again.

**Main options (under `dnf:`):**

| Option | Description |
|--------|-------------|
| `local_base_dir` | Local directory for synced repos (e.g. `~/repos`) |
| `rsync_options` | Default rsync flags (archive, timeout, etc.) |
| `rsync_delete_options` | Options for removing obsolete files (e.g. `--delete-after`) |
| `retry_settings` | `max_retries`, `base_delay`, `max_delay`, `connection_delay` for retries and connection spreading |

**Per-repository options:**

| Option | Description |
|--------|-------------|
| `name` | Short name (e.g. `rocky`, `epel`) |
| `rsync_url` | Rsync module URL (e.g. `msync.rockylinux.org::rocky-linux`) |
| `versions` | List of versions (e.g. `['8','9','10']`) |
| `repos` | Map of version → list of components (e.g. `8: [BaseOS, AppStream, ...]`) |
| `architectures` | Architectures (e.g. `['x86_64']`) |
| **`path_suffix`** | Path segment after version/component/arch. Rocky uses `/os`; EPEL uses none. |

Path layout examples:

- **Rocky:** `{version}/{component}/{arch}/os/` → e.g. `8/BaseOS/x86_64/os/`
- **EPEL:** `{version}/{component}/{arch}/` → e.g. `8/Everything/x86_64/`

Set `path_suffix: '/os'` for Rocky and `path_suffix: ''` for EPEL.

### Usage

```bash
./dnf-repo-sync.py
```

Use **`--debug`** or **`-v`** for debug logging. Progress is reported as `[current/total]` for each component (e.g. `[3/12] Syncing 8/AppStream/x86_64/os`).

### Behavior

1. **repodata** — Full `repodata/` directory (repomd.xml and all `*-primary.xml.zst`, filelists, etc.). No decompression or parsing.
2. **Packages** — Full `Packages/` directory for each component.
3. **Metadata** — Optional sync of `RPM-GPG-KEY-*`, `EULA`, `LICENSE`, `media.repo`.
4. **Retries** — On “max connections” (or other) rsync errors, retries with exponential backoff and a small initial delay.
5. **Delete-after** — Uses `--delete-after` so local content mirrors the server.

### Example local layout

With `local_base_dir: ~/repos` and default repo config:

```
~/repos/
├── rocky/
│   ├── 8/BaseOS/x86_64/os/{repodata/, Packages/, ...}
│   ├── 8/AppStream/x86_64/os/
│   └── ...
└── epel/
    ├── 8/Everything/x86_64/{repodata/, Packages/, ...}
    ├── 8/Modular/x86_64/
    └── ...
```

---

## Quick reference

| | apt-repo-sync | dnf-repo-sync |
|---|----------------|----------------|
| **Config** | `~/.config/repo-sync/repo-sync.yaml` | `~/.config/repo-sync/dnf-repo-sync.yaml` |
| **Config key** | `apt` | `dnf` |
| **What’s synced** | dists + pool (only referenced packages) | repodata + Packages + optional metadata |
| **Parsing** | Reads Packages.gz/xz for pool file list | None |
| **Retries** | No (single attempt per rsync) | Yes (exponential backoff, connection delay) |
| **Progress** | Per-suite/component log lines | `[current/total]` per component |

Use **apt-repo-sync** for Debian/Ubuntu mirrors; use **dnf-repo-sync** for Rocky, EPEL, and other DNF/YUM-based repositories.
