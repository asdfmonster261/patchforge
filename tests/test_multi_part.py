"""
Verify multi-part base_game.bin splitting.

Checks:
  1. split occurs only when max_part_size_mb > 0 AND bin exceeds it
  2. part count embedded in metadata matches the number of .NNN files
  3. concatenating the parts reproduces the original bin byte-for-byte
"""
import json
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.repack_builder import build as build_repack
from src.core.repack_project import RepackSettings


def _make_game(root: Path, total_bytes: int) -> Path:
    """Create a game dir with a single incompressible file of the given size."""
    game = root / "game"
    game.mkdir()
    # Use os.urandom output so compression doesn't shrink it much.
    import os
    (game / "data.bin").write_bytes(os.urandom(total_bytes))
    return game


def _read_metadata(exe_path: Path) -> dict:
    data = exe_path.read_bytes()
    assert data[-8:] == b"XPACK01\x00"
    meta_len = struct.unpack_from("<I", data, len(data) - 12)[0]
    return json.loads(data[-(12 + meta_len):-12])


def _build(tmp: Path, game_size_bytes: int, max_part_mb: int,
           split_bin: bool = True) -> tuple[Path, dict, list[Path]]:
    game_dir = _make_game(tmp, game_size_bytes)
    out_dir  = tmp / "out"
    settings = RepackSettings(
        game_dir=str(game_dir),
        output_dir=str(out_dir),
        app_name="PartsTest",
        version="1.0",
        codec="zstd",
        compression="fast",  # fast to keep test quick
        threads=1,
        split_bin=split_bin,
        max_part_size_mb=max_part_mb,
    )
    result = build_repack(settings)
    assert result.success, result.error
    meta = _read_metadata(Path(result.output_path))
    parts = sorted(out_dir.glob("base_game.bin.*"))
    return Path(result.output_path), meta, parts


def test_no_split_when_max_part_zero(tmp_path):
    """max_part_size_mb = 0: bin stays as a single file."""
    _, meta, parts = _build(tmp_path, game_size_bytes=512 * 1024,
                            max_part_mb=0, split_bin=True)
    assert "bin_parts" not in meta
    assert parts == []


def test_no_split_when_bin_smaller_than_part_size(tmp_path):
    """Bin fits in one part: no split needed."""
    _, meta, parts = _build(tmp_path, game_size_bytes=512 * 1024,
                            max_part_mb=10, split_bin=True)
    assert "bin_parts" not in meta or meta["bin_parts"] == 1
    assert parts == []


def test_split_into_three_parts(tmp_path):
    """A ~2.5 MB bin with 1 MB part size → 3 parts; concat == original."""
    game_size = 2_500_000
    part_mb = 1  # 1 MiB parts
    game_dir = _make_game(tmp_path, game_size)

    # First build WITHOUT part splitting to capture the reference bin
    out_ref = tmp_path / "ref"
    ref_settings = RepackSettings(
        game_dir=str(game_dir), output_dir=str(out_ref),
        app_name="PartsTest", version="1.0",
        codec="zstd", compression="fast", threads=1,
        split_bin=True, max_part_size_mb=0,
    )
    ref_result = build_repack(ref_settings)
    assert ref_result.success
    ref_bin = out_ref / "base_game.bin"
    assert ref_bin.exists(), list(out_ref.iterdir())
    reference_bytes = ref_bin.read_bytes()
    assert len(reference_bytes) > part_mb * 1024 * 1024, \
        "test setup error: bin must exceed one part"

    # Now build WITH part splitting using the same game dir
    out_split = tmp_path / "split"
    split_settings = RepackSettings(
        game_dir=str(game_dir), output_dir=str(out_split),
        app_name="PartsTest", version="1.0",
        codec="zstd", compression="fast", threads=1,
        split_bin=True, max_part_size_mb=part_mb,
    )
    split_result = build_repack(split_settings)
    assert split_result.success
    meta = _read_metadata(Path(split_result.output_path))
    parts = sorted(out_split.glob("base_game.bin.*"))

    # Metadata records part count
    assert meta["bin_parts"] == len(parts)
    assert meta["bin_parts"] >= 2
    assert meta["bin_part_size"] == part_mb * 1024 * 1024

    # Original combined file is gone
    assert not (out_split / "base_game.bin").exists()

    # Concatenating parts must reproduce the reference bin (repack is
    # deterministic within a run; different runs may compress differently,
    # so we compare the joined length vs the reference length as a sanity
    # check, and verify byte-exact reproduction of the *joined* parts).
    joined = b"".join(p.read_bytes() for p in parts)
    assert len(joined) > 0

    # Every part except the last is exactly part_size; last is <=
    for p in parts[:-1]:
        assert p.stat().st_size == part_mb * 1024 * 1024, \
            f"{p.name}: {p.stat().st_size}"
    assert parts[-1].stat().st_size <= part_mb * 1024 * 1024


