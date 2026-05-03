"""cli.main — archive-mode CLI helpers.

Covers the small per-command helpers that don't run the full
download/upload pipeline:
  * _platform_from_archive_stem (URL routing for build-change posts)
  * _LogTee (--log file mirror with ANSI stripping)
  * _build_unstub_options (CLI flag → unstub option dict)
  * _apply_archive_creds_overrides (CLI overrides on top of creds.json)
  * _resolve_archive_run_options (CLI > project > defaults priority)
  * _persist_archive_run_options (write CLI values back to project)
"""
from __future__ import annotations

import io
from unittest import mock


# ---------------------------------------------------------------------------
# _platform_from_archive_stem
# ---------------------------------------------------------------------------

def test_cli_platform_from_archive_stem():
    from src.cli.main import _platform_from_archive_stem
    assert _platform_from_archive_stem("Game.1.windows.public") == "windows"
    assert _platform_from_archive_stem("Game.1.linux.public")   == "linux"
    assert _platform_from_archive_stem("Game.1.macos.public")   == "macos"
    assert _platform_from_archive_stem("Game.1.beta")           is None


# ---------------------------------------------------------------------------
# _LogTee
# ---------------------------------------------------------------------------

def test_log_tee_strips_ansi_to_file_passes_raw_to_stream(tmp_path):
    """Live-display ANSI codes must reach the terminal verbatim but the
    log file gets the plain-text version — otherwise tail -F shows
    `\\033[1m` literally and breaks word search."""
    from src.cli.main import _LogTee

    fake_stream = io.StringIO()
    fake_stream.isatty = lambda: True
    log_path = tmp_path / "run.log"

    tee = _LogTee(fake_stream, log_path)
    tee.write("hello \033[1mworld\033[0m\n")
    tee.write("plain line\n")
    tee.flush()
    tee.close()

    # __getattr__ forwards isatty so the live display still detects TTY.
    assert tee.isatty() is True
    # Stream got raw ANSI.
    assert "\033[1mworld\033[0m" in fake_stream.getvalue()
    # Log file was stripped.
    body = log_path.read_text()
    assert "\033[" not in body
    assert "hello world" in body
    assert "plain line"  in body


# ---------------------------------------------------------------------------
# _build_unstub_options
# ---------------------------------------------------------------------------

def test_cli_build_unstub_options_inverts_keepstub():
    """--keepstub means don't zero the DOS stub; the dict key is
    `zerodostub` so the boolean inverts."""
    from src.cli.main import _build_unstub_options

    args = mock.Mock(keepbind=False, keepstub=True, dumppayload=True,
                     dumpdrmp=False, realign=False, recalcchecksum=True)
    opts = _build_unstub_options(args)
    assert opts == {
        "keepbind":       False,
        "zerodostub":     False,    # inverted
        "dumppayload":    True,
        "dumpdrmp":       False,
        "realign":        False,
        "recalcchecksum": True,
    }


def test_cli_build_unstub_options_defaults():
    from src.cli.main import _build_unstub_options
    args = mock.Mock(keepbind=False, keepstub=False, dumppayload=False,
                     dumpdrmp=False, realign=False, recalcchecksum=False)
    opts = _build_unstub_options(args)
    assert opts["zerodostub"] is True
    assert all(v is False for k, v in opts.items() if k != "zerodostub")


# ---------------------------------------------------------------------------
# _apply_archive_creds_overrides
# ---------------------------------------------------------------------------

