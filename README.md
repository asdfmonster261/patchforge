# PatchForge

A video game binary patch and installer generator that produces self-contained Windows executables. Two modes:

- **Update Patch** — diff an old and new copy of a game directory and produce a standalone patcher `.exe` users double-click to update their install
- **Repack** — compress a complete game directory into a standalone installer `.exe` users double-click to install the game from scratch

---

## Features

### Update Patch mode

- **Three diff engines** — HDiffPatch 4.12.2, xdelta3 3.0.8, JojoDiff 0.8.1
- **Directory patching** — handles multi-file games; tracks new, modified, and deleted files
- **Engine-specific presets** — named compression presets tuned for each engine
- **Multi-threading** — parallel patch generation for directory mode
- **Source version verification** — checksums of original files are embedded; patcher aborts with a clear message if the user has the wrong game version
- **Post-patch verification** — CRC32C, MD5, or filesize checksums confirm the update applied correctly
- **Change summary** — patcher window shows a _N modified · M added · K removed_ header at launch
- **Target discovery** — end-users can locate their game via manual browse, Windows Registry, or INI file
- **Extra file bundling** — ship additional files (DLC, configs, redistributables) inside the patch exe
- **Run before/after** — execute arbitrary commands before patching starts or after it succeeds
- **Backup** — optionally create a full backup of the game folder before applying the patch

### Repack mode

- **Solid archive** — entire game directory compressed into an XPACK01 blob using XZ/LZMA2 or Zstandard
- **Two codecs** — LZMA (`lzma`) and Zstandard (`zstd`); selectable per project
- **Multi-threaded compression** — MT encoder for both codecs; thread count derived automatically from CPU core count at runtime
- **Optional components** — extra folders offered as checkboxes or radio-button groups during install; each group has an enable/disable toggle; components can declare dependencies on other components; a per-component flag shows an antivirus / Smart App Control warning when that component is selected
- **Multi-threaded installation** — LZMA installer uses `lzma_stream_decoder_mt` to decompress across all available cores; respects the "Reduce system load" checkbox
- **CRC32 integrity verification** — every file is checksummed at build time and verified on extraction
- **Uninstaller** — optionally embeds a standalone uninstaller and registers the app in Add/Remove Programs
- **Shortcuts** — Desktop and Start Menu shortcuts with configurable target and display name
- **Repack project files** — save and reload all settings as `.xpr` JSON files

### Shared

- **Icon injection** — embed a custom `.ico` into the output executable
- **Backdrop image** — optional PNG/JPEG/BMP background drawn behind the UI; displayed at fixed 616:353 aspect ratio
- **Smart UAC elevation** — automatically relaunches as administrator if write access is denied
- **Metadata fields** — app note, copyright, contact, company info, custom window title, custom output exe name/version
- **Drag-and-drop** — drop folders and files directly onto path fields in the GUI
- **x64 and x86 stubs** — target both 32-bit and 64-bit Windows installs
- **GUI + CLI** — dark-themed PySide6 GUI when run with no arguments; full CLI for both patch and repack modes

---

## Requirements

- Python 3.10+
- PySide6 ≥ 6.5.0 (GUI only; CLI works without it)
- Linux build host (outputs are Windows executables)
- `xz` CLI (for multi-threaded LZMA repack compression; already present on most Linux distros)
- `zstd` CLI (for Zstandard repack compression)
- MinGW-w64 cross-compiler (only needed to rebuild stubs from source)

## Installation

```bash
git clone https://github.com/asdfmonster261/patchforge
cd patchforge
pip install -e .
```

The Linux engine binaries (`engines/linux-x64/`) are included in the repository. They are statically linked against everything except `libc` and run on any x86-64 Linux.

---

## Usage

### GUI

```bash
patchforge
```

Launch with no arguments to open the GUI. Use the **Update Patch** tab to generate a patch, or the **Repack** tab to build an installer. Fill in the panels and click **Build Patch** / **Build Repack**.

### CLI (Update Patch mode)

