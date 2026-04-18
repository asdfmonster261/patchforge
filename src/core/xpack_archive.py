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

import concurrent.futures
import lzma
import multiprocessing
import queue as _queue_mod
import struct
import subprocess
from pathlib import Path
from typing import Callable, Optional

MAGIC = b"XPACK01\x00"

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

QUALITY_LABELS: dict[str, str] = {
    "fast":    "Fast (lzma2-1)",
    "normal":  "Normal (lzma2-6)",
    "max":     "Max (lzma2-9)",
    "ultra64": "Ultra64 (lzma2-9, 64 MB dict)",
}

THREAD_OPTIONS = [1, 2, 4, 8, 16, 32]


# ---------------------------------------------------------------------------
# Compression helpers
# ---------------------------------------------------------------------------

def _compress(data: bytes, quality: str, threads: int = 1) -> bytes:
    """
    Compress *data* to XZ format.

    threads > 1: delegates to the ``xz`` CLI which uses liblzma's native
    multi-threaded encoder (lzma_stream_encoder_mt).  Output is a single
    valid XZ stream with multiple internal blocks — fully compatible with
    the installer's lzma_stream_decoder.

    threads == 1: falls back to stdlib lzma (single-threaded).
    """
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
# ProcessPoolExecutor worker (must be module-level for pickling)
# ---------------------------------------------------------------------------

def _stream_worker(
    file_specs: list[tuple[str, str]],  # [(rel_path, abs_path_str), ...]
    comp_idx: int,
    stream_label: str,
    quality: str,
    threads: int,
    progress_q,
) -> tuple[int, bytes, list[dict]]:
    """
    Child-process worker: read all files for one component stream,
    compress them, and post progress events to *progress_q*.

    Returns (comp_idx, compressed_bytes, file_entries).
    """
    pieces: list[bytes] = []
    file_entries: list[dict] = []
    offset = 0
    total = len(file_specs)

    for i, (rel, abs_path) in enumerate(file_specs):
        data = Path(abs_path).read_bytes()
        file_entries.append({
            "path":      rel,
            "offset":    offset,
            "size":      len(data),
            "component": comp_idx,
        })
        pieces.append(data)
        offset += len(data)
        if (i + 1) % 200 == 0 or i == total - 1:
            progress_q.put({
                "stream":      comp_idx,
                "label":       stream_label,
                "stage":       "reading",
                "files_done":  i + 1,
                "files_total": total,
            })

    raw = b"".join(pieces)
    del pieces

    progress_q.put({
        "stream":   comp_idx,
        "label":    stream_label,
        "stage":    "compressing",
        "raw_size": len(raw),
    })

    compressed = _compress(raw, quality, threads)
    del raw

    progress_q.put({
        "stream":          comp_idx,
        "label":           stream_label,
        "stage":           "done",
        "compressed_size": len(compressed),
    })

    return comp_idx, compressed, file_entries


# ---------------------------------------------------------------------------
# Main build entry point
# ---------------------------------------------------------------------------

