"""
patch_builder.py — Orchestrates the full patch build process.

Steps:
  1. Validate inputs (source/target must be directories)
  2. Run the selected engine to generate the raw patch
  3. Package stub + patch data + metadata into output .exe
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

    if settings.engine == "jojodiff" and settings.compression != "none":
        return BuildResult(
            success=False,
            error="JojoDiff does not support compression — set compression to 'none'",
        )

    _progress(5, "Validating directories...")

    # ------------------------------------------------------------------ #
    # 2. Generate patch                                                    #
    # ------------------------------------------------------------------ #
    _progress(15, f"Generating directory patch with {settings.engine}...")

    engine_cls = _ENGINE_MAP[settings.engine]
    engine: PatchEngine = engine_cls(ENGINE_DIR)

    with tempfile.TemporaryDirectory(prefix="patchforge_") as tmpdir:
        raw_patch = Path(tmpdir) / "patch.bin"

        result = engine.generate(source, target, raw_patch, settings.compression)
        if not result.success:
            return BuildResult(success=False, error=f"Patch generation failed: {result.error}")

        _progress(70, "Reading patch data...")
        patch_data = raw_patch.read_bytes()

    # ------------------------------------------------------------------ #
    # 3. Build metadata                                                    #
    # ------------------------------------------------------------------ #
    _progress(80, "Packaging output exe...")

    metadata = {
        "app_name":       settings.app_name,
        "version":        settings.version,
        "description":    settings.description,
        "engine":         settings.engine,
        "compression":    settings.compression,
        "verify_method":  settings.verify_method,
        "find_method":    settings.find_method,
        "registry_key":   settings.registry_key,
        "registry_value": settings.registry_value,
        "ini_path":       settings.ini_path,
        "ini_section":    settings.ini_section,
        "ini_key":        settings.ini_key,
    }

    # ------------------------------------------------------------------ #
    # 4. Package                                                           #
    # ------------------------------------------------------------------ #
    output_dir = Path(settings.output_dir) if settings.output_dir else Path.cwd()
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in settings.app_name)
    version_tag = f"_{settings.version}" if settings.version else ""
    output_path = output_dir / f"{safe_name}{version_tag}_patch_{settings.arch}.exe"

    try:
        package(
            stub_engine=settings.engine,
            arch=settings.arch,
            compression=settings.compression,
            patch_data=patch_data,
            metadata=metadata,
            output_path=output_path,
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
