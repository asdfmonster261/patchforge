"""
patch_builder.py — Orchestrates the full patch build process.

Steps:
  1. Validate inputs (source/target must be directories)
  2. Run the selected engine to generate the raw patch
  3. Package stub + patch data + extra files + backdrop + metadata into output .exe
  4. Clean up temp files
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import compression as comp_mod
from .engines import HDiffPatchEngine, JojoDiffEngine, XDelta3Engine, PatchEngine
from .exe_packager import package
from .project import ProjectSettings

ENGINE_DIR = Path(__file__).parent.parent.parent / "engines" / "linux-x64"

_ENGINE_MAP = {
    "hdiffpatch": HDiffPatchEngine,
    "jojodiff":   JojoDiffEngine,
    "xdelta3":    XDelta3Engine,
}


@dataclass
class BuildResult:
    success: bool
    output_path: Optional[Path] = None
    patch_size: int = 0
    output_size: int = 0
    error: str = ""


def build(
    settings: ProjectSettings,
    progress: Optional[Callable[[int, str], None]] = None,
) -> BuildResult:
    """
    Build a self-contained Windows patcher exe from settings.

    progress(pct, message) is called with 0–100 as the build proceeds.
    """

    def _progress(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    # ------------------------------------------------------------------ #
    # 1. Validate                                                          #
    # ------------------------------------------------------------------ #
    source = Path(settings.source_dir)
    target = Path(settings.target_dir)

    if not source.exists():
        return BuildResult(success=False, error=f"Source directory not found: {source}")
    if not source.is_dir():
        return BuildResult(success=False, error=f"Source path is not a directory: {source}")
    if not target.exists():
        return BuildResult(success=False, error=f"Target directory not found: {target}")
    if not target.is_dir():
        return BuildResult(success=False, error=f"Target path is not a directory: {target}")
    if not settings.app_name.strip():
        return BuildResult(success=False, error="App name is required")
    if settings.engine not in _ENGINE_MAP:
        return BuildResult(success=False, error=f"Unknown engine: {settings.engine!r}")

    # Validate extra files
    extra_file_blobs = []  # [{dest: str, data: bytes}]
    for ef in (settings.extra_files or []):
        src_path = Path(ef.get("src", ""))
        dest = ef.get("dest", "")
        if not dest:
            return BuildResult(success=False, error=f"Extra file entry missing 'dest': {ef}")
        if not src_path or not src_path.exists():
            return BuildResult(success=False,
                               error=f"Extra file source not found: {src_path}")
        extra_file_blobs.append({"dest": dest, "data": src_path.read_bytes()})

    _progress(5, "Validating directories...")

    # ------------------------------------------------------------------ #
    # 2. Generate patch                                                    #
    # ------------------------------------------------------------------ #
    _progress(15, f"Generating directory patch with {settings.engine}...")

    engine_cls = _ENGINE_MAP[settings.engine]
    engine: PatchEngine = engine_cls(ENGINE_DIR)

    with tempfile.TemporaryDirectory(prefix="patchforge_") as tmpdir:
        raw_patch = Path(tmpdir) / "patch.bin"

        result = engine.generate(source, target, raw_patch, settings.compression,
                                 threads=settings.threads,
                                 compressor_quality=settings.compressor_quality,
                                 extra_diff_args=settings.extra_diff_args)
        if not result.success:
            return BuildResult(success=False, error=f"Patch generation failed: {result.error}")

        _progress(70, "Reading patch data...")
        patch_data = raw_patch.read_bytes()

    # ------------------------------------------------------------------ #
    # 3. Build metadata                                                    #
    # ------------------------------------------------------------------ #
    _progress(78, "Computing verification checksums...")

    metadata = {
        "app_name":            settings.app_name,
        "app_note":            settings.app_note,
        "version":             settings.version,
        "description":         settings.description,
        "copyright":           settings.copyright,
        "contact":             settings.contact,
        "company_info":        settings.company_info,
        "window_title":        settings.window_title,
        "patch_exe_version":   settings.patch_exe_version,
        "engine":              settings.engine,
        "compression":         settings.compression,
        "verify_method":       settings.verify_method,
        "find_method":         settings.find_method,
        "registry_key":        settings.registry_key,
        "registry_value":      settings.registry_value,
        "ini_path":            settings.ini_path,
        "ini_section":         settings.ini_section,
        "ini_key":             settings.ini_key,
        # Patching-behaviour fields
        "delete_extra_files":  1 if settings.delete_extra_files else 0,
        "run_before":          settings.run_before,
        "run_after":           settings.run_after,
        "backup_at":           settings.backup_at,
        "backup_path":         settings.backup_path,
    }

    # Always compute file trees so change counts are available for the UI
    src_files = {
        f.relative_to(source).as_posix(): f
        for f in source.rglob("*") if f.is_file()
    }
    tgt_files = {
        f.relative_to(target).as_posix(): f
        for f in target.rglob("*") if f.is_file()
    }
    entries = []
    for rel, tgt_f in sorted(tgt_files.items()):
        if rel not in src_files:
            entries.append((rel, tgt_f, None))
        else:
            src_f = src_files[rel]
            if src_f.stat().st_size != tgt_f.stat().st_size or \
               src_f.read_bytes() != tgt_f.read_bytes():
                entries.append((rel, tgt_f, src_f))

    files_modified = sum(1 for _, _, src_f in entries if src_f is not None)
    files_added    = sum(1 for _, _, src_f in entries if src_f is None)
    files_removed  = sum(1 for rel in src_files if rel not in tgt_files)
    metadata["files_modified"] = files_modified
    metadata["files_added"]    = files_added
    metadata["files_removed"]  = files_removed

    if settings.verify_method:
        from . import verification as _ver
        if entries:
            metadata["checksums"] = ";".join(
                f"{rel}|{_ver.compute(tgt_f, settings.verify_method)}"
                for rel, tgt_f, _ in entries
            )
            src_entries = [(rel, src_f) for rel, _, src_f in entries if src_f is not None]
            if src_entries:
                metadata["source_checksums"] = ";".join(
                    f"{rel}|{_ver.compute(src_f, settings.verify_method)}"
                    for rel, src_f in src_entries
                )

    # ------------------------------------------------------------------ #
    # 4. Load backdrop                                                     #
    # ------------------------------------------------------------------ #
    backdrop_data: Optional[bytes] = None
    if settings.backdrop_path:
        bp = Path(settings.backdrop_path)
        if not bp.exists():
            return BuildResult(success=False,
                               error=f"Backdrop image not found: {bp}")
        backdrop_data = bp.read_bytes()

    # ------------------------------------------------------------------ #
    # 5. Package                                                           #
    # ------------------------------------------------------------------ #
    _progress(80, "Packaging output exe...")

    output_dir = Path(settings.output_dir) if settings.output_dir else Path.cwd()
    if settings.patch_exe_name.strip():
        safe_name = "".join(c if c.isalnum() or c in "-_." else "_"
                            for c in settings.patch_exe_name.strip())
        output_path = output_dir / f"{safe_name}_{settings.arch}.exe"
    else:
        safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in settings.app_name)
        version_tag = f"_{settings.version}" if settings.version else ""
        output_path = output_dir / f"{safe_name}{version_tag}_patch_{settings.arch}.exe"

    try:
        icon = Path(settings.icon_path) if settings.icon_path else None
        package(
            stub_engine=settings.engine,
            arch=settings.arch,
            compression=settings.compression,
            patch_data=patch_data,
            metadata=metadata,
            output_path=output_path,
            icon_path=icon,
            extra_files=extra_file_blobs if extra_file_blobs else None,
            backdrop_data=backdrop_data,
        )
    except Exception as exc:
        return BuildResult(success=False, error=str(exc))

    _progress(100, "Done.")

    return BuildResult(
        success=True,
        output_path=output_path,
        patch_size=len(patch_data),
        output_size=output_path.stat().st_size,
    )
