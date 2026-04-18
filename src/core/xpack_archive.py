"""
xpack_archive.py — XPACK01 solid-archive format for PatchForge repack mode.

Binary layout of an XPACK01 blob:
  [4B LE:  num_files]
  Per file:
    [2B LE:  path_len]
    [path_len bytes: UTF-8 relative path (forward slashes)]
    [8B LE:  decompressed_offset]  — offset within *this component's* stream
    [8B LE:  uncompressed_size]
    [4B LE:  component_index]      — 0 = base game; 1..N = optional components
  [4B LE:  num_streams]
  Per stream:
    [4B LE:  component_index]
    [8B LE:  compressed_data_size]
    [N bytes: XZ/LZMA2-compressed concatenated file data for this component]

The EXE layout produced by exe_packager.package_repack():
  [installer_stub.exe]
  [XPACK01 blob      ]
  [JSON metadata     ]
  [4B LE: meta_len   ]
  [8B magic: XPACK01\\x00]
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
    components: Optional[list] = None,
    progress: Optional[Callable[[int, str], None]] = None,
) -> tuple[bytes, int, int]:
    """
    Walk game_dir (and any optional component folders), compress into XPACK01 blob.

    components: list of {"label": str, "folder": str, ...}
                Each becomes a separate compressed stream (component_index 1, 2, ...).
    Returns (xpack01_blob, total_files, total_uncompressed_bytes).
    progress(pct 0-100, message) is called throughout.
    """
    def _prog(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    game_dir = Path(game_dir)
    components = components or []

    # Build ordered list of (component_index, folder_path)
    streams_spec = [(0, game_dir)] + [
        (i + 1, Path(c["folder"])) for i, c in enumerate(components)
    ]

    _prog(0, "Scanning directories…")

    # Scan all files per component
    stream_files: list[list[tuple[str, Path]]] = []  # indexed by stream order
    for comp_idx, folder in streams_spec:
        folder = Path(folder)
        files = sorted(
            (f for f in folder.rglob("*") if f.is_file()),
            key=lambda f: (f.suffix.lower(), f.as_posix()),
        )
        entries = [(f.relative_to(folder).as_posix(), f) for f in files]
        stream_files.append(entries)

    total_files = sum(len(ef) for ef in stream_files)
    if total_files == 0:
        raise ValueError(f"No files found in: {game_dir}")

    _prog(5, f"Found {total_files} files. Reading…")

    # Read all file data and build file table + raw blobs per component
    file_table: list[dict] = []
    stream_raws: list[bytes] = []
    total_uncompressed = 0
    files_read = 0

    for stream_idx, ((comp_idx, folder), entries) in enumerate(zip(streams_spec, stream_files)):
        pieces: list[bytes] = []
        offset = 0
        for rel, path in entries:
            data = path.read_bytes()
            file_table.append({
                "path":      rel,
                "offset":    offset,
                "size":      len(data),
                "component": comp_idx,
            })
            pieces.append(data)
            offset += len(data)
            total_uncompressed += len(data)
            files_read += 1
            if files_read % 200 == 0:
                pct = 5 + int(files_read / total_files * 20)
                _prog(pct, f"Reading… ({files_read}/{total_files})")
        stream_raws.append(b"".join(pieces))

    _prog(25, f"Compressing {_fmt_size(total_uncompressed)} across "
              f"{len(streams_spec)} stream(s) with LZMA2 ({quality})…")

    # Compress each stream separately
    compressed_streams: list[bytes] = []
    compress_progress_base = 25
    for stream_idx, raw in enumerate(stream_raws):
        label = "base game" if stream_idx == 0 else f"component {stream_idx}"
        _prog(compress_progress_base + int(stream_idx / len(stream_raws) * 65),
              f"Compressing {label} ({_fmt_size(len(raw))})…")
        compressed_streams.append(_compress(raw, quality) if raw else b"")
        del raw
    stream_raws.clear()

    _prog(92, "Encoding archive…")
    blob = _encode(file_table, list(zip(streams_spec, compressed_streams)))
    _prog(100, "Archive complete.")

    return blob, total_files, total_uncompressed


def _encode(
    files: list[dict],
    streams: list[tuple[tuple[int, Path], bytes]],  # ((comp_idx, folder), compressed)
) -> bytes:
    """Pack file table + per-component compressed streams into an XPACK01 blob."""
    buf = bytearray()

    # File table
    buf += struct.pack("<I", len(files))
    for f in files:
        path_b = f["path"].encode("utf-8")
        buf += struct.pack("<H", len(path_b))
        buf += path_b
        buf += struct.pack("<QQI", f["offset"], f["size"], f["component"])

    # Streams
    buf += struct.pack("<I", len(streams))
    for (comp_idx, _folder), compressed in streams:
        buf += struct.pack("<I", comp_idx)
        buf += struct.pack("<Q", len(compressed))
        buf += compressed

    return bytes(buf)


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
