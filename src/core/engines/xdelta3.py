"""xdelta3 engine wrapper — uses Linux native binary.

Supports both single-file mode (legacy) and directory mode.
Directory mode builds a PFMD container (see dir_format.py) where each modified
file gets its own xdelta3 patch, new files are stored as raw content, and
deleted files are recorded.  The matching C decoder is in xdelta3_stub.c.
"""

import subprocess
import tempfile
from pathlib import Path

from .base import EngineResult, PatchEngine
from . import dir_format

# xdelta3 CLI compression flags
_COMPRESSION_ARGS: dict[str, list[str]] = {
    "none":         ["-0", "-S", "none"],
    "zip/1":        ["-1"],
    "zip/9":        ["-9"],
    "lzma/fast":    ["-1", "-S", "djw"],
    "lzma/normal":  ["-6", "-S", "djw"],
    "lzma/ultra":   ["-9", "-S", "djw"],
    "lzma/ultra2":  ["-9", "-S", "fgk"],
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
        if source.is_dir():
            return self._generate_dir(source, target, output, compression)
        return self._generate_file(source, target, output, compression)

    # ------------------------------------------------------------------ #

    def _generate_file(self, source, target, output, compression) -> EngineResult:
        args = _COMPRESSION_ARGS.get(compression, _COMPRESSION_ARGS["lzma/ultra"])
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

    def _generate_dir(self, source, target, output, compression) -> EngineResult:
        args = _COMPRESSION_ARGS.get(compression, _COMPRESSION_ARGS["lzma/ultra"])
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
            dir_format.build(source, target, output, make_patch)
        except Exception as exc:
            return EngineResult(success=False, patch_path=None, patch_size=0, error=str(exc))

        sz = output.stat().st_size if output.exists() else 0
        return EngineResult(success=True, patch_path=output, patch_size=sz)
