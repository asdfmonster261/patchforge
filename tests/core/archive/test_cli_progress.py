"""archive.cli_progress — DownloadEvent subscribers (PlainLog + Live).

Tests the picker (`build_subscriber`), the offline plain-log shape,
and the live-display state-machine handling for download → compress →
crack transitions.  No greenlet / gevent is exercised here; live tests
construct the subscriber and feed events directly so accounting is
verified without a real-time redraw loop.
"""
from __future__ import annotations

import io
import sys


# ---------------------------------------------------------------------------
# DownloadEvent dataclass
# ---------------------------------------------------------------------------

def test_download_event_default_fields():
    from src.core.archive.download import DownloadEvent
    ev = DownloadEvent(kind="stage", stage_msg="hi")
    assert ev.kind == "stage"
    assert ev.name == "" and ev.total == 0 and ev.done == 0


# ---------------------------------------------------------------------------
# build_subscriber — picker
# ---------------------------------------------------------------------------

def test_build_subscriber_falls_back_when_no_tty(monkeypatch):
    from src.core.archive import cli_progress
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    sub = cli_progress.build_subscriber(plain=False)
    assert sub.__class__.__name__ == "PlainLogSubscriber"


def test_build_subscriber_plain_flag_overrides_tty(monkeypatch):
    """plain=True forces the offline subscriber even when stdout is a TTY."""
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


# ---------------------------------------------------------------------------
# PlainLogSubscriber — offline file output
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# LiveDisplaySubscriber — state accounting without greenlet
# ---------------------------------------------------------------------------

def test_live_display_accumulates_bytes_without_greenlet():
    """Construct LiveDisplaySubscriber and feed it events without
    entering a gevent context.  The greenlet only spawns on file_started
    when gevent is importable, but state accounting must work either
    way."""
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
    """compress_started must drop the per-file download rows so the
    live block stops redrawing stale '0 active 999MB downloaded'
    between download and compression stages."""
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


def test_live_display_switches_to_upload_phase_label():
    """Once an upload starts the footer label flips from 'downloaded'
    to 'uploaded' and bytes accumulate against the upload counter,
    not the download counter."""
    from src.core.archive.cli_progress import LiveDisplaySubscriber
    from src.core.archive.download     import DownloadEvent

    sub = LiveDisplaySubscriber()
    # Simulate end-of-download state: counters set, files cleared by compress.
    sub._downloaded = 1000
    sub(DownloadEvent(kind="compress_started",  name="game.7z"))
    sub(DownloadEvent(kind="compress_finished", name="game.7z"))

    # Upload phase begins.
    sub(DownloadEvent(kind="upload_started",  name="game.7z", total=500))
    assert sub._phase == "upload"
    sub(DownloadEvent(kind="upload_progress", name="game.7z", total=500, done=300))
    assert sub._uploaded == 300
    sub(DownloadEvent(kind="upload_finished", name="game.7z", total=500, done=500))
    assert sub._uploaded == 500

    bytes_, label = sub._phase_counters()
    assert label  == "uploaded"
    assert bytes_ == 500

    # paste_created shouldn't crash and shouldn't touch the byte counters.
    sub(DownloadEvent(kind="paste_created", name="game",
                      stage_msg="https://pb/x"))
    assert sub._uploaded == 500
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