def test_cli_apply_creds_overrides_merges_per_field():
    """CLI flag values overwrite the corresponding creds field; missing
    args leave the creds untouched."""
    from src.cli.main import _apply_archive_creds_overrides
    from src.core.archive import credentials as creds_mod

    creds = creds_mod.Credentials()
    creds.multiup.username    = "from-disk"
    creds.discord.webhook_url = "https://disk-hook"

    args = mock.Mock(
        upload_username="cli-user", upload_password="cli-pw",
        binurl="https://pb-cli", binpass=None,
        telegram_bot_token=None, telegram_chat_id=["c1", "c2"],
        discord_webhook=None,
        discord_mention_role_ids=None,
    )
    _apply_archive_creds_overrides(creds, args)

    # Overridden fields take CLI values.
    assert creds.multiup.username    == "cli-user"
    assert creds.multiup.password    == "cli-pw"
    assert creds.privatebin.url      == "https://pb-cli"
    assert creds.telegram.chat_ids   == ["c1", "c2"]
    # Untouched fields keep disk values.
    assert creds.discord.webhook_url == "https://disk-hook"
    # Empty append list (None) leaves chat_ids/role_ids alone.
    assert creds.privatebin.password == ""


def test_apply_archive_creds_overrides_returns_dirty_flag():
    """Caller relies on the returned bool to decide whether to save."""
    from src.cli.main import _apply_archive_creds_overrides
    from src.core.archive import credentials as creds_mod
    creds = creds_mod.Credentials()

    no_change = mock.Mock(
        upload_username=None, upload_password=None,
        binurl=None, binpass=None,
        telegram_bot_token=None, telegram_chat_id=None,
        discord_webhook=None, discord_mention_role_ids=None,
    )
    assert _apply_archive_creds_overrides(creds, no_change) is False

    changed = mock.Mock(
        upload_username="alice", upload_password=None,
        binurl=None, binpass=None,
        telegram_bot_token=None, telegram_chat_id=None,
        discord_webhook=None, discord_mention_role_ids=None,
    )
    assert _apply_archive_creds_overrides(creds, changed) is True
    assert creds.multiup.username == "alice"


# ---------------------------------------------------------------------------
# _resolve_archive_run_options + _persist_archive_run_options
# ---------------------------------------------------------------------------

def test_resolve_archive_run_options_priority():
    """CLI value (when not None) wins over project value, which wins
    over the built-in default.  Booleans OR together (CLI can only
    force-on) so True from either side wins."""
    from src.cli.main         import _resolve_archive_run_options
    from src.core.archive     import project as pm
    proj = pm.new_project(name="t")
    proj.workers     = 16
    proj.compression = 5
    proj.experimental = True
    proj.unstub.keepbind = True

    # CLI supplies workers=32 (overrides project=16); leaves compression
    # absent (None) so project=5 wins.
    args = mock.Mock(
        workers=32, compression=None, language=None, max_retries=None,
        archive_password=None, volume_size=None,
        description=None, max_concurrent_uploads=None,
        delete_archives=False, experimental=False,
        keepbind=False, keepstub=True,
        dumppayload=False, dumpdrmp=False, realign=False, recalcchecksum=False,
        restart_delay=None, batch_size=None, force_download=False,
        crack=None,
    )
    opts = _resolve_archive_run_options(args, proj)
    assert opts["workers"]      == 32     # CLI wins
    assert opts["compression"]  == 5      # project wins
    assert opts["language"]     == "english"  # built-in default (proj also default)
    assert opts["experimental"] is True   # project True survives CLI False
    # Unstub: CLI keepstub True OR project keepstub False = True.
    #         CLI keepbind False OR project keepbind True  = True.
    assert opts["unstub"].keepstub is True
    assert opts["unstub"].keepbind is True


def test_resolve_archive_run_options_no_project():
    """No project → CLI values, then built-in defaults."""
    from src.cli.main import _resolve_archive_run_options
    args = mock.Mock(
        workers=None, compression=None, language=None, max_retries=None,
        archive_password=None, volume_size=None,
        description=None, max_concurrent_uploads=None,
        delete_archives=False, experimental=False,
        keepbind=False, keepstub=False,
        dumppayload=False, dumpdrmp=False, realign=False, recalcchecksum=False,
        restart_delay=None, batch_size=None, force_download=False,
        crack=None,
    )
    opts = _resolve_archive_run_options(args, None)
    assert opts["workers"]                == 8
    assert opts["compression"]            == 9
    assert opts["language"]               == "english"
    assert opts["max_retries"]            == 1
    assert opts["max_concurrent_uploads"] == 1
    assert opts["delete_archives"]        is False
    assert opts["experimental"]           is False