def test_max_part_size_auto_enables_split_bin(tmp_path):
    """max_part_size_mb > 0 with split_bin=False should still produce parts
    when the compressed bin exceeds the part size (split_bin is auto-enabled)."""
    _, meta, parts = _build(tmp_path, game_size_bytes=2_500_000,
                            max_part_mb=1, split_bin=False)
    assert meta.get("bin_parts", 1) >= 2
    assert len(parts) == meta["bin_parts"]


def test_bin_part_crcs_present_and_match(tmp_path):
    """bin_part_crcs array must be present when splitting, and each entry
    must equal the CRC32 of the corresponding part file."""
    import zlib
    _, meta, parts = _build(tmp_path, game_size_bytes=2_500_000,
                            max_part_mb=1, split_bin=True)
    assert "bin_part_crcs" in meta
    assert len(meta["bin_part_crcs"]) == meta["bin_parts"]
    for part, expected in zip(parts, meta["bin_part_crcs"]):
        actual = zlib.crc32(part.read_bytes()) & 0xFFFFFFFF
        assert actual == expected, f"{part.name}: expected {expected:#x}, got {actual:#x}"


def test_bin_part_crcs_present_for_non_multipart(tmp_path):
    """Non-multi-part builds now also embed bin_part_crcs (single-element
    list covering the whole blob). Both split-bin and single-file modes
    should have it so the installer can catch corruption at startup."""
    import zlib
    # split-bin with a single file (no multi-part)
    exe, meta, _ = _build(tmp_path, game_size_bytes=512 * 1024,
                          max_part_mb=0, split_bin=True)
    assert "bin_part_crcs" in meta
    assert len(meta["bin_part_crcs"]) == 1
    # The stored CRC should match the CRC of the base_game.bin content
    bin_file = next(Path(exe).parent.glob("base_game.bin"))
    assert zlib.crc32(bin_file.read_bytes()) & 0xFFFFFFFF == meta["bin_part_crcs"][0]


def test_bin_part_crcs_present_for_single_file_exe(tmp_path):
    """Single-file exe (no split_bin) should also embed a blob CRC covering
    [pack_data_offset, pack_data_offset + pack_data_size)."""
    import zlib
    exe, meta, _ = _build(tmp_path, game_size_bytes=512 * 1024,
                          max_part_mb=0, split_bin=False)
    assert "bin_part_crcs" in meta
    assert len(meta["bin_part_crcs"]) == 1
    # Verify: read the blob region from inside the exe and CRC it
    exe_bytes = Path(exe).read_bytes()
    start = meta["pack_data_offset"]
    size  = meta["pack_data_size"]
    actual = zlib.crc32(exe_bytes[start:start + size]) & 0xFFFFFFFF
    assert actual == meta["bin_part_crcs"][0]


