"""app_settings.py — persistent global application preferences."""

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _config_dir() -> Path:
    """Per-platform config dir: %APPDATA%\\PatchForge on Windows, ~/.config/patchforge elsewhere."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "PatchForge"
    return Path.home() / ".config" / "patchforge"


_CONFIG_DIR = _config_dir()
_SETTINGS_FILE = _CONFIG_DIR / "app_settings.json"


@dataclass
class AppSettings:
    # Auto-split threshold: when pack data exceeds this many GB the builder
    # automatically writes a separate base_game.bin instead of embedding the
    # data inside the exe.  Per-project split_bin=True forces the split
    # regardless of size.
    bin_split_threshold_gb: float = 3.5

    # Persisted GUI state — restored on next launch.  Defaults match the
    # values previously hardcoded in MainWindow.__init__ / _build_ui so
    # first-run users see identical layout to before G5.
    window_width:    int       = 1100
    window_height:   int       = 780
    splitter_sizes:  list[int] = field(default_factory=lambda: [580, 480])
    mode_tab_index:  int       = 0  # 0 = Update Patch, 1 = Repack


def load() -> AppSettings:
    try:
        data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            known = set(AppSettings.__dataclass_fields__)
            return AppSettings(**{k: v for k, v in data.items() if k in known})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return AppSettings()


def save(settings: AppSettings) -> None:
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _SETTINGS_FILE.write_text(
            json.dumps(asdict(settings), indent=2), encoding="utf-8"
        )
    except OSError:
        pass
