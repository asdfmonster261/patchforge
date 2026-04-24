# PatchForge

A video game binary patch and installer generator that produces self-contained Windows executables. Two modes:

- **Update Patch** — diff an old and new copy of a game directory and produce a standalone patcher `.exe` users double-click to update their install
- **Repack** — compress a complete game directory into a standalone installer `.exe` users double-click to install the game from scratch

Full documentation is on the [wiki](https://github.com/asdfmonster261/patchforge/wiki).

---

## Requirements

- Python 3.10+
- PySide6 ≥ 6.5.0 (GUI only; CLI works without it)
- Linux build host (outputs are Windows executables)
- `xz` CLI (for multi-threaded LZMA repack compression)
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

## Quick start

```bash
# GUI
patchforge

# Update patch (CLI)
patchforge build \
  --source-dir game_v1.0/ \
  --target-dir game_v1.1/ \
  --output-dir dist/ \
  --app-name "My Game" \
  --version 1.1

# Repack installer (CLI)
patchforge repack \
  --game-dir game/ \
  --output-dir dist/ \
  --app-name "My Game" \
  --codec lzma \
  --compression max \
  --threads 8
```

For the full CLI reference, engine presets, repack options, project file format, and binary format documentation see the [wiki](https://github.com/asdfmonster261/patchforge/wiki).
