# PatchForge

A video game binary patch generator that produces self-contained Windows patcher executables. Point it at an old and new copy of a game directory, choose an engine and compression preset, and get a standalone `.exe` your end-users can double-click to update their installation.

---

## Features

- **Three diff engines** — HDiffPatch 4.12.2, xdelta3 3.0.8, JojoDiff 0.8.1
- **Directory patching** — handles multi-file games; tracks new, modified, and deleted files
- **Engine-specific presets** — named compression presets tuned for each engine
- **Multi-threading** — parallel patch generation for directory mode
- **File verification** — CRC32C, MD5, or filesize checksums embedded in the output
- **Target discovery** — end-users can locate their game via manual browse, Windows Registry, or INI file
- **Icon injection** — embed a custom `.ico` into the output executable
- **x64 and x86 stubs** — target both 32-bit and 64-bit Windows installs
- **Project files** — save and reload all settings as `.xpm` JSON files
- **GUI + CLI** — dark-themed PySide6 GUI when run with no arguments; full CLI otherwise

---

## Requirements

- Python 3.10+
- PySide6 ≥ 6.5.0 (GUI only; CLI works without it)
- Linux build host (outputs are Windows executables)
- MinGW-w64 cross-compiler (only needed to rebuild stubs from source)

## Installation

```bash
git clone https://github.com/asdfmonster261/patchforge
cd patchforge
pip install -e .
```

---

## Usage

### GUI

```bash
patchforge
```

Launch with no arguments to open the GUI. Fill in the panels and click **Build Patch**.

### CLI

```bash
patchforge build \
  --source-dir game_v1.0/ \
  --target-dir game_v1.1/ \
  --output-dir dist/ \
  --app-name "My Game" \
  --version 1.1 \
  --engine hdiffpatch \
  --compression set5_lzma2 \
  --arch x64
```

Output: `dist/MyGame_1.1_patch_x64.exe`

#### All `build` flags

| Flag | Default | Description |
|------|---------|-------------|
| `--project FILE` | — | Load settings from a `.xpm` project file (CLI flags override) |
| `--source-dir DIR` | — | Original (old) game folder |
| `--target-dir DIR` | — | Patched (new) game folder |
| `--output-dir DIR` | `.` | Directory for the output `.exe` |
| `--app-name NAME` | — | Application name shown in the patcher UI |
| `--version VER` | — | Version string (e.g. `1.1`) |
| `--description TEXT` | — | Optional description shown in the patcher |
| `--engine ENGINE` | `hdiffpatch` | `hdiffpatch` \| `xdelta3` \| `jojodiff` |
| `--compression PRESET` | engine default | Compression preset key (see below) |
| `--threads N` | `1` | Worker threads for patch generation |
| `--verify METHOD` | `crc32c` | `crc32c` \| `md5` \| `filesize` |
| `--find-method METHOD` | `manual` | `manual` \| `registry` \| `ini` |
| `--registry-key KEY` | — | Windows registry key path |
| `--registry-value VALUE` | `InstallPath` | Registry value name |
| `--ini-path FILE` | — | Path to INI file |
| `--ini-section SECTION` | — | INI section name |
| `--ini-key KEY` | — | INI key name |
| `--arch ARCH` | `x64` | `x64` \| `x86` |
| `--icon-path FILE` | — | `.ico` file to embed as the patcher's icon |
| `--save-project FILE` | — | Save resolved settings to a `.xpm` after building |

#### Other commands

```bash
patchforge new-project --output patch.xpm --app-name "My Game" --engine xdelta3
patchforge show-project patch.xpm
```

---

## Engines & Presets

### HDiffPatch (default)

12 presets combining stream block size and compressor. Smaller block size = better compression, slower diffing.

| Preset key | Label |
|------------|-------|
| `set1_lzma2` | Set1 \| 64k + LZMA2 |
| `set2_lzma2` | Set2 \| 16k + LZMA2 |
| `set3_lzma2` | Set3 \| 4k + LZMA2 |
| `set4_lzma2` | Set4 \| 1k + LZMA2 |
| **`set5_lzma2`** | **Set5 \| 640b + LZMA2 (default)** |
| `set6_lzma2` | Set6 \| 64b + LZMA2 |
| `set1_bzip2` … `set6_bzip2` | Same sets with PBZIP2 |

