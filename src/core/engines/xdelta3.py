"""xdelta3 engine wrapper — uses Linux native binary."""

import subprocess
from pathlib import Path

from .base import EngineResult, PatchEngine

# xdelta3 compression levels: -1 (fast) to -9 (best); lzma is a secondary codec
_COMPRESSION_ARGS: dict[str, list[str]] = {
    "none":       ["-0", "-S", "none"],
    "zip/1":      ["-1"],
    "zip/9":      ["-9"],
    "lzma/fast":  ["-1", "-S", "djw"],
    "lzma/normal":  ["-6", "-S", "djw"],
    "lzma/ultra": ["-9", "-S", "djw"],
    "lzma/ultra2": ["-9", "-S", "fgk"],
}


class XDelta3Engine(PatchEngine):
    name = "xdelta3"
    label = "xdelta3 3.0.8"

    def _binary(self) -> Path:
        return self.engine_dir / "xdelta3"

    def supported_compressions(self) -> list[str]:
        return list(_COMPRESSION_ARGS.keys())

    def generate(
        self,
        source: Path,
        target: Path,
        output: Path,
        compression: str = "lzma/ultra",
    ) -> EngineResult:
        args = _COMPRESSION_ARGS.get(compression, _COMPRESSION_ARGS["lzma/ultra"])
        cmd = [str(self._binary()), "-e", "-f"] + args + [
            "-s", str(source), str(target), str(output)
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                return EngineResult(
                    success=False,
                    patch_path=None,
                    patch_size=0,
                    error=result.stderr.strip() or f"xdelta3 exited {result.returncode}",
                )
            sz = output.stat().st_size if output.exists() else 0
            return EngineResult(success=True, patch_path=output, patch_size=sz)
        except Exception as exc:
            return EngineResult(success=False, patch_path=None, patch_size=0, error=str(exc))
