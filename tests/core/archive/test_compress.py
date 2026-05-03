"""archive.compress — name sanitisation, size-string parser, manual
volume splitter, native-7z compress_platform integration.

The compress_platform tests need the actual 7z binary; they are
skipped when it isn't on PATH so the suite stays runnable on systems
without it.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _native_7z_or_skip():
    from src.core.archive import sevenzip
    sevenzip.reset_cache()
    if sevenzip.get_7zip() is None:
        pytest.skip("native 7z binary not available")


def _populate_dest(dest: Path, payload_bytes: int) -> None:
    """Drop a steamapps/ subtree under dest with one big random file so
    7z actually has compressible content.  Random bytes (not zeros) so
    the archive can't trivially crunch below a useful size for split
    tests."""
    sa = dest / "steamapps" / "common" / "Game"
    sa.mkdir(parents=True, exist_ok=True)
    (sa / "data.bin").write_bytes(os.urandom(payload_bytes))


# ---------------------------------------------------------------------------
# sanitize_name + parse_size — pure helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("inp,out", [
    ("Counter-Strike 2",       "Counter-Strike.2"),
    ("Counter-Strike: 2",      "Counter-Strike.2"),
    ("Foo  Bar / Baz!",        "Foo.Bar.Baz"),
    ("./.../weird___name",     "weirdname"),
    ("Already-Sanitized.Name", "Already-Sanitized.Name"),
])
def test_sanitize_name(inp, out):
    from src.core.archive.compress import sanitize_name
    assert sanitize_name(inp) == out


@pytest.mark.parametrize("inp,out", [
    ("4g",        4 * 1024 ** 3),
    ("700m",      700 * 1024 ** 2),
    ("1024k",     1024 ** 2),
    ("2t",        2 * 1024 ** 4),
    ("12345",     12345),
    ("1.5g",      int(1.5 * 1024 ** 3)),
])
def test_parse_size_human(inp, out):
    from src.core.archive.compress import parse_size
    assert parse_size(inp) == out


def test_parse_size_invalid():
    from src.core.archive.compress import parse_size
    with pytest.raises(ValueError):
        parse_size("not-a-size")


# ---------------------------------------------------------------------------
# _split_file — manual byte-level volume splitter
# ---------------------------------------------------------------------------

def test_split_file_volumes(tmp_path):
    from src.core.archive.compress import _split_file
    src = tmp_path / "big.bin"
    src.write_bytes(b"A" * 10 + b"B" * 10 + b"C" * 5)
    parts = _split_file(src, 10)
    assert len(parts) == 3
    assert parts[0].read_bytes() == b"A" * 10
    assert parts[1].read_bytes() == b"B" * 10
    assert parts[2].read_bytes() == b"C" * 5
    # Original removed on success.
    assert not src.exists()


def test_split_file_no_split_when_volume_larger(tmp_path):
    from src.core.archive.compress import _split_file
    src = tmp_path / "small.bin"
    src.write_bytes(b"X" * 20)
    parts = _split_file(src, 50)
    assert len(parts) == 1
    assert parts[0].read_bytes() == b"X" * 20
    assert not src.exists()


# ---------------------------------------------------------------------------
# compress_platform — native -v<size> multi-volume integration
# ---------------------------------------------------------------------------

def test_compress_native_volumes_multipart(tmp_path):
    from src.core.archive.compress import compress_platform
    _native_7z_or_skip()

    _populate_dest(tmp_path, 4 * 1024 * 1024)   # 4 MiB random payload
    parts = compress_platform(
        dest=tmp_path, archive_stem="multi",
        password=None,
        compression_level=1,        # fast — we only need bytes to land
        volume_size=512 * 1024,     # 512 KiB chunks → at least a few parts
    )
    assert len(parts) >= 2
    for p in parts:
        # Numbered parts retain .001/.002/... suffix.
        assert p.name.startswith("multi.7z.")
        assert p.exists() and p.stat().st_size > 0
    # Plain unsplit form must NOT exist when archive was split.
    assert not (tmp_path / "multi.7z").exists()


def test_compress_native_volumes_single_part_renamed(tmp_path):
    """When the archive ends up smaller than volume_size, native 7z
    still emits .7z.001 — compress_platform renames it to plain .7z so
    the user-facing layout doesn't carry a .001 suffix on a single-part
    build."""
    from src.core.archive.compress import compress_platform
    _native_7z_or_skip()

    _populate_dest(tmp_path, 64 * 1024)        # 64 KiB
    parts = compress_platform(
        dest=tmp_path, archive_stem="single",
        password=None,
        compression_level=1,
        volume_size=4 * 1024 * 1024,           # 4 MiB ≫ archive size
    )
    assert len(parts) == 1
    assert parts[0].name == "single.7z"
    assert parts[0].exists()
    # The .001 form is gone post-rename.
    assert not (tmp_path / "single.7z.001").exists()


def test_compress_native_no_volume_size(tmp_path):
    """No volume_size = no -v flag.  Output is plain .7z; no .001 ever
    appears."""
    from src.core.archive.compress import compress_platform
    _native_7z_or_skip()

    _populate_dest(tmp_path, 64 * 1024)
    parts = compress_platform(
        dest=tmp_path, archive_stem="plain",
        password=None,
        compression_level=1,
        volume_size=None,
    )
    assert len(parts) == 1
    assert parts[0].name == "plain.7z"
    assert not (tmp_path / "plain.7z.001").exists()


def test_compress_platform_emits_compress_events(tmp_path):
    """compress_platform on the native path must surround the work in
    compress_started / compress_finished and emit compress_progress
    between."""
    from src.core.archive import sevenzip
    from src.core.archive.compress import compress_platform
    sevenzip.reset_cache()
    if sevenzip.get_7zip() is None:
        pytest.skip("native 7z binary not available")

    sa = tmp_path / "steamapps" / "common" / "Game"
    sa.mkdir(parents=True)
    (sa / "data.bin").write_bytes(b"\x00" * (256 * 1024))   # easily compressed

    events: list = []
    compress_platform(
        dest=tmp_path, archive_stem="evtest",
        password=None,
        compression_level=1,
        volume_size=None,
        on_event=events.append,
    )
    kinds = [e.kind for e in events]
    assert "compress_started"  in kinds
    assert "compress_finished" in kinds
    # compress_started must precede compress_finished.
    assert kinds.index("compress_started") < kinds.index("compress_finished")
    # Archive is small but at least one progress sample is OK; allow
    # zero on absurdly fast runs.
    for pe in (e for e in events if e.kind == "compress_progress"):
        assert 0 <= pe.done <= 100
