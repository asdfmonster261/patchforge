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
from .app_settings import load as load_app_settings


@dataclass
class RepackResult:
    success: bool
    output_path: Optional[Path] = None
    bin_path: Optional[Path] = None
    total_files: int = 0
    uncompressed_size: int = 0
    output_size: int = 0
    error: str = ""


def build(
    settings: RepackSettings,
    progress: Optional[Callable[[int, str], None]] = None,
    stream_progress: Optional[Callable[[int, int, str, int, int, str], None]] = None,
) -> RepackResult:
    """
    Build a self-contained Windows installer exe from settings.

    progress(pct, message) is called with 0–100 as the build proceeds.
    stream_progress(stream_idx, num_streams, label, files_done, files_total)
    is called once per file during the compression phase.
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
    # Resolve output dir early so temp files land there, not in /tmp.
    output_dir = Path(settings.output_dir) if settings.output_dir else Path.cwd()
    output_dir.mkdir(parents=True, exist_ok=True)

    def _archive_prog(pct: int, msg: str) -> None:
        # Map archive progress (0-100) to overall range 10-75
        _progress(10 + int(pct * 0.65), msg)

    try:
        blob_path, total_files, uncompressed_size, file_list, ext_info = build_archive(
            game_dir,
            quality=settings.compression,
            components=settings.components or [],
            threads=settings.threads,
            codec=settings.codec,
            progress=_archive_prog,
            tmp_dir=output_dir,
            stream_progress=stream_progress,
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
                "sac_warning":      bool(c.get("sac_warning", False)),
                "size_bytes":       sum(e["size"] for e in file_list if e["component"] == i + 1),
            }
            for i, c in enumerate(settings.components or [])
        ],
    }

    # External component sidecar files
    if ext_info:
        metadata["external_components"] = {
            str(ci): info["path"].name for ci, info in ext_info.items()
        }
        metadata["external_offsets"] = {
            str(ci): info["offset"] for ci, info in ext_info.items()
        }
        metadata["external_csizes"] = {
            str(ci): info["csize"] for ci, info in ext_info.items()
        }

    # Multi-part bin splitting: decide upfront so the part count gets baked
    # into metadata (the split itself happens after packaging).
    bin_part_size = 0
    bin_num_parts = 1
    if settings.max_part_size_mb > 0:
        bin_part_size = settings.max_part_size_mb * 1024 * 1024
        blob_size = blob_path.stat().st_size
        if blob_size > bin_part_size:
            bin_num_parts = (blob_size + bin_part_size - 1) // bin_part_size
            metadata["bin_parts"] = bin_num_parts
            metadata["bin_part_size"] = bin_part_size

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
    # Determine whether to split pack data into a separate .bin file.
    app_cfg   = load_app_settings()
    threshold = app_cfg.bin_split_threshold_gb * 1024 ** 3
    use_bin   = (settings.split_bin or bin_num_parts > 1 or
                 blob_path.stat().st_size >= threshold)

    _progress(82, "Packaging installer exe…")

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
        output_path, bin_path = package_repack(
            arch=settings.arch,
            pack_blob_path=blob_path,
            file_list=file_list,
            metadata=metadata,
            output_path=output_path,
            icon_path=icon,
            backdrop_data=backdrop_data,
            include_uninstaller=settings.include_uninstaller,
            split_bin=use_bin,
        )
    except Exception as exc:
        return RepackResult(success=False, error=str(exc))
    finally:
        try:
            blob_path.unlink(missing_ok=True)
        except OSError:
            pass

    # Multi-part splitting of base_game.bin (after packaging, since parts
    # are just raw byte chunks of the final sidecar).
    bin_part_paths: list[Path] = []
    if bin_path and bin_num_parts > 1:
        _progress(98, f"Splitting {bin_path.name} into {bin_num_parts} parts…")
        CHUNK = 1024 * 1024  # 1 MB copy buffer
        with open(bin_path, "rb") as src:
            for i in range(bin_num_parts):
                part_path = bin_path.with_name(f"{bin_path.name}.{i + 1:03d}")
                remaining = bin_part_size
                with open(part_path, "wb") as dst:
                    while remaining > 0:
                        buf = src.read(min(CHUNK, remaining))
                        if not buf:
                            break
                        dst.write(buf)
                        remaining -= len(buf)
                bin_part_paths.append(part_path)
        bin_path.unlink()
        # Return the first part as the "bin_path" in the result
        bin_path = bin_part_paths[0]

    _progress(100, "Done.")

    # Total compressed output: exe stub + base_game.bin (if split) + all sidecars
    total_compressed = output_path.stat().st_size
    if bin_part_paths:
        for p in bin_part_paths:
            total_compressed += p.stat().st_size
    elif bin_path:
        total_compressed += bin_path.stat().st_size
    for info in ext_info.values():
        p = info["path"]
        if p.exists():
            total_compressed += p.stat().st_size

    return RepackResult(
        success=True,
        output_path=output_path,
        bin_path=bin_path,
        total_files=total_files,
        uncompressed_size=uncompressed_size,
        output_size=total_compressed,
    )
