"""
repack_builder.py — Orchestrates the full repack build process.

Steps:
  1. Validate inputs
  2. Walk game_dir, compress all files into an XPACK01 solid archive
  3. Build metadata JSON
  4. Package installer_stub + XPACK01 blob + backdrop + metadata into output .exe
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .repack_project import RepackSettings
from .xpack_archive import build as build_archive
from .exe_packager import package_repack


@dataclass
class RepackResult:
    success: bool
    output_path: Optional[Path] = None
    total_files: int = 0
    uncompressed_size: int = 0
    output_size: int = 0
    error: str = ""


def build(
    settings: RepackSettings,
    progress: Optional[Callable[[int, str], None]] = None,
) -> RepackResult:
    """
    Build a self-contained Windows installer exe from settings.

    progress(pct, message) is called with 0–100 as the build proceeds.
    """

    def _progress(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    # ------------------------------------------------------------------ #
    # 1. Validate                                                          #
    # ------------------------------------------------------------------ #
    game_dir = Path(settings.game_dir)

    if not game_dir.exists():
        return RepackResult(success=False, error=f"Game directory not found: {game_dir}")
    if not game_dir.is_dir():
        return RepackResult(success=False, error=f"Game path is not a directory: {game_dir}")
    if not settings.app_name.strip():
        return RepackResult(success=False, error="App name is required")
    if settings.arch not in ("x64", "x86"):
        return RepackResult(success=False,
                            error=f"Invalid architecture: {settings.arch!r} (must be x64 or x86)")
    if settings.threads < 1 or settings.threads > 256:
        return RepackResult(success=False,
                            error=f"Invalid thread count: {settings.threads} (must be 1–256)")
    if settings.codec not in ("lzma", "zstd"):
        return RepackResult(success=False,
                            error=f"Unknown codec: {settings.codec!r} (must be lzma or zstd)")
    from .xpack_archive import _QUALITY_MAP, _ZSTD_LEVEL_MAP
    valid_qualities = _ZSTD_LEVEL_MAP if settings.codec == "zstd" else _QUALITY_MAP
    if settings.compression not in valid_qualities:
        return RepackResult(success=False,
                            error=f"Unknown compression quality for {settings.codec}: {settings.compression!r}")

    for i, c in enumerate(settings.components or []):
        cf = Path(c.get("folder", ""))
        if not cf.is_dir():
            return RepackResult(
                success=False,
                error=f"Component {i + 1} folder not found: {cf}"
            )

    _progress(5, "Validating…")

    # ------------------------------------------------------------------ #
    # 2. Build XPACK01 archive                                            #
    # ------------------------------------------------------------------ #
    def _archive_prog(pct: int, msg: str) -> None:
        # Map archive progress (0-100) to overall range 10-75
        _progress(10 + int(pct * 0.65), msg)

    try:
        blob, total_files, uncompressed_size, file_list = build_archive(
            game_dir,
            quality=settings.compression,
            components=settings.components or [],
            threads=settings.threads,
            codec=settings.codec,
            progress=_archive_prog,
        )
    except Exception as exc:
        return RepackResult(success=False, error=f"Compression failed: {exc}")

    # ------------------------------------------------------------------ #
    # 3. Build metadata                                                    #
    # ------------------------------------------------------------------ #
    _progress(78, "Building metadata…")

    metadata = {
        "app_name":                settings.app_name,
        "app_note":                settings.app_note,
        "version":                 settings.version,
        "description":             settings.description,
        "copyright":               settings.copyright,
        "contact":                 settings.contact,
        "company_info":            settings.company_info,
        "window_title":            settings.window_title,
        "installer_exe_version":   settings.installer_exe_version,
        # Install-time info
        "total_files":             total_files,
        "total_uncompressed_size": uncompressed_size,
        "install_subdir":          game_dir.name,   # e.g. "CloverPit"
        # Post-install behaviour
        "install_registry_key":    settings.install_registry_key,
        "run_after_install":       settings.run_after_install,
        "detect_running_exe":      settings.detect_running_exe,
        "close_delay":             settings.close_delay,
        "required_free_space_gb":  settings.required_free_space_gb,
        # Uninstaller
        "include_uninstaller": settings.include_uninstaller,
        # Integrity
        "codec":        settings.codec,
        "verify_crc32": settings.verify_crc32,
        # Shortcuts
        "shortcut_target":           settings.shortcut_target,
        "shortcut_name":             settings.shortcut_name or settings.app_name,
        "shortcut_create_desktop":   settings.shortcut_create_desktop,
        "shortcut_create_startmenu": settings.shortcut_create_startmenu,
        # Optional components metadata (installer renders checkboxes/radio buttons)
        "components": [
            {
                "index":            i + 1,
                "label":            c.get("label", f"Component {i + 1}"),
                "group":            c.get("group", ""),
                "default_checked":  bool(c.get("default_checked", True)),
                "requires":         [int(r) for r in c.get("requires", [])],
                "shortcut_target":  c.get("shortcut_target", ""),
            }
            for i, c in enumerate(settings.components or [])
        ],
    }

    # ------------------------------------------------------------------ #
    # 4. Load backdrop                                                     #
    # ------------------------------------------------------------------ #
    backdrop_data: Optional[bytes] = None
    if settings.backdrop_path:
        bp = Path(settings.backdrop_path)
        if not bp.exists():
            return RepackResult(success=False, error=f"Backdrop image not found: {bp}")
        backdrop_data = bp.read_bytes()

    # ------------------------------------------------------------------ #
    # 5. Package                                                           #
    # ------------------------------------------------------------------ #
    _progress(82, "Packaging installer exe…")

    output_dir = Path(settings.output_dir) if settings.output_dir else Path.cwd()
    if settings.installer_exe_name.strip():
        safe = "".join(c if c.isalnum() or c in "-_." else "_"
                       for c in settings.installer_exe_name.strip())
        output_path = output_dir / f"{safe}_{settings.arch}.exe"
    else:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in settings.app_name)
        version_tag = f"_{settings.version}" if settings.version else ""
        output_path = output_dir / f"{safe}{version_tag}_installer_{settings.arch}.exe"

    try:
        icon = Path(settings.icon_path) if settings.icon_path else None
        package_repack(
            arch=settings.arch,
            pack_blob=blob,
            file_list=file_list,
            metadata=metadata,
            output_path=output_path,
            icon_path=icon,
            backdrop_data=backdrop_data,
            include_uninstaller=settings.include_uninstaller,
        )
    except Exception as exc:
        return RepackResult(success=False, error=str(exc))

    _progress(100, "Done.")

    return RepackResult(
        success=True,
        output_path=output_path,
        total_files=total_files,
        uncompressed_size=uncompressed_size,
        output_size=output_path.stat().st_size,
    )
