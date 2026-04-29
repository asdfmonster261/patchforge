"""Compress depotcache/, steamapps/, and optional crack output into a 7z archive.

Native 7z subprocess primary path uses -mmt=on for parallel LZMA2 — much
faster than py7zr's single-threaded fallback.  py7zr is used when the native
binary is unavailable or its download failed.

Volume splitting is post-compression: produces .7z.001, .7z.002, ... when a
volume_size is specified and the archive exceeds it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .sevenzip import get_7zip
from .utils import run_in_thread


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sanitize_name(name: str) -> str:
    """Replace spaces with periods and strip characters unsafe in filenames."""
    name = name.replace(" ", ".")
    name = re.sub(r"[^a-zA-Z0-9.\-]", "", name)
    name = re.sub(r"\.{2,}", ".", name)
    return name.strip(".")


def parse_size(s: str) -> int:
    """Parse a human size string like '4g', '700m', '1024k', or plain bytes."""
    s = s.strip().lower()
    multipliers = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            return int(float(s[:-1]) * mult)
    return int(s)


def _split_file(src: Path, volume_size: int) -> list[Path]:
    """Split src into volume_size-byte chunks named src.001, src.002, ...

    Returns the list of part paths created.  Original file is removed on success.
    """
    parts: list[Path] = []
    part_num = 1
    try:
        with open(src, "rb") as fh:
            while True:
                chunk = fh.read(volume_size)
                if not chunk:
                    break
                part_path = Path(f"{src}.{part_num:03d}")
                part_path.write_bytes(chunk)
                parts.append(part_path)
                part_num += 1
    except Exception:
        for p in parts:
            p.unlink(missing_ok=True)
        raise
    src.unlink()
    return parts


# ---------------------------------------------------------------------------
# Native 7z (preferred)
# ---------------------------------------------------------------------------

_PCT_RE = re.compile(rb"(\d+)%")


def _compress_native(seven_zip: Path, dest: Path, archive_path: Path,
                     compression_level: int, password: str | None,
                     gse_dir: Path | None,
                     on_pct=None) -> None:
    cmd = [
        str(seven_zip), "a", "-t7z",
        f"-mx={compression_level}",
        "-mmt=on",
        "-bso0", "-bse0", "-bsp1",  # silence stdout/stderr, stream % to stdout
        str(archive_path.resolve()),
    ]
    if password:
        cmd += [f"-p{password}", "-mhe=on"]

    sources = ["depotcache", "steamapps"]
    if gse_dir and gse_dir.exists():
        sources.append(gse_dir.name)
    cmd += [s for s in sources if (dest / s).exists()]

    proc = subprocess.Popen(cmd, cwd=dest,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buf = b""
    last_pct = -1
    try:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read1(256)
            if not chunk:
                break
            buf += chunk
            for m in _PCT_RE.finditer(buf):
                pct = int(m.group(1))
                if pct != last_pct:
                    last_pct = pct
                    if on_pct is not None:
                        on_pct(pct)
            buf = buf[-8:]   # keep tail in case a percent match spans chunks
    finally:
        proc.wait()

    if proc.returncode != 0:
        stderr = (proc.stderr.read() if proc.stderr else b"").decode(errors="replace").strip()
        raise RuntimeError(
            f"7z exited with code {proc.returncode}: {stderr}"
        )


# ---------------------------------------------------------------------------
# py7zr fallback
# ---------------------------------------------------------------------------

def _compress_py7zr(dest: Path, archive_path: Path,
                    compression_level: int, password: str | None,
                    gse_dir: Path | None) -> None:
    import py7zr
    if compression_level == 0:
        filters = [{"id": py7zr.FILTER_COPY}]
    else:
        filters = [{"id": py7zr.FILTER_LZMA2, "preset": compression_level}]

    kwargs: dict = {"filters": filters}
    if password:
        kwargs["password"] = password
        kwargs["header_encryption"] = True

    with py7zr.SevenZipFile(archive_path, "w", **kwargs) as z:
        depotcache = dest / "depotcache"
        steamapps  = dest / "steamapps"
        if depotcache.exists():
            z.writeall(depotcache, "depotcache")
        if steamapps.exists():
            z.writeall(steamapps, "steamapps")
        if gse_dir and gse_dir.exists():
            z.writeall(gse_dir, gse_dir.name)


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def compress_platform(dest: Path, archive_stem: str,
                      password: str | None,
                      compression_level: int,
                      volume_size: int | None,
                      gse_dir: Path | None = None,
                      on_event=None,
                      use_native: bool | None = None) -> list[Path]:
    """Compress depotcache/, steamapps/, and optionally a gse_config_*/ folder
    under dest into a 7z archive.

    The archive is written to dest/<archive_stem>.7z.  If volume_size is set
    and the archive exceeds it, the result is split into
    dest/<archive_stem>.7z.001, .002, ... parts and the unsplit file removed.

    on_event, if given, is called with DownloadEvent-shaped objects for stage
    transitions and per-percent compression progress.  May be None (silent).

    use_native overrides binary detection: True forces native (raises if no
    binary), False forces py7zr.  None auto-detects (default).
    """
    from .download import DownloadEvent  # local import to avoid cycle

    archive_path = dest / f"{archive_stem}.7z"

    def _emit(kind: str, **kw):
        if on_event is None:
            return
        on_event(DownloadEvent(kind=kind, **kw))

    seven_zip: Path | None
    if use_native is False:
        seven_zip = None
    elif use_native is True:
        seven_zip = get_7zip()
        if seven_zip is None:
            raise RuntimeError("Native 7z binary unavailable and use_native=True")
    else:
        seven_zip = get_7zip()

    if seven_zip:
        _emit("stage", stage_msg=f"Compressing with native 7z ({seven_zip.name})")
        run_in_thread(_compress_native, seven_zip, dest, archive_path,
                      compression_level, password, gse_dir,
                      lambda pct: _emit("file_progress",
                                        name=archive_path.name,
                                        total=100, done=pct))
    else:
        _emit("stage", stage_msg="Compressing with py7zr (slower fallback)")
        run_in_thread(_compress_py7zr, dest, archive_path,
                      compression_level, password, gse_dir)

    size = archive_path.stat().st_size

    if volume_size and size > volume_size:
        _emit("stage", stage_msg=f"Splitting into {volume_size}-byte volumes")
        parts = run_in_thread(_split_file, archive_path, volume_size)
        return parts
    return [archive_path]
