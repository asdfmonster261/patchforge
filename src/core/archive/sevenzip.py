"""Native 7z binary management — download from ip7z/7zip releases on demand,
cache under PatchForge's per-user cache dir, fall back to py7zr for the
unsupported / failure paths.

Why native: -mmt=on multithreaded LZMA2 compression is significantly faster
than py7zr's pure-Python single-threaded equivalent on large depots.

Why download instead of vendoring the binary in the repo: keeps the repo
small, picks up upstream point releases, and doesn't ship binaries we'd
have to update on every release cycle.  First-archive run pays the
~1.5MB download cost once.
"""

from __future__ import annotations

import platform
import stat
import sys
import tarfile
import urllib.request
from functools import cache
from pathlib import Path

from .utils import bin_dir


SEVEN_ZIP_VERSION = "26.00"
SEVEN_ZIP_TAG     = "2600"

_DOWNLOAD_URLS = {
    ("windows", "x86_64"): (
        f"https://github.com/ip7z/7zip/releases/download/{SEVEN_ZIP_VERSION}/7zr.exe"
    ),
    ("linux",   "x86_64"): (
        f"https://github.com/ip7z/7zip/releases/download/{SEVEN_ZIP_VERSION}"
        f"/7z{SEVEN_ZIP_TAG}-linux-x64.tar.xz"
    ),
    ("linux",   "arm64"): (
        f"https://github.com/ip7z/7zip/releases/download/{SEVEN_ZIP_VERSION}"
        f"/7z{SEVEN_ZIP_TAG}-linux-arm64.tar.xz"
    ),
}

_BIN_NAMES = {
    "windows": "7zr.exe",
    "linux":   "7z",
}

# Linux upstream tarball includes 7zzs (static), 7zz, and a wrapper named 7z.
# We prefer the static binary for portability across libc versions.
_LINUX_BIN_CANDIDATES = ("7zzs", "7zz", "7z")


def _detect() -> tuple[str | None, str | None]:
    system  = sys.platform
    machine = platform.machine().lower()

    if system == "win32":
        os_name = "windows"
    elif system == "linux":
        os_name = "linux"
    elif system == "darwin":
        # ip7z does not ship a macOS binary.  Caller falls back to py7zr.
        return None, None
    else:
        return None, None

    if machine in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        return os_name, None

    return os_name, arch


def _download_to(url: str, dest: Path) -> None:
    """Wrapper around urlretrieve so tests can monkey-patch it cleanly."""
    urllib.request.urlretrieve(url, dest)


@cache
def get_7zip() -> Path | None:
    """Return path to a native 7z binary, downloading on first use.

    Returns None if the platform is unsupported (macOS), the architecture
    is not in our binary table, or the download/extract failed.  Caller
    should fall back to py7zr in any of those cases.

    Cached so subsequent calls within one process don't re-stat the disk.
    """
    os_name, arch = _detect()
    if not os_name or not arch:
        return None

    target = bin_dir() / _BIN_NAMES[os_name]
    if target.is_file():
        return target

    url = _DOWNLOAD_URLS.get((os_name, arch))
    if not url:
        return None

    bin_dir().mkdir(parents=True, exist_ok=True)

    try:
        if os_name == "windows":
            _download_to(url, target)

        elif os_name == "linux":
            tmp = target.with_suffix(".tar.xz")
            _download_to(url, tmp)
            try:
                with tarfile.open(tmp, "r:xz") as tf:
                    names = tf.getnames()
                    candidate = next(
                        (n for n in _LINUX_BIN_CANDIDATES if n in names), None
                    )
                    if candidate is None:
                        raise RuntimeError(
                            f"Could not find 7z binary in archive (members: {names})"
                        )
                    member = tf.getmember(candidate)
                    member.name = _BIN_NAMES[os_name]
                    tf.extract(member, bin_dir(), filter="data")
            finally:
                tmp.unlink(missing_ok=True)
            target.chmod(target.stat().st_mode
                         | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    except Exception:
        target.unlink(missing_ok=True)
        return None

    return target


def reset_cache() -> None:
    """Clear get_7zip's lru_cache.  Used by tests; not part of normal flow."""
    get_7zip.cache_clear()