```bash
patchforge build \
  --source-dir game_v1.0/ \
  --target-dir game_v1.1/ \
  --output-dir dist/ \
  --app-name "My Game" \
  --version 1.1 \
  --engine hdiffpatch \
  --compression set6_lzma2 \
  --arch x64
```

Output: `dist/MyGame_1.1_patch_x64.exe`

Use `--patch-exe-name` to override the output filename stem:

```bash
patchforge build ... --patch-exe-name "MyGame_Update_Nov2025"
# → dist/MyGame_Update_Nov2025_x64.exe
```

### CLI (Repack mode)

```bash
patchforge repack \
  --game-dir game/ \
  --output-dir dist/ \
  --app-name "My Game" \
  --codec lzma \
  --compression max \
  --threads 8 \
  --arch x64
```

Output: `dist/MyGame_1.0_installer_x64.exe`

With optional components:

```bash
patchforge repack --game-dir game/ --app-name "My Game" \
  --component '{"label":"High-res textures","folder":"hires/","default_checked":false,"group":""}' \
  --component '{"label":"English voices","folder":"voices_en/","default_checked":true,"group":"voices"}' \
  --component '{"label":"Japanese voices","folder":"voices_jp/","default_checked":false,"group":"voices"}'
```

Each `--component` is a JSON object with:

| Key | Type | Description |
|-----|------|-------------|
| `label` | string | Display name shown in the installer |
| `folder` | string | Path to the folder of files for this component |
| `default_checked` | bool | Whether the component is selected by default |
| `group` | string | Empty = standalone checkbox; shared name = mutually exclusive radio-button group |
| `requires` | int[] | 1-based indices of components that must be selected for this one to be enabled |
| `shortcut_target` | string | Overrides the main shortcut target when this component is selected |
| `sac_warning` | bool | Show an antivirus / Smart App Control warning when this component is selected |

The flag is repeatable.

Load a `.xpr` project file and override individual fields:

```bash
patchforge repack --project installer.xpr --threads 16 --output-dir dist/
```

#### All `repack` flags

| Flag | Default | Description |
|------|---------|-------------|
| `--project FILE` | — | Load settings from a `.xpr` project file (flags override) |
| `--game-dir DIR` | — | Game directory to compress |
| `--output-dir DIR` | `.` | Directory for the output `.exe` |
| `--app-name NAME` | — | Application name shown in the installer |
| `--app-note TEXT` | — | Short subtitle shown next to the app name |
| `--version VER` | — | Version string (e.g. `1.0`) |
| `--description TEXT` | — | Description shown in the installer |
| `--copyright TEXT` | — | Copyright notice |
| `--contact TEXT` | — | Contact email or URL |
| `--company-info TEXT` | — | Publisher / company name |
| `--window-title TEXT` | — | Installer title bar text (defaults to app name) |
| `--installer-exe-name STEM` | — | Output exe filename stem (default: auto) |
| `--installer-exe-version VER` | — | Informational version string for the exe |
| `--codec CODEC` | `lzma` | `lzma` \| `zstd` |
| `--compression QUALITY` | `max` | LZMA: `fast` \| `normal` \| `max` — Zstd: `fast` \| `normal` \| `max` \| `ultra` |
| `--threads N` | `1` | Compression threads (auto-populated from CPU count in GUI) |
| `--arch ARCH` | `x64` | `x64` \| `x86` |
| `--icon-path FILE` | — | `.ico` file to embed as the installer's icon |
| `--backdrop FILE` | — | Background image (PNG/JPEG/BMP); displayed at 616:353 aspect ratio |
| `--install-registry-key KEY` | — | Registry key written to HKCU after install |
| `--run-after CMD` | — | Shell command to run after successful install |
| `--detect-running EXE` | — | Warn if this process is running before install |
| `--close-delay N` | `0` | Seconds before auto-closing after success (0 = stay open) |
| `--required-free-space GB` | `0` | Warn if disk space is below this threshold in GB (0 = disabled) |
| `--no-verify-crc32` | — | Skip CRC32 integrity check after installation (default: enabled) |
| `--shortcut-target REL_PATH` | — | Relative path to the exe for shortcuts (e.g. `Bin\Game.exe`) |
| `--shortcut-name NAME` | — | Shortcut display name (default: app name) |
| `--shortcut-desktop` / `--no-shortcut-desktop` | off | Create a Desktop shortcut |
| `--shortcut-startmenu` / `--no-shortcut-startmenu` | on | Create a Start Menu shortcut |
| `--component JSON` | — | Add an optional component (repeatable; see above) |
| `--no-uninstaller` | — | Omit the uninstaller and Add/Remove Programs registration |
| `--save-project FILE` | — | Save resolved settings to a `.xpr` after building |

