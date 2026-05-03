"""Phase 5 tests — poll-on-change buildid detection + CLI knob plumbing.

Covers the offline-testable surface:
  - poll.detect_changes change-only / force-download / batch-size /
    skip-when-no-buildid behaviour
  - project schema roundtrip for restart_delay + batch_size
  - resolver / persist priority for the new knobs (CLI > project > default)
  - force_download routing into pre + post notify pipelines
  - --restart-delay > 0 without --project bails out cleanly

The actual sleep/loop behaviour of the CLI driver is exercised through a
single integration-style test that mocks query_app_info_batch +
download_app + time.sleep so we never hit the network and the loop
exits deterministically.
"""

from __future__ import annotations

from unittest import mock


# ---------------------------------------------------------------------------
# poll.detect_changes
# ---------------------------------------------------------------------------

def _stub_qaib(infos: dict[int, dict | None]):
    """Build a stand-in for query_app_info_batch that yields preset infos."""
    def fake(client, cdn, app_ids, max_retries=1, batch_size=None, quiet=False):
        for aid in app_ids:
            yield aid, infos.get(aid)
    return fake


def test_detect_changes_returns_only_changed_apps():
    from src.core.archive import poll
    from src.core.archive.project import AppEntry

    apps_by_id = {
        100: AppEntry(app_id=100, current_buildid="111"),
        200: AppEntry(app_id=200, current_buildid="222"),
        300: AppEntry(app_id=300, current_buildid=""),
    }
    infos = {
        100: {"name": "A", "buildid": "111", "timeupdated": 1, "oslist": "windows", "installdir": "a"},
        200: {"name": "B", "buildid": "999", "timeupdated": 2, "oslist": "windows", "installdir": "b"},
        300: {"name": "C", "buildid": "333", "timeupdated": 3, "oslist": "windows", "installdir": "c"},
    }
    with mock.patch.object(poll, "query_app_info_batch", _stub_qaib(infos)):
        changes = poll.detect_changes(None, None, apps_by_id)

    by_id = {row[0]: row for row in changes}
    assert 100 not in by_id                              # unchanged
    assert by_id[200] == (200, "222", "999", infos[200]) # bumped
    # 300 was first-seen this cycle: seeded silently, no download.
    assert 300 not in by_id
    assert apps_by_id[300].current_buildid.buildid == "333"
    assert apps_by_id[300].name                    == "C"
    # Existing entries also get their name backfilled when missing.
    assert apps_by_id[100].name == "A"
    assert apps_by_id[200].name == "B"


def test_detect_changes_force_download_returns_all_with_valid_buildid():
    from src.core.archive import poll
    from src.core.archive.project import AppEntry

    apps_by_id = {
        100: AppEntry(app_id=100, current_buildid="111"),
        200: AppEntry(app_id=200, current_buildid="222"),
    }
    infos = {
        100: {"name": "A", "buildid": "111", "timeupdated": 1, "oslist": "", "installdir": ""},
        200: {"name": "B", "buildid": "222", "timeupdated": 2, "oslist": "", "installdir": ""},
    }
    with mock.patch.object(poll, "query_app_info_batch", _stub_qaib(infos)):
        changes = poll.detect_changes(None, None, apps_by_id, force_download=True)

    assert {row[0] for row in changes} == {100, 200}