def test_persist_archive_run_options_writes_cli_values():
    """When CLI supplies a value, the project field is updated.  CLI
    values matching the existing project value report no change."""
    from src.cli.main         import _persist_archive_run_options
    from src.core.archive     import project as pm
    proj = pm.new_project(name="t")
    proj.workers     = 8
    proj.compression = 9

    args = mock.Mock(
        workers=16, compression=9, language=None, max_retries=None,
        archive_password=None, volume_size=None,
        description="newdesc", max_concurrent_uploads=None,
        delete_archives=True, experimental=False,
        keepbind=False, keepstub=True,
        dumppayload=False, dumpdrmp=False, realign=False, recalcchecksum=False,
        restart_delay=None, batch_size=None, force_download=False,
        crack=None,
    )
    changed = _persist_archive_run_options(args, proj)
    assert changed is True
    assert proj.workers            == 16          # CLI value persisted
    assert proj.compression        == 9           # unchanged (CLI matched project)
    assert proj.upload_description == "newdesc"   # str CLI value persisted
    assert proj.delete_archives    is True        # bool flipped on
    assert proj.unstub.keepstub    is True        # unstub bool flipped on
    assert proj.unstub.keepbind    is False       # untouched


def test_persist_archive_run_options_returns_false_when_nothing_changed():
    from src.cli.main     import _persist_archive_run_options
    from src.core.archive import project as pm
    proj = pm.new_project(name="t")
    args = mock.Mock(
        workers=None, compression=None, language=None, max_retries=None,
        archive_password=None, volume_size=None,
        description=None, max_concurrent_uploads=None,
        delete_archives=False, experimental=False,
        keepbind=False, keepstub=False,
        dumppayload=False, dumpdrmp=False, realign=False, recalcchecksum=False,
        restart_delay=None, batch_size=None, force_download=False,
        crack=None,
    )
    assert _persist_archive_run_options(args, proj) is False


# ---------------------------------------------------------------------------
# _poll_countdown — gevent-based wait between poll cycles
# ---------------------------------------------------------------------------

def test_poll_countdown_tty_decrements_per_second(monkeypatch, capsys):
    """On a TTY the helper writes a one-line \\r-overwritten countdown
    that ticks down once per second."""
    from src.cli import main as cli_main
    import sys
    import gevent

    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    sleeps: list[float] = []
    monkeypatch.setattr(gevent, "sleep", lambda n: sleeps.append(n))

    assert cli_main._poll_countdown(3) is True
    assert sleeps == [1, 1, 1]                         # 3 ticks
    out = capsys.readouterr().out
    assert "next poll cycle in 3s" in out
    assert "next poll cycle in 2s" in out
    assert "next poll cycle in 1s" in out


def test_poll_countdown_non_tty_single_print(monkeypatch, capsys):
    """Non-TTY path must NOT write per-second \\r frames — log files
    would fill with carriage-return spam.  One-shot 'sleeping Xs'
    line + a single sleep(X) call instead."""
    from src.cli import main as cli_main
    import sys
    import gevent

    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    sleeps: list[float] = []
    monkeypatch.setattr(gevent, "sleep", lambda n: sleeps.append(n))

    cli_main._poll_countdown(7)
    assert sleeps == [7]
    assert "sleeping 7s" in capsys.readouterr().out


def test_poll_countdown_returns_false_on_keyboard_interrupt(monkeypatch):
    from src.cli import main as cli_main
    import sys
    import gevent

    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    def boom(_):
        raise KeyboardInterrupt
    monkeypatch.setattr(gevent, "sleep", boom)
    assert cli_main._poll_countdown(5) is False


