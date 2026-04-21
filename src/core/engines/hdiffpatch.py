"""HDiffPatch engine wrapper — uses Linux native hdiffz binary.

Presets: Set1–Set6 × {LZMA2, PBZIP2} = 12 entries.
Thread count is a separate parameter (1, 2, 4, 8, 16, 32) passed via -p-N.
Compressor quality is a separate parameter controlling the -c-lzma2-N /
-c-bzip2-N flag, allowing compression strength to vary independently of the
stream block-size preset.

All sets use stream mode; block size decreases for finer matching:
  Set1 64k → Set2 16k → Set3 4k → Set4 1k → Set5 640b → Set6 64b
"""

import os
import shlex
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

def _build_thread_options() -> list[int]:
    cores = os.cpu_count() or 1
    opts: list[int] = []
    p = 1
    while p <= cores:
        opts.append(p)
        p *= 2
    if opts[-1] != cores:
        opts.append(cores)
    return opts

THREAD_OPTIONS = _build_thread_options()

# Quality options per compressor family.
# Each entry: key → (display label, hdiffz -c flag)
LZMA2_QUALITIES: dict[str, tuple[str, str]] = {
    "fast":   ("Fast (lzma2-1)",   "-c-lzma2-1"),
    "normal": ("Normal (lzma2-6)", "-c-lzma2-6"),
    "max":    ("Max (lzma2-9)",    "-c-lzma2-9"),
}
BZIP2_QUALITIES: dict[str, tuple[str, str]] = {
    "fast":   ("Fast (bzip2-1)",   "-c-bzip2-1"),
    "normal": ("Normal (bzip2-5)", "-c-bzip2-5"),
    "max":    ("Max (bzip2-9)",    "-c-bzip2-9"),
}

DEFAULT_QUALITY        = "max"
_LZMA2_QUALITY_KEYS    = set(LZMA2_QUALITIES)
_BZIP2_QUALITY_KEYS    = set(BZIP2_QUALITIES)


def _build_presets() -> dict[str, tuple[str, list[str]]]:
    """Presets store only the stream flag; the -c quality flag is injected at
    generate() time so quality can be changed without altering the preset."""
    presets: dict[str, tuple[str, list[str]]] = {}
    for n, stream, size in _SET_CONFIGS:
        presets[f"set{n}_lzma2"] = (f"Set{n}|{size}+LZMA2", [stream])
        presets[f"set{n}_bzip2"] = (f"Set{n}|{size}+PBZIP2", [stream])
    return presets


_PRESETS = _build_presets()
_DEFAULT_PRESET  = "set6_lzma2"
_DEFAULT_THREADS = 1


def preset_compressor(preset_key: str) -> str:
    """Return 'lzma2' or 'bzip2' for a given preset key."""
    return "bzip2" if preset_key.endswith("_bzip2") else "lzma2"


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

    @staticmethod
    def qualities_for_preset(preset_key: str) -> dict[str, tuple[str, str]]:
        """Return the quality dict appropriate for the given preset."""
        return BZIP2_QUALITIES if preset_key.endswith("_bzip2") else LZMA2_QUALITIES

    def supported_compressions(self) -> list[str]:
        return list(_PRESETS.keys())

    def generate(
        self,
        source: Path,
        target: Path,
        output: Path,
        compression: str = _DEFAULT_PRESET,
        threads: int = _DEFAULT_THREADS,
        compressor_quality: str = DEFAULT_QUALITY,
        extra_diff_args: str = "",
    ) -> EngineResult:
        _label, stream_flags = _PRESETS.get(compression, _PRESETS[_DEFAULT_PRESET])

        qualities = self.qualities_for_preset(compression)
        _qlabel, quality_flag = qualities.get(
            compressor_quality, qualities[DEFAULT_QUALITY]
        )

        src_arg = str(source).rstrip("/") + "/"
        tgt_arg = str(target).rstrip("/") + "/"
        thread_flag = [f"-p-{threads}"] if threads > 1 else []
        extra = shlex.split(extra_diff_args) if extra_diff_args else []
        cmd = ([str(self._binary()), "-f"]
               + stream_flags
               + [quality_flag]
               + thread_flag
               + extra
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
        except OSError as exc:
            return EngineResult(success=False, patch_path=None, patch_size=0, error=str(exc))
