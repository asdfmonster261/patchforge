"""JojoDiff engine wrapper — uses Linux native jdiff binary."""

import subprocess
from pathlib import Path

from .base import EngineResult, PatchEngine

# JojoDiff has no built-in compression; the patch data is raw.
# Compression is handled externally by the packager if needed.
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
        cmd = [str(self._binary()), str(source), str(target), str(output)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            # jdiff exits 0 on success, non-zero on error
            if result.returncode != 0:
                return EngineResult(
                    success=False,
                    patch_path=None,
                    patch_size=0,
                    error=result.stderr.strip() or f"jdiff exited {result.returncode}",
                )
            sz = output.stat().st_size if output.exists() else 0
            if sz == 0:
                return EngineResult(
                    success=False,
                    patch_path=None,
                    patch_size=0,
                    error="jdiff produced empty patch",
                )
            return EngineResult(success=True, patch_path=output, patch_size=sz)
        except Exception as exc:
            return EngineResult(success=False, patch_path=None, patch_size=0, error=str(exc))
