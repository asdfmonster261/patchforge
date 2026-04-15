"""
patch_builder.py — Orchestrates the full patch build process.

Steps:
  1. Validate inputs
  2. Compute source/target checksums
  3. Run the selected engine to generate the raw patch
  4. Package stub + patch data + metadata into output .exe
  5. Clean up temp files
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import compression as comp_mod
from . import verification
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
    orig_checksum: str = ""
    new_checksum: str = ""
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
    source = Path(settings.source_file)
    target = Path(settings.target_file)

    if not source.exists():
        return BuildResult(success=False, error=f"Source file not found: {source}")
    if not target.exists():
        return BuildResult(success=False, error=f"Target file not found: {target}")
    if not settings.app_name.strip():
        return BuildResult(success=False, error="App name is required")
    if settings.engine not in _ENGINE_MAP:
        return BuildResult(success=False, error=f"Unknown engine: {settings.engine!r}")

    # JojoDiff doesn't support compression
    if settings.engine == "jojodiff" and settings.compression != "none":
        return BuildResult(
            success=False,
            error="JojoDiff does not support compression — set compression to 'none'",
        )

    _progress(5, "Validating files...")

    # ------------------------------------------------------------------ #
    # 2. Checksums                                                         #
    # ------------------------------------------------------------------ #
    _progress(10, f"Computing {settings.verify_method.upper()} checksum of source...")
    orig_checksum = verification.compute(source, settings.verify_method)

    _progress(20, f"Computing {settings.verify_method.upper()} checksum of target...")
    new_checksum = verification.compute(target, settings.verify_method)

    # ------------------------------------------------------------------ #
    # 3. Generate patch                                                    #
    # ------------------------------------------------------------------ #
    _progress(30, f"Generating patch with {settings.engine}...")

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
    # 4. Build metadata                                                    #
    # ------------------------------------------------------------------ #
    _progress(80, "Packaging output exe...")

    metadata = {
        "app_name":       settings.app_name,
        "version":        settings.version,
        "description":    settings.description,
        "engine":         settings.engine,
        "compression":    settings.compression,
        "verify_method":  settings.verify_method,
        "orig_checksum":  orig_checksum,
        "new_checksum":   new_checksum,
        "orig_size":      source.stat().st_size,
        "new_size":       target.stat().st_size,
        "find_method":    settings.find_method,
        "registry_key":   settings.registry_key,
        "registry_value": settings.registry_value,
        "ini_path":       settings.ini_path,
        "ini_section":    settings.ini_section,
        "ini_key":        settings.ini_key,
    }

    # ------------------------------------------------------------------ #
    # 5. Package                                                           #
    # ------------------------------------------------------------------ #
    output_dir = Path(settings.output_dir) if settings.output_dir else source.parent
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
        orig_checksum=orig_checksum,
        new_checksum=new_checksum,
    )
