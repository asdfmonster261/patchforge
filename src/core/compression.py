"""Compression level definitions and helpers."""

from dataclasses import dataclass

LEVELS = [
    "none",
    "zip/1",
    "zip/9",
    "bzip/5",
    "bzip/9",
    "lzma/fast",
    "lzma/normal",
    "lzma/ultra",
]

# Levels that require the full stub (zlib/bzip2 deps) on the Windows side
STUB_FULL_REQUIRED = {"zip/1", "zip/9", "bzip/5", "bzip/9"}

# Levels not supported by JojoDiff (which has no built-in compression)
JOJODIFF_UNSUPPORTED = {
    "zip/1", "zip/9", "bzip/5", "bzip/9",
    "lzma/fast", "lzma/normal", "lzma/ultra",
}


def requires_full_stub(compression: str) -> bool:
    """Return True if the selected compression needs the full HDiffPatch stub."""
    return compression in STUB_FULL_REQUIRED


def label_for(compression: str) -> str:
    labels = {
        "none":         "None (uncompressed)",
        "zip/1":        "zlib — level 1 (fast)",
        "zip/9":        "zlib — level 9 (best)",
        "bzip/5":       "bzip2 — level 5",
        "bzip/9":       "bzip2 — level 9",
        "lzma/fast":   "LZMA — fast",
        "lzma/normal": "LZMA — normal",
        "lzma/ultra":  "LZMA — ultra",
    }
    return labels.get(compression, compression)
