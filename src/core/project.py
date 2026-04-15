"""PatchForge project file — save/load .xpm (JSON) project state."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ProjectSettings:
    # Basic info
    app_name: str = ""
    version: str = ""
    description: str = ""

    # Directories
    source_dir: str = ""   # original (old) game folder
    target_dir: str = ""   # patched (new) game folder
    output_dir: str = ""   # where to write the output .exe

    # Engine + compression
    engine: str = "hdiffpatch"       # "hdiffpatch" | "xdelta3" | "jojodiff"
    compression: str = "lzma/ultra"  # see compression.py LEVELS

    # Verification
    verify_method: str = "crc32c"    # "crc32c" | "md5" | "filesize"

    # Target file discovery (on the end-user's machine)
    find_method: str = "manual"      # "manual" | "registry" | "ini"
    registry_key: str = ""
    registry_value: str = ""
    ini_path: str = ""
    ini_section: str = ""
    ini_key: str = ""

    # Stub architecture
    arch: str = "x64"   # "x64" | "x86"

    # Thread count for patch generation (HDiffPatch: -p-N; xdelta3/jojodiff dir mode)
    threads: int = 1

    # Optional icon (.ico) to embed in the output exe
    icon_path: str = ""


def save(settings: ProjectSettings, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(settings), f, indent=2)


def load(path: Path) -> ProjectSettings:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Forward-compat: ignore unknown keys
    known = {k for k in ProjectSettings.__dataclass_fields__}
    filtered = {k: v for k, v in data.items() if k in known}
    return ProjectSettings(**filtered)
