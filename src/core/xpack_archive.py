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
    [4B LE:  crc32]                — IEEE 802.3 CRC32 of uncompressed file data
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

Memory model
------------
Files are piped one at a time through the compressor — the entire game is
never loaded into RAM simultaneously.  Compressed output is written directly
to a temp file on disk.  The caller receives a Path to that temp file and is
responsible for deleting it after packaging.  Peak in-process memory is
bounded by a single file read + the compressor's own internal buffers.
"""

import lzma
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import zlib
from pathlib import Path
from typing import Callable, Optional

MAGIC = b"XPACK01\x00"

# Read/write chunk size used when streaming files through the compressor.
# 4 MB keeps per-file memory bounded regardless of individual file size.
_COPY_CHUNK = 4 * 1024 * 1024

# ---- LZMA quality maps ----

# quality key → (stdlib-lzma preset, optional dict_size override in bytes)
_QUALITY_MAP: dict[str, tuple[int, Optional[int]]] = {
    "fast":   (1, None),
    "normal": (6, None),
    "max":    (9, None),
}

_XZ_PRESET: dict[str, int] = {
    "fast":   1,
    "normal": 6,
    "max":    9,
}

LZMA_QUALITY_LABELS: dict[str, str] = {
    "fast":   "Fast (lzma2-1)",
    "normal": "Normal (lzma2-6)",
    "max":    "Max (lzma2-9)",
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

def _build_thread_options() -> list[int]:
    cores = os.cpu_count() or 1
    opts: list[int] = []
    p = 1
    while p <= cores:
        opts.append(p)
        p *= 2
    if opts[-1] != cores:
        opts.append(cores)
    return opts

THREAD_OPTIONS = _build_thread_options()


# ---------------------------------------------------------------------------
# Internal: compress one component's files directly to a temp file
# ---------------------------------------------------------------------------

def _compress_stream_to_tmpfile(
    comp_idx: int,
    specs: list[tuple[str, str]],
    quality: str,
    threads: int,
    codec: str,
    file_entries_out: list,
    prog_callback: Optional[Callable[[int, int], None]] = None,
    tmp_dir: Optional[Path] = None,
) -> Path:
    """
    Stream all files in *specs* through the compressor, writing compressed
    output to a new temp file.  Returns the Path to that temp file.

    Populates *file_entries_out* with one dict per file.
    The caller must delete the returned Path when done.
    """
    fd, tmp_name = tempfile.mkstemp(suffix=".xpk_stream", dir=tmp_dir)
    try:
        with os.fdopen(fd, "wb") as out_f:
            _do_compress_to_file(
                comp_idx, specs, quality, threads, codec,
                file_entries_out, prog_callback, out_f,
            )
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return Path(tmp_name)


def _do_compress_to_file(
    comp_idx: int,
    specs: list[tuple[str, str]],
    quality: str,
    threads: int,
    codec: str,
    file_entries_out: list,
    prog_callback: Optional[Callable[[int, int], None]],
    out_f,
) -> None:
    """Write compressed stream for *specs* into *out_f*.  Never loads all
    file data into memory at once: files are read and forwarded one at a time."""

    if not specs:
        # Produce a valid empty compressed stream so the reader doesn't choke.
        if codec == "zstd":
            try:
                r = subprocess.run(["zstd", "-1", "-c"], input=b"", capture_output=True)
                if r.returncode == 0:
                    out_f.write(r.stdout)
                    return
            except FileNotFoundError:
                pass
        out_f.write(lzma.compress(b"", format=lzma.FORMAT_XZ))
        return

    use_cli = (codec == "zstd") or (threads > 1)

    if use_cli:
        if codec == "zstd":
            level = _ZSTD_LEVEL_MAP.get(quality, 19)
            if level == 22:
                cmd = ["zstd", "--ultra", "-22", f"-T{threads}", "-c"]
            else:
                cmd = ["zstd", f"-{level}", f"-T{threads}", "-c"]
        else:
            preset = _XZ_PRESET.get(quality, 9)
            cmd = ["xz", f"-T{threads}", f"-{preset}", "-c"]
            if preset >= 9:
                cmd.insert(-1, "--block-size=64MiB")

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            tool = "zstd" if codec == "zstd" else "xz"
            raise RuntimeError(f"{tool} not found — install it and ensure it is on PATH")

        write_error: list[Optional[Exception]] = [None]
        stderr_buf: list[bytes] = []

        def _write_stdin() -> None:
            stream_offset = 0
            try:
                for i, (rel, abs_path) in enumerate(specs):
                    file_size = 0
                    crc = 0
                    with open(abs_path, "rb") as fh:
                        while True:
                            chunk = fh.read(_COPY_CHUNK)
                            if not chunk:
                                break
                            crc = zlib.crc32(chunk, crc)
                            file_size += len(chunk)
                            proc.stdin.write(chunk)
                    file_entries_out.append({
                        "path":      rel,
                        "offset":    stream_offset,
                        "size":      file_size,
                        "component": comp_idx,
                        "crc32":     crc & 0xFFFFFFFF,
                    })
                    stream_offset += file_size
                    if prog_callback and ((i + 1) % 200 == 0 or i + 1 == len(specs)):
                        prog_callback(i + 1, len(specs))
            except Exception as exc:
                write_error[0] = exc
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        def _read_stderr() -> None:
            stderr_buf.append(proc.stderr.read())

        writer = threading.Thread(target=_write_stdin, daemon=True)
        stderr_reader = threading.Thread(target=_read_stderr, daemon=True)
        writer.start()
        stderr_reader.start()

        # Stream compressor stdout directly to temp file — no in-memory buffer.
        shutil.copyfileobj(proc.stdout, out_f)

        writer.join()
        stderr_reader.join()
        proc.wait()

        if write_error[0]:
            raise write_error[0]
        if proc.returncode != 0:
            tool = "zstd" if codec == "zstd" else "xz"
            err = b"".join(stderr_buf).decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"{tool} failed (returncode={proc.returncode}): {err}")

    else:
        # Single-threaded stdlib lzma — incremental compressor, one file at a time.
        preset, dict_size = _QUALITY_MAP.get(quality, (9, None))
        if dict_size is not None:
            filters = [{"id": lzma.FILTER_LZMA2, "dict_size": dict_size}]
            compressor = lzma.LZMACompressor(format=lzma.FORMAT_XZ, filters=filters)
        else:
            compressor = lzma.LZMACompressor(format=lzma.FORMAT_XZ, preset=preset)

        stream_offset = 0
        for i, (rel, abs_path) in enumerate(specs):
            file_size = 0
            crc = 0
            with open(abs_path, "rb") as fh:
                while True:
                    chunk = fh.read(_COPY_CHUNK)
                    if not chunk:
                        break
                    crc = zlib.crc32(chunk, crc)
                    file_size += len(chunk)
                    out_chunk = compressor.compress(chunk)
                    if out_chunk:
                        out_f.write(out_chunk)
            file_entries_out.append({
                "path":      rel,
                "offset":    stream_offset,
                "size":      file_size,
                "component": comp_idx,
                "crc32":     crc & 0xFFFFFFFF,
            })
            stream_offset += file_size
            if prog_callback and ((i + 1) % 200 == 0 or i + 1 == len(specs)):
                prog_callback(i + 1, len(specs))

        out_f.write(compressor.flush())


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
    tmp_dir: Optional[Path] = None,
) -> tuple[Path, int, int, list[dict]]:
    """
    Walk *game_dir* (and any optional component folders), compress into an
    XPACK01 temp file, and return its path.

    Returns (xpack_tmp_path, total_files, total_uncompressed_bytes, file_list).
    The caller is responsible for deleting *xpack_tmp_path* after use.

    file_list is [{"path": str, "component": int, "offset": int, "size": int}].
    progress(pct 0-100, message) is called throughout.
    """
    def _prog(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    game_dir = Path(game_dir)
    components = components or []

    streams_info = [(0, "base game", game_dir)] + [
        (i + 1, c.get("label", f"component {i + 1}"), Path(c["folder"]))
        for i, c in enumerate(components)
    ]

    _prog(0, "Scanning directories…")

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

    if total_files == 0:
        raise ValueError(f"No files found in: {game_dir}")

    num_streams = len(stream_specs)
    stream_word = "stream" if num_streams == 1 else "streams"
    _prog(3, f"Found {total_files} files across {num_streams} {stream_word}. "
             f"Compressing with {threads} thread(s)…")

    all_file_entries: list[dict] = []
    # (comp_idx, stream_tmp_path, compressed_size)
    streams_out: list[tuple[int, Path, int]] = []

    _compress_sequential(
        stream_specs, quality, threads, codec,
        all_file_entries, streams_out, _prog, tmp_dir,
    )

    total_uncompressed = sum(e["size"] for e in all_file_entries)

    _prog(95, "Encoding archive…")
    xpack_tmp = _assemble_xpack(all_file_entries, streams_out, tmp_dir)
    _prog(100, "Archive complete.")

    return xpack_tmp, total_files, total_uncompressed, all_file_entries


# ---------------------------------------------------------------------------
# Sequential compression
# ---------------------------------------------------------------------------

def _compress_sequential(
    stream_specs: list,
    quality: str,
    threads: int,
    codec: str,
    all_file_entries: list,
    streams_out: list,
    _prog: Callable,
    tmp_dir: Optional[Path] = None,
) -> None:
    """Compress each stream in turn, writing to temp files."""
    weights = [est for _, _, _, est in stream_specs]
    total_w = max(sum(weights), 1)
    cum = [0.0]
    for w in weights:
        cum.append(cum[-1] + w / total_w)

    for stream_idx, (comp_idx, label, specs, _) in enumerate(stream_specs):
        s_start = cum[stream_idx]
        s_end   = cum[stream_idx + 1]
        s_range = s_end - s_start
        total = len(specs)

        _prog(5 + int(s_start * 88),
              f"{label}: compressing {total} file(s)…")

        file_entries: list[dict] = []

        def _file_prog(done: int, tot: int,
                       _lbl=label, _ss=s_start, _sr=s_range) -> None:
            pct = 5 + int((_ss + done / tot * _sr) * 88)
            _prog(pct, f"{_lbl}: {done}/{tot} files…")

        stream_tmp = _compress_stream_to_tmpfile(
            comp_idx, specs, quality, threads, codec,
            file_entries, _file_prog, tmp_dir,
        )

        all_file_entries.extend(file_entries)
        csize = stream_tmp.stat().st_size
        streams_out.append((comp_idx, stream_tmp, csize))

        _prog(5 + int(s_end * 88),
              f"{label}: done — {_fmt_size(csize)} compressed")


# ---------------------------------------------------------------------------
# XPACK01 assembly — stream temp files into the final blob temp file
# ---------------------------------------------------------------------------

def _assemble_xpack(
    files: list[dict],
    streams: list[tuple[int, Path, int]],  # (comp_idx, tmp_path, csize)
    tmp_dir: Optional[Path] = None,
) -> Path:
    """
    Assemble the XPACK01 blob into a new temp file, streaming each compressed
    stream from its own temp file.  Cleans up the per-stream temp files.
    Returns a Path to the assembled XPACK01 temp file.
    """
    fd, xpack_name = tempfile.mkstemp(suffix=".xpack01", dir=tmp_dir)
    try:
        with os.fdopen(fd, "wb") as out:
            # File table
            out.write(struct.pack("<I", len(files)))
            for f in files:
                path_b = f["path"].encode("utf-8")
                out.write(struct.pack("<H", len(path_b)))
                out.write(path_b)
                out.write(struct.pack("<QQII",
                                      f["offset"], f["size"],
                                      f["component"], f["crc32"]))

            # Streams
            out.write(struct.pack("<I", len(streams)))
            for comp_idx, tmp_path, csize in streams:
                out.write(struct.pack("<I", comp_idx))
                out.write(struct.pack("<Q", csize))
                with open(tmp_path, "rb") as sf:
                    shutil.copyfileobj(sf, out)
    except Exception:
        try:
            os.unlink(xpack_name)
        except OSError:
            pass
        raise
    finally:
        for _, tmp_path, _ in streams:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    return Path(xpack_name)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
