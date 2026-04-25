"""PatchForge project file — save/load .xpm (JSON) project state."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Bump this whenever an existing field's semantics change in a
# non-additive way (renamed values, restructured shapes). Pure additions
# of new fields don't require a bump — defaults handle backfill.
_CURRENT_SCHEMA = 1


@dataclass
class ProjectSettings:
    # Schema version of the on-disk format (see _CURRENT_SCHEMA).
    schema_version: int = _CURRENT_SCHEMA

    # Basic info
    app_name: str = ""
    app_note: str = ""        # short subtitle shown next to app name
    version: str = ""
    description: str = ""
    copyright: str = ""
    contact: str = ""
    company_info: str = ""

    # Output exe customisation
    window_title: str = ""    # title bar text (falls back to app_name)
    patch_exe_name: str = ""  # output exe stem (auto-derived from app_name if blank)
    patch_exe_version: str = ""  # informational version string for the patch exe

    # Directories
    source_dir: str = ""   # original (old) game folder
    target_dir: str = ""   # patched (new) game folder
    output_dir: str = ""   # where to write the output .exe

    # Engine + compression
    engine: str = "hdiffpatch"       # "hdiffpatch" | "xdelta3" | "jojodiff"
    compression: str = "set6_lzma2"  # engine-specific preset key; hdiffpatch default

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

    # Compressor quality for HDiffPatch (fast/normal/max).
    # Ignored by xdelta3 and JojoDiff.
    compressor_quality: str = "max"

    # Optional icon (.ico) to embed in the output exe
    icon_path: str = ""

    # Feature: custom diff parameters (passed verbatim to the engine CLI)
    extra_diff_args: str = ""

    # Feature: patching behaviour
    delete_extra_files: bool = True   # delete game files absent from target version
    run_on_startup: str = ""          # shell command run when patcher window opens
    run_before: str = ""              # shell command run before patching starts
    run_after:  str = ""              # shell command run after patching succeeds
    run_on_finish: str = ""           # shell command run after successful patch + dialog
    detect_running_exe: str = ""      # warn if this process is running before patching

    # Feature: backup
    backup_at: str = "same_folder"   # "disabled" | "same_folder" | "custom"
    backup_path: str = ""            # used when backup_at == "custom"

    # Feature: backdrop image shown in patcher window (PNG/JPEG/BMP)
    backdrop_path: str = ""

    # Feature: patcher UX options
    close_delay: int = 0               # seconds to auto-close after success (0 = stay open)
    required_free_space_gb: float = 0.0  # minimum free disk space in GB (0 = no check)
    preserve_timestamps: bool = False  # restore original file mtimes after patching

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
    # Files written before the schema_version field existed default to 1.
    version = int(data.get("schema_version", 1))
    if version > _CURRENT_SCHEMA:
        raise ValueError(
            f"{path} was written by a newer PatchForge "
            f"(schema {version}, this build supports {_CURRENT_SCHEMA}). "
            f"Upgrade PatchForge to open this project."
        )
    # Forward-compat: ignore unknown keys
    known = {k for k in ProjectSettings.__dataclass_fields__}
    filtered = {k: v for k, v in data.items() if k in known}
    return ProjectSettings(**filtered)
