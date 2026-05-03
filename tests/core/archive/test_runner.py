"""archive.runner — top-level run driver shared between CLI and GUI.

Covers:
  * resolve_notify_mode picker (creds + flag + project field priority)
  * run_pre_notify / run_post_pipeline gating + forwarding
  * run_one_app per-app shift of current/previous buildid
  * SessionDead recovery loop in run_session (single-pass + poll modes)
  * single-pass + poll-mode driver behaviour against mocked download_app

The runner module is the integration point most likely to develop
silent regressions; every behaviour change to upload, notify, poll,
or relogin plumbing should land a test here.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

from src.core.archive import project as project_mod
from src.core.archive import runner as runner_mod
from src.core.archive.download import DownloadEvent


# ---------------------------------------------------------------------------
# resolve_notify_mode picker
# ---------------------------------------------------------------------------

def test_resolve_notify_mode_priority():
    """CLI flag wins over project field; project field wins over auto-default;
    auto-default falls back to 'delay' iff multiup creds are set."""
    from src.core.archive.runner import resolve_notify_mode
    from src.core.archive import credentials as creds_mod

    creds = creds_mod.Credentials()
    creds.discord.webhook_url = "https://h"   # at least one notify target

    # No notify creds → "none" regardless of flag/field.
    no_notify = creds_mod.Credentials()
    no_notify.multiup.username = "u"
    assert resolve_notify_mode("pre", "delay", no_notify) == "none"

    # CLI flag overrides everything.
    assert resolve_notify_mode("pre",   "delay", creds) == "pre"
    assert resolve_notify_mode("both",  "delay", creds) == "both"

    # Project field used when CLI flag absent.
    assert resolve_notify_mode(None, "pre",   creds) == "pre"
    assert resolve_notify_mode(None, "delay", creds) == "delay"
    assert resolve_notify_mode(None, "both",  creds) == "both"

    # Auto-default: 'delay' when uploads can produce links, else 'pre'.
    creds.multiup.username = "u"
    assert resolve_notify_mode(None, "", creds) == "delay"
    creds.multiup.username = ""
    assert resolve_notify_mode(None, "", creds) == "pre"

    # Invalid project field is ignored, falls through to auto.
    assert resolve_notify_mode(None, "garbage", creds) == "pre"


# ---------------------------------------------------------------------------
# run_pre_notify gating
# ---------------------------------------------------------------------------

def test_pre_notify_fires_only_in_pre_or_both(tmp_path):
    """run_pre_notify is a no-op for notify_mode in {none, delay}.  In
    {pre, both} it fires once per call, with upload_links=None (the
    pre-download notify can't possibly know the eventual links yet)."""
    from src.core.archive.runner import run_pre_notify
    from src.core.archive import credentials as creds_mod

    notify_mod = MagicMock()
    creds = creds_mod.Credentials()
    creds.discord.webhook_url = "https://h"

    base = dict(
        app_meta={"appid": 9, "name": "G", "buildid": "", "timeupdated": 0},
        previous_buildid="100",
        creds=creds, notify_mod=notify_mod,
    )
    run_pre_notify(notify_mode="delay", **base)
    notify_mod.send_discord_notification.assert_not_called()
    run_pre_notify(notify_mode="pre", **base)
    run_pre_notify(notify_mode="both", **base)
    assert notify_mod.send_discord_notification.call_count == 2

    # Pre-notify must NOT include upload links.
    for call in notify_mod.send_discord_notification.call_args_list:
        assert call.kwargs["upload_links"] is None


def test_pre_pipeline_passes_force_download_into_telegram_and_discord():
    from src.core.archive import credentials as creds_mod
    from src.core.archive.runner import run_pre_notify
    creds = creds_mod.Credentials()
    creds.discord  = creds_mod.DiscordCreds(webhook_url="https://hook")
    creds.telegram = creds_mod.TelegramCreds(token="t", chat_ids=["1"])

    fake_notify = mock.Mock()
    fake_notify.send_discord_notification.return_value  = True
    fake_notify.send_telegram_notification.return_value = True

    run_pre_notify(
        app_meta={"appid": 1, "name": "X", "buildid": "5", "timeupdated": 0},
        previous_buildid="4", creds=creds, notify_mode="pre",
        notify_mod=fake_notify, force_download=True,
    )
    assert fake_notify.send_discord_notification.call_args.kwargs["force_download"]  is True
    assert fake_notify.send_telegram_notification.call_args.kwargs["force_download"] is True


# ---------------------------------------------------------------------------
# run_post_pipeline — upload + bbcode + notify
# ---------------------------------------------------------------------------

def test_post_pipeline_skips_when_no_creds_set(tmp_path):
    """No upload creds, no notify creds → upload_archives must not be
    called and the helper must complete without raising."""
    from src.core.archive.runner import run_post_pipeline
    from src.core.archive import credentials as creds_mod

    upload_mod = MagicMock()
    notify_mod = MagicMock()
    creds = creds_mod.Credentials()  # all blocks empty

    run_post_pipeline(
        archives=[tmp_path / "x.7z"],
        app_meta={"appid": 1, "name": "G", "buildid": "2", "timeupdated": 0},
        previous_buildid="1",
        creds=creds,
        upload_mod=upload_mod, notify_mod=notify_mod,
        output_dir=tmp_path, subscriber=lambda ev: None,
    )
    upload_mod.upload_archives.assert_not_called()
    notify_mod.send_discord_notification.assert_not_called()
    notify_mod.send_telegram_notification.assert_not_called()


def test_post_pipeline_skips_post_notify_when_mode_pre(tmp_path):
    """notify_mode='pre' means upload still runs (so links land in the
    paste/links file) but the *post* notify is suppressed — the
    pre-notify already fired, sending a second one would duplicate."""
    from src.core.archive.runner import run_post_pipeline
    from src.core.archive import credentials as creds_mod

    upload_mod = MagicMock()
    upload_mod.upload_archives.return_value = {"Game.1.windows.public": "https://u"}
    notify_mod = MagicMock()

    creds = creds_mod.Credentials()
    creds.multiup.username    = "alice"
    creds.discord.webhook_url = "https://hook"

    run_post_pipeline(
        archives=[tmp_path / "Game.1.windows.public.7z"],
        app_meta={"appid": 9, "name": "G", "buildid": "200", "timeupdated": 0},
        previous_buildid="100",
        creds=creds,
        upload_mod=upload_mod, notify_mod=notify_mod,
        output_dir=tmp_path, subscriber=lambda ev: None,
        notify_mode="pre",
    )
    upload_mod.upload_archives.assert_called_once()      # still runs
    notify_mod.send_discord_notification.assert_not_called()  # post suppressed


def test_post_pipeline_routes_upload_links_to_notify(tmp_path):
    """When upload + discord creds are set, the platform_links dict
    must be derived from the upload result and forwarded to send_discord."""
    from src.core.archive.runner import run_post_pipeline
    from src.core.archive import credentials as creds_mod

    upload_mod = MagicMock()
    upload_mod.upload_archives.return_value = {
        "Game.1.windows.public": "https://pb/win",
        "Game.1.linux.public":   "https://pb/lin",
    }
    notify_mod = MagicMock()

    creds = creds_mod.Credentials()
    creds.multiup.username    = "alice"
    creds.discord.webhook_url = "https://hook"

    archive_paths = [tmp_path / "Game.1.windows.public.7z",
                     tmp_path / "Game.1.linux.public.7z"]
    run_post_pipeline(
        archives=archive_paths,
        app_meta={"appid": 9, "name": "Game", "buildid": "200", "timeupdated": 0},
        previous_buildid="100",
        creds=creds,
        upload_mod=upload_mod, notify_mod=notify_mod,
        output_dir=tmp_path, subscriber=lambda ev: None,
    )
    upload_mod.upload_archives.assert_called_once()
    notify_mod.send_discord_notification.assert_called_once()
    _args, kwargs = notify_mod.send_discord_notification.call_args
    assert kwargs["upload_links"] == {
        "windows": "https://pb/win",
        "linux":   "https://pb/lin",
    }
    notify_mod.send_telegram_notification.assert_not_called()


def test_post_pipeline_forwards_upload_knobs(tmp_path):
    """description / max_concurrent / delete_archives must reach
    upload_archives() — they're the user-visible knobs that
    distinguish a casual run from a tuned production drop."""
    from src.core.archive.runner import run_post_pipeline
    from src.core.archive import credentials as creds_mod

    upload_mod = MagicMock()
    upload_mod.upload_archives.return_value = {}
    notify_mod = MagicMock()
    creds = creds_mod.Credentials()
    creds.multiup.username = "u"

    run_post_pipeline(
        archives=[tmp_path / "x.7z"],
        app_meta={"appid": 1, "name": "G", "buildid": "1", "timeupdated": 0},
        previous_buildid="0",
        creds=creds,
        upload_mod=upload_mod, notify_mod=notify_mod,
        output_dir=tmp_path, subscriber=lambda ev: None,
        notify_mode="delay",
        description="custom description",
        max_concurrent=4,
        delete_archives=True,
    )
    _args, kwargs = upload_mod.upload_archives.call_args
    assert kwargs["description"]     == "custom description"
    assert kwargs["max_concurrent"]  == 4
    assert kwargs["delete_archives"] is True


def test_post_pipeline_renders_bbcode_when_template_and_links_present(tmp_path):
    """End-to-end shape: with creds.multiup set + a populated
    bbcode_template + at least one upload URL, the post-pipeline must
    write <safe_name>.<buildid>.post.txt and remove the per-stem
    sidecar .txt files."""
    from src.core.archive.runner import run_post_pipeline
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

    run_post_pipeline(
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

    posts = list(tmp_path.glob("*.post.txt"))
    assert len(posts) == 1
    body = posts[0].read_text(encoding="utf-8")
    assert "Cool Game"        in body
    assert "build 123, was 99" in body
    assert "https://pb/post"   in body
    # Linux line dropped (no LINUX_LINK in upload_links).
    assert "Linux:" not in body
    # Sidecar removed in favour of the post.
    assert not sidecar.exists()


def test_post_pipeline_skips_bbcode_when_no_template():
    """Empty template = no .post.txt — pure smoke that the path bails
    cleanly without trying to render."""
    from src.core.archive.runner import run_post_pipeline
    from src.core.archive import credentials as creds_mod
    creds = creds_mod.Credentials()
    creds.multiup = creds_mod.MultiUpCreds(username="u", password="p")
    fake_upload = mock.Mock()
    fake_upload.upload_archives.return_value = {"x": "https://y"}
    run_post_pipeline(
        archives=[mock.Mock()],
        app_meta={"appid": 1, "name": "X", "buildid": "1", "timeupdated": 0},
        previous_buildid="0", creds=creds,
        upload_mod=fake_upload, notify_mod=mock.Mock(),
        output_dir="/tmp", subscriber=None,
        bbcode_template="",
    )


def test_post_pipeline_passes_force_download_into_notifications(tmp_path):
    from src.core.archive import credentials as creds_mod
    from src.core.archive.runner import run_post_pipeline

    creds = creds_mod.Credentials()
    creds.discord = creds_mod.DiscordCreds(webhook_url="https://hook")

    fake_upload = mock.Mock()
    fake_upload.upload_archives.return_value = {}
    fake_notify = mock.Mock()
    fake_notify.send_discord_notification.return_value = True

    archive = tmp_path / "x.7z"
    archive.write_bytes(b"x")

    run_post_pipeline(
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
# run_one_app — buildid shift on change
# ---------------------------------------------------------------------------

def test_run_one_app_shifts_previous_buildid_when_current_changes(tmp_path,
                                                                    archive_project_factory,
                                                                    stub_creds, archive_run_opts):
    """run_one_app must move the existing current_buildid to
    previous_buildid before assigning the new one."""
    from src.core.archive import runner as runner_mod
    from src.core.archive import project as pm

    proj = archive_project_factory(app_id=730, current="100")
    creds = stub_creds()

    fake_archives = [tmp_path / "game.7z"]
    download_app = MagicMock(return_value=(
        fake_archives,
        {"windows": []},
        {"appid": 730, "name": "Foo", "buildid": "200", "timeupdated": 0},
    ))

    with patch("src.core.archive.download.download_app", download_app):
        runner_mod.run_session(
            client=MagicMock(), cdn=MagicMock(),
            project_obj=proj, project_path=tmp_path / "p.xarchive",
            creds=creds, output_dir=tmp_path,
            app_ids=[730], opts=archive_run_opts(),
            platform="windows", notify_mode="none",
            branch="public", crack=False,
            crack_identity=None, unstub_options=None,
            volume_size=None, depot_names={},
            subscriber=None,
            upload_mod=MagicMock(), notify_mod=MagicMock(),
            log=lambda m: None, warn=lambda m: None,
        )
    assert proj.apps[0].current_buildid.buildid  == "200"
    assert proj.apps[0].previous_buildid.buildid == "100"


# ---------------------------------------------------------------------------
# SessionDead recovery — single-pass branch
# ---------------------------------------------------------------------------

def test_runner_recovers_from_session_dead_in_single_pass(tmp_path,
                                                          archive_project_factory,
                                                          stub_creds, archive_run_opts):
    """A SessionDead raised by run_one_app should disconnect the dead
    client, call relogin(), and retry the same app on the new client."""
    from src.core.archive import runner as runner_mod
    from src.core.archive.errors import SessionDead

    proj = archive_project_factory(app_id=42, current="1")
    creds = stub_creds()

    call_count = {"n": 0}
    def flaky_dl(client, cdn, app_id, *a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise SessionDead("CM timed out")
        return ([tmp_path / "ok.7z"],
                {"windows": []},
                {"appid": app_id, "name": "x", "buildid": "2"})

    new_client, new_cdn = MagicMock(), MagicMock()
    relogin_calls = {"n": 0}
    def relogin():
        relogin_calls["n"] += 1
        return new_client, new_cdn

    initial_client = MagicMock()

    with patch("src.core.archive.download.download_app", side_effect=flaky_dl):
        runner_mod.run_session(
            client=initial_client, cdn=MagicMock(),
            project_obj=proj, project_path=None,
            creds=creds, output_dir=tmp_path,
            app_ids=[42], opts=archive_run_opts(),
            platform="windows", notify_mode="none",
            branch="public", crack=False,
            crack_identity=None, unstub_options=None,
            volume_size=None, depot_names={},
            subscriber=None,
            upload_mod=MagicMock(), notify_mod=MagicMock(),
            relogin=relogin,
            log=lambda m: None, warn=lambda m: None,
        )

    initial_client.disconnect.assert_called_once()
    assert relogin_calls["n"] == 1
    assert call_count["n"]    == 2  # first failed, retry succeeded


def test_runner_single_pass_collects_archives(tmp_path, archive_project_factory,
                                                stub_creds, archive_run_opts):
    """Single-pass mode returns the archives produced by download_app
    and shifts the AppEntry's buildids: previous := old current,
    current := new buildid."""
    proj = archive_project_factory(app_id=730, current="100")
    creds = stub_creds()
    fake_archives = [tmp_path / "game.7z"]
    download_app = MagicMock(return_value=(
        fake_archives,
        {"windows": [(1234, "main", "g1")]},
        {"appid": 730, "name": "Foo", "buildid": "200", "timeupdated": 0},
    ))

    with patch("src.core.archive.download.download_app", download_app):
        result = runner_mod.run_session(
            client=MagicMock(), cdn=MagicMock(),
            project_obj=proj, project_path=tmp_path / "p.xarchive",
            creds=creds, output_dir=tmp_path,
            app_ids=[730], opts=archive_run_opts(),
            platform="windows", notify_mode="none",
            branch="public", crack=False,
            crack_identity=None, unstub_options=None,
            volume_size=None, depot_names={},
            subscriber=None,
            upload_mod=MagicMock(), notify_mod=MagicMock(),
            log=lambda m: None, warn=lambda m: None,
        )

    assert result.archives == fake_archives
    assert proj.apps[0].current_buildid.buildid  == "200"
    assert proj.apps[0].previous_buildid.buildid == "100"


def test_runner_single_pass_continues_after_app_failure(tmp_path,
                                                          stub_creds,
                                                          archive_run_opts):
    """A download_app crash for one app must not abort the whole run."""
    proj = project_mod.new_project()
    proj.apps.extend([
        project_mod.AppEntry(app_id=1, branch="public"),
        project_mod.AppEntry(app_id=2, branch="public"),
    ])
    creds = stub_creds()
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
            app_ids=[1, 2], opts=archive_run_opts(),
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


def test_runner_single_pass_emits_app_info_progress(tmp_path,
                                                      archive_project_factory,
                                                      stub_creds,
                                                      archive_run_opts):
    """Single-pass mode synthesises app_info_progress events so the
    GUI / cli display can show 'X / N apps processed' without a poll
    cycle."""
    proj = archive_project_factory()
    creds = stub_creds()

    events: list = []
    def subscriber(ev):
        events.append((ev.kind, ev.done, ev.total, ev.name))

    fake_archives = [tmp_path / "a.7z"]
    download_app = MagicMock(return_value=(
        fake_archives,
        {"windows": []},
        {"appid": 730, "name": "Foo", "buildid": "200"},
    ))

    with patch("src.core.archive.download.download_app", download_app):
        runner_mod.run_session(
            client=MagicMock(), cdn=MagicMock(),
            project_obj=proj, project_path=None,
            creds=creds, output_dir=tmp_path,
            app_ids=[730, 999], opts=archive_run_opts(),
            platform="windows", notify_mode="none",
            branch="public", crack=False,
            crack_identity=None, unstub_options=None,
            volume_size=None, depot_names={},
            subscriber=subscriber,
            upload_mod=MagicMock(), notify_mod=MagicMock(),
            log=lambda m: None, warn=lambda m: None,
        )

    appinfo_events = [e for e in events if e[0] == "app_info_progress"]
    assert [(d, t) for _, d, t, _ in appinfo_events] == [(1, 2), (2, 2)]


def test_runner_aborts_single_pass_between_apps(tmp_path, stub_creds,
                                                  archive_run_opts):
    """abort callable returning True between apps must short-circuit
    the single-pass loop."""
    proj = project_mod.new_project()
    proj.apps.append(project_mod.AppEntry(app_id=1))
    proj.apps.append(project_mod.AppEntry(app_id=2))
    proj.apps.append(project_mod.AppEntry(app_id=3))
    creds = stub_creds()

    seen = []
    def fake_dl(client, cdn, app_id, *a, **kw):
        seen.append(app_id)
        return ([tmp_path / f"{app_id}.7z"],
                {"windows": []},
                {"appid": app_id, "name": str(app_id), "buildid": "10"})

    aborted = {"flag": False}
    def abort_after_one():
        if seen:  # abort after first app processed
            aborted["flag"] = True
        return aborted["flag"]

    with patch("src.core.archive.download.download_app", side_effect=fake_dl):
        runner_mod.run_session(
            client=MagicMock(), cdn=MagicMock(),
            project_obj=proj, project_path=None,
            creds=creds, output_dir=tmp_path,
            app_ids=[1, 2, 3], opts=archive_run_opts(),
            platform="windows", notify_mode="none",
            branch="public", crack=False,
            crack_identity=None, unstub_options=None,
            volume_size=None, depot_names={},
            subscriber=None,
            upload_mod=MagicMock(), notify_mod=MagicMock(),
            log=lambda m: None, warn=lambda m: None,
            abort=abort_after_one,
        )

    # First app ran; abort fired before app 2.
    assert seen == [1]


# ---------------------------------------------------------------------------
# Poll-mode driver
# ---------------------------------------------------------------------------

def test_runner_poll_mode_only_runs_changed_apps(tmp_path,
                                                   archive_project_factory,
                                                   stub_creds, archive_run_opts):
    proj = archive_project_factory(current="100")
    creds = stub_creds()

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
            app_ids=[], opts=archive_run_opts(restart_delay=1),
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
    assert proj.apps[0].current_buildid.buildid  == "200"
    assert proj.apps[0].previous_buildid.buildid == "100"
    assert len(result.archives) == 1


def test_runner_poll_aborts_via_countdown_returning_false(tmp_path,
                                                            archive_project_factory,
                                                            stub_creds,
                                                            archive_run_opts):
    proj = archive_project_factory()

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
            creds=stub_creds(), output_dir=tmp_path,
            app_ids=[], opts=archive_run_opts(restart_delay=5),
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


def test_runner_session_dead_no_relogin_aborts(tmp_path,
                                               archive_project_factory,
                                               stub_creds, archive_run_opts):
    """Without a relogin callback the runner must stop the loop instead
    of spinning forever."""
    from src.core.archive import runner as runner_mod
    from src.core.archive.errors import SessionDead

    proj = archive_project_factory(app_id=42, current="1")
    creds = stub_creds()

    def always_dead(*a, **kw):
        raise SessionDead("cm dead")

    with patch("src.core.archive.download.download_app", side_effect=always_dead):
        runner_mod.run_session(
            client=MagicMock(), cdn=MagicMock(),
            project_obj=proj, project_path=None,
            creds=creds, output_dir=tmp_path,
            app_ids=[42], opts=archive_run_opts(),
            platform="windows", notify_mode="none",
            branch="public", crack=False,
            crack_identity=None, unstub_options=None,
            volume_size=None, depot_names={},
            subscriber=None,
            upload_mod=MagicMock(), notify_mod=MagicMock(),
            relogin=None,
            log=lambda m: None, warn=lambda m: None,
        )
    # Test passes if we returned without hanging.