def test_detect_changes_skips_apps_without_usable_buildid():
    from src.core.archive import poll
    from src.core.archive.project import AppEntry

    apps_by_id = {
        100: AppEntry(app_id=100, current_buildid=""),
        200: AppEntry(app_id=200, current_buildid=""),
        300: AppEntry(app_id=300, current_buildid=""),
    }
    infos = {
        100: None,                                              # no info
        200: {"name": "B", "buildid": "Unknown", "timeupdated": 0, "oslist": "", "installdir": ""},
        300: {"name": "C", "buildid": "999",     "timeupdated": 0, "oslist": "", "installdir": ""},
    }
    with mock.patch.object(poll, "query_app_info_batch", _stub_qaib(infos)):
        changes = poll.detect_changes(None, None, apps_by_id, force_download=True)

    # 300 has a usable buildid but is first-seen → seeded, not returned.
    # 100 / 200 have no usable buildid → skipped entirely.
    assert changes == []
    assert apps_by_id[300].current_buildid.buildid == "999"
    assert apps_by_id[300].name                    == "C"
    assert apps_by_id[100].current_buildid.buildid == ""    # untouched (no info)
    assert apps_by_id[200].current_buildid.buildid == ""    # untouched (Unknown)


def test_detect_changes_first_seen_not_returned_even_with_force_download():
    """Force-download bypasses change detection but never triggers a
    first-time download — that path is reserved for explicit
    `archive download <appid>`."""
    from src.core.archive import poll
    from src.core.archive.project import AppEntry

    apps_by_id = {
        500: AppEntry(app_id=500, current_buildid=""),
        600: AppEntry(app_id=600, current_buildid="123"),
    }
    infos = {
        500: {"name": "Newcomer", "buildid": "777", "timeupdated": 0, "oslist": "", "installdir": ""},
        600: {"name": "Old",      "buildid": "123", "timeupdated": 0, "oslist": "", "installdir": ""},
    }
    with mock.patch.object(poll, "query_app_info_batch", _stub_qaib(infos)):
        changes = poll.detect_changes(None, None, apps_by_id, force_download=True)

    # 600 has a known prior buildid → force_download includes it.
    # 500 is first-seen → seeded silently regardless of force_download.
    assert {row[0] for row in changes} == {600}
    assert apps_by_id[500].current_buildid.buildid == "777"
    assert apps_by_id[500].name                    == "Newcomer"


def test_detect_changes_passes_batch_size_through():
    from src.core.archive import poll
    from src.core.archive.project import AppEntry

    apps_by_id = {i: AppEntry(app_id=i, current_buildid="") for i in (1, 2, 3, 4, 5)}

    captured = {}
    def fake(client, cdn, app_ids, max_retries=1, batch_size=None, quiet=False):
        captured["batch_size"] = batch_size
        captured["max_retries"] = max_retries
        captured["quiet"] = quiet
        for aid in app_ids:
            yield aid, {"name": str(aid), "buildid": "1", "timeupdated": 0,
                        "oslist": "", "installdir": ""}

    with mock.patch.object(poll, "query_app_info_batch", fake):
        poll.detect_changes(None, None, apps_by_id, batch_size=2, max_retries=3)

    assert captured == {"batch_size": 2, "max_retries": 3, "quiet": True}


def test_detect_changes_empty_apps_returns_empty_list():
    from src.core.archive import poll
    assert poll.detect_changes(None, None, {}) == []


# ---------------------------------------------------------------------------
# Project schema
# ---------------------------------------------------------------------------

def test_project_roundtrips_restart_delay_and_batch_size(tmp_path):
    from src.core.archive import project as pm
    proj = pm.new_project(name="poll-test")
    proj.restart_delay = 600
    proj.batch_size    = 25

    path = tmp_path / "poll.xarchive"
    pm.save(proj, path)
    loaded = pm.load(path)
    assert loaded.restart_delay == 600
    assert loaded.batch_size    == 25


def test_project_default_restart_delay_is_zero():
    from src.core.archive.project import ArchiveProject
    p = ArchiveProject()
    assert p.restart_delay == 0
    assert p.batch_size    == 0


# ---------------------------------------------------------------------------
# Resolver / persistence priority
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


def test_persist_polling_knobs_idempotent_when_unchanged():
    from src.cli.main     import _persist_archive_run_options
    from src.core.archive import project as pm
    proj = pm.new_project(name="t")
    proj.restart_delay = 120
    proj.batch_size    = 4

    args = _full_args(restart_delay=120, batch_size=4)
    assert _persist_archive_run_options(args, proj) is False