Thread count (1, 2, 4, 8, 16, 32) is a separate setting passed as `-p-N` to `hdiffz`.

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

## Project Files (`.xpm`)

All settings can be saved and reloaded as a JSON project file:

```json
{
  "app_name": "My Game",
  "version": "1.1",
  "description": "",
  "source_dir": "/path/to/game_v1.0",
  "target_dir": "/path/to/game_v1.1",
  "output_dir": "dist/",
  "engine": "hdiffpatch",
  "compression": "set5_lzma2",
  "verify_method": "crc32c",
  "find_method": "registry",
  "registry_key": "SOFTWARE\\MyCompany\\MyGame",
  "registry_value": "InstallPath",
  "ini_path": "",
  "ini_section": "",
  "ini_key": "",
  "arch": "x64",
  "threads": 4,
  "icon_path": "assets/patcher.ico"
}
```

---

## Output Format

The output executable is the patcher stub with patch data and metadata appended:

```
[ stub EXE bytes        ]
[ patch data            ]
[ JSON metadata (UTF-8) ]
[ metadata length  4B LE]
[ magic "XPATCH01"  8B  ]  ← end of file
```

The stub reads backwards from the end of its own file to locate and parse the embedded data. End-users just double-click the `.exe` — no installer or runtime required.

---

## Stub System

Pre-built stubs live in `stub/prebuilt/`. They are compiled C programs that provide the patcher UI and apply the embedded patch at runtime.

| File | Description |
|------|-------------|
| `hdiffpatch_x64.exe` / `_x86.exe` | HDiffPatch stub (LZMA2 only) |
| `hdiffpatch_full_x64.exe` / `_x86.exe` | HDiffPatch stub + zlib + bzip2 |
| `xdelta3_x64.exe` / `_x86.exe` | xdelta3 stub (DJW + LZMA secondary) |
| `jojodiff_x64.exe` / `_x86.exe` | JojoDiff stub |

### Rebuilding stubs from source

Requires MinGW-w64 cross-compilers.

```bash
cd stub
make           # x64 stubs for all three engines
make win32     # x86 stubs
make full      # HDiffPatch full stubs (zlib + bzip2); run `make deps` first
make deps      # Cross-compile zlib and bzip2 static libs
make clean     # Remove prebuilt stubs
```

Full stubs are only needed when using `zip/*` or `bzip/*` compression in HDiffPatch. All other presets work with the standard stubs.

---

## Directory Structure

```
patchforge/
├── main.py                      # Entry point
├── pyproject.toml
├── engines/linux-x64/           # Linux diff binaries (hdiffz, xdelta3, jdiff, …)
├── src/
│   ├── cli/main.py              # CLI argument parser & commands
│   └── core/
│       ├── project.py           # ProjectSettings dataclass, save/load
│       ├── patch_builder.py     # Build orchestration
│       ├── exe_packager.py      # Appends patch + metadata to stub
│       ├── pe_icon.py           # PE icon injection
│       ├── verification.py      # CRC32C / MD5 / filesize
│       └── engines/
│           ├── base.py          # PatchEngine ABC
│           ├── hdiffpatch.py
│           ├── xdelta3.py
│           ├── jojodiff.py
│           └── dir_format.py    # PFMD container (xdelta3 / JojoDiff dir mode)
│   └── gui/
│       ├── main_window.py       # PySide6 GUI
│       └── theme.py             # Dark theme QSS
└── stub/
    ├── Makefile
    ├── stub_common.h            # Shared UI, metadata parsing, patch reading
    ├── hdiffpatch_stub.c
    ├── xdelta3_stub.c
    ├── jojodiff_stub.c
    ├── dir_patch_format.h       # PFMD container parser (C)
    ├── prebuilt/                # Pre-compiled stub EXEs
    └── third_party/             # liblzma, zlib, bzip2 headers + static libs
```
