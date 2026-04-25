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

from .compression import xz_command, zstd_command
from .fmt import THREAD_OPTIONS as THREAD_OPTIONS  # re-export for GUI
from .fmt import format_size as _fmt_size

MAGIC = b"XPACK01\x00"

# Read/write chunk size used when streaming files through the compressor.
# 4 MB keeps per-file memory bounded regardless of individual file size.
_COPY_CHUNK = 4 * 1024 * 1024

# Fire the stream_progress "starting file" callback before compressing files
# above this size, so the GUI label updates immediately when a large file
# begins (rather than waiting for the next 200-file batch tick to fire).
_LARGE_FILE_PREFIRE_BYTES = 50 * 1024 * 1024

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


def supported_quality_keys(codec: str) -> set[str]:
    """Return the set of valid quality keys for a given codec."""
    return set(_ZSTD_LEVEL_MAP) if codec == "zstd" else set(_QUALITY_MAP)


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
                r = subprocess.run([zstd_command(), "-1", "-c"], input=b"", capture_output=True)
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
            zstd = zstd_command()
            if level == 22:
                cmd = [zstd, "--ultra", "-22", f"-T{threads}", "-c"]
            else:
                cmd = [zstd, f"-{level}", f"-T{threads}", "-c"]
        else:
            preset = _XZ_PRESET.get(quality, 9)
            cmd = [xz_command(), f"-T{threads}", f"-{preset}", "-c"]
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
                    try:
                        pre_size = os.path.getsize(abs_path)
                    except OSError:
                        pre_size = 0
                    if prog_callback and pre_size > _LARGE_FILE_PREFIRE_BYTES:
                        prog_callback(i, len(specs), pre_size)
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
                        prog_callback(i + 1, len(specs), file_size)
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
            try:
                pre_size = os.path.getsize(abs_path)
            except OSError:
                pre_size = 0
            if prog_callback and pre_size > _LARGE_FILE_PREFIRE_BYTES:
                prog_callback(i, len(specs), pre_size)
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
                prog_callback(i + 1, len(specs), file_size)

        out_f.write(compressor.flush())


# ---------------------------------------------------------------------------
# Main build entry point
# ---------------------------------------------------------------------------

def _safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s).strip("_")


