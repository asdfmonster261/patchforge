"""
PFMD container format — directory-mode patches for xdelta3 and JojoDiff.

Wire format (version 2):
  4 bytes  magic "PFMD"
  1 byte   version = 2
  4 bytes  LE uint32 num_entries
  entries:
    1 byte   op  (0=delete, 1=patch, 2=new-file)
    2 bytes  LE uint16 path_len
    N bytes  path (UTF-8, forward slashes, no leading slash)
    8 bytes  LE uint64 data_len (0 for delete)
    N bytes  data  (engine patch bytes for op=1, raw file content for op=2)

Version 2 widened data_len from uint32 to uint64 so OP_NEW entries for files
larger than 4 GB no longer overflow.

Matching C parser: stub/dir_patch_format.h
"""
from __future__ import annotations

import struct
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from ..fmt import files_equal, walk_file_pair

OP_DELETE = 0
OP_PATCH  = 1
OP_NEW    = 2

_MAGIC   = b"PFMD"
_VERSION = 2


def build(
    source: Path,
    target: Path,
    output: Path,
    make_patch: Callable[[Path, Path], bytes],
    workers: int = 1,
) -> None:
    """
    Walk source/target directories and write a PFMD container to output.

    make_patch(src_file, tgt_file) -> bytes
        Called for each modified file.  Must return the engine's raw patch bytes.
        New files (OP_NEW) bypass make_patch — their raw content is stored directly.
    Raises on any error (engine failure, I/O).
    """
    entries: list[tuple[int, str, bytes]] = []

    # Collect which files need patching (OP_PATCH) so we can parallelise them.
    patch_jobs: list[tuple[str, Path, Path]] = []

    for rel, src_f, tgt_f in walk_file_pair(source, target):
        if tgt_f is None:
            entries.append((OP_DELETE, rel, b""))
        elif src_f is None:
            entries.append((OP_NEW, rel, tgt_f.read_bytes()))
        else:
            if src_f.stat().st_size == tgt_f.stat().st_size and \
               files_equal(src_f, tgt_f):
                continue
            patch_jobs.append((rel, src_f, tgt_f))

    # Run patch jobs — parallel if workers > 1, sequential otherwise.
    if workers > 1 and patch_jobs:
        patch_results: dict[str, bytes] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(make_patch, src_f, tgt_f): rel
                for rel, src_f, tgt_f in patch_jobs
            }
            for future in as_completed(futures):
                rel = futures[future]
                patch_results[rel] = future.result()  # propagates exceptions
        for rel, _, _ in patch_jobs:
            entries.append((OP_PATCH, rel, patch_results[rel]))
    else:
        for rel, src_f, tgt_f in patch_jobs:
            entries.append((OP_PATCH, rel, make_patch(src_f, tgt_f)))

    with open(output, "wb") as fh:
        fh.write(_MAGIC)
        fh.write(bytes([_VERSION]))
        fh.write(struct.pack("<I", len(entries)))
        for op, rel_path, data in entries:
            path_bytes = rel_path.encode("utf-8")
            if len(path_bytes) > 0xFFFF:
                raise ValueError(
                    f"Path too long for PFMD (UTF-8 byte length "
                    f"{len(path_bytes)} exceeds 65535): {rel_path!r}"
                )
            fh.write(struct.pack("<BH", op, len(path_bytes)))
            fh.write(path_bytes)
            fh.write(struct.pack("<Q", len(data)))
            if data:
                fh.write(data)
