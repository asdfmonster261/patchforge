"""Phase 6 — runner module + GUI worker translation tests.

Runs entirely offline:
  - runner.run_session is exercised against a mock download_app /
    upload_archives / notify_mod stack.
  - GUI tests use the offscreen Qt platform so they pass headlessly in CI.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Make sure GUI tests don't pop a window in CI.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Match the `src.` import style the rest of the suite uses.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.archive import project as project_mod
from src.core.archive import runner as runner_mod
from src.core.archive.download import DownloadEvent


# ---------------------------------------------------------------------------
# Fixtures: a fully-stubbed creds object that exposes the .is_set() probes
# the runner asks about, and a project with a single AppEntry.
# ---------------------------------------------------------------------------

def _stub_creds(*, multiup=False, telegram=False, discord=False):
    creds = SimpleNamespace(
        username="u", steam_id=1, client_refresh_token="t",
        web_api_key="",
        multiup    = SimpleNamespace(username="x" if multiup else "",
                                     password="y" if multiup else "",
                                     is_set=lambda: multiup),
        privatebin = SimpleNamespace(url="", password="", is_set=lambda: False),
        telegram   = SimpleNamespace(token="t" if telegram else "", chat_ids=[],
                                     is_set=lambda: telegram),
        discord    = SimpleNamespace(webhook_url="w" if discord else "",
                                     mention_role_ids=[],
                                     is_set=lambda: discord),
    )
    creds.has_login_tokens = lambda: True
    return creds


def _project_with_one_app(app_id=730, current="100"):
    p = project_mod.new_project()
    p.apps.append(project_mod.AppEntry(
        app_id=app_id, branch="public", current_buildid=current,
    ))
    return p


def _opts(**overrides):
    base = dict(
        workers=4, compression=5, archive_password="",
        volume_size="", language="english", max_retries=1,
        description="", max_concurrent_uploads=1, delete_archives=False,
        experimental=False, unstub=project_mod.UnstubOptions(),
        restart_delay=0, batch_size=0, force_download=False,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# runner.run_session — single-pass mode
# ---------------------------------------------------------------------------

def test_runner_single_pass_collects_archives(tmp_path):
    proj = _project_with_one_app()
    creds = _stub_creds()

    fake_archives = [tmp_path / "game.7z"]
    download_app = MagicMock(return_value=(
        fake_archives,
        {"windows": [(1234, "main", "g1")]},
        {"appid": 730, "name": "Foo", "buildid": "200", "timeupdated": 0},
    ))

    upload_mod = MagicMock()
    notify_mod = MagicMock()

    with patch("src.core.archive.runner.download_app", create=True), \
         patch("src.core.archive.download.download_app", download_app):
        result = runner_mod.run_session(
            client=MagicMock(), cdn=MagicMock(),
            project_obj=proj, project_path=tmp_path / "p.xarchive",
            creds=creds, output_dir=tmp_path,
            app_ids=[730], opts=_opts(),
            platform="windows", notify_mode="none",
            branch="public", crack=False,
            crack_identity=None, unstub_options=None,
            volume_size=None, depot_names={},
            subscriber=None,
            upload_mod=upload_mod, notify_mod=notify_mod,
            log=lambda m: None, warn=lambda m: None,
        )

    assert result.archives == fake_archives
    # current_buildid should have shifted: previous becomes 100, current 200
    assert proj.apps[0].current_buildid  == "200"
    assert proj.apps[0].previous_buildid == "100"


def test_runner_single_pass_continues_after_app_failure(tmp_path):
    """A download_app crash for one app must not abort the whole run."""
    proj = project_mod.new_project()
    proj.apps.extend([
        project_mod.AppEntry(app_id=1, branch="public"),
        project_mod.AppEntry(app_id=2, branch="public"),
    ])
    creds = _stub_creds()

    calls = []

    def fake_dl(client, cdn, app_id, *a, **kw):
        calls.append(app_id)
        if app_id == 1:
            raise RuntimeError("simulated transient")
        return ([tmp_path / f"app{app_id}.7z"],
                {"windows": []},
                {"appid": app_id, "name": str(app_id), "buildid": "10"})

    with patch("src.core.archive.download.download_app", side_effect=fake_dl):
        result = runner_mod.run_session(
            client=MagicMock(), cdn=MagicMock(),
            project_obj=proj, project_path=None,
            creds=creds, output_dir=tmp_path,
            app_ids=[1, 2], opts=_opts(),
            platform="windows", notify_mode="none",
            branch="public", crack=False,
            crack_identity=None, unstub_options=None,
            volume_size=None, depot_names={},
            subscriber=None,
            upload_mod=MagicMock(), notify_mod=MagicMock(),
            log=lambda m: None, warn=lambda m: None,
        )

    assert calls == [1, 2]
    assert len(result.archives) == 1


# ---------------------------------------------------------------------------
# runner.run_session — polling mode
# ---------------------------------------------------------------------------

def test_runner_poll_mode_only_runs_changed_apps(tmp_path):
    proj = _project_with_one_app(current="100")
    creds = _stub_creds()

    download_app = MagicMock(return_value=(
        [tmp_path / "game.7z"],
        {"windows": []},
        {"appid": 730, "name": "Foo", "buildid": "200", "timeupdated": 0},
    ))

    iteration = {"n": 0}

    def fake_detect(client, cdn, apps_by_id, *, force_download=False, **kw):
        iteration["n"] += 1
        if iteration["n"] == 1:
            return [(730, "100", "200", {"name": "Foo", "buildid": "200"})]
        return []  # no changes second cycle

    def fake_countdown(seconds):
        # only allow one wait — second invocation aborts the loop
        return iteration["n"] < 2

    with patch("src.core.archive.poll.detect_changes", fake_detect), \
         patch("src.core.archive.download.download_app", download_app):
        result = runner_mod.run_session(
            client=MagicMock(), cdn=MagicMock(),
            project_obj=proj, project_path=None,
            creds=creds, output_dir=tmp_path,
            app_ids=[], opts=_opts(restart_delay=1),
            platform="windows", notify_mode="none",
            branch="public", crack=False,
            crack_identity=None, unstub_options=None,
            volume_size=None, depot_names={},
            subscriber=None,
            upload_mod=MagicMock(), notify_mod=MagicMock(),
            countdown_sleep=fake_countdown,
            log=lambda m: None, warn=lambda m: None,
        )

    # download_app called exactly once (cycle 1 had a change, cycle 2 didn't)
    assert download_app.call_count == 1
    assert proj.apps[0].current_buildid  == "200"
    assert proj.apps[0].previous_buildid == "100"
    assert len(result.archives) == 1


def test_runner_poll_aborts_via_countdown_returning_false(tmp_path):
    proj = _project_with_one_app()

    def fake_detect(*a, **kw):
        return []

    aborted = []
    def fake_countdown(seconds):
        aborted.append(seconds)
        return False  # immediate abort

    with patch("src.core.archive.poll.detect_changes", fake_detect):
        result = runner_mod.run_session(
            client=MagicMock(), cdn=MagicMock(),
            project_obj=proj, project_path=None,
            creds=_stub_creds(), output_dir=tmp_path,
            app_ids=[], opts=_opts(restart_delay=5),
            platform="windows", notify_mode="none",
            branch="public", crack=False,
            crack_identity=None, unstub_options=None,
            volume_size=None, depot_names={},
            subscriber=None,
            upload_mod=MagicMock(), notify_mod=MagicMock(),
            countdown_sleep=fake_countdown,
            log=lambda m: None, warn=lambda m: None,
        )

    assert aborted == [5]
    assert result.archives == []


# ---------------------------------------------------------------------------
# resolve_notify_mode — drift check after extraction
# ---------------------------------------------------------------------------

def test_resolve_notify_mode_priority_unchanged():
    creds = _stub_creds(telegram=True, multiup=True)
    no_notify = _stub_creds()

    # CLI flag wins
    assert runner_mod.resolve_notify_mode("pre",   "delay", creds) == "pre"
    assert runner_mod.resolve_notify_mode("both",  "delay", creds) == "both"
    # project field used when CLI silent
    assert runner_mod.resolve_notify_mode(None, "pre",   creds) == "pre"
    assert runner_mod.resolve_notify_mode(None, "delay", creds) == "delay"
    # auto: prefers delay when MultiUp creds are present
    assert runner_mod.resolve_notify_mode(None, "", creds) == "delay"
    # auto: pre when no MultiUp creds
    creds_no_mu = _stub_creds(telegram=True)
    assert runner_mod.resolve_notify_mode(None, "", creds_no_mu) == "pre"
    # never returns a mode when no notify creds set
    assert runner_mod.resolve_notify_mode("pre", "delay", no_notify) == "none"


# ---------------------------------------------------------------------------
# CLI shims still delegate correctly (safety net for existing callers).
# ---------------------------------------------------------------------------

def test_cli_shim_resolve_notify_mode_delegates():
    from src.cli.main import _resolve_notify_mode
    creds = _stub_creds(telegram=True)
    assert _resolve_notify_mode(None, "pre", creds) == "pre"


def test_cli_shim_pre_pipeline_delegates():
    from src.cli.main import _archive_run_pre_pipeline
    creds = _stub_creds(telegram=True)
    notify_mod = MagicMock()
    notify_mod.send_telegram_notification = MagicMock()
    _archive_run_pre_pipeline(
        {"appid": 1, "name": "X", "buildid": "10", "timeupdated": 0},
        previous_buildid="9", creds=creds, notify_mode="pre",
        notify_mod=notify_mod,
    )
    assert notify_mod.send_telegram_notification.called


# ---------------------------------------------------------------------------
# GUI: ArchivePanel + ArchiveWorker
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_archive_panel_constructs(qapp):
    from src.gui.archive_panel import ArchivePanel
    p = ArchivePanel()
    assert p.stack.count() == 6
    assert p.body_tabs.count() == 2


def test_archive_panel_save_load_roundtrip(qapp, tmp_path):
    from src.gui.archive_panel import ArchivePanel
    p = ArchivePanel()
    p.project().apps.append(project_mod.AppEntry(app_id=730, branch="public"))
    p.project().bbcode_template = "[b]{APP_NAME}[/b]"
    # Refresh pages so widget state mirrors the in-memory project — the
    # panel's flush() pass during save reads from widgets, not the model.
    p._refresh_pages()
    target = tmp_path / "test.xarchive"
    p._save_to(target)
    assert target.exists()

    p2 = ArchivePanel()
    p2._project = project_mod.load(target)
    p2._refresh_pages()
    assert len(p2.project().apps) == 1
    assert p2.project().apps[0].app_id == 730
    assert p2.project().bbcode_template.startswith("[b]")


def test_archive_run_view_event_translation(qapp):
    from src.gui.archive_run_view import ArchiveRunView, STAGES
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

    # File list should record the transferred file, upload list the archive.
    file_texts = [v.files_list.item(i).text() for i in range(v.files_list.count())]
    assert any("pak01.vpk" in t for t in file_texts)
    upload_texts = [v.upload_list.item(i).text() for i in range(v.upload_list.count())]
    assert any("game.7z" in t for t in upload_texts)


def test_archive_run_view_countdown_format(qapp):
    from src.gui.archive_run_view import ArchiveRunView
    v = ArchiveRunView()
    v._on_countdown(45)
    assert v.countdown_label.text() == "45s"
    v._on_countdown(125)
    assert v.countdown_label.text() == "2:05"
    v._on_countdown(0)
    assert v.countdown_label.text() == "running"


def test_archive_worker_signals_finished_via_runner(qapp, tmp_path):
    """Mock runner.run_session and verify the worker emits started + finished."""
    from src.gui.archive_worker import ArchiveWorker
    from src.core.archive import runner as runner_mod_

    proj = _project_with_one_app()
    creds = _stub_creds()

    fake_result = runner_mod_.RunResult(
        archives=[tmp_path / "x.7z"],
        unknown_depot_ids=set(),
    )

    started_seen = []
    finished_seen = []
    failed_seen = []

    with patch("src.core.archive.credentials.load", return_value=creds), \
         patch("src.core.archive.depots_ini.load", return_value={}), \
         patch("src.core.archive.appinfo.login",
               return_value=(MagicMock(), MagicMock())), \
         patch("src.core.archive.runner.run_session", return_value=fake_result):
        w = ArchiveWorker(
            project_obj=proj, project_path=None,
            app_ids=[730], platform="windows",
        )
        w.started.connect(lambda: started_seen.append(True))
        w.finished.connect(lambda r: finished_seen.append(r))
        w.failed.connect(lambda m: failed_seen.append(m))
        w.run()

    assert started_seen == [True]
    assert finished_seen == [fake_result]
    assert failed_seen == []


def test_archive_worker_emits_failed_when_no_login_tokens(qapp, tmp_path):
    from src.gui.archive_worker import ArchiveWorker

    creds = _stub_creds()
    creds.has_login_tokens = lambda: False

    proj = _project_with_one_app()
    fails = []

    with patch("src.core.archive.credentials.load", return_value=creds):
        w = ArchiveWorker(
            project_obj=proj, project_path=None,
            app_ids=[730], platform="windows",
        )
        w.failed.connect(lambda m: fails.append(m))
        w.run()

    assert fails and "tokens" in fails[0].lower()


def test_archive_worker_countdown_sleep_emits_ticks(qapp):
    """Drive _countdown_sleep directly without spawning a thread.  We
    install a fake time.sleep so the test stays sub-second."""
    from src.gui import archive_worker as aw

    w = aw.ArchiveWorker(
        project_obj=project_mod.new_project(), project_path=None,
        app_ids=[], platform=None,
    )

    ticks: list[int] = []
    w.countdown_tick.connect(lambda n: ticks.append(n))

    with patch.object(aw.time, "sleep", lambda s: None):
        ok = w._countdown_sleep(3)

    assert ok is True
    assert ticks == [3, 2, 1, 0]


def test_archive_worker_countdown_aborts(qapp):
    from src.gui import archive_worker as aw

    w = aw.ArchiveWorker(
        project_obj=project_mod.new_project(), project_path=None,
        app_ids=[], platform=None,
    )
    w.request_abort()
    with patch.object(aw.time, "sleep", lambda s: None):
        ok = w._countdown_sleep(10)
    assert ok is False
