"""xdelta3 engine wrapper — uses Linux native binary.

Supports both single-file mode (legacy) and directory mode.
Directory mode builds a PFMD container (see dir_format.py) where each modified
file gets its own xdelta3 patch, new files are stored as raw content, and
deleted files are recorded.  The matching C decoder is in xdelta3_stub.c.

Presets mirror ISXPM's three xdelta3 modes (GENERATING_SPEED=0/1/2):
  none      — -0 -S none   (no encoding, no secondary compression)
  paul44    — -9 -S djw    (DJW Huffman secondary; paul44's method)
  lzma_mem  — -9 -S lzma -B 536870912  (LZMA secondary + 512 MB source window)
"""

import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .base import EngineResult, PatchEngine
from . import dir_format

# (label, flags inserted between "xdelta3 -e -f" and "-s src tgt out")
_PRESETS: dict[str, tuple[str, list[str]]] = {
    "none":     ("No encoding/no compression (Fast speed/Maximize diff)",
                 ["-0", "-S", "none"]),
    "paul44":   ("paul44's compression method (Medium speed/Medium diff)",
                 ["-9", "-S", "djw"]),
    "lzma_mem": ("LZMA compression+encoding+mem (Slow speed/Minimize diff)",
                 ["-9", "-S", "lzma", "-B", "536870912"]),
}

_DEFAULT_PRESET = "paul44"


class XDelta3Engine(PatchEngine):
    name = "xdelta3"
    label = "xdelta3 3.0.8"

    def _binary(self) -> Path:
        return self.engine_dir / "xdelta3"

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
        if source.is_dir():
            return self._generate_dir(source, target, output, compression, threads)
        return self._generate_file(source, target, output, compression)

    # ------------------------------------------------------------------ #

    def _generate_file(self, source, target, output, compression) -> EngineResult:
        _label, args = _PRESETS.get(compression, _PRESETS[_DEFAULT_PRESET])
        cmd = [str(self._binary()), "-e", "-f"] + args + [
            "-s", str(source), str(target), str(output)
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                return EngineResult(
                    success=False, patch_path=None, patch_size=0,
                    error=result.stderr.strip() or f"xdelta3 exited {result.returncode}",
                )
            sz = output.stat().st_size if output.exists() else 0
            return EngineResult(success=True, patch_path=output, patch_size=sz)
        except Exception as exc:
            return EngineResult(success=False, patch_path=None, patch_size=0, error=str(exc))

    def _generate_dir(self, source, target, output, compression, threads=1) -> EngineResult:
        _label, args = _PRESETS.get(compression, _PRESETS[_DEFAULT_PRESET])
        binary = str(self._binary())

        def make_patch(src_file: Path, tgt_file: Path) -> bytes:
            with tempfile.NamedTemporaryFile(suffix=".xd3", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                cmd = [binary, "-e", "-f"] + args + [
                    "-s", str(src_file), str(tgt_file), str(tmp_path)
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    raise RuntimeError(
                        result.stderr.strip() or f"xdelta3 exited {result.returncode}"
                    )
                return tmp_path.read_bytes()
            finally:
                tmp_path.unlink(missing_ok=True)

        try:
            workers = threads if threads > 1 else 1
            dir_format.build(source, target, output, make_patch, workers=workers)
        except Exception as exc:
            return EngineResult(success=False, patch_path=None, patch_size=0, error=str(exc))

        sz = output.stat().st_size if output.exists() else 0
        return EngineResult(success=True, patch_path=output, patch_size=sz)
