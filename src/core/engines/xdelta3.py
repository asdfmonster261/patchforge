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

import shlex
import subprocess
import tempfile
from pathlib import Path

from .base import EXE_SUFFIX, EngineResult, PatchEngine
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


def _strip_flag_with_value(args: list[str], flag: str) -> list[str]:
    """Remove `flag <value>` (or `flag=value`) pairs from an arg list."""
    out: list[str] = []
    skip_next = False
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if a == flag:
            skip_next = True
            continue
        if a.startswith(flag + "="):
            continue
        out.append(a)
    return out


class XDelta3Engine(PatchEngine):
    name = "xdelta3"
    label = "xdelta3 3.0.8"

    def _binary(self) -> Path:
        return self.engine_dir / f"xdelta3{EXE_SUFFIX}"

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
        compressor_quality: str = "max",  # noqa: ARG002  PatchEngine interface, xdelta3 has no equivalent
        extra_diff_args: str = "",
    ) -> EngineResult:
        if source.is_dir():
            return self._generate_dir(source, target, output, compression, threads,
                                      extra_diff_args)
        return self._generate_file(source, target, output, compression, extra_diff_args)

    # ------------------------------------------------------------------ #

    def _generate_file(self, source, target, output, compression,
                        extra_diff_args: str = "") -> EngineResult:
        _label, args = _PRESETS.get(compression, _PRESETS[_DEFAULT_PRESET])
        extra = shlex.split(extra_diff_args) if extra_diff_args else []
        cmd = [str(self._binary()), "-e", "-f"] + args + extra + [
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
            if sz == 0:
                return EngineResult(
                    success=False, patch_path=None, patch_size=0,
                    error="xdelta3 produced empty patch",
                )
            return EngineResult(success=True, patch_path=output, patch_size=sz)
        except OSError as exc:
            return EngineResult(success=False, patch_path=None, patch_size=0, error=str(exc))

    def _generate_dir(self, source, target, output, compression, threads=1,
                       extra_diff_args: str = "") -> EngineResult:
        _label, args = _PRESETS.get(compression, _PRESETS[_DEFAULT_PRESET])
        extra = shlex.split(extra_diff_args) if extra_diff_args else []
        binary = str(self._binary())

        # Strip any preset-supplied `-B` so we can size the source window
        # per-file below.  xdelta3 takes the LAST `-B` on the cmdline, but
        # being explicit keeps the cmdline readable in error messages.
        args_no_B = _strip_flag_with_value(args, "-B")

        def make_patch(src_file: Path, tgt_file: Path) -> bytes:
            with tempfile.NamedTemporaryFile(suffix=".xd3", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                # Size the source window to cover the whole source file so
                # back-references from late target windows can always reach
                # any earlier source position — xdelta3's encoder otherwise
                # bails with XD3_TOOFARBACK on multi-GB sources.  Floor at
                # the preset's 512 MB so small files don't shrink the
                # window unnecessarily.  xdelta3 hard-caps -B at
                # XD3_MAXSRCWINSZ = 2 GiB - 1 (32-bit hash addresses); for
                # sources bigger than that the encoder may still trip
                # XD3_TOOFARBACK on non-local matches, but at least the
                # cmdline parses and most game updates have localised
                # changes that fit inside the 2 GiB window.
                XD3_MAX_B = (1 << 31) - 1
                src_size = src_file.stat().st_size
                srcwin   = max(src_size, 512 * 1024 * 1024)
                srcwin   = min(srcwin, XD3_MAX_B)
                cmd = ([binary, "-e", "-f"] + args_no_B
                       + ["-B", str(srcwin)] + extra
                       + ["-s", str(src_file), str(tgt_file), str(tmp_path)])
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
        except (OSError, RuntimeError) as exc:
            return EngineResult(success=False, patch_path=None, patch_size=0, error=str(exc))

        sz = output.stat().st_size if output.exists() else 0
        return EngineResult(success=True, patch_path=output, patch_size=sz)
