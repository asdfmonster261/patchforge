"""Read/write archive_depots.ini — the user-only depot ID → name mapping.

Located at ~/.config/patchforge/archive_depots.ini (Linux) or under
%APPDATA%\\PatchForge\\ on Windows, alongside archive_credentials.json.

NO vendored read-only default ships — the file starts empty and grows as
unknown depot IDs are encountered during downloads.  Users edit names
manually (or via the GUI page) to fill them in.
"""

from __future__ import annotations

import configparser
import os
import sys
from pathlib import Path


def _config_dir() -> Path:
    """PatchForge user config dir.  Mirrors src/core/app_settings.py."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "PatchForge"
    return Path.home() / ".config" / "patchforge"


_DEPOTS_FILE = _config_dir() / "archive_depots.ini"


def depots_path() -> Path:
    """Return the absolute path to archive_depots.ini (may not exist)."""
    return _DEPOTS_FILE


def load() -> dict[str, str]:
    """Return the depot ID → name map.  Missing file yields {}."""
    if not _DEPOTS_FILE.exists():
        return {}
    cp = configparser.ConfigParser()
    cp.read(_DEPOTS_FILE, encoding="utf-8")
    if not cp.has_section("depots"):
        return {}
    return dict(cp["depots"])


def record_unknown(unknown_depot_ids: list[str]) -> list[str]:
    """Append depot IDs to archive_depots.ini as blank entries.

    Skips IDs already present in the file.  Returns the list of IDs actually
    added (caller can use this to print "added N unknown depot(s)").  Creates
    the file (and config dir) if it doesn't exist.
    """
    if not unknown_depot_ids:
        return []

    cp = configparser.ConfigParser()
    if _DEPOTS_FILE.exists():
        cp.read(_DEPOTS_FILE, encoding="utf-8")
    if not cp.has_section("depots"):
        cp.add_section("depots")

    added = [d for d in unknown_depot_ids if not cp.has_option("depots", d)]
    if not added:
        return []

    for depot_id in added:
        cp.set("depots", depot_id, "")

    _DEPOTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _DEPOTS_FILE.open("w", encoding="utf-8") as fh:
        cp.write(fh)
    return added
