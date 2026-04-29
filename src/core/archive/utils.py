"""Shared archive-mode helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def cache_dir() -> Path:
    """Per-platform cache directory for archive-mode binaries (7z, etc.).

    Linux: ~/.cache/patchforge/
    Win:   %LOCALAPPDATA%\\PatchForge\\Cache\\
    Mac:   ~/Library/Caches/PatchForge/

    Kept SEPARATE from the config dir (~/.config/patchforge/) so that wiping
    the cache to force a fresh binary download doesn't blow away credentials
    or settings.
    """
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "PatchForge" / "Cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "PatchForge"
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "patchforge"


def bin_dir() -> Path:
    """Directory where downloaded native binaries (7z, etc.) live."""
    return cache_dir() / "bin"


def gbe_dir() -> Path:
    """Directory where Goldberg Steam Emulator release archives are cached."""
    return cache_dir() / "archive" / "gbe"


def dlc_db_dir() -> Path:
    """Directory where per-app DLC name databases live (one .ini per app)."""
    return cache_dir() / "archive" / "dlc_db"


def run_in_thread(fn, *args, **kwargs):
    """Run a blocking function in gevent's threadpool, yielding to the loop.

    SteamArchiver wraps subprocess / py7zr calls this way to avoid stalling
    the green-pool event loop during multi-second compression runs.  Imported
    lazily so simply importing this module does not require gevent.
    """
    import gevent.hub
    return gevent.get_hub().threadpool.apply(fn, args, kwargs)
