"""Shared formatting + CPU + IO helpers used by core, CLI, and GUI."""

import os
from pathlib import Path


def files_equal(a: Path, b: Path, chunk_size: int = 1024 * 1024) -> bool:
    """Stream-compare two files chunk by chunk. Used to detect whether two
    same-sized files have identical contents without loading either fully
    into memory — important for multi-GB game assets."""
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            ca = fa.read(chunk_size)
            cb = fb.read(chunk_size)
            if ca != cb:
                return False
            if not ca:
                return True


def format_size(n: int) -> str:
    """Format a byte count as 'X.X B/KB/MB/GB/TB'."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


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


# Powers of 2 up to cpu_count, plus cpu_count itself if not already present.
# Shared thread-count list presented in both the patch-mode and repack-mode
# thread dropdowns in the GUI.
THREAD_OPTIONS: list[int] = _build_thread_options()
