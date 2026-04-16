"""Abstract base class for all patch engines."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..compression import STUB_FULL_REQUIRED


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
        compression: str = "",
        threads: int = 1,
        compressor_quality: str = "max",
        extra_diff_args: str = "",
    ) -> EngineResult:
        """Generate a binary diff from source → target, writing to output."""
        ...

    @abstractmethod
    def supported_compressions(self) -> list[str]:
        """Return list of compression level strings this engine supports."""
        ...

    def stub_supports_compression(self, compression: str) -> bool:
        """Return False for presets that require the full stub (zlib/bzip2 deps)."""
        return compression not in STUB_FULL_REQUIRED
