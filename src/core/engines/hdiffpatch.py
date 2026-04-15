"""HDiffPatch engine wrapper — uses Linux native hdiffz binary."""

import subprocess
from pathlib import Path

from .base import EngineResult, PatchEngine

# Maps PatchForge compression label → hdiffz -c flag value
_COMPRESSION_MAP: dict[str, str] = {
    "none":          "",
    "zip/1":         "zlib-1",
    "zip/9":         "zlib-9",
    "bzip/5":        "bzip2-5",
    "bzip/9":        "bzip2-9",
    "lzma/fast":     "lzma2-1",
    "lzma/normal":   "lzma2-6",
    "lzma/ultra":    "lzma2-9",
    "lzma/ultra64":  "lzma2-9x",
}


class HDiffPatchEngine(PatchEngine):
    name = "hdiffpatch"
    label = "HDiffPatch 4.5.2"

    def _binary(self) -> Path:
        return self.engine_dir / "hdiffz"

    def supported_compressions(self) -> list[str]:
        return list(_COMPRESSION_MAP.keys())

    def generate(
        self,
        source: Path,
        target: Path,
        output: Path,
        compression: str = "lzma/ultra",
    ) -> EngineResult:
        codec = _COMPRESSION_MAP.get(compression, _COMPRESSION_MAP["lzma/ultra"])
        cmd = [str(self._binary()), "-f"]
        if codec:
            cmd += [f"-c-{codec}"]
        cmd += [str(source), str(target), str(output)]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                return EngineResult(
                    success=False,
                    patch_path=None,
                    patch_size=0,
                    error=result.stderr.strip() or f"hdiffz exited {result.returncode}",
                )
            sz = output.stat().st_size if output.exists() else 0
            return EngineResult(success=True, patch_path=output, patch_size=sz)
        except Exception as exc:
            return EngineResult(success=False, patch_path=None, patch_size=0, error=str(exc))
