"""JojoDiff engine wrapper — uses Linux native jdiff binary.

Supports both single-file mode (legacy) and directory mode.
Directory mode builds a PFMD container (see dir_format.py) where each modified
file gets its own jdiff patch, new files are stored as raw content, and deleted
files are recorded.  The matching C decoder is in jojodiff_stub.c.

Presets mirror ISXPM's three JojoDiff modes (GENERATING_SPEED2=0/1/2):
  optimal — no extra flags (medium speed, minimise diff)
  good    — -b (better quality, more memory, slow)
  minimal — -ff (fastest: skip out-of-buffer compares and pre-scanning)
"""

import subprocess
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from .base import EngineResult, PatchEngine
from . import dir_format

# (label, extra CLI flags passed before source/target/output)
_PRESETS: dict[str, tuple[str, list[str]]] = {
    "minimal": ("Minimal (Fast speed/Maximize diff)", ["-ff"]),
    "good":    ("Good (Slow speed/Minimize diff)",    ["-b"]),
    "optimal": ("Optimal (Medium speed/Minimize diff)", []),
}

_DEFAULT_PRESET = "optimal"


class JojoDiffEngine(PatchEngine):
    name = "jojodiff"
    label = "JojoDiff 0.8.1"

    def _binary(self) -> Path:
        return self.engine_dir / "jdiff"

    @staticmethod
    def presets() -> dict[str, str]:
        """Return {key: label} for all presets in order."""
        return {k: v[0] for k, v in _PRESETS.items()}

    @staticmethod
    def default_preset() -> str:
        return _DEFAULT_PRESET

    def supported_compressions(self) -> list[str]:
        return list(_PRESETS.keys())

    def generate(
        self,
        source: Path,
        target: Path,
        output: Path,
        compression: str = _DEFAULT_PRESET,
        threads: int = 1,
        compressor_quality: str = "max",
    ) -> EngineResult:
        _label, flags = _PRESETS.get(compression, _PRESETS[_DEFAULT_PRESET])
        if source.is_dir():
            return self._generate_dir(source, target, output, flags, threads)
        return self._generate_file(source, target, output, flags)

    # ------------------------------------------------------------------ #

    def _generate_file(self, source, target, output, flags: list[str] = []) -> EngineResult:
        cmd = [str(self._binary())] + flags + [str(source), str(target), str(output)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                return EngineResult(
                    success=False, patch_path=None, patch_size=0,
                    error=result.stderr.strip() or f"jdiff exited {result.returncode}",
                )
            sz = output.stat().st_size if output.exists() else 0
            if sz == 0:
                return EngineResult(
                    success=False, patch_path=None, patch_size=0,
                    error="jdiff produced empty patch",
                )
            return EngineResult(success=True, patch_path=output, patch_size=sz)
        except Exception as exc:
            return EngineResult(success=False, patch_path=None, patch_size=0, error=str(exc))

    def _generate_dir(self, source, target, output, flags: list[str] = [], threads=1) -> EngineResult:
        binary = str(self._binary())

        def make_patch(src_file: Path, tgt_file: Path) -> bytes:
            with tempfile.NamedTemporaryFile(suffix=".jdf", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                cmd = [binary] + flags + [str(src_file), str(tgt_file), str(tmp_path)]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    raise RuntimeError(
                        result.stderr.strip() or f"jdiff exited {result.returncode}"
                    )
                data = tmp_path.read_bytes()
                if not data:
                    raise RuntimeError("jdiff produced empty patch")
                return data
            finally:
                tmp_path.unlink(missing_ok=True)

        try:
            workers = threads if threads > 1 else 1
            dir_format.build(source, target, output, make_patch, workers=workers)
        except Exception as exc:
            return EngineResult(success=False, patch_path=None, patch_size=0, error=str(exc))

        sz = output.stat().st_size if output.exists() else 0
        return EngineResult(success=True, patch_path=output, patch_size=sz)