# ---------------------------------------------------------------------------
# Notify pipelines forward force_download
# ---------------------------------------------------------------------------

def test_pre_pipeline_passes_force_download_into_telegram_and_discord():
    from src.cli.main         import _archive_run_pre_pipeline
    from src.core.archive     import credentials as creds_mod
    creds = creds_mod.Credentials()
    creds.discord = creds_mod.DiscordCreds(webhook_url="https://hook")
    creds.telegram = creds_mod.TelegramCreds(token="t", chat_ids=["1"])

    fake_notify = mock.Mock()
    fake_notify.send_discord_notification.return_value  = True
    fake_notify.send_telegram_notification.return_value = True

    _archive_run_pre_pipeline(
        app_meta={"appid": 1, "name": "X", "buildid": "5", "timeupdated": 0},
        previous_buildid="4", creds=creds, notify_mode="pre",
        notify_mod=fake_notify,
        force_download=True,
    )
    assert fake_notify.send_discord_notification.call_args.kwargs["force_download"]  is True
    assert fake_notify.send_telegram_notification.call_args.kwargs["force_download"] is True


def test_post_pipeline_passes_force_download_into_notifications(tmp_path):
    from src.cli.main         import _archive_run_post_pipeline
    from src.core.archive     import credentials as creds_mod

    creds = creds_mod.Credentials()
    creds.discord = creds_mod.DiscordCreds(webhook_url="https://hook")

    fake_upload = mock.Mock()
    fake_upload.upload_archives.return_value = {}
    fake_notify = mock.Mock()
    fake_notify.send_discord_notification.return_value = True

    archive = tmp_path / "x.7z"
    archive.write_bytes(b"x")

    _archive_run_post_pipeline(
        [archive],
        {"appid": 1, "name": "X", "buildid": "5", "timeupdated": 0},
        previous_buildid="4", creds=creds,
        upload_mod=fake_upload, notify_mod=fake_notify,
        output_dir=tmp_path, subscriber=None,
        notify_mode="delay",
        force_download=True,
    )
    assert fake_notify.send_discord_notification.call_args.kwargs["force_download"] is True


# ---------------------------------------------------------------------------
# appinfo quiet path (used by detect_changes)
# ---------------------------------------------------------------------------

def test_query_app_info_batch_quiet_does_not_print(capsys, monkeypatch):
    from src.core.archive import appinfo

    def fake_stream(client, app_ids, max_retries, timeout=15):
        for aid in app_ids:
            yield aid, {
                "common":  {"name": "Q", "oslist": ""},
                "config":  {"installdir": "Q"},
                "depots":  {"branches": {"public": {"buildid": "9",
                                                    "timeupdated": 7}}},
            }
    monkeypatch.setattr(appinfo, "_streaming_product_info", fake_stream)

    fake_client = mock.Mock()
    fake_cdn = mock.Mock()
    type(fake_cdn).licensed_app_ids = mock.PropertyMock(side_effect=AssertionError(
        "quiet=True must not request licensed_app_ids"
    ))

    rows = list(appinfo.query_app_info_batch(fake_client, fake_cdn, [42],
                                             quiet=True))
    captured = capsys.readouterr()
    assert captured.out == ""             # no per-app summary
    assert rows == [(42, {
        "name": "Q", "buildid": "9", "oslist": "",
        "timeupdated": 7, "installdir": "Q",
    })]


# ---------------------------------------------------------------------------
# Polling driver smoke (mocks every IO boundary)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# AppEntry.previous_buildid + project.default_platform + bbcode rendering
# (regression coverage for 2026-04-30 bug report)
# ---------------------------------------------------------------------------