#### Repack project commands

```bash
patchforge new-repack-project --output installer.xpr --app-name "My Game" --compression max
patchforge show-repack-project installer.xpr
```

#### All `build` flags

| Flag | Default | Description |
|------|---------|-------------|
| `--project FILE` | — | Load settings from a `.xpm` project file (CLI flags override) |
| `--source-dir DIR` | — | Original (old) game folder |
| `--target-dir DIR` | — | Patched (new) game folder |
| `--output-dir DIR` | `.` | Directory for the output `.exe` |
| `--app-name NAME` | — | Application name shown in the patcher UI |
| `--app-note TEXT` | — | Short subtitle shown next to the app name |
| `--version VER` | — | Version string (e.g. `1.1`) |
| `--description TEXT` | — | Optional description shown in the patcher |
| `--copyright TEXT` | — | Copyright notice shown in the patcher window |
| `--contact TEXT` | — | Contact email or URL shown in the patcher window |
| `--company-info TEXT` | — | Publisher / company name shown in the patcher window |
| `--window-title TEXT` | — | Title bar text of the patcher window (defaults to app name) |
| `--patch-exe-name STEM` | — | Output exe filename stem (default: auto from app name + version) |
| `--patch-exe-version VER` | — | Informational version string for the patch exe (e.g. `1.0.0.0`) |
| `--engine ENGINE` | `hdiffpatch` | `hdiffpatch` \| `xdelta3` \| `jojodiff` |
| `--compression PRESET` | engine default | Compression preset key (see below) |
| `--threads N` | `1` | Worker threads for patch generation |
| `--quality LEVEL` | `max` | HDiffPatch compressor quality: `fast` / `normal` / `max` |
| `--verify METHOD` | `crc32c` | `crc32c` \| `md5` \| `filesize` |
| `--find-method METHOD` | `manual` | `manual` \| `registry` \| `ini` |
| `--registry-key KEY` | — | Windows registry key path |
| `--registry-value VALUE` | `InstallPath` | Registry value name |
| `--ini-path FILE` | — | Path to INI file |
| `--ini-section SECTION` | — | INI section name |
| `--ini-key KEY` | — | INI key name |
| `--arch ARCH` | `x64` | `x64` \| `x86` |
| `--icon-path FILE` | — | `.ico` file to embed as the patcher's icon |
| `--backdrop FILE` | — | Background image for the patcher window (PNG/JPEG/BMP) |
| `--extra-args ARGS` | — | Extra CLI arguments passed verbatim to the diff engine |
| `--delete-extra-files` | on | Delete game files absent from the target version |
| `--no-delete-extra-files` | — | Keep game files absent from the target version |
| `--run-before CMD` | — | Shell command to run before patching starts |
| `--run-after CMD` | — | Shell command to run after patching succeeds |
| `--backup-at MODE` | `same_folder` | `disabled` \| `same_folder` \| `custom` |
| `--backup-path DIR` | — | Backup directory (used when `--backup-at custom`) |
| `--save-project FILE` | — | Save resolved settings to a `.xpm` after building |

#### Other commands

```bash
patchforge new-project --output patch.xpm --app-name "My Game" --engine hdiffpatch
patchforge show-project patch.xpm
```

---

## Engines & Presets

### HDiffPatch (default, recommended)

12 presets combining stream block size and compressor. Smaller block size = better compression, slower diffing. The default (`set6_lzma2`) gives the best compression ratio for most games.