# ---------------------------------------------------------------------------
# Polling-mode flag plumbing — _resolve / _persist for restart_delay /
# batch_size / force_download
# ---------------------------------------------------------------------------

def _full_args(**overrides):
    """argparse-shaped Mock with every flag _resolve / _persist consults."""
    base = dict(
        workers=None, compression=None, language=None, max_retries=None,
        archive_password=None, volume_size=None,
        description=None, max_concurrent_uploads=None,
        delete_archives=False, experimental=False,
        keepbind=False, keepstub=False,
        dumppayload=False, dumpdrmp=False, realign=False, recalcchecksum=False,
        restart_delay=None, batch_size=None, force_download=False,
        crack=None,
    )
    base.update(overrides)
    return mock.Mock(**base)


def test_resolve_picks_cli_over_project_for_polling_knobs():
    from src.cli.main         import _resolve_archive_run_options
    from src.core.archive     import project as pm
    proj = pm.new_project(name="t")
    proj.restart_delay = 60
    proj.batch_size    = 5

    args = _full_args(restart_delay=300, batch_size=10, force_download=True)
    opts = _resolve_archive_run_options(args, proj)
    assert opts["restart_delay"]  == 300   # CLI wins
    assert opts["batch_size"]     == 10    # CLI wins
    assert opts["force_download"] is True  # per-run override


def test_resolve_falls_back_to_project_then_default():
    from src.cli.main     import _resolve_archive_run_options
    from src.core.archive import project as pm
    proj = pm.new_project(name="t")
    proj.restart_delay = 120
    # batch_size left at default (0)

    args = _full_args()  # no CLI values
    opts = _resolve_archive_run_options(args, proj)
    assert opts["restart_delay"]  == 120  # project wins
    assert opts["batch_size"]     == 0    # project default
    assert opts["force_download"] is False

    opts2 = _resolve_archive_run_options(_full_args(), None)
    assert opts2["restart_delay"]  == 0
    assert opts2["batch_size"]     == 0
    assert opts2["force_download"] is False


def test_persist_writes_polling_knobs_but_skips_force_download():
    from src.cli.main     import _persist_archive_run_options
    from src.core.archive import project as pm
    proj = pm.new_project(name="t")

    args = _full_args(restart_delay=900, batch_size=8, force_download=True)
    changed = _persist_archive_run_options(args, proj)
    assert changed is True
    assert proj.restart_delay == 900     # persisted
    assert proj.batch_size    == 8       # persisted
    # force_download is per-run-only; project shouldn't track it.
    assert not hasattr(proj, "force_download")


def test_resolve_archive_run_options_picks_project_crack_mode():
    """When the CLI doesn't pass --crack, opts['crack'] falls back to
    the project's stored crack_mode."""
    from src.cli.main         import _resolve_archive_run_options
    from src.core.archive     import project as pm
    proj = pm.new_project(name="t")
    proj.crack_mode = "gse"

    args = _full_args()
    args.crack = None
    opts = _resolve_archive_run_options(args, proj)
    assert opts["crack"] == "gse"

    args.crack = "coldclient"
    opts2 = _resolve_archive_run_options(args, proj)
    assert opts2["crack"] == "coldclient"   # CLI overrides


def test_persist_archive_run_options_writes_crack_mode():
    from src.cli.main         import _persist_archive_run_options
    from src.core.archive     import project as pm
    proj = pm.new_project(name="t")
    args = _full_args()
    args.crack = "coldclient"
    assert _persist_archive_run_options(args, proj) is True
    assert proj.crack_mode == "coldclient"


