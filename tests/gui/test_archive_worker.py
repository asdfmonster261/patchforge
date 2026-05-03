"""gui.archive_worker — QObject that drives runner.run_session on a QThread.

Tests don't actually spawn the QThread — they call `worker.run()` on
the test thread and verify started/finished/failed signal emission +
the kwargs that reached runner.run_session.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.core.archive import project as project_mod
from src.core.archive import runner as runner_mod


def test_archive_worker_signals_finished_via_runner(qapp, tmp_path,
                                                     stub_creds,
                                                     archive_project_factory):
    """Mock runner.run_session and verify the worker emits started +
    finished."""
    from src.gui.archive_worker import ArchiveWorker

    proj = archive_project_factory()
    creds = stub_creds()

    fake_result = runner_mod.RunResult(
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

    assert started_seen  == [True]
    assert finished_seen == [fake_result]
    assert failed_seen   == []


def test_archive_worker_emits_failed_when_no_login_tokens(qapp, tmp_path,
                                                          stub_creds,
                                                          archive_project_factory):
    """No saved Steam tokens → fail fast with a user-readable message
    rather than running through to a confusing CM-login crash."""
    from src.gui.archive_worker import ArchiveWorker

    creds = stub_creds()
    creds.has_login_tokens = lambda: False

    proj = archive_project_factory()
    fails = []

    with patch("src.core.archive.credentials.load", return_value=creds):
        w = ArchiveWorker(
            project_obj=proj, project_path=None,
            app_ids=[730], platform="windows",
        )
        w.failed.connect(lambda m: fails.append(m))
        w.run()

    assert fails and "tokens" in fails[0].lower()


def test_archive_worker_countdown_sleep_emits_ticks(qapp, monkeypatch):
    """Drive _countdown_sleep directly without spawning a thread.  We
    install a fake gevent.sleep so the test stays sub-second."""
    from src.gui import archive_worker as aw
    import gevent

    w = aw.ArchiveWorker(
        project_obj=project_mod.new_project(), project_path=None,
        app_ids=[], platform=None,
    )

    ticks: list[int] = []
    w.countdown_tick.connect(lambda n: ticks.append(n))

    monkeypatch.setattr(gevent, "sleep", lambda s: None)
    ok = w._countdown_sleep(3)

    assert ok is True
    assert ticks == [3, 2, 1, 0]


def test_archive_worker_countdown_aborts(qapp, monkeypatch):
    """request_abort during countdown must short-circuit the next tick."""
    from src.gui import archive_worker as aw
    import gevent

    w = aw.ArchiveWorker(
        project_obj=project_mod.new_project(), project_path=None,
        app_ids=[], platform=None,
    )
    w.request_abort()
    monkeypatch.setattr(gevent, "sleep", lambda s: None)
    ok = w._countdown_sleep(10)
    assert ok is False


def test_archive_worker_forwards_per_run_options_to_runner(qapp, tmp_path,
                                                            stub_creds,
                                                            archive_project_factory):
    """ArchiveWorker must thread crack_mode / branch / force_download
    into the runner's kwargs verbatim."""
    from src.gui.archive_worker import ArchiveWorker

    proj = archive_project_factory()
    creds = stub_creds()

    fake_result = runner_mod.RunResult()
    captured = {}

    def fake_run(**kw):
        captured.update(kw)
        return fake_result

    with patch("src.core.archive.credentials.load", return_value=creds), \
         patch("src.core.archive.depots_ini.load", return_value={}), \
         patch("src.core.archive.appinfo.login",
               return_value=(MagicMock(), MagicMock())), \
         patch("src.core.archive.runner.run_session", side_effect=fake_run):
        w = ArchiveWorker(
            project_obj=proj, project_path=None,
            app_ids=[730], platform="windows",
            branch="beta", crack_mode="coldclient",
            force_download=True, log_file=tmp_path / "run.log",
        )
        w.run()

    assert captured["branch"]       == "beta"
    assert captured["crack"]        == "coldclient"
    assert captured["opts"]["force_download"] is True
    # crack_identity must be populated when crack_mode is set.
    assert captured["crack_identity"]  is not None
    assert captured["unstub_options"]  is not None
    # Log file should be created (we wrote the open header into it) —
    # open with mode "a" creates the file if missing.
    assert (tmp_path / "run.log").exists()
