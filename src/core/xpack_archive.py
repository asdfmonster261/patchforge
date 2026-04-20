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
import subprocess
import zlib
from pathlib import Path
from typing import Callable, Optional

MAGIC = b"XPACK01\x00"

# ---- LZMA quality maps ----

# quality key → (stdlib-lzma preset, optional dict_size override in bytes)
_QUALITY_MAP: dict[str, tuple[int, Optional[int]]] = {
    "fast":    (1, None),
    "normal":  (6, None),
    "max":     (9, None),
    "ultra64": (9, 64 * 1024 * 1024),
}

# xz CLI preset for each quality key.
# ultra64 maps to the same -9 as max; -9 already uses a 64 MB dict.
_XZ_PRESET: dict[str, int] = {
    "fast":    1,
    "normal":  6,
    "max":     9,
    "ultra64": 9,
}

LZMA_QUALITY_LABELS: dict[str, str] = {
    "fast":    "Fast (lzma2-1)",
    "normal":  "Normal (lzma2-6)",
    "max":     "Max (lzma2-9)",
    "ultra64": "Ultra64 (lzma2-9, 64 MB dict)",
}

# Back-compat alias
QUALITY_LABELS = LZMA_QUALITY_LABELS

# ---- Zstd quality maps ----

_ZSTD_LEVEL_MAP: dict[str, int] = {
    "fast":   1,
    "normal": 9,
    "max":    19,
    "ultra":  22,
}

ZSTD_QUALITY_LABELS: dict[str, str] = {
    "fast":   "Fast (zstd-1)",
    "normal": "Normal (zstd-9)",
    "max":    "Max (zstd-19)",
    "ultra":  "Ultra (zstd-22)",
}

THREAD_OPTIONS = [1, 2, 4, 8, 16, 32]


# ---------------------------------------------------------------------------
# Compression helpers
# ---------------------------------------------------------------------------

def _compress(data: bytes, quality: str, threads: int = 1, codec: str = "lzma") -> bytes:
    """
    Compress *data* using the selected codec.

    codec == "lzma":
      threads > 1 delegates to the ``xz`` CLI (multi-threaded XZ stream).
      threads == 1 uses stdlib lzma (single-threaded).

    codec == "zstd":
      Delegates to the ``zstd`` CLI (avoids Python-binding version mismatches).
    """
    if codec == "zstd":
        level = _ZSTD_LEVEL_MAP.get(quality, 19)
        cmd = ["zstd", f"-{level}", f"-T{threads}", "-c"]
        if level == 22:
            cmd = ["zstd", "--ultra", "-22", f"-T{threads}", "-c"]
        result = subprocess.run(cmd, input=data, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"zstd failed (level={level}, T={threads}): "
                + result.stderr.decode("utf-8", errors="replace")[:300]
            )
        return result.stdout

    # --- LZMA path ---
    if not data:
        return lzma.compress(b"", format=lzma.FORMAT_XZ)

    if threads > 1:
        preset = _XZ_PRESET.get(quality, 9)
        cmd = ["xz", f"-T{threads}", f"-{preset}", "-c"]
        # For preset 9, xz's auto block-size is 3 × 64 MB = 192 MB.
        # Force 64 MB blocks so MT kicks in on any input larger than ~128 MB.
        if preset >= 9:
            cmd.insert(-1, "--block-size=64MiB")
        result = subprocess.run(cmd, input=data, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"xz failed (preset={preset}, T={threads}): "
                + result.stderr.decode("utf-8", errors="replace")[:300]
            )
        return result.stdout

    # Single-threaded: stdlib lzma
    preset, dict_size = _QUALITY_MAP.get(quality, (9, None))
    if dict_size is not None:
        filters = [{"id": lzma.FILTER_LZMA2, "dict_size": dict_size}]
        return lzma.compress(data, format=lzma.FORMAT_XZ, filters=filters)
    return lzma.compress(data, format=lzma.FORMAT_XZ, preset=preset)


# ---------------------------------------------------------------------------
# Main build entry point
# ---------------------------------------------------------------------------