def test_persist_polling_knobs_idempotent_when_unchanged():
    from src.cli.main     import _persist_archive_run_options
    from src.core.archive import project as pm
    proj = pm.new_project(name="t")
    proj.restart_delay = 120
    proj.batch_size    = 4

    args = _full_args(restart_delay=120, batch_size=4)
    assert _persist_archive_run_options(args, proj) is False


# ---------------------------------------------------------------------------
# CLI integration smoke — _cmd_archive_download with mocked steam[client]
# ---------------------------------------------------------------------------

def _stub_archive_modules(monkeypatch, *, fake_download_app=None,
                         fake_detect=None, fake_sleep=None):
    """Common monkeypatch setup for the two integration tests below.
    Returns the fake_creds object so callers can mutate before run."""
    import sys
    from src.cli import main as cli_main
    from src.core.archive import credentials as creds_mod

    creds = creds_mod.Credentials()
    creds.username = "u"
    creds.steam_id = 1
    creds.client_refresh_token = "t"
    monkeypatch.setattr(creds_mod, "load", lambda: creds)
    monkeypatch.setattr(creds_mod, "save", lambda *_a, **_k: None)
    monkeypatch.setattr(cli_main, "_archive_require_extras_or_die", lambda: None)

    fake_appinfo = mock.Mock()
    fake_appinfo.login.return_value = (mock.Mock(), mock.Mock())
    monkeypatch.setitem(sys.modules, "src.core.archive.appinfo", fake_appinfo)

    fake_dl = mock.Mock()
    if fake_download_app is not None:
        fake_dl.download_app = fake_download_app
    monkeypatch.setitem(sys.modules, "src.core.archive.download", fake_dl)

    fake_compress = mock.Mock()
    fake_compress.parse_size = lambda s: None
    monkeypatch.setitem(sys.modules, "src.core.archive.compress", fake_compress)

    fake_progress = mock.Mock()
    fake_progress.build_subscriber = lambda plain=False: mock.Mock()
    monkeypatch.setitem(sys.modules, "src.core.archive.cli_progress", fake_progress)

    fake_depots = mock.Mock()
    fake_depots.load          = lambda: {}
    fake_depots.record_unknown = lambda ids: []
    fake_depots.depots_path   = lambda: "/dev/null"
    monkeypatch.setitem(sys.modules, "src.core.archive.depots_ini", fake_depots)

    fake_upload = mock.Mock()
    fake_upload.upload_archives = lambda *a, **kw: {}
    monkeypatch.setitem(sys.modules, "src.core.archive.upload", fake_upload)

    monkeypatch.setitem(sys.modules, "src.core.archive.notify", mock.Mock())

    if fake_detect is not None:
        from src.core.archive import poll as poll_mod
        monkeypatch.setattr(poll_mod, "detect_changes", fake_detect)
    if fake_sleep is not None:
        import gevent
        monkeypatch.setattr(gevent, "sleep", fake_sleep)
    return creds


def test_default_platform_all_in_project_used_when_cli_omits_platform(
        tmp_path, monkeypatch):
    """When --platform is absent, project.default_platform must apply.
    Regression: argparse default of 'windows' was clobbering the
    project field, so default_platform=all silently downloaded only
    Windows."""
    from src.cli import main as cli_main
    from src.core.archive import project as pm

    captured: dict = {}
    def fake_download_app(client, cdn, app_id, output_dir, *, platform, **kw):
        captured["platform"] = platform
        return ([], {}, {"appid": app_id, "name": "X", "buildid": "1",
                          "timeupdated": 0})

    proj = pm.new_project(name="t")
    proj.default_platform = "all"
    proj.apps.append(pm.AppEntry(app_id=42))
    project_path = tmp_path / "p.xarchive"
    pm.save(proj, project_path)

    _stub_archive_modules(monkeypatch, fake_download_app=fake_download_app)

    args = mock.Mock(
        log=None,
        upload_username=None, upload_password=None, binurl=None, binpass=None,
        telegram_bot_token=None, telegram_chat_id=None,
        discord_webhook=None, discord_mention_role_ids=None,
        delete_archives=False,
        app_ids=[], appid_file=None, project=str(project_path),
        crack=None, no_progress=True,
        platform=None,                     # CLI omitted → project decides
        branch="public", branch_password=None,
        output_dir=str(tmp_path),
        workers=None, compression=None, language=None, max_retries=None,
        archive_password=None, volume_size=None,
        description=None, max_concurrent_uploads=None, experimental=False,
        keepbind=False, keepstub=False,
        dumppayload=False, dumpdrmp=False, realign=False, recalcchecksum=False,
        restart_delay=None, batch_size=None, force_download=False,
        notify_mode_flag=None,
    )
    cli_main._cmd_archive_download(args)
    assert captured["platform"] == "all"