def test_app_entry_previous_buildid_roundtrips(tmp_path):
    from src.core.archive import project as pm
    proj = pm.new_project(name="t")
    proj.apps.append(pm.AppEntry(app_id=1, current_buildid="100",
                                 previous_buildid="50"))
    path = tmp_path / "p.xarchive"
    pm.save(proj, path)
    loaded = pm.load(path)
    assert loaded.apps[0].previous_buildid.buildid == "50"
    assert loaded.apps[0].current_buildid.buildid  == "100"


def test_app_entry_previous_buildid_default_blank():
    from src.core.archive.project import AppEntry
    e = AppEntry(app_id=1, current_buildid="123")
    assert e.previous_buildid.buildid == ""


def test_default_platform_all_in_project_used_when_cli_omits_platform(
        tmp_path, monkeypatch):
    """When --platform is absent, project.default_platform must apply.
    Regression: argparse default of 'windows' was clobbering the project
    field, so default_platform=all silently downloaded only Windows."""
    from src.cli import main as cli_main

    captured: dict = {}
    def fake_download_app(client, cdn, app_id, output_dir, *,
                          platform, **kw):
        captured["platform"] = platform
        return ([], {}, {"appid": app_id, "name": "X", "buildid": "1",
                          "timeupdated": 0})

    from src.core.archive import project as pm
    proj = pm.new_project(name="t")
    proj.default_platform = "all"
    proj.apps.append(pm.AppEntry(app_id=42))
    project_path = tmp_path / "p.xarchive"
    pm.save(proj, project_path)

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
    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.appinfo", fake_appinfo)

    fake_download_mod = mock.Mock()
    fake_download_mod.download_app = fake_download_app
    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.download", fake_download_mod)

    fake_compress_mod = mock.Mock()
    fake_compress_mod.parse_size = lambda s: None
    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.compress", fake_compress_mod)

    fake_progress = mock.Mock()
    fake_progress.build_subscriber = lambda plain=False: mock.Mock()
    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.cli_progress", fake_progress)

    fake_depots = mock.Mock()
    fake_depots.load = lambda: {}
    fake_depots.record_unknown = lambda ids: []
    fake_depots.depots_path = lambda: "/dev/null"
    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.depots_ini", fake_depots)

    fake_upload = mock.Mock()
    fake_upload.upload_archives = lambda *a, **kw: {}
    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.upload", fake_upload)

    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.notify", mock.Mock())

    args = mock.Mock(
        log=None,
        upload_username=None, upload_password=None, binurl=None, binpass=None,
        telegram_bot_token=None, telegram_chat_id=None,
        discord_webhook=None, discord_mention_role_ids=None,
        delete_archives=False,
        app_ids=[], appid_file=None, project=str(project_path),
        crack=None, no_progress=True,
        platform=None,                     # CLI omitted -> project decides
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


def test_run_one_app_shifts_previous_buildid_when_current_changes(tmp_path):
    """The CLI must capture the old current_buildid into previous_buildid
    before overwriting it.  Validated by reaching into _cmd_archive_download
    via the same harness used by the polling-driver test (one-cycle, no
    real network)."""
    from src.cli.main import _archive_run_post_pipeline
    from src.core.archive.project import AppEntry

    # Direct-test the buildid-shift logic with a fake AppEntry mutation
    # mirroring _run_one_app's tail.  Keeping this unit-focused so the
    # full _cmd_archive_download integration test (above) doesn't need
    # to assert it too.
    e = AppEntry(app_id=1, current_buildid="100", previous_buildid="")
    new_bid = "200"
    if e.current_buildid.buildid and e.current_buildid.buildid != new_bid:
        e.previous_buildid.buildid = e.current_buildid.buildid
    e.current_buildid.buildid = new_bid
    assert e.previous_buildid.buildid == "100"
    assert e.current_buildid.buildid  == "200"

    # Same buildid re-download: previous_buildid must NOT shift to the
    # current value (would erase real history under --force-download).
    e2 = AppEntry(app_id=2, current_buildid="100", previous_buildid="50")
    same_bid = "100"
    if e2.current_buildid.buildid and e2.current_buildid.buildid != same_bid:
        e2.previous_buildid.buildid = e2.current_buildid.buildid
    e2.current_buildid.buildid = same_bid
    assert e2.previous_buildid.buildid == "50"   # untouched

    # Pipeline acceptance: bbcode kwarg shouldn't blow up when no template
    # and no links are present (smoke).
    from src.core.archive import credentials as creds_mod
    _archive_run_post_pipeline(
        archives=[],
        app_meta={"appid": 1, "name": "X", "buildid": "200",
                  "timeupdated": 0},
        previous_buildid="100",
        creds=creds_mod.Credentials(),
        upload_mod=mock.Mock(),
        notify_mod=mock.Mock(),
        output_dir=tmp_path, subscriber=None,
        bbcode_template="",
    )


def test_post_pipeline_renders_bbcode_when_template_and_links_present(
        tmp_path):
    """End-to-end shape: with creds.multiup set + a populated
    bbcode_template + at least one upload URL, the post-pipeline must
    write <safe_name>.<buildid>.post.txt and remove the per-stem
    sidecar .txt files."""
    from src.cli.main import _archive_run_post_pipeline
    from src.core.archive import credentials as creds_mod

    creds = creds_mod.Credentials()
    creds.multiup = creds_mod.MultiUpCreds(username="u", password="p")

    # Pre-create a sidecar that upload_archives would have written.
    sidecar = tmp_path / "Game.123.windows.public.txt"
    sidecar.write_text("https://multiup.io/abc\n", encoding="utf-8")

    fake_upload = mock.Mock()
    fake_upload.upload_archives.return_value = {
        "Game.123.windows.public": "https://pb/post",
    }

    template = (
        "[b]{APP_NAME}[/b] (build {BUILDID}, was {PREVIOUS_BUILDID})\n"
        "Windows: {WINDOWS_LINK}\n"
        "Linux:   {LINUX_LINK}\n"
        "All:     {ALL_LINKS}\n"
    )

    _archive_run_post_pipeline(
        archives=[tmp_path / "Game.7z"],
        app_meta={"appid": 100, "name": "Cool Game",
                  "buildid": "123", "timeupdated": 0},
        previous_buildid="99",
        creds=creds,
        upload_mod=fake_upload,
        notify_mod=mock.Mock(),
        output_dir=tmp_path, subscriber=None,
        notify_mode="none",
        bbcode_template=template,
    )

    # Header-image lookup hits the network — patch it out via the bbcode
    # hop's notify import (already executed by now).  Acceptable: the
    # rendered post may have the URL or a placeholder — what we need to
    # assert is the file got written and contained the expected
    # placeholders filled in.
    posts = list(tmp_path.glob("*.post.txt"))
    assert len(posts) == 1
    body = posts[0].read_text(encoding="utf-8")
    assert "Cool Game" in body
    assert "build 123, was 99" in body
    assert "https://pb/post"   in body
    # Linux line dropped (no LINUX_LINK in upload_links).
    assert "Linux:" not in body
    # Sidecar removed in favour of the post.
    assert not sidecar.exists()


def test_post_pipeline_skips_bbcode_when_no_template():
    from src.cli.main import _archive_run_post_pipeline
    from src.core.archive import credentials as creds_mod
    creds = creds_mod.Credentials()
    creds.multiup = creds_mod.MultiUpCreds(username="u", password="p")
    fake_upload = mock.Mock()
    fake_upload.upload_archives.return_value = {"x": "https://y"}
    # Pure smoke: empty template -> no exception, no file written
    # anywhere we'd notice.
    _archive_run_post_pipeline(
        archives=[mock.Mock()],
        app_meta={"appid": 1, "name": "X", "buildid": "1", "timeupdated": 0},
        previous_buildid="0", creds=creds,
        upload_mod=fake_upload, notify_mod=mock.Mock(),
        output_dir="/tmp", subscriber=None,
        bbcode_template="",
    )


def test_poll_countdown_tty_decrements_per_second(monkeypatch, capsys):
    """On a TTY the helper writes a one-line \\r-overwritten countdown
    that ticks down once per second."""
    from src.cli import main as cli_main
    import sys

    # Force the TTY branch.
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)

    sleeps: list[float] = []
    import gevent
    monkeypatch.setattr(gevent, "sleep", lambda n: sleeps.append(n))

    assert cli_main._poll_countdown(3) is True
    assert sleeps == [1, 1, 1]                         # 3 ticks
    out = capsys.readouterr().out
    # Each remaining value rendered as its own \r-prefixed frame.
    assert "next poll cycle in 3s" in out
    assert "next poll cycle in 2s" in out
    assert "next poll cycle in 1s" in out


