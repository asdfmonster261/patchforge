"""
End-to-end test for patch_builder.build() on a synthetic source/target dir
pair. Covers the Update Patch flow that wasn't reached by the existing
repack-focused tests.

Verifies:
  1. build() succeeds on a small directory diff (modified, added, removed)
  2. file change counts in the embedded metadata match reality
  3. the output exe carries the XPATCH01 trailer and parseable JSON
  4. the output exe is large enough to contain the stub + patch + metadata
"""
import json
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.patch_builder import build as build_patch
from src.core.project import ProjectSettings


_MAGIC = b"XPATCH01"


def _read_metadata(exe_path: Path) -> dict:
    data = exe_path.read_bytes()
    assert data[-8:] == _MAGIC, "XPATCH01 magic not found"
    meta_len = struct.unpack_from("<I", data, len(data) - 12)[0]
    return json.loads(data[-(12 + meta_len):-12])


def _make_pair(root: Path) -> tuple[Path, Path]:
    """Build source/ and target/ trees with one modified, one added, one removed file."""
    src = root / "src"
    tgt = root / "tgt"
    src.mkdir()
    tgt.mkdir()
    # Modified
    (src / "data.txt").write_bytes(b"hello world\n" * 32)
    (tgt / "data.txt").write_bytes(b"hello universe\n" * 32)
    # Added (in tgt only)
    (tgt / "new.txt").write_bytes(b"freshly added\n")
    # Removed (in src only)
    (src / "old.txt").write_bytes(b"to be deleted\n")
    # Identical (should not appear in diff)
    (src / "stable.txt").write_bytes(b"unchanged\n")
    (tgt / "stable.txt").write_bytes(b"unchanged\n")
    return src, tgt


def test_build_basic_dir_diff(tmp_path):
    src, tgt = _make_pair(tmp_path)
    settings = ProjectSettings(
        source_dir=str(src),
        target_dir=str(tgt),
        output_dir=str(tmp_path / "out"),
        app_name="EndToEndTest",
        version="1.0",
        engine="hdiffpatch",
        compression="set6_lzma2",
        verify_method="crc32c",
        arch="x64",
    )
    result = build_patch(settings)
    assert result.success, result.error
    assert result.output_path is not None
    out = Path(result.output_path)
    assert out.exists()
    assert out.stat().st_size > 0
    assert result.patch_size > 0

    meta = _read_metadata(out)
    assert meta["app_name"]      == "EndToEndTest"
    assert meta["engine"]        == "hdiffpatch"
    assert meta["files_modified"] == 1
    assert meta["files_added"]    == 1
    assert meta["files_removed"]  == 1
    # checksums should cover the two files in the target diff (modified + added)
    assert "checksums" in meta
    assert len(meta["checksums"].split(";")) == 2


def test_build_rejects_missing_source_dir(tmp_path):
    settings = ProjectSettings(
        source_dir=str(tmp_path / "does_not_exist"),
        target_dir=str(tmp_path),
        app_name="Test",
        engine="hdiffpatch",
        compression="set6_lzma2",
    )
    result = build_patch(settings)
    assert not result.success
    assert "Source directory not found" in result.error


def test_build_rejects_unknown_engine(tmp_path):
    src, tgt = _make_pair(tmp_path)
    settings = ProjectSettings(
        source_dir=str(src),
        target_dir=str(tgt),
        app_name="Test",
        engine="unknown_engine",
        compression="set6_lzma2",
    )
    result = build_patch(settings)
    assert not result.success
    assert "Unknown engine" in result.error
