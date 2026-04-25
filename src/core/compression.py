"""Compression level metadata."""

import sys
from pathlib import Path

# Levels that require the full HDiffPatch stub (zlib + bzip2 deps) on the
# Windows side. Anything else can use the standard LZMA-only stub.
STUB_FULL_REQUIRED = {"zip/1", "zip/9", "bzip/5", "bzip/9"}


def xz_command() -> str:
    """Path to the xz CLI: bundled win-x64/xz.exe on Windows, PATH lookup elsewhere."""
    if sys.platform == "win32":
        return str(Path(__file__).parent.parent.parent / "engines" / "win-x64" / "xz.exe")
    return "xz"


def zstd_command() -> str:
    """Path to the zstd CLI: bundled win-x64/zstd.exe on Windows, PATH lookup elsewhere."""
    if sys.platform == "win32":
        return str(Path(__file__).parent.parent.parent / "engines" / "win-x64" / "zstd.exe")
    return "zstd"