| Preset key | Label |
|------------|-------|
| `set1_lzma2` | Set1 \| 64k + LZMA2 |
| `set2_lzma2` | Set2 \| 16k + LZMA2 |
| `set3_lzma2` | Set3 \| 4k + LZMA2 |
| `set4_lzma2` | Set4 \| 1k + LZMA2 |
| `set5_lzma2` | Set5 \| 640b + LZMA2 |
| **`set6_lzma2`** | **Set6 \| 64b + LZMA2 (default)** |
| `set1_bzip2` … `set6_bzip2` | Same sets with PBZIP2 |

Thread count is a separate setting passed as `-p-N` to `hdiffz`. The GUI populates the thread dropdown automatically from the system's CPU core count.

### xdelta3

| Preset key | Description |
|------------|-------------|
| `none` | No encoding, no secondary compression (fastest) |
| **`paul44`** | **DJW Huffman secondary (default)** |
| `lzma_mem` | LZMA secondary + 512 MB source window (smallest patches) |

### JojoDiff

| Preset key | Description |
|------------|-------------|
| `minimal` | `-ff` — skip out-of-buffer compares (fastest) |
| `good` | `-b` — better quality, more memory |
| **`optimal`** | **No extra flags — balanced (default)** |

---

## Engine recommendation

| Scenario | Recommended engine |
|---|---|
| General use (most games) | **HDiffPatch** `set6_lzma2` |
| Games with many small files and no large ones | **JojoDiff** `optimal` |
| Smallest possible patch, long diff time acceptable | HDiffPatch `set6_lzma2` with `max` quality |
| Quick test builds | xdelta3 `paul44` |

xdelta3 is almost always the least efficient choice for binary game data.

---

## Repack Mode

Repack compresses a complete game directory into a standalone installer exe. The end user runs it on a clean machine — no previous game installation required.

### Compression

Two codecs are available:

**LZMA (`--codec lzma`, default)**

| Quality key | Description |
|------------|-------------|
| `fast` | lzma2-1 — fastest, largest output |
| `normal` | lzma2-6 — balanced |
| **`max`** | **lzma2-9 — best compression (default)** |

- 1 thread: uses stdlib lzma (no external dependencies)
- \>1 threads: delegates to the `xz` CLI with `--block-size=64MiB` so the installer can decompress each block independently across multiple cores

**Zstandard (`--codec zstd`)**

| Quality key | Description |
|------------|-------------|
| `fast` | zstd-1 — fastest |
| `normal` | zstd-9 — balanced |
| `max` | zstd-19 — best compression |
| `ultra` | zstd-22 — maximum compression |

- Delegates to the `zstd` CLI for both single and multi-threaded compression

### Installation performance

The installer uses `lzma_stream_decoder_mt` (liblzma 5.3+) to decompress LZMA streams across all available CPU cores, taking advantage of the independent 64 MB blocks produced by the multi-threaded encoder. ZSTD decompression is single-threaded (ZSTD frames have inter-block dependencies that prevent parallel decoding).

Checking **Reduce system load** in the installer UI drops the decoder to 1 thread, which is recommended for HDDs to avoid seek thrashing.

### Optional Components

Each component is a folder of files merged on top of the base game during install. Components are shown to the user as:

- **Checkboxes** — when the group field is blank (independent, togglable)
- **Group header + radio buttons** — when two or more components share the same group name; the group header is a checkbox that enables or disables the whole group, and the radio buttons beneath it are mutually exclusive within the group

Components can declare `requires` dependencies on other components (by 1-based index); the installer auto-enables required components and greys out unavailable ones. Components with `sac_warning: true` display an amber warning banner in the installer when selected, useful for components that may trigger antivirus or Windows Smart App Control.

Up to 16 components are supported.

### Repack Project Files (`.xpr`)

All repack settings are saved as a JSON file. Example:

```json
{
  "app_name": "My Game",
  "app_note": "Complete Edition",
  "version": "1.0",
  "description": "Full game installer",
  "copyright": "© 2025 My Company",
  "contact": "support@example.com",
  "company_info": "My Company",
  "window_title": "My Game Installer",
  "installer_exe_name": "",
  "installer_exe_version": "1.0.0.0",
  "game_dir": "/path/to/game_files",
  "output_dir": "dist/",
  "arch": "x64",
  "codec": "lzma",
  "compression": "max",
  "threads": 8,
  "icon_path": "assets/icon.ico",
  "backdrop_path": "assets/backdrop.jpg",
  "install_registry_key": "SOFTWARE\\MyCompany\\MyGame",
  "run_after_install": "",
  "detect_running_exe": "MyGame.exe",
  "close_delay": 0,
  "required_free_space_gb": 0.0,
  "include_uninstaller": true,
  "verify_crc32": true,
  "shortcut_target": "Bin\\MyGame.exe",
  "shortcut_name": "My Game",
  "shortcut_create_desktop": false,
  "shortcut_create_startmenu": true,
  "components": [
    {
      "label": "High-res textures",
      "folder": "/path/to/hires_textures",
      "default_checked": false,
      "group": "",
      "requires": [],
      "shortcut_target": "",
      "sac_warning": false
    },
    {
      "label": "English voices",
      "folder": "/path/to/voices_en",
      "default_checked": true,
      "group": "voices",
      "requires": [],
      "shortcut_target": "",
      "sac_warning": false
    },
    {
      "label": "Japanese voices",
      "folder": "/path/to/voices_jp",
      "default_checked": false,
      "group": "voices",
      "requires": [],
      "shortcut_target": "",
      "sac_warning": false
    }
  ]
}
```

---

## Project Files (`.xpm`) — Update Patch mode

All patch mode settings can be saved and reloaded as a JSON project file. Example:

```json
{
  "app_name": "My Game",
  "app_note": "Hotfix release",
  "version": "1.1",
  "description": "Fixes the inventory crash",
  "copyright": "© 2025 My Company",
  "contact": "support@example.com",
  "company_info": "My Company",
  "window_title": "My Game Patcher",
  "patch_exe_name": "",
  "patch_exe_version": "1.1.0.0",
  "source_dir": "/path/to/game_v1.0",
  "target_dir": "/path/to/game_v1.1",
  "output_dir": "dist/",
  "engine": "hdiffpatch",
  "compression": "set6_lzma2",
  "verify_method": "crc32c",
  "find_method": "registry",
  "registry_key": "SOFTWARE\\MyCompany\\MyGame",
  "registry_value": "InstallPath",
  "arch": "x64",
  "threads": 4,
  "icon_path": "assets/patcher.ico",
  "backdrop_path": "assets/backdrop.jpg",
  "delete_extra_files": true,
  "backup_at": "same_folder",
  "run_before": "",
  "run_after": "",
  "extra_files": []
}
```

---

## Output Formats

### Update Patch exe

```
[ stub EXE bytes                ]
[ patch data                    ]
[ extra file 0 bytes            ]  \
[ extra file 1 bytes            ]   > zero or more bundled files
[ ...                           ]  /
[ backdrop image bytes          ]  (zero bytes if none)
[ JSON metadata (UTF-8)         ]
[ metadata length   4 bytes LE  ]
[ magic "XPATCH01"  8 bytes     ]  ← end of file
```

### Repack (XPACK01) exe

```
[ installer_stub EXE bytes      ]
[ XPACK01 blob                  ]  ← see format below
[ backdrop image bytes          ]  (zero bytes if none)
[ JSON metadata (UTF-8)         ]
[ metadata length   4 bytes LE  ]
[ magic "XPACK01\0" 8 bytes     ]  ← end of file
```

**XPACK01 blob layout:**

```
[ 4B LE: num_files              ]
  Per file:
    [ 2B LE: path_len           ]
    [ path_len bytes: UTF-8 path (forward slashes) ]
    [ 8B LE: offset within stream ]
    [ 8B LE: uncompressed size  ]
    [ 4B LE: component_index    ]  0 = base game; 1..N = optional components
    [ 4B LE: CRC32              ]  IEEE 802.3 checksum of uncompressed file data
[ 4B LE: num_streams            ]
  Per stream:
    [ 4B LE: component_index    ]
    [ 8B LE: compressed size    ]
    [ N bytes: XZ/LZMA2 or Zstandard stream  ]
```