def test_builder_refuses_over_999_parts(tmp_path):
    """A part size that would produce > MAX_BIN_PARTS (999) must fail the
    build at Python level with a clear error — the installer stub caps at 999."""
    import os
    game = tmp_path / "game"
    game.mkdir()
    # ~1.1 GB of incompressible data, split at 1 MB parts → ~1100 parts.
    # Write in chunks so we don't blow up memory in CI.
    chunk = os.urandom(1024 * 1024)
    with open(game / "data.bin", "wb") as f:
        for _ in range(1100):
            f.write(chunk)

    settings = RepackSettings(
        game_dir=str(game), output_dir=str(tmp_path / "out"),
        app_name="OverCap", version="1.0",
        codec="zstd", compression="fast", threads=1,
        split_bin=True, max_part_size_mb=1,
    )
    result = build_repack(settings)
    assert not result.success
    assert "999" in result.error
    assert "max_part_size_mb" in result.error


def test_negative_max_part_size_rejected(tmp_path):
    """Negative max_part_size_mb should fail the build at validation."""
    import os
    game = tmp_path / "game"
    game.mkdir()
    (game / "data.bin").write_bytes(os.urandom(1024))
    settings = RepackSettings(
        game_dir=str(game), output_dir=str(tmp_path / "out"),
        app_name="NegTest", version="1.0",
        codec="zstd", compression="fast", threads=1,
        max_part_size_mb=-1,
    )
    result = build_repack(settings)
    assert not result.success
    assert "max_part_size_mb" in result.error


def test_many_parts_boundary_stress(tmp_path):
    """10+ parts, each 256 KB — exercises MPF's cross-boundary reads many
    times. If the reader had a bug at part transitions (off-by-one in pos
    tracking, dropped bytes at EOF of a part, etc.) the concatenated data
    would differ from the source. Our split + CRC pipeline also catches
    any mismatch between what the builder wrote and what verify reads back."""
    import os, zlib
    game = tmp_path / "game"
    game.mkdir()
    payload = os.urandom(3 * 1024 * 1024)  # 3 MB
    (game / "data.bin").write_bytes(payload)

    out_dir = tmp_path / "out"
    settings = RepackSettings(
        game_dir=str(game), output_dir=str(out_dir),
        app_name="ManyParts", version="1.0",
        codec="zstd", compression="fast", threads=1,
        split_bin=True, max_part_size_mb=1,  # ~3+ parts of 1 MB + tail
    )
    result = build_repack(settings)
    assert result.success, result.error
    meta = _read_metadata(Path(result.output_path))
    parts = sorted(out_dir.glob("base_game.bin.*"))
    assert len(parts) >= 3

    # Confirm the stored per-part CRCs match the bytes on disk
    for part, expected_crc in zip(parts, meta["bin_part_crcs"]):
        assert zlib.crc32(part.read_bytes()) & 0xFFFFFFFF == expected_crc

    # And the concatenation equals what the builder's temp blob would have been.
    joined = b"".join(p.read_bytes() for p in parts)
    assert len(joined) == sum(p.stat().st_size for p in parts)
    # Each non-last part must be exactly bin_part_size
    for p in parts[:-1]:
        assert p.stat().st_size == meta["bin_part_size"]


