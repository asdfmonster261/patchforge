"""Phase 2 archive-mode tests — remaining helpers not yet moved into the
new tests/core/archive/ tree.  Targets for follow-up reorganisation:
sevenzip cache + arch detect, parse_size / sanitize_name / _split_file,
compress_platform multi-volume, DownloadEvent / cli_progress subscribers,
download_platform crack-identity guard, utils.cache_dir.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest import mock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# compress — sanitize_name + parse_size
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
# compress._split_file
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
# These tests need the actual 7z binary; skipped automatically when absent.
# ---------------------------------------------------------------------------

def _native_7z_or_skip():
    from src.core.archive import sevenzip
    sevenzip.reset_cache()
    p = sevenzip.get_7zip()
    if p is None:
        pytest.skip("native 7z binary not available")
    return p


def _populate_dest(dest: Path, payload_bytes: int) -> None:
    """Drop a steamapps/ subtree under dest with one big random file so 7z
    actually has compressible content.  Random bytes beat zeros to ensure
    the archive can't trivially crunch below a useful size for split tests."""
    import os
    sa = dest / "steamapps" / "common" / "Game"
    sa.mkdir(parents=True, exist_ok=True)
    (sa / "data.bin").write_bytes(os.urandom(payload_bytes))


def test_compress_native_volumes_multipart(tmp_path):
    from src.core.archive.compress import compress_platform
    _native_7z_or_skip()

    _populate_dest(tmp_path, 4 * 1024 * 1024)   # 4 MiB random payload
    parts = compress_platform(
        dest=tmp_path,
        archive_stem="multi",
        password=None,
        compression_level=1,        # fast; we just need the bytes to land
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
    """When the archive ends up smaller than volume_size, native 7z still
    emits .7z.001 — compress_platform renames it to plain .7z so the
    user-facing layout doesn't carry a .001 suffix on a single-part build."""
    from src.core.archive.compress import compress_platform
    _native_7z_or_skip()

    _populate_dest(tmp_path, 64 * 1024)        # 64 KiB
    parts = compress_platform(
        dest=tmp_path,
        archive_stem="single",
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
    """No volume_size = no -v flag.  Output is plain .7z; no .001 ever appears."""
    from src.core.archive.compress import compress_platform
    _native_7z_or_skip()

    _populate_dest(tmp_path, 64 * 1024)
    parts = compress_platform(
        dest=tmp_path,
        archive_stem="plain",
        password=None,
        compression_level=1,
        volume_size=None,
    )
    assert len(parts) == 1
    assert parts[0].name == "plain.7z"
    assert not (tmp_path / "plain.7z.001").exists()


# ---------------------------------------------------------------------------
# sevenzip — cache hit/miss with mocked urllib
# ---------------------------------------------------------------------------

def _redirect_bin_dir(tmp_path):
    from src.core.archive import sevenzip as sz
    fake_bin = tmp_path / "bin"
    return mock.patch.object(sz, "bin_dir", lambda: fake_bin)


def test_sevenzip_returns_none_on_unsupported_platform(tmp_path):
    from src.core.archive import sevenzip as sz
    with _redirect_bin_dir(tmp_path), \
         mock.patch.object(sz, "_detect", return_value=(None, None)):
        sz.reset_cache()
        assert sz.get_7zip() is None


def test_sevenzip_cache_hit_skips_download(tmp_path):
    from src.core.archive import sevenzip as sz
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "7z").write_bytes(b"\x7fELF...")
    download_calls: list = []
    with mock.patch.object(sz, "bin_dir", lambda: fake_bin), \
         mock.patch.object(sz, "_detect", return_value=("linux", "x86_64")), \
         mock.patch.object(sz, "_download_to",
                           side_effect=lambda *a, **kw: download_calls.append(a)):
        sz.reset_cache()
        path = sz.get_7zip()
        assert path == fake_bin / "7z"
        assert download_calls == []


def test_sevenzip_download_failure_returns_none(tmp_path):
    from src.core.archive import sevenzip as sz
    fake_bin = tmp_path / "bin"
    with mock.patch.object(sz, "bin_dir", lambda: fake_bin), \
         mock.patch.object(sz, "_detect", return_value=("windows", "x86_64")), \
         mock.patch.object(sz, "_download_to",
                           side_effect=OSError("network down")):
        sz.reset_cache()
        assert sz.get_7zip() is None
        # Partial file cleaned up.
        assert not (fake_bin / "7zr.exe").exists()


def test_sevenzip_no_url_for_arch_returns_none(tmp_path):
    from src.core.archive import sevenzip as sz
    with _redirect_bin_dir(tmp_path), \
         mock.patch.object(sz, "_detect", return_value=("linux", "ppc64")):
        sz.reset_cache()
        assert sz.get_7zip() is None


# ---------------------------------------------------------------------------
# DownloadEvent dataclass + cli_progress
# ---------------------------------------------------------------------------

def test_download_event_default_fields():
    from src.core.archive.download import DownloadEvent
    ev = DownloadEvent(kind="stage", stage_msg="hi")
    assert ev.kind == "stage"
    assert ev.name == "" and ev.total == 0 and ev.done == 0


def test_plain_log_subscriber_writes_lines():
    from src.core.archive.cli_progress import PlainLogSubscriber
    from src.core.archive.download     import DownloadEvent

    buf = io.StringIO()
    sub = PlainLogSubscriber(file=buf)
    sub(DownloadEvent(kind="stage", stage_msg="Fetching manifests"))
    sub(DownloadEvent(kind="file_started", name="foo.bin", total=100))
    sub(DownloadEvent(kind="file_finished", name="foo.bin", total=100, done=100))
    sub(DownloadEvent(kind="file_skipped",  name="bar.bin", total=50))
    sub(DownloadEvent(kind="error", name="baz.bin", error_msg="bad chunk"))
    sub.close()

    out = buf.getvalue()
    assert "[stage]    Fetching manifests" in out
    assert "[start]    foo.bin" in out
    assert "[done]     foo.bin" in out
    assert "[skip]     bar.bin" in out
    assert "[error]   bad chunk" in out
    # file_progress events are intentionally dropped from log mode.
    sub(DownloadEvent(kind="file_progress", name="x", total=10, done=5))
    assert "file_progress" not in buf.getvalue()


def test_build_subscriber_falls_back_when_no_tty(monkeypatch):
    from src.core.archive import cli_progress
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    sub = cli_progress.build_subscriber(plain=False)
    assert sub.__class__.__name__ == "PlainLogSubscriber"


def test_build_subscriber_plain_flag_overrides_tty(monkeypatch):
    from src.core.archive import cli_progress
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    sub = cli_progress.build_subscriber(plain=True)
    assert sub.__class__.__name__ == "PlainLogSubscriber"


def test_build_subscriber_default_is_live(monkeypatch):
    """Default (TTY, plain=False) returns the SteamArchiver-style live display."""
    from src.core.archive import cli_progress
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(cli_progress, "_TTY", True, raising=False)
    sub = cli_progress.build_subscriber(plain=False)
    assert sub.__class__.__name__ == "LiveDisplaySubscriber"


def test_live_display_accumulates_bytes_without_greenlet():
    """Construct a LiveDisplaySubscriber and feed it events without ever
    entering a gevent context.  The greenlet only spawns on file_started
    when gevent is importable, but state accounting must work either way."""
    from src.core.archive.cli_progress import LiveDisplaySubscriber
    from src.core.archive.download     import DownloadEvent

    sub = LiveDisplaySubscriber()
    # No file_started yet — greenlet must NOT spawn.
    assert sub._greenlet is None

    sub(DownloadEvent(kind="file_started", name="a.bin", total=100))
    sub(DownloadEvent(kind="file_progress", name="a.bin", total=100, done=30))
    sub(DownloadEvent(kind="file_progress", name="a.bin", total=100, done=70))
    sub(DownloadEvent(kind="file_finished", name="a.bin", total=100, done=100))
    sub(DownloadEvent(kind="file_skipped",  name="b.bin", total=50))
    assert sub._downloaded == 100
    assert sub._skipped    == 50
    assert sub._files["a.bin"]["active"] is False

    sub.close()
    assert sub._closed is True


def test_live_display_compress_clears_download_files():
    """compress_started must drop the per-file download rows so the live
    block stops redrawing stale '0 active 999MB downloaded' between
    download and compression stages."""
    from src.core.archive.cli_progress import LiveDisplaySubscriber
    from src.core.archive.download     import DownloadEvent

    sub = LiveDisplaySubscriber()
    sub(DownloadEvent(kind="file_started", name="a.bin", total=100))
    sub(DownloadEvent(kind="file_progress", name="a.bin", total=100, done=100))
    sub(DownloadEvent(kind="file_finished", name="a.bin", total=100, done=100))
    assert sub._files            # still has the finished file
    assert sub._compress_name is None

    sub(DownloadEvent(kind="compress_started", name="game.7z"))
    assert sub._files == {}      # cleared
    assert sub._compress_name == "game.7z"
    assert sub._compress_pct  == 0

    sub(DownloadEvent(kind="compress_progress", name="game.7z", total=100, done=42))
    assert sub._compress_pct == 42

    sub(DownloadEvent(kind="compress_progress", name="game.7z", total=100, done=100))
    assert sub._compress_pct == 100

    sub(DownloadEvent(kind="compress_finished", name="game.7z"))
    assert sub._compress_name is None
    assert sub._compress_pct == 0

    sub.close()


def test_live_display_crack_suppresses_redraw():
    """crack_started must drop the per-file rows and silence _redraw so
    the crack step's print() output isn't fought by the redraw greenlet."""
    from src.core.archive.cli_progress import LiveDisplaySubscriber
    from src.core.archive.download     import DownloadEvent

    sub = LiveDisplaySubscriber()
    sub(DownloadEvent(kind="file_started", name="a.bin", total=100))
    sub(DownloadEvent(kind="file_finished", name="a.bin", total=100, done=100))
    assert sub._files
    assert sub._crack_active is False

    sub(DownloadEvent(kind="crack_started"))
    assert sub._files == {}
    assert sub._crack_active is True

    # Force a manual redraw — must early-return, leaving prev_lines untouched.
    sub._prev_lines = 0
    sub._redraw()
    assert sub._prev_lines == 0

    sub(DownloadEvent(kind="crack_finished"))
    assert sub._crack_active is False

    sub.close()


def test_compress_platform_emits_compress_events(tmp_path):
    """compress_platform on the native path must surround the work in
    compress_started/compress_finished and emit compress_progress between."""
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
        dest=tmp_path,
        archive_stem="evtest",
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
    # The archive is small but at least one progress sample should appear
    # for non-zero work.  Allow zero on absurdly fast runs.
    progress_events = [e for e in events if e.kind == "compress_progress"]
    for pe in progress_events:
        assert 0 <= pe.done <= 100


# ---------------------------------------------------------------------------
# Crack guard — Phase 3 not yet implemented
# ---------------------------------------------------------------------------

def test_download_platform_requires_crack_identity():
    """Calling _download_platform with --crack but no identity must surface
    a clear error rather than silently dropping the flag or crashing later
    with an obscure NoneType access deep in the crack pipeline."""
    from src.core.archive.download import _download_platform
    with pytest.raises(ValueError, match="crack_identity"):
        _download_platform(
            cdn=None, client=None, app_id=730, app_data={},
            dest=Path("/tmp/x"), platform="windows",
            crack="gse",
        )


# ---------------------------------------------------------------------------
# utils — cache_dir layout
# ---------------------------------------------------------------------------

def test_cache_dir_separate_from_config(monkeypatch, tmp_path):
    """Cache dir must NOT live under config dir.  The whole point of the
    split is that wiping cache doesn't lose credentials."""
    from src.core.archive import utils
    from src.core.archive import credentials as cm
    cache = utils.cache_dir()
    config = cm.credentials_path().parent
    # On every supported platform the two should be disjoint paths.
    assert cache != config
    assert config not in cache.parents
    assert cache not in config.parents