Each optional component has its own compressed stream. The installer decompresses only the streams corresponding to the user's selections. File offsets are relative to the start of their own stream's decompressed data. The codec (`lzma` or `zstd`) is recorded in the JSON metadata.

The stub reads backwards from the end of its own file to locate and parse the embedded data. End-users just double-click the `.exe` — no installer or runtime required.

---

## Stub System

Pre-built stubs live in `stub/prebuilt/`. They are compiled C programs that provide the UI and apply the embedded data at runtime.

**Update Patch stubs:**

| File | Description |
|------|-------------|
| `hdiffpatch_x64.exe` / `_x86.exe` | HDiffPatch stub (LZMA2 only) |
| `hdiffpatch_full_x64.exe` / `_x86.exe` | HDiffPatch stub + zlib + bzip2 |
| `xdelta3_x64.exe` / `_x86.exe` | xdelta3 stub (DJW + LZMA secondary) |
| `jojodiff_x64.exe` / `_x86.exe` | JojoDiff stub |

**Repack stubs:**

| File | Description |
|------|-------------|
| `installer_x64.exe` / `_x86.exe` | XPACK01 installer stub (LZMA2 + Zstandard) |
| `uninstaller_x64.exe` / `_x86.exe` | Standalone uninstaller stub |

### Rebuilding stubs from source

Requires MinGW-w64 cross-compilers.

```bash
cd stub
make                # x64 update-patch stubs (hdiffpatch, xdelta3, jojodiff)
make win32          # x86 update-patch stubs
make installer      # installer stubs (x64 + x86)
make uninstaller    # uninstaller stubs (x64 + x86)
make full           # HDiffPatch full stubs (zlib + bzip2); run `make deps` first
make full32         # HDiffPatch full stubs, x86
make deps           # Cross-compile zlib and bzip2 static libs
make clean          # Remove all prebuilt stubs
```

Full stubs are only needed when using `zip/*` or `bzip/*` compression in HDiffPatch. All other presets work with the standard stubs.

---

## Directory Structure

```
patchforge/
├── pyproject.toml
├── engines/linux-x64/           # Linux diff binaries (hdiffz, xdelta3, jdiff, …)
├── src/
│   ├── cli/main.py              # CLI argument parser & commands
│   └── core/
│       ├── project.py           # ProjectSettings dataclass, save/load (.xpm)
│       ├── patch_builder.py     # Patch build orchestration
│       ├── repack_project.py    # RepackSettings dataclass, save/load (.xpr)
│       ├── repack_builder.py    # Repack build orchestration
│       ├── xpack_archive.py     # XPACK01 solid-archive format + MT compression
│       ├── exe_packager.py      # Appends data + metadata to stub
│       ├── pe_icon.py           # PE icon injection
│       ├── verification.py      # CRC32C / MD5 / filesize
│       └── engines/
│           ├── base.py          # PatchEngine ABC
│           ├── hdiffpatch.py
│           ├── xdelta3.py
│           ├── jojodiff.py
│           └── dir_format.py    # PFMD container (xdelta3 / JojoDiff dir mode)
│   └── gui/
│       ├── main_window.py       # PySide6 GUI (both modes)
│       └── theme.py             # Dark theme QSS
└── stub/
    ├── Makefile
    ├── stub_common.h            # Shared UI, metadata parsing, patch reading
    ├── hdiffpatch_stub.c
    ├── xdelta3_stub.c
    ├── jojodiff_stub.c
    ├── installer_stub.c         # XPACK01 installer stub (LZMA2 + Zstandard)
    ├── uninstaller_stub.c       # Standalone uninstaller stub
    ├── prebuilt/                # Pre-compiled stub EXEs
    └── third_party/             # liblzma, zlib, bzip2, zstd headers + static libs
```
