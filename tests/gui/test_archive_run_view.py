"""gui.archive_run_view — DownloadEvent → Qt-widget translation.

Tests drive synthetic DownloadEvent instances directly into the
view's _on_event handler so signals are exercised without an actual
ArchiveWorker / QThread spinning up.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from src.core.archive.download import DownloadEvent


def test_archive_run_view_event_translation(qapp):
    from src.gui.archive_run_view import ArchiveRunView
    v = ArchiveRunView()
    # Drive a fake DownloadEvent stream and verify state transitions.
    v._on_event(DownloadEvent(kind="file_started", name="pak01.vpk", total=1000))
    v._on_event(DownloadEvent(kind="file_progress", name="pak01.vpk",
                               total=1000, done=500))
    v._on_event(DownloadEvent(kind="file_finished", name="pak01.vpk",
                               total=1000, done=1000))
    v._on_event(DownloadEvent(kind="upload_started", name="game.7z", total=2000))
    v._on_event(DownloadEvent(kind="upload_progress", name="game.7z",
                               total=2000, done=1000))
    v._on_event(DownloadEvent(kind="upload_finished", name="game.7z",
                               total=2000, done=2000))
    v._on_event(DownloadEvent(kind="paste_created", name="game",
                               stage_msg="https://privatebin.example/abc"))

    # Stage strip should have advanced past Download into Upload/Paste.
    assert v.stage_strip._state["Download"] in ("active", "done")
    assert v.stage_strip._state["Upload"]   in ("active", "done")
    assert v.stage_strip._state["Paste"]    in ("active", "done")

    # File list records the transferred file, upload list the archive.
    file_texts = [v.files_list.item(i).text() for i in range(v.files_list.count())]
    assert any("pak01.vpk" in t for t in file_texts)
    upload_texts = [v.upload_list.item(i).text() for i in range(v.upload_list.count())]
    assert any("game.7z" in t for t in upload_texts)


def test_archive_run_view_countdown_format(qapp):
    """_on_countdown formats remaining seconds as 'Xs' under one
    minute, 'M:SS' above, 'running' at zero."""
    from src.gui.archive_run_view import ArchiveRunView
    v = ArchiveRunView()
    v._on_countdown(45)
    assert v.countdown_label.text() == "45s"
    v._on_countdown(125)
    assert v.countdown_label.text() == "2:05"
    v._on_countdown(0)
    assert v.countdown_label.text() == "running"


def test_archive_run_view_handles_app_info_progress(qapp):
    """app_info_progress events bind the appinfo bar's range + value
    and fill the label so the user sees X / N apps probed."""
    from src.gui.archive_run_view import ArchiveRunView
    v = ArchiveRunView()
    v._on_event(DownloadEvent(kind="app_info_progress",
                               name="730", done=1, total=3))
    assert v.appinfo_bar.maximum() == 3
    assert v.appinfo_bar.value()   == 1
    assert "1 / 3" in v.appinfo_label.text()
    v._on_event(DownloadEvent(kind="app_info_progress",
                               name="440", done=3, total=3))
    assert v.appinfo_bar.value() == 3


def test_archive_run_view_stop_button_uses_indirection(qapp):
    """Stop click must route through _on_stop_clicked + worker
    request_abort, then disable the button + update label so the user
    sees feedback even if the abort takes effect at the next checkpoint."""
    from src.gui.archive_run_view import ArchiveRunView

    class FakeWorker(QObject):
        event           = Signal(object)
        log_line        = Signal(str, str)
        countdown_tick  = Signal(int)
        started         = Signal()
        finished        = Signal(object)
        failed          = Signal(str)
        def __init__(self):
            super().__init__()
            self.aborted = False
        def request_abort(self):
            self.aborted = True

    v = ArchiveRunView()
    w = FakeWorker()
    v.attach(w)
    v.btn_stop.click()
    assert w.aborted is True
    assert v.btn_stop.isEnabled() is False
    assert v.btn_stop.text().lower().startswith("stopping")
    assert "stopping" in v.summary_label.text().lower()
