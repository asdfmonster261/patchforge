"""Abstract base class for all patch engines."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class EngineResult:
    success: bool
    patch_path: Optional[Path]
    patch_size: int
    error: str = ""


class PatchEngine(ABC):
    """Base class for patch generation engines."""

    name: str = ""
    label: str = ""

    def __init__(self, engine_dir: Path):
        self.engine_dir = engine_dir

    @abstractmethod
    def generate(
        self,
        source: Path,
        target: Path,
        output: Path,
        compression: str = "lzma/ultra",
        threads: int = 1,
        compressor_quality: str = "max",
    ) -> EngineResult:
        """Generate a binary diff from source → target, writing to output."""
        ...

    @abstractmethod
    def supported_compressions(self) -> list[str]:
        """Return list of compression level strings this engine supports."""
        ...

    # Compression / preset keys whose decompressor is NOT compiled into the
    # Windows stubs (zlib and bzip2 require extra third-party sources).
    # HDiffPatch preset keys ("set1"…"set6") are all lzma2-based and are
    # supported by default; only xdelta3/jojodiff zip and bzip modes hit this.
    WIN_STUB_UNSUPPORTED: set[str] = {"zip/1", "zip/9", "bzip/5", "bzip/9"}

    def stub_supports_compression(self, compression: str) -> bool:
        return compression not in self.WIN_STUB_UNSUPPORTED
