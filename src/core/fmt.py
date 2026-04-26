"""Shared formatting + CPU + IO helpers used by core, CLI, and GUI."""

import os
from pathlib import Path
from typing import Iterator, Optional


def walk_files(root: Path) -> Iterator[tuple[str, Path]]:
    """Yield (rel_posix_path, abs_path) for every regular file under root.

    Uses os.walk (scandir-backed) so we don't pay an extra is_file() stat
    per entry the way Path.rglob('*') + is_file() does. Order within each
    directory is deterministic (sorted) so callers can rely on stable
    output across runs."""
    root_str = str(root)
    root_path = Path(root_str)
    for dirpath, dirnames, filenames in os.walk(root_str):
        dirnames.sort()
        rel_dir = Path(dirpath).relative_to(root_path).as_posix()
        for fn in sorted(filenames):
            rel = fn if rel_dir == "." else f"{rel_dir}/{fn}"
            yield rel, Path(dirpath) / fn


def walk_file_pair(
    src: Path, tgt: Path
) -> Iterator[tuple[str, Optional[Path], Optional[Path]]]:
    """Walk src and tgt directory trees and yield one record per unique
    relative path: (rel, src_path_or_None, tgt_path_or_None).

    Used by patch_builder and engines/dir_format to compute file deltas
    in one consolidated pass instead of each consumer rebuilding its own
    src_files / tgt_files dicts."""
    src_files = dict(walk_files(src))
    tgt_files = dict(walk_files(tgt))
    for rel in sorted(set(src_files) | set(tgt_files)):
        yield rel, src_files.get(rel), tgt_files.get(rel)


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
