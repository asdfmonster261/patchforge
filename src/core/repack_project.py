"""PatchForge repack project file — save/load .xpr (JSON) project state."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class RepackSettings:
    # Basic info
    app_name: str = ""
    app_note: str = ""
    version: str = ""
    description: str = ""
    copyright: str = ""
    contact: str = ""
    company_info: str = ""

    # Output exe customisation
    window_title: str = ""
    installer_exe_name: str = ""      # output exe stem (auto-derived if blank)
    installer_exe_version: str = ""   # informational version string

    # Directories
    game_dir: str = ""    # source game folder to repack
    output_dir: str = ""  # where to write the output .exe

    # Compression — same quality keys as patch mode LZMA2
    # "fast" | "normal" | "max" | "ultra64"
    arch: str = "x64"
    compression: str = "max"

    # Visual customisation
    icon_path: str = ""
    backdrop_path: str = ""

    # Post-install behaviour
    install_registry_key: str = ""   # HKCU\Software\<key>, written after install
    run_after_install: str = ""      # shell command run after successful install
    detect_running_exe: str = ""     # warn if this process is running before install
    close_delay: int = 0             # seconds to auto-close after success (0 = stay open)
    required_free_space_gb: float = 0.0

    # Compression threads (1 = single-threaded stdlib; >1 = xz CLI MT)
    threads: int = 1

    # Optional components — list of dicts:
    #   {"label": str, "folder": str, "default_checked": bool, "group": str}
    # index 0 = base game (always installed); components here start at index 1.
    components: list = field(default_factory=list)


def save(settings: RepackSettings, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(settings), f, indent=2)


def load(path: Path) -> RepackSettings:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    known = {k for k in RepackSettings.__dataclass_fields__}
    filtered = {k: v for k, v in data.items() if k in known}
    return RepackSettings(**filtered)