def build(
    game_dir: Path,
    quality: str = "max",
    components: Optional[list] = None,
    threads: int = 1,
    progress: Optional[Callable[[int, str], None]] = None,
) -> tuple[bytes, int, int]:
    """
    Walk *game_dir* (and any optional component folders), compress into
    an XPACK01 blob.

    components: list of {"label": str, "folder": str, ...}
    threads:    total worker threads (1 = single-threaded stdlib lzma;
                >1 = xz CLI MT, plus stream-level ProcessPoolExecutor when
                multiple streams exist).

    Returns (xpack01_blob, total_files, total_uncompressed_bytes).
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
    if threads > 1 and num_streams > 1:
        num_processes = min(num_streams, threads)
        threads_per_stream = max(1, threads // num_processes)
        _prog(3, f"Found {total_files} files across {num_streams} streams — "
                 f"compressing with {num_processes} parallel processes, "
                 f"{threads_per_stream} thread(s) each…")
    else:
        num_processes = 1
        threads_per_stream = threads
        _prog(3, f"Found {total_files} files. Compressing with {threads} thread(s)…")

    all_file_entries: list[dict] = []
    streams_out: list[tuple[int, bytes]] = []  # (comp_idx, compressed_bytes), ordered

    if num_processes > 1:
        _compress_parallel(
            stream_specs, quality, threads_per_stream,
            all_file_entries, streams_out, num_streams, _prog,
        )
    else:
        _compress_sequential(
            stream_specs, quality, threads_per_stream,
            total_estimated, all_file_entries, streams_out, _prog,
        )

    total_uncompressed = sum(e["size"] for e in all_file_entries)

    _prog(95, "Encoding archive…")
    blob = _encode(all_file_entries, streams_out)
    _prog(100, "Archive complete.")

    return blob, total_files, total_uncompressed


# ---------------------------------------------------------------------------
# Sequential compression path  (num_processes == 1)
# ---------------------------------------------------------------------------

def _compress_sequential(
    stream_specs: list,
    quality: str,
    threads: int,
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

        compressed = _compress(raw, quality, threads)
        del raw

        all_file_entries.extend(file_entries)
        streams_out.append((comp_idx, compressed))

        _prog(5 + int(s_end * 88), f"{label}: done — "
              f"{_fmt_size(len(streams_out[-1][1]))} compressed")


# ---------------------------------------------------------------------------
# Parallel compression path  (num_processes > 1)
# ---------------------------------------------------------------------------

def _compress_parallel(
    stream_specs: list,
    quality: str,
    threads_per_stream: int,
    all_file_entries: list,
    streams_out: list,
    num_streams: int,
    _prog: Callable,
) -> None:
    """Compress all streams in parallel using ProcessPoolExecutor (spawn)."""
    ctx = multiprocessing.get_context("spawn")

    with ctx.Manager() as manager:
        q = manager.Queue()

        with concurrent.futures.ProcessPoolExecutor(
            max_workers=len(stream_specs),
            mp_context=ctx,
        ) as executor:
            # Submit all streams
            future_map: dict = {}
            for comp_idx, label, specs, _ in stream_specs:
                f = executor.submit(
                    _stream_worker,
                    specs, comp_idx, label, quality, threads_per_stream, q,
                )
                future_map[f] = comp_idx

            # Per-stream progress state (0–100)
            stream_pct: dict[int, float] = {
                comp_idx: 0.0 for comp_idx, *_ in stream_specs
            }
            stream_weight: dict[int, int] = {
                comp_idx: est for comp_idx, _, _, est in stream_specs
            }
            results: dict[int, tuple[bytes, list]] = {}
            pending = set(future_map)

            while pending:
                done_now, pending = concurrent.futures.wait(
                    pending,
                    timeout=0.1,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )

                for f in done_now:
                    comp_idx, compressed, entries = f.result()  # re-raises on error
                    results[comp_idx] = (compressed, entries)

                # Drain progress queue
                while True:
                    try:
                        msg = q.get_nowait()
                    except _queue_mod.Empty:
                        break

                    ci    = msg["stream"]
                    stage = msg["stage"]
                    label = msg["label"]

                    if stage == "reading":
                        stream_pct[ci] = msg["files_done"] / msg["files_total"] * 40
                    elif stage == "compressing":
                        stream_pct[ci] = 40.0
                    elif stage == "done":
                        stream_pct[ci] = 100.0

                    # Weighted overall progress → 5–93 %
                    total_w = sum(stream_weight.values())
                    w_avg = sum(
                        stream_pct[k] * stream_weight[k] for k in stream_pct
                    ) / total_w
                    overall = 5 + int(w_avg * 0.88)

                    done_count = sum(1 for p in stream_pct.values() if p >= 100)

                    if stage == "reading":
                        status = (
                            f"[{done_count}/{num_streams}] {label}: "
                            f"reading ({msg['files_done']}/{msg['files_total']} files)"
                        )
                    elif stage == "compressing":
                        status = (
                            f"[{done_count}/{num_streams}] {label}: "
                            f"compressing {_fmt_size(msg['raw_size'])}…"
                        )
                    else:
                        status = (
                            f"[{done_count}/{num_streams}] {label}: done — "
                            f"{_fmt_size(msg['compressed_size'])} compressed"
                        )
                    _prog(overall, status)

            # Final queue drain (messages that arrived after last wait)
            while True:
                try:
                    msg = q.get_nowait()
                    ci    = msg["stream"]
                    stage = msg["stage"]
                    if stage == "done":
                        stream_pct[ci] = 100.0
                except _queue_mod.Empty:
                    break

    # Assemble results in comp_idx order (stream_specs preserves the order)
    for comp_idx, _, _, _ in stream_specs:
        compressed, entries = results[comp_idx]
        all_file_entries.extend(entries)
        streams_out.append((comp_idx, compressed))


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
        buf += struct.pack("<QQI", f["offset"], f["size"], f["component"])

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
