"""JojoDiff engine wrapper — uses Linux native jdiff binary.

Supports both single-file mode (legacy) and directory mode.
Directory mode builds a PFMD container (see dir_format.py) where each modified
file gets its own jdiff patch, new files are stored as raw content, and deleted
files are recorded.  The matching C decoder is in jojodiff_stub.c.

JojoDiff has no built-in compression; compression must be "none".
"""

import subprocess
import tempfile
from pathlib import Path

from .base import EngineResult, PatchEngine
from . import dir_format

_COMPRESSION_ARGS: dict[str, list[str]] = {
    "none": [],
}


class JojoDiffEngine(PatchEngine):
    name = "jojodiff"
    label = "JojoDiff 0.8.1"

    def _binary(self) -> Path:
        return self.engine_dir / "jdiff"

    def supported_compressions(self) -> list[str]:
        return list(_COMPRESSION_ARGS.keys())

    def generate(
        self,
        source: Path,
        target: Path,
        output: Path,
        compression: str = "none",
    ) -> EngineResult:
        if source.is_dir():
            return self._generate_dir(source, target, output)
        return self._generate_file(source, target, output)

    # ------------------------------------------------------------------ #

    def _generate_file(self, source, target, output) -> EngineResult:
        cmd = [str(self._binary()), str(source), str(target), str(output)]
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

    def _generate_dir(self, source, target, output) -> EngineResult:
        binary = str(self._binary())

        def make_patch(src_file: Path, tgt_file: Path) -> bytes:
            with tempfile.NamedTemporaryFile(suffix=".jdf", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                cmd = [binary, str(src_file), str(tgt_file), str(tmp_path)]
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
            dir_format.build(source, target, output, make_patch)
        except Exception as exc:
            return EngineResult(success=False, patch_path=None, patch_size=0, error=str(exc))

        sz = output.stat().st_size if output.exists() else 0
        return EngineResult(success=True, patch_path=output, patch_size=sz)
