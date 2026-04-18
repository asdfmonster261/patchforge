"""
xpack_archive.py — XPACK01 solid-archive format for PatchForge repack mode.

Binary layout of an XPACK01 blob:
  [4B LE:  num_files]
  Per file:
    [2B LE:  path_len]
    [path_len bytes: UTF-8 relative path (forward slashes)]
    [8B LE:  decompressed_offset]
    [8B LE:  uncompressed_size]
    [4B LE:  component_index]   — always 0 in v1; reserved for optional components
  [8B LE:  compressed_data_size]
  [N bytes: XZ/LZMA2-compressed concatenated file data (solid)]

The EXE layout produced by exe_packager.package_repack():
  [installer_stub.exe]
  [XPACK01 blob      ]
  [JSON metadata     ]
  [4B LE: meta_len   ]
  [8B magic: XPACK01\x00]
"""

import lzma
import struct
from pathlib import Path
from typing import Callable, Optional

MAGIC = b"XPACK01\x00"

# quality key → (lzma preset int, dict_size override in bytes or None)
_QUALITY_MAP: dict[str, tuple[int, Optional[int]]] = {
    "fast":    (1, None),
    "normal":  (6, None),
    "max":     (9, None),
    "ultra64": (9, 64 * 1024 * 1024),
}

QUALITY_LABELS: dict[str, str] = {
    "fast":    "Fast (lzma2-1)",
    "normal":  "Normal (lzma2-6)",
    "max":     "Max (lzma2-9)",
    "ultra64": "Ultra64 (lzma2-9, 64 MB dict)",
}


def _compress(data: bytes, quality: str) -> bytes:
    preset, dict_size = _QUALITY_MAP.get(quality, (9, None))
    if dict_size is not None:
        filters = [{"id": lzma.FILTER_LZMA2, "dict_size": dict_size}]
        return lzma.compress(data, format=lzma.FORMAT_XZ, filters=filters)
    return lzma.compress(data, format=lzma.FORMAT_XZ, preset=preset)


def build(
    game_dir: Path,
    quality: str = "max",
    progress: Optional[Callable[[int, str], None]] = None,
) -> tuple[bytes, int, int]:
    """
    Walk game_dir, compress all files as a solid LZMA2/XZ archive.

    Returns (xpack01_blob, total_files, total_uncompressed_bytes).
    progress(pct 0-100, message) is called throughout.
    """
    def _prog(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    game_dir = Path(game_dir)

    _prog(0, "Scanning game directory…")
    # Sort by extension first — groups similar data for better solid compression
    all_files = sorted(
        (f for f in game_dir.rglob("*") if f.is_file()),
        key=lambda f: (f.suffix.lower(), f.as_posix()),
    )
    if not all_files:
        raise ValueError(f"No files found in: {game_dir}")

    total = len(all_files)
    _prog(5, f"Found {total} files. Reading…")

    file_entries: list[dict] = []
    pieces: list[bytes] = []
    offset = 0

    for i, f in enumerate(all_files):
        data = f.read_bytes()
        rel = f.relative_to(game_dir).as_posix()
        file_entries.append({"path": rel, "offset": offset, "size": len(data)})
        pieces.append(data)
        offset += len(data)
        if i % 200 == 0:
            pct = 5 + int(i / total * 20)
            _prog(pct, f"Reading… ({i + 1}/{total})")

    total_uncompressed = offset
    _prog(25, f"Compressing {_fmt_size(total_uncompressed)} with LZMA2 ({quality})…")

    raw = b"".join(pieces)
    del pieces  # free memory before compression
    compressed = _compress(raw, quality)
    del raw

    _prog(92, "Encoding archive…")
    blob = _encode(file_entries, compressed)
    _prog(100, "Archive complete.")

    return blob, total, total_uncompressed


def _encode(files: list[dict], compressed: bytes) -> bytes:
    """Pack file table + compressed data into an XPACK01 blob."""
    buf = bytearray()
    buf += struct.pack("<I", len(files))
    for f in files:
        path_b = f["path"].encode("utf-8")
        buf += struct.pack("<H", len(path_b))
        buf += path_b
        buf += struct.pack("<QQI", f["offset"], f["size"], 0)  # offset, size, component=0
    buf += struct.pack("<Q", len(compressed))
    buf += compressed
    return bytes(buf)


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