def build(
    game_dir: Path,
    quality: str = "max",
    components: Optional[list] = None,
    threads: int = 1,
    codec: str = "lzma",
    progress: Optional[Callable[[int, str], None]] = None,
) -> tuple[bytes, int, int, list[dict]]:
    """
    Walk *game_dir* (and any optional component folders), compress into
    an XPACK01 blob.

    components: list of {"label": str, "folder": str, ...}
    codec:      "lzma" (XZ/LZMA2) or "zstd"
    threads:    thread count per stream (1 = single-threaded; >1 = MT).
                Streams are always compressed sequentially.

    Returns (xpack01_blob, total_files, total_uncompressed_bytes, file_list).
    file_list is [{"path": str, "component": int, "offset": int, "size": int}].
    progress(pct 0-100, message) is called throughout.
    """
    def _prog(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    game_dir = Path(game_dir)
    components = components or []

    # Build ordered list of (comp_idx, label, folder)
    streams_info = [(0, "base game", game_dir)] + [
        (i + 1, c.get("label", f"component {i + 1}"), Path(c["folder"]))
        for i, c in enumerate(components)
    ]

    _prog(0, "Scanning directories…")

    # Scan file paths and sizes (metadata only — no reading yet)
    stream_specs: list[tuple[int, str, list[tuple[str, str]], int]] = []
    for comp_idx, label, folder in streams_info:
        files = sorted(
            (f for f in folder.rglob("*") if f.is_file()),
            key=lambda f: (f.suffix.lower(), f.as_posix()),
        )
        specs = [(f.relative_to(folder).as_posix(), str(f)) for f in files]
        try:
            estimated = sum(f.stat().st_size for f in files)
        except OSError:
            estimated = 1
        stream_specs.append((comp_idx, label, specs, max(estimated, 1)))

    total_files = sum(len(specs) for _, _, specs, _ in stream_specs)
    total_estimated = sum(est for _, _, _, est in stream_specs)

    if total_files == 0:
        raise ValueError(f"No files found in: {game_dir}")

    num_streams = len(stream_specs)
    stream_word = "stream" if num_streams == 1 else "streams"
    _prog(3, f"Found {total_files} files across {num_streams} {stream_word}. "
             f"Compressing with {threads} thread(s)…")

    all_file_entries: list[dict] = []
    streams_out: list[tuple[int, bytes]] = []  # (comp_idx, compressed_bytes), ordered

    _compress_sequential(
        stream_specs, quality, threads, codec,
        total_estimated, all_file_entries, streams_out, _prog,
    )

    total_uncompressed = sum(e["size"] for e in all_file_entries)

    _prog(95, "Encoding archive…")
    blob = _encode(all_file_entries, streams_out)
    _prog(100, "Archive complete.")

    return blob, total_files, total_uncompressed, all_file_entries


# ---------------------------------------------------------------------------
# Sequential compression path  (num_processes == 1)
# ---------------------------------------------------------------------------

def _compress_sequential(
    stream_specs: list,
    quality: str,
    threads: int,
    codec: str,
    total_estimated: int,
    all_file_entries: list,
    streams_out: list,
    _prog: Callable,
) -> None:
    """Read and compress each stream one at a time."""
    # Cumulative weight boundaries so progress is proportional to stream size.
    weights = [est for _, _, _, est in stream_specs]
    total_w = max(sum(weights), 1)
    cum = [0.0]
    for w in weights:
        cum.append(cum[-1] + w / total_w)

    for stream_idx, (comp_idx, label, specs, _) in enumerate(stream_specs):
        s_start = cum[stream_idx]       # fraction of total at stream start
        s_end   = cum[stream_idx + 1]   # fraction of total at stream end
        s_range = s_end - s_start

        pieces: list[bytes] = []
        file_entries: list[dict] = []
        offset = 0
        total = len(specs)

        for i, (rel, abs_path) in enumerate(specs):
            data = Path(abs_path).read_bytes()
            file_entries.append({
                "path":      rel,
                "offset":    offset,
                "size":      len(data),
                "component": comp_idx,
                "crc32":     zlib.crc32(data) & 0xFFFFFFFF,
            })
            pieces.append(data)
            offset += len(data)
            if (i + 1) % 200 == 0 or i == total - 1:
                # Reading occupies 0→40 % of this stream's share
                frac = (i + 1) / total * 0.4
                pct = 5 + int((s_start + frac * s_range) * 88)
                _prog(pct, f"{label}: reading ({i + 1}/{total} files)…")

        raw = b"".join(pieces)
        del pieces

        _prog(5 + int((s_start + 0.4 * s_range) * 88),
              f"{label}: compressing {_fmt_size(len(raw))}…")

        compressed = _compress(raw, quality, threads, codec)
        del raw

        all_file_entries.extend(file_entries)
        streams_out.append((comp_idx, compressed))

        _prog(5 + int(s_end * 88), f"{label}: done — "
              f"{_fmt_size(len(streams_out[-1][1]))} compressed")


# ---------------------------------------------------------------------------
# Archive encoding
# ---------------------------------------------------------------------------

def _encode(
    files: list[dict],
    streams: list[tuple[int, bytes]],   # (comp_idx, compressed_bytes)
) -> bytes:
    """Pack file table + per-component compressed streams into an XPACK01 blob."""
    buf = bytearray()

    # File table
    buf += struct.pack("<I", len(files))
    for f in files:
        path_b = f["path"].encode("utf-8")
        buf += struct.pack("<H", len(path_b))
        buf += path_b
        buf += struct.pack("<QQII", f["offset"], f["size"], f["component"], f["crc32"])

    # Compressed streams
    buf += struct.pack("<I", len(streams))
    for comp_idx, compressed in streams:
        buf += struct.pack("<I", comp_idx)
        buf += struct.pack("<Q", len(compressed))
        buf += compressed

    return bytes(buf)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
