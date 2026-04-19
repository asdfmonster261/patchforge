"""recent_files.py — persist a short list of recently opened project files."""

import json
from pathlib import Path
from typing import Literal

MAX_RECENT = 10
Kind = Literal["patch", "repack"]

_CONFIG_DIR = Path.home() / ".config" / "patchforge"
_RECENT_FILE = _CONFIG_DIR / "recent.json"


def load() -> list[dict]:
    """Return list of {path, kind} dicts, most-recent first."""
    try:
        data = json.loads(_RECENT_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict) and "path" in e and "kind" in e]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return []


def add(path: Path | str, kind: Kind) -> None:
    """Prepend *path* to the recent list, deduplicating and capping at MAX_RECENT."""
    entries = load()
    path_str = str(Path(path).resolve())
    entries = [e for e in entries if e["path"] != path_str]
    entries.insert(0, {"path": path_str, "kind": kind})
    entries = entries[:MAX_RECENT]
    _save(entries)


def remove(path: Path | str) -> None:
    path_str = str(Path(path).resolve())
    entries = [e for e in load() if e["path"] != path_str]
    _save(entries)


def clear() -> None:
    _save([])


def _save(entries: list[dict]) -> None:
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _RECENT_FILE.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    except OSError:
        pass