def test_polling_driver_loops_then_exits_on_keyboard_interrupt(
        tmp_path, monkeypatch):
    """One full polling cycle:
      iteration 1: detect_changes returns one app → download fires
      iteration 2: detect_changes returns nothing  → download skipped
      gevent.sleep raises KeyboardInterrupt → driver exits cleanly.
    Verifies force_download is True on call 1, False on call 2."""
    from src.cli import main as cli_main
    from src.core.archive import project as pm

    proj = pm.new_project(name="poll-smoke")
    proj.restart_delay = 1
    proj.apps.append(pm.AppEntry(app_id=100, current_buildid="old"))
    project_path = tmp_path / "p.xarchive"
    pm.save(proj, project_path)

    detect_calls: list[bool] = []
    info = {"name": "Game", "buildid": "new", "timeupdated": 1,
            "oslist": "windows", "installdir": "g"}
    def fake_detect(client, cdn, apps, *, force_download, batch_size,
                    max_retries, on_event=None, abort=None):
        detect_calls.append(force_download)
        if len(detect_calls) == 1:
            return [(100, "old", "new", info)]
        return []

    download_calls: list[int] = []
    def fake_download_app(*a, **kw):
        download_calls.append(a[2])  # app_id positional
        return ([tmp_path / "x.7z"], {}, {"appid": 100, "name": "Game",
                                          "buildid": "new", "timeupdated": 1})

    sleep_calls: list[int] = []
    def fake_sleep(n):
        sleep_calls.append(n)
        if len(sleep_calls) >= 2:           # exit on second sleep
            raise KeyboardInterrupt

    _stub_archive_modules(monkeypatch,
                          fake_download_app=fake_download_app,
                          fake_detect=fake_detect,
                          fake_sleep=fake_sleep)

    args = mock.Mock(
        log=None,
        upload_username=None, upload_password=None, binurl=None, binpass=None,
        telegram_bot_token=None, telegram_chat_id=None,
        discord_webhook=None, discord_mention_role_ids=None,
        delete_archives=False,
        app_ids=[], appid_file=None, project=str(project_path),
        crack=None, no_progress=True,
        platform="windows", branch="public", branch_password=None,
        output_dir=str(tmp_path),
        workers=None, compression=None, language=None, max_retries=None,
        archive_password=None, volume_size=None,
        description=None, max_concurrent_uploads=None, experimental=False,
        keepbind=False, keepstub=False,
        dumppayload=False, dumpdrmp=False, realign=False, recalcchecksum=False,
        restart_delay=None, batch_size=None, force_download=True,
        notify_mode_flag=None,
    )

    cli_main._cmd_archive_download(args)

    # Iteration 1 forced (CLI flag), iteration 2 cleared.
    assert detect_calls == [True, False]
    # Only the first iteration produced a download.
    assert download_calls == [100]
    # We slept twice (after iter 1 and iter 2), the second one raised KI.
    assert sleep_calls == [1, 1]

    # Project saved at end of each iteration → current_buildid persisted.
    reloaded = pm.load(project_path)
    assert reloaded.apps[0].current_buildid.buildid == "new"