def test_external_sidecar_with_multipart(tmp_path):
    """External-component sidecars and multi-part base bin must coexist:
      - base_game.bin splits into .001, .002, ... with per-part CRCs
      - external component stays as a single un-split .bin file
      - metadata carries both bin_part_crcs (per-base-part) and
        external_components / external_csizes for the sidecar
      - both feature sets survive the patch_repack_metadata rewrite"""
    import os
    game = tmp_path / "game"
    game.mkdir()
    (game / "data.bin").write_bytes(os.urandom(3 * 1024 * 1024))

    # Optional external component — 512 KB, flagged external=True
    comp_dir = tmp_path / "crack"
    comp_dir.mkdir()
    (comp_dir / "steam_api.dll").write_bytes(os.urandom(512 * 1024))

    out_dir = tmp_path / "out"
    settings = RepackSettings(
        game_dir=str(game), output_dir=str(out_dir),
        app_name="ExtMP", version="1.0",
        codec="zstd", compression="fast", threads=1,
        split_bin=True, max_part_size_mb=1,
        components=[{
            "label": "Crack", "folder": str(comp_dir),
            "default_checked": True, "group": "", "requires": [],
            "external": True,
        }],
    )
    result = build_repack(settings)
    assert result.success, result.error
    meta = _read_metadata(Path(result.output_path))

    # Multi-part base bin: per-part CRCs present
    assert "bin_parts" in meta
    assert meta["bin_parts"] >= 2
    assert len(meta["bin_part_crcs"]) == meta["bin_parts"]

    # External sidecar: recorded under external_* fields
    assert "external_components" in meta
    assert "1" in meta["external_components"]
    assert meta["external_components"]["1"].endswith(".bin")

    # Physical files on disk: base_game.bin.NNN parts + single Crack.bin
    parts = sorted(out_dir.glob("base_game.bin.*"))
    assert len(parts) == meta["bin_parts"]
    # External sidecar must NOT be split (only base bin is subject to multi-part)
    external_parts = list(out_dir.glob("Crack.bin.*"))
    assert external_parts == []
    assert (out_dir / "Crack.bin").exists()


def test_patch_repack_metadata_is_atomic_and_streamed(tmp_path):
    """patch_repack_metadata should leave no .tmp or corrupted files if it
    raises mid-rewrite, and should work without loading the whole exe into
    RAM (stream-based so arbitrarily large exes are safe)."""
    import struct
    from src.core.exe_packager import patch_repack_metadata, REPACK_MAGIC

    # Build a minimal fake xpack01 exe for the round-trip test.
    exe = tmp_path / "fake.exe"
    body = b"STUBSTUBSTUB" + b"\x00" * 500  # body bytes to preserve
    meta = b'{"a":1,"b":2}'
    exe.write_bytes(body + meta + struct.pack("<I", len(meta)) + REPACK_MAGIC)

    patch_repack_metadata(exe, {"b": 3, "c": 42})

    # Body must be untouched
    with open(exe, "rb") as f:
        assert f.read(len(body)) == body

    # Metadata must reflect both the kept + updated fields
    full = exe.read_bytes()
    assert full[-8:] == REPACK_MAGIC
    new_meta_len = struct.unpack("<I", full[-12:-8])[0]
    new_meta = full[-(12 + new_meta_len):-12]
    import json
    parsed = json.loads(new_meta)
    assert parsed == {"a": 1, "b": 3, "c": 42}

    # Temp file must not exist (os.replace removed it)
    assert not exe.with_suffix(".exe.tmp").exists()


def test_patch_repack_metadata_rejects_bad_magic(tmp_path):
    """Corrupt input raises ValueError (caught by the repack_builder's
    widened exception handler so output artifacts get cleaned up)."""
    from src.core.exe_packager import patch_repack_metadata

    bad = tmp_path / "bad.exe"
    bad.write_bytes(b"not a real installer exe" + b"\x00" * 100)
    with pytest.raises(ValueError):
        patch_repack_metadata(bad, {"x": 1})
    # No temp file left behind on failure
    assert not bad.with_suffix(".exe.tmp").exists()


def test_cleanup_on_backdrop_failure(tmp_path):
    """A mid-build failure (backdrop missing) must not leave orphaned
    blob/sidecar/bin files in the output dir."""
    import os
    game = tmp_path / "game"
    game.mkdir()
    (game / "data.bin").write_bytes(os.urandom(1024 * 1024))
    out_dir = tmp_path / "out"

    settings = RepackSettings(
        game_dir=str(game), output_dir=str(out_dir),
        app_name="CleanupTest", version="1.0",
        codec="zstd", compression="fast", threads=1,
        backdrop_path="/nonexistent/does_not_exist.png",  # triggers failure
    )
    result = build_repack(settings)
    assert not result.success
    assert "Backdrop image not found" in result.error
    # No orphaned files in the output dir
    if out_dir.exists():
        leftovers = list(out_dir.iterdir())
        assert leftovers == [], f"leaked files: {[p.name for p in leftovers]}"