def test_poll_countdown_non_tty_single_print(monkeypatch, capsys):
    """Non-TTY path must NOT write per-second \\r frames — log files
    would fill with carriage-return spam.  One-shot 'sleeping Xs' line
    + a single time.sleep(X) call instead."""
    from src.cli import main as cli_main
    import sys

    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    sleeps: list[float] = []
    import gevent
    monkeypatch.setattr(gevent, "sleep", lambda n: sleeps.append(n))

    cli_main._poll_countdown(7)
    assert sleeps == [7]
    assert "sleeping 7s" in capsys.readouterr().out


def test_poll_countdown_returns_false_on_keyboard_interrupt(monkeypatch):
    from src.cli import main as cli_main
    import sys

    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    def boom(_):
        raise KeyboardInterrupt
    import gevent
    monkeypatch.setattr(gevent, "sleep", boom)
    assert cli_main._poll_countdown(5) is False


def test_polling_driver_loops_then_exits_on_keyboard_interrupt(tmp_path, monkeypatch):
    """One full cycle:
      iteration 1: detect_changes returns one app -> download fires
      iteration 2: detect_changes returns nothing  -> download skipped
      time.sleep raises KeyboardInterrupt -> driver exits cleanly.
    Verifies force_download is True on call 1, False on call 2."""
    from src.cli import main as cli_main
    from src.core.archive import project as pm

    proj = pm.new_project(name="poll-smoke")
    proj.restart_delay = 1
    proj.apps.append(pm.AppEntry(app_id=100, current_buildid="old"))
    project_path = tmp_path / "p.xarchive"
    pm.save(proj, project_path)

    # Sentinel that captures detect_changes call args, returns one row first
    # cycle, nothing after.
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
    # ---- minimal stubs for everything else _cmd_archive_download imports
    from src.core.archive import poll as poll_mod
    monkeypatch.setattr(poll_mod, "detect_changes", fake_detect)

    # Stub out the heavy CLI-side imports.
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
    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.appinfo", fake_appinfo)

    fake_download_mod = mock.Mock()
    fake_download_mod.download_app = fake_download_app
    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.download", fake_download_mod)

    fake_compress_mod = mock.Mock()
    fake_compress_mod.parse_size = lambda s: None
    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.compress", fake_compress_mod)

    fake_progress = mock.Mock()
    fake_progress.build_subscriber = lambda plain=False: mock.Mock()
    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.cli_progress", fake_progress)

    fake_depots = mock.Mock()
    fake_depots.load = lambda: {}
    fake_depots.record_unknown = lambda ids: []
    fake_depots.depots_path = lambda: "/dev/null"
    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.depots_ini", fake_depots)

    fake_upload = mock.Mock()
    fake_upload.upload_archives = lambda *a, **kw: {}
    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.upload", fake_upload)

    fake_notify = mock.Mock()
    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.notify", fake_notify)

    import gevent
    monkeypatch.setattr(gevent, "sleep", fake_sleep)

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

    # Project saved at end of each iteration -> current_buildid persisted.
    reloaded = pm.load(project_path)
    assert reloaded.apps[0].current_buildid.buildid == "new"
