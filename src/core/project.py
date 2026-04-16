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

    # Compressor quality for HDiffPatch (lzma2: fast/normal/max/ultra64;
    # bzip2: fast/normal/max).  Ignored by xdelta3 and JojoDiff.
    compressor_quality: str = "max"

    # Optional icon (.ico) to embed in the output exe
    icon_path: str = ""

    # Feature: custom diff parameters (passed verbatim to the engine CLI)
    extra_diff_args: str = ""

    # Feature: patching behaviour
    delete_extra_files: bool = True   # delete game files absent from target version
    run_before: str = ""              # shell command run before patching starts
    run_after:  str = ""              # shell command run after patching succeeds

    # Feature: backup
    backup_at: str = "same_folder"   # "disabled" | "same_folder" | "custom"
    backup_path: str = ""            # used when backup_at == "custom"

    # Feature: backdrop image shown in patcher window (PNG/JPEG/BMP)
    backdrop_path: str = ""

    # Feature: extra files to write into the game folder after patching
    # Each entry: {"src": absolute_path_on_build_machine, "dest": relative_path_in_game_folder}
    extra_files: list = field(default_factory=list)


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
