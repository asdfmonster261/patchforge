"""HDiffPatch engine wrapper — uses Linux native hdiffz binary.

Presets: Set1–Set6 × {LZMA2, PBZIP2} = 12 entries.
Thread count is a separate parameter (1, 2, 4, 8, 16, 32) passed via -p-N.
Setting threads=1 gives single-threaded behaviour (replaces old "default" entries).

All sets use stream mode; block size decreases for finer matching:
  Set1 64k → Set2 16k → Set3 4k → Set4 1k → Set5 640b → Set6 64b
"""

import subprocess
from pathlib import Path

from .base import EngineResult, PatchEngine

# (set_num, stream_flag, size_label)
_SET_CONFIGS = [
    (1, "-s-64k",  "64k"),
    (2, "-s-16k",  "16k"),
    (3, "-s-4k",   "4k"),
    (4, "-s-1k",   "1k"),
    (5, "-s-640",  "640b"),
    (6, "-s-64",   "64b"),
]

THREAD_OPTIONS = [1, 2, 4, 8, 16, 32]


def _build_presets() -> dict[str, tuple[str, list[str]]]:
    presets: dict[str, tuple[str, list[str]]] = {}
    for n, stream, size in _SET_CONFIGS:
        presets[f"set{n}_lzma2"] = (
            f"Set{n}|{size}+LZMA2",
            [stream, "-c-lzma2-9"],
        )
        presets[f"set{n}_bzip2"] = (
            f"Set{n}|{size}+PBZIP2",
            [stream, "-c-bzip2-9"],
        )
    return presets


_PRESETS = _build_presets()
_DEFAULT_PRESET  = "set5_lzma2"
_DEFAULT_THREADS = 1


class HDiffPatchEngine(PatchEngine):
    name = "hdiffpatch"
    label = "HDiffPatch 4.12.2"

    def _binary(self) -> Path:
        return self.engine_dir / "hdiffz"

    @staticmethod
    def presets() -> dict[str, str]:
        """Return {key: label} for all presets in order."""
        return {k: v[0] for k, v in _PRESETS.items()}

    @staticmethod
    def default_preset() -> str:
        return _DEFAULT_PRESET

    @staticmethod
    def default_threads() -> int:
        return _DEFAULT_THREADS

    def supported_compressions(self) -> list[str]:
        return list(_PRESETS.keys())

    def generate(
        self,
        source: Path,
        target: Path,
        output: Path,
        compression: str = _DEFAULT_PRESET,
        threads: int = _DEFAULT_THREADS,
    ) -> EngineResult:
        _label, flags = _PRESETS.get(compression, _PRESETS[_DEFAULT_PRESET])
        src_arg = str(source).rstrip("/") + "/"
        tgt_arg = str(target).rstrip("/") + "/"
        thread_flag = [f"-p-{threads}"] if threads > 1 else []
        cmd = ([str(self._binary()), "-f"]
               + flags
               + thread_flag
               + [src_arg, tgt_arg, str(output)])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                return EngineResult(
                    success=False, patch_path=None, patch_size=0,
                    error=result.stderr.strip() or f"hdiffz exited {result.returncode}",
                )
            sz = output.stat().st_size if output.exists() else 0
            return EngineResult(success=True, patch_path=output, patch_size=sz)
        except Exception as exc:
            return EngineResult(success=False, patch_path=None, patch_size=0, error=str(exc))