def build(
    game_dir: Path,
    quality: str = "max",
    components: Optional[list] = None,
    threads: int = 1,
    codec: str = "lzma",
    progress: Optional[Callable[[int, str], None]] = None,
    tmp_dir: Optional[Path] = None,
    stream_progress: Optional[Callable[[int, int, str, int, int, str], None]] = None,
) -> tuple[Path, int, int, list[dict], dict[int, dict]]:
    """
    Walk *game_dir* (and any optional component folders), compress into an
    XPACK01 temp file, and return its path.

    Returns (xpack_tmp_path, total_files, total_uncompressed_bytes,
             file_list, ext_info).

    ext_info maps component index → {"path": Path, "offset": int, "csize": int}
    for every component with external=True.  Components sharing the same group
    name are written sequentially into the same sidecar .bin file.  Their
    streams appear as csize=0 sentinels in the XPACK01 blob; the actual
    compressed data lives in the sidecars.

    The caller is responsible for deleting xpack_tmp_path after use; the
    sidecar .bin files are permanent outputs.

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

    # Build external-component bin-file map with group-awareness.
    # Components sharing the same group share the same .bin file.
    out_dir = tmp_dir or Path(".")
    group_to_name: dict[str, str] = {}   # group name → assigned bin filename
    used_names:    set[str]       = set()
    ext_bin_paths: dict[int, Path] = {}   # comp_idx → bin Path

    for i, c in enumerate(components):
        if c.get("external", False):
            comp_idx = i + 1
            group    = c.get("group", "").strip()
            label    = c.get("label", "").strip()

            if group and group in group_to_name:
                name = group_to_name[group]          # reuse same bin for same group
            else:
                raw  = group or label or f"component_{comp_idx}"
                base = _safe_name(raw) or f"component_{comp_idx}"
                name = f"{base}.bin"
                if name in used_names:               # name collision
                    name = f"{base}_{comp_idx}.bin"
                used_names.add(name)
                if group:
                    group_to_name[group] = name

            ext_bin_paths[comp_idx] = out_dir / name

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
    streams_out: list[tuple[int, Path, int]] = []
    xpack_tmp: Optional[Path] = None

    try:
        _compress_sequential(
            stream_specs, quality, threads, codec,
            all_file_entries, streams_out, _prog, tmp_dir,
            stream_progress=stream_progress,
        )

        total_uncompressed = sum(e["size"] for e in all_file_entries)

        _prog(95, "Encoding archive…")
        xpack_tmp, ext_offsets, ext_csizes = _assemble_xpack(
            all_file_entries, streams_out, tmp_dir, ext_bin_paths,
        )
        _prog(100, "Archive complete.")
    except BaseException:
        # Clean up any partial temp files and sidecars we may have written
        # before re-raising. Stream temp files live under tmp_dir with a
        # .xpk_stream suffix; sidecars have user-facing paths in ext_bin_paths.
        for _, p, _ in streams_out:
            try: p.unlink(missing_ok=True)
            except OSError: pass
        if xpack_tmp:
            try: xpack_tmp.unlink(missing_ok=True)
            except OSError: pass
        for bp in ext_bin_paths.values():
            try: bp.unlink(missing_ok=True)
            except OSError: pass
        raise

    ext_info = {
        comp_idx: {
            "path":   ext_bin_paths[comp_idx],
            "offset": ext_offsets[comp_idx],
            "csize":  ext_csizes[comp_idx],
        }
        for comp_idx in ext_bin_paths
    }
    return xpack_tmp, total_files, total_uncompressed, all_file_entries, ext_info


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
    stream_progress: Optional[Callable[[int, int, str, int, int, str], None]] = None,
) -> None:
    """Compress each stream in turn, writing to temp files."""
    weights = [est for _, _, _, est in stream_specs]
    total_w = max(sum(weights), 1)
    cum = [0.0]
    for w in weights:
        cum.append(cum[-1] + w / total_w)

    num_streams = len(stream_specs)

    for stream_idx, (comp_idx, label, specs, _) in enumerate(stream_specs):
        s_start = cum[stream_idx]
        s_end   = cum[stream_idx + 1]
        s_range = s_end - s_start
        total = len(specs)

        _prog(5 + int(s_start * 88),
              f"{label}: compressing {total} file(s)…")
        if stream_progress:
            stream_progress(stream_idx, num_streams, label, 0, max(total, 1), "")

        file_entries: list[dict] = []

        def _file_prog(done: int, tot: int, file_size: int = 0,
                       _lbl=label, _ss=s_start, _sr=s_range,
                       _si=stream_idx, _ns=num_streams) -> None:
            pct = 5 + int((_ss + done / tot * _sr) * 88)
            _prog(pct, f"{_lbl}: {done}/{tot} files…")
            if stream_progress:
                size_str = _fmt_size(file_size) if file_size else ""
                stream_progress(_si, _ns, _lbl, done, tot, size_str)

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
    ext_bin_paths: dict[int, Path] | None = None,
) -> tuple[Path, dict[int, int], dict[int, int]]:
    """
    Assemble the XPACK01 blob into a new temp file, streaming each compressed
    stream from its own temp file.  Cleans up the per-stream temp files.

    Streams whose comp_idx appears in ext_bin_paths are written to their
    sidecar .bin file instead of embedded; a csize=0 sentinel is written in
    the XPACK01 so the installer knows to look externally.  Multiple streams
    that share the same sidecar path are written sequentially into that file.

    Returns (xpack_path, ext_offsets, ext_csizes) where ext_offsets and
    ext_csizes map comp_idx → byte offset within the sidecar and compressed
    size respectively.
    """
    ext_bin_paths = ext_bin_paths or {}
    ext_offsets: dict[int, int] = {}
    ext_csizes:  dict[int, int] = {}

    # Track write position per sidecar file so groups accumulate correctly.
    bin_write_pos: dict[Path, int] = {}
    sidecar_handles: dict[Path, object] = {}

    fd, xpack_name = tempfile.mkstemp(suffix=".xpack01", dir=tmp_dir)
    # Take ownership of fd immediately so a sidecar-open failure below
    # can't leak it.  out is closed in the finally clause.
    out = os.fdopen(fd, "wb")
    try:
        # Open each distinct sidecar file once for writing.  Done inside the
        # try block so the finally clause below closes any handles opened so
        # far if a later open() raises mid-loop.
        for comp_idx, _tmp, _csize in streams:
            if comp_idx in ext_bin_paths:
                bp = ext_bin_paths[comp_idx]
                if bp not in sidecar_handles:
                    sidecar_handles[bp] = open(bp, "wb")
                    bin_write_pos[bp] = 0

        # File table
        out.write(struct.pack("<I", len(files)))
        for f in files:
            path_b = f["path"].encode("utf-8")
            if len(path_b) > 0xFFFF:
                raise ValueError(
                    f"Path too long for XPACK01 (UTF-8 byte length "
                    f"{len(path_b)} exceeds 65535): {f['path']!r}"
                )
            out.write(struct.pack("<H", len(path_b)))
            out.write(path_b)
            out.write(struct.pack("<QQII",
                                  f["offset"], f["size"],
                                  f["component"], f["crc32"]))

        # Streams
        out.write(struct.pack("<I", len(streams)))
        for comp_idx, tmp_path, csize in streams:
            if comp_idx in ext_bin_paths:
                bp  = ext_bin_paths[comp_idx]
                off = bin_write_pos[bp]
                ext_offsets[comp_idx] = off
                ext_csizes[comp_idx]  = csize
                bin_write_pos[bp]     = off + csize
                # Write csize=0 sentinel in the XPACK01.
                out.write(struct.pack("<I", comp_idx))
                out.write(struct.pack("<Q", 0))
                with open(tmp_path, "rb") as sf:
                    shutil.copyfileobj(sf, sidecar_handles[bp])
            else:
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
        out.close()
        for fh in sidecar_handles.values():
            fh.close()
        for _, tmp_path, _ in streams:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    return Path(xpack_name), ext_offsets, ext_csizes
