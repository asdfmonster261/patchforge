"""Phase 4 archive-mode tests: bbcode renderer, credentials schema for
upload/notify destinations, upload helpers, notify message shaping, and
the CLI post-pipeline wiring.

External services (MultiUp, PrivateBin, Telegram, Discord) are mocked at
the requests / SDK boundary so these tests don't hit the network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# bbcode
# ---------------------------------------------------------------------------

def test_bbcode_render_full_links():
    from src.core.archive import bbcode
    template = (
        "[size=150]{APP_NAME}[/size]\n"
        "Build: {BUILDID} (was {PREVIOUS_BUILDID})\n"
        "Released: {DATETIME}\n"
        "Platforms: {PLATFORMS}\n"
        "Links: {ALL_LINKS}\n"
        "Linux only: {LINUX_LINK}\n"
        "{MANIFESTS}\n"
    )
    data = bbcode.build_data(
        name="My Game",
        appid=12345,
        buildid="9999",
        previous_buildid="9998",
        timeupdated=1_700_000_000,
        upload_links={"windows": "https://w", "linux": "https://l"},
        manifests={"windows": [(100, "Game", "abc123")]},
    )
    out = bbcode.render(template, data)
    assert "My Game" in out
    assert "Build: 9999 (was 9998)" in out
    assert "Windows" in out and "Linux" in out
    assert "https://l" in out
    assert "Depots & Manifests - Windows" in out
    assert "100 - Game [Manifest abc123]" in out


def test_bbcode_render_drops_empty_optional_lines():
    from src.core.archive import bbcode
    template = (
        "Name: {APP_NAME}\n"
        "Linux: {LINUX_LINK}\n"
        "Windows: {WINDOWS_LINK}\n"
        "[b]Header[/b]: {HEADER_IMAGE}\n"
    )
    data = bbcode.build_data(
        name="X", appid=1, buildid="1", previous_buildid="0",
        timeupdated=0,
        upload_links={"windows": "https://w"},  # no linux
    )
    out = bbcode.render(template, data)
    assert "Linux:" not in out          # dropped (no linux link)
    assert "Windows: https://w" in out
    assert "Header" not in out          # dropped (no header image)


def test_bbcode_wrapped_all_links_repeats_tags_per_link():
    from src.core.archive import bbcode
    data = bbcode.build_data(
        name="X", appid=1, buildid="1", previous_buildid="0",
        timeupdated=0,
        upload_links={"linux": "https://l", "windows": "https://w"},
    )
    out = bbcode.render("Links: [b]{ALL_LINKS}[/b]\n", data)
    # Tags must wrap each label individually inside the [url=...] tag.
    assert "[url=https://l][b]Linux[/b][/url]" in out
    assert "[url=https://w][b]Windows[/b][/url]" in out


def test_bbcode_safe_name_strips_disallowed_chars():
    from src.core.archive import bbcode
    assert bbcode.safe_name("My Game!  v2") == "My.Game.v2"
    assert bbcode.safe_name("a/b\\c.txt")    == "abc.txt"


def test_bbcode_default_template_loads():
    from src.core.archive import bbcode
    body = bbcode.load_default_template()
    assert "{APP_NAME}" in body and "{BUILDID}" in body


# ---------------------------------------------------------------------------
# Credentials — nested upload/notify blocks roundtrip through JSON.
# ---------------------------------------------------------------------------

def test_credentials_nested_blocks_roundtrip(tmp_path, monkeypatch):
    from src.core.archive import credentials as creds_mod
    monkeypatch.setattr(creds_mod, "_CREDENTIALS_FILE", tmp_path / "c.json")

    creds = creds_mod.load()
    assert creds.multiup.is_set()    is False
    assert creds.privatebin.is_set() is False
    assert creds.telegram.is_set()   is False
    assert creds.discord.is_set()    is False

    creds.multiup.username    = "alice"
    creds.multiup.password    = "p"
    creds.privatebin.url      = "https://pb"
    creds.telegram.token      = "tt"
    creds.telegram.chat_ids   = ["c1", "c2"]
    creds.discord.webhook_url = "https://hook"
    creds.discord.mention_role_ids = ["r1"]
    creds_mod.save(creds)

    on_disk = json.loads((tmp_path / "c.json").read_text())
    assert on_disk["multiup"]    == {"username": "alice", "password": "p"}
    assert on_disk["telegram"]["chat_ids"] == ["c1", "c2"]

    re = creds_mod.load()
    assert re.multiup.is_set()    is True
    assert re.privatebin.is_set() is True
    assert re.telegram.is_set()   is True
    assert re.discord.is_set()    is True
    assert re.discord.mention_role_ids == ["r1"]


def test_credentials_load_drops_unknown_nested_keys(tmp_path, monkeypatch):
    """Older config files carrying renamed/removed keys must not crash load()."""
    from src.core.archive import credentials as creds_mod
    monkeypatch.setattr(creds_mod, "_CREDENTIALS_FILE", tmp_path / "c.json")
    (tmp_path / "c.json").write_text(json.dumps({
        "multiup": {"username": "u", "password": "p", "OLD_FIELD": "x"},
        "discord": "not a dict",
    }))
    creds = creds_mod.load()
    assert creds.multiup.username == "u"
    # Non-dict nested entry just falls back to default-empty.
    assert creds.discord.webhook_url == ""


# ---------------------------------------------------------------------------
# Upload helpers (pure logic — no network)
# ---------------------------------------------------------------------------

def test_upload_archive_stem_strips_volume_and_archive_extensions():
    from src.core.archive.upload import _archive_stem
    assert _archive_stem("Game.123.windows.public.7z.001") == "Game.123.windows.public"
    assert _archive_stem("Game.123.windows.public.7z")     == "Game.123.windows.public"
    assert _archive_stem("foo.tar.zst")                    == "foo"
    # Non-archive extensions must be left alone.
    assert _archive_stem("readme.txt") == "readme.txt"


def test_upload_shorten_url_collapses_download_path():
    from src.core.archive.upload import _shorten_url
    assert _shorten_url("https://multiup.io/download/abc/foo.7z") == "https://multiup.io/abc"
    # Already-short URLs are returned unchanged.
    assert _shorten_url("https://multiup.io/abc") == "https://multiup.io/abc"


def test_upload_archives_groups_by_stem(monkeypatch, tmp_path):
    """Multi-part archives sharing a stem must collapse into one MultiUp
    project and one PrivateBin paste."""
    from src.core.archive import upload as up

    # Stub the HTTP boundary.
    monkeypatch.setattr(up, "_login",            lambda u, p: "uid42")
    monkeypatch.setattr(up, "_get_hosts",        lambda u, p: ["uploaded.net"])
    monkeypatch.setattr(up, "_get_fastest_server", lambda: "https://srv/upload")
    created_projects: list[str] = []
    def _create(name, description, user_id):
        created_projects.append(name)
        return f"hash-{name}"
    monkeypatch.setattr(up, "_create_project", _create)

    sent = []
    def _send(server, file_path, hosts, on_event, **kw):
        sent.append((file_path.name, kw["project_hash"]))
        # Use the filename inside the path so _shorten_url's collapse
        # leaves us with distinguishable URLs per file.
        return f"https://multiup.io/download/{file_path.name}/x"
    monkeypatch.setattr(up, "_upload_file", _send)

    pasted = []
    def _paste(bin_url, urls, password=None):
        pasted.append((bin_url, list(urls)))
        return "https://pb/aaa"
    monkeypatch.setattr(up, "_create_paste", _paste)

    parts = [
        tmp_path / "Game.1.windows.public.7z.001",
        tmp_path / "Game.1.windows.public.7z.002",
        tmp_path / "Game.1.linux.public.7z",
    ]
    for p in parts:
        p.write_bytes(b"x")

    events = []
    result = up.upload_archives(
        parts, username="alice", password="pw",
        links_dir=tmp_path / "links",
        bin_url="https://pb",
        on_event=events.append,
    )

    # Two stems -> two MultiUp projects -> two PrivateBin pastes.
    assert sorted(created_projects) == ["Game.1.linux.public", "Game.1.windows.public"]
    assert len(pasted) == 2
    # Windows project bundled both .001 and .002 — three uploads total
    # but only two project hashes, the windows hash repeated twice.
    project_hashes = [ph for _, ph in sent]
    assert project_hashes.count("hash-Game.1.windows.public") == 2
    assert project_hashes.count("hash-Game.1.linux.public")   == 1
    win_paste = next(urls for url, urls in pasted
                     if any("windows" in u for u in urls))
    assert len(win_paste) == 2
    # Returned canonical URLs come from the paste responses.
    assert result["Game.1.windows.public"] == "https://pb/aaa"
    assert result["Game.1.linux.public"]   == "https://pb/aaa"
    # Stage events emitted for login + host-list + per-stem upload heading.
    kinds = [e.kind for e in events]
    assert "stage" in kinds
    assert kinds.count("paste_created") == 2


def test_upload_archives_uses_gevent_pool_when_max_concurrent_gt_1(monkeypatch, tmp_path):
    """max_concurrent > 1 must dispatch via gevent.pool.Pool, not
    concurrent.futures.ThreadPoolExecutor — patch_minimal() leaves
    threading unpatched, so a thread-pool-based upload would block the
    main hub and starve the live-display redraw greenlet."""
    from src.core.archive import upload as up

    monkeypatch.setattr(up, "_login",              lambda u, p: "uid")
    monkeypatch.setattr(up, "_get_hosts",          lambda u, p: ["h"])
    monkeypatch.setattr(up, "_get_fastest_server", lambda: "https://srv")
    monkeypatch.setattr(up, "_create_project",     lambda *a, **kw: "ph")
    monkeypatch.setattr(up, "_upload_file",
                        lambda *a, **kw: "https://multiup.io/download/x/y")

    # Sentinel: assert thread executor is NOT touched on the parallel
    # path.  If a future change reintroduces it the test fails loudly.
    import concurrent.futures as cf
    boom = mock.Mock(side_effect=AssertionError(
        "ThreadPoolExecutor must not be used for upload concurrency"
    ))
    monkeypatch.setattr(cf, "ThreadPoolExecutor", boom)

    pool_calls: list[int] = []
    real_pool_cls = __import__("gevent.pool", fromlist=["Pool"]).Pool
    class _SpyPool(real_pool_cls):
        def __init__(self, size):
            pool_calls.append(size)
            super().__init__(size)
    monkeypatch.setattr("gevent.pool.Pool", _SpyPool)

    files = [tmp_path / f"Game.7z.{i:03d}" for i in (1, 2, 3)]
    for f in files:
        f.write_bytes(b"x")

    result = up.upload_archives(
        files, username="alice", password="pw", max_concurrent=3,
        links_dir=None, bin_url=None,
        on_event=None,
    )
    assert pool_calls == [3]
    assert "Game" in result


def test_upload_archives_inline_path_when_max_concurrent_le_1(monkeypatch, tmp_path):
    """max_concurrent <= 1 calls _upload_one directly on the main
    greenlet so socket writes inside requests.post yield to the live
    display's redraw loop chunk-by-chunk."""
    from src.core.archive import upload as up

    monkeypatch.setattr(up, "_login",              lambda u, p: "uid")
    monkeypatch.setattr(up, "_get_hosts",          lambda u, p: ["h"])
    monkeypatch.setattr(up, "_get_fastest_server", lambda: "https://srv")
    monkeypatch.setattr(up, "_create_project",     lambda *a, **kw: "ph")
    monkeypatch.setattr(up, "_upload_file",
                        lambda *a, **kw: "https://multiup.io/download/x/y")

    boom_pool = mock.Mock(side_effect=AssertionError(
        "gevent.pool.Pool must not be used when max_concurrent <= 1"
    ))
    monkeypatch.setattr("gevent.pool.Pool", boom_pool)

    f = tmp_path / "Game.7z"
    f.write_bytes(b"x")
    result = up.upload_archives(
        [f], username="alice", password="pw", max_concurrent=1,
        links_dir=None, bin_url=None, on_event=None,
    )
    assert "Game" in result


def test_upload_emits_started_progress_finished_per_archive(monkeypatch, tmp_path):
    """_upload_file must bracket every archive in started/finished events
    and forward MultipartEncoderMonitor progress through upload_progress."""
    from src.core.archive import upload as up

    monkeypatch.setattr(up, "_login",              lambda u, p: "uid")
    monkeypatch.setattr(up, "_get_hosts",          lambda u, p: ["h"])
    monkeypatch.setattr(up, "_get_fastest_server", lambda: "https://srv")
    monkeypatch.setattr(up, "_create_project",     lambda *a, **k: "ph")
    monkeypatch.setattr(up, "_create_paste",       lambda *a, **k: "https://pb/x")

    # Stub _upload_file to drive the events directly so we don't need the
    # requests-toolbelt monitor stack.
    def _stub(server, file_path, hosts, on_event, **kw):
        from src.core.archive.download import DownloadEvent
        on_event(DownloadEvent(kind="upload_started",  name=file_path.name, total=100))
        on_event(DownloadEvent(kind="upload_progress", name=file_path.name, total=100, done=42))
        on_event(DownloadEvent(kind="upload_finished", name=file_path.name, total=100, done=100))
        return f"https://multiup.io/download/h/{file_path.name}"
    monkeypatch.setattr(up, "_upload_file", _stub)

    f = tmp_path / "Game.1.windows.public.7z"
    f.write_bytes(b"x")
    events = []
    up.upload_archives([f], username="u", password="p",
                       links_dir=None, bin_url=None,
                       on_event=events.append)
    kinds = [e.kind for e in events]
    assert "upload_started"  in kinds
    assert "upload_progress" in kinds
    assert "upload_finished" in kinds
    assert kinds.index("upload_started") < kinds.index("upload_finished")


# ---------------------------------------------------------------------------
# LiveDisplaySubscriber: upload-phase footer label + paste line
# ---------------------------------------------------------------------------

def test_live_display_switches_to_upload_phase_label():
    from src.core.archive.cli_progress import LiveDisplaySubscriber
    from src.core.archive.download     import DownloadEvent

    sub = LiveDisplaySubscriber()
    # Simulate end-of-download state: counters set, files cleared by compress.
    sub._downloaded = 1000
    sub(DownloadEvent(kind="compress_started", name="game.7z"))
    sub(DownloadEvent(kind="compress_finished", name="game.7z"))

    # Upload phase begins.
    sub(DownloadEvent(kind="upload_started",  name="game.7z", total=500))
    assert sub._phase == "upload"
    sub(DownloadEvent(kind="upload_progress", name="game.7z", total=500, done=300))
    assert sub._uploaded == 300
    sub(DownloadEvent(kind="upload_finished", name="game.7z", total=500, done=500))
    assert sub._uploaded == 500

    bytes_, label = sub._phase_counters()
    assert label == "uploaded"
    assert bytes_ == 500

    # paste_created shouldn't crash and should not touch the byte counters.
    sub(DownloadEvent(kind="paste_created", name="game", stage_msg="https://pb/x"))
    assert sub._uploaded == 500

    sub.close()


# ---------------------------------------------------------------------------
# Notify message shaping (mock the SDK boundary)
# ---------------------------------------------------------------------------

def test_notify_telegram_includes_buildids_and_links():
    from src.core.archive import notify as nt
    fake_bot = mock.MagicMock()
    sent = []
    def _send_photo(chat_id, header, caption, parse_mode):
        sent.append({"chat_id": chat_id, "caption": caption})
    fake_bot.send_photo = _send_photo

    fake_module = mock.MagicMock()
    fake_module.OrigamiBot = mock.MagicMock(return_value=fake_bot)

    with mock.patch.dict(sys.modules, {"origamibot": fake_module}), \
         mock.patch.object(nt, "_steam_image_url", return_value="https://hdr"):
        ok = nt.send_telegram_notification(
            "tok", ["c1", "c2"],
            {"appid": 12345, "name": "Game",
             "previous_buildid": "100", "current_buildid": "200",
             "timeupdated": 1_700_000_000},
            upload_links={"windows": "https://w", "linux": "https://l"},
        )
    assert ok is True
    assert len(sent) == 2
    cap = sent[0]["caption"]
    assert "Game" in cap and "12345" in cap
    assert "100" in cap and "200" in cap
    assert "Linux" in cap and "Windows" in cap


def test_notify_telegram_force_download_warning():
    from src.core.archive import notify as nt
    fake_bot = mock.MagicMock()
    captured = {}
    fake_bot.send_photo = lambda chat_id, header, caption, parse_mode: captured.setdefault("c", caption)
    fake_module = mock.MagicMock()
    fake_module.OrigamiBot = mock.MagicMock(return_value=fake_bot)

    with mock.patch.dict(sys.modules, {"origamibot": fake_module}), \
         mock.patch.object(nt, "_steam_image_url", return_value="x"):
        nt.send_telegram_notification(
            "t", ["c"],
            {"appid": 1, "name": "G", "previous_buildid": "0",
             "current_buildid": "1", "timeupdated": 0},
            force_download=True,
        )
    assert "Forced download" in captured["c"]


def test_notify_discord_returns_false_on_webhook_failure():
    from src.core.archive import notify as nt
    bad_resp = mock.MagicMock(ok=False)
    fake_webhook = mock.MagicMock()
    fake_webhook.execute.return_value = bad_resp

    fake_module = mock.MagicMock()
    fake_module.DiscordWebhook = mock.MagicMock(return_value=fake_webhook)
    fake_module.DiscordEmbed   = mock.MagicMock(return_value=mock.MagicMock())

    with mock.patch.dict(sys.modules, {"discord_webhook": fake_module}), \
         mock.patch.object(nt, "_steam_image_url", return_value="x"):
        ok = nt.send_discord_notification(
            "https://hook",
            {"appid": 1, "name": "G", "previous_buildid": "0",
             "current_buildid": "1", "timeupdated": 0},
        )
    assert ok is False


# ---------------------------------------------------------------------------
# CLI post-pipeline helper — stem-to-platform parsing + creds gating
# ---------------------------------------------------------------------------

def test_cli_platform_from_archive_stem():
    from src.cli.main import _platform_from_archive_stem
    assert _platform_from_archive_stem("Game.1.windows.public") == "windows"
    assert _platform_from_archive_stem("Game.1.linux.public")   == "linux"
    assert _platform_from_archive_stem("Game.1.macos.public")   == "macos"
    assert _platform_from_archive_stem("Game.1.beta")           is None


def test_cli_post_pipeline_skips_when_no_creds_set(tmp_path):
    """No upload creds, no notify creds → upload_archives must not be called
    and the helper must complete without raising."""
    from src.cli.main import _archive_run_post_pipeline
    from src.core.archive import credentials as creds_mod

    upload_mod = mock.MagicMock()
    notify_mod = mock.MagicMock()
    creds = creds_mod.Credentials()  # all blocks empty

    _archive_run_post_pipeline(
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


def test_cli_resolve_notify_mode_priority():
    """CLI flag wins over project field; project field wins over auto-default;
    auto-default falls back to 'delay' iff multiup creds are set."""
    from src.cli.main import _resolve_notify_mode
    from src.core.archive import credentials as creds_mod

    creds = creds_mod.Credentials()
    creds.discord.webhook_url = "https://h"   # at least one notify target

    # No notify creds → "none" regardless of flag/field.
    no_notify = creds_mod.Credentials()
    no_notify.multiup.username = "u"
    assert _resolve_notify_mode("pre", "delay", no_notify) == "none"

    # CLI flag overrides everything.
    assert _resolve_notify_mode("pre",   "delay", creds) == "pre"
    assert _resolve_notify_mode("both",  "delay", creds) == "both"

    # Project field used when CLI flag absent.
    assert _resolve_notify_mode(None, "pre",   creds) == "pre"
    assert _resolve_notify_mode(None, "delay", creds) == "delay"
    assert _resolve_notify_mode(None, "both",  creds) == "both"

    # Auto-default: 'delay' when uploads can produce links, else 'pre'.
    creds.multiup.username = "u"
    assert _resolve_notify_mode(None, "", creds) == "delay"
    creds.multiup.username = ""
    assert _resolve_notify_mode(None, "", creds) == "pre"

    # Invalid project field is ignored, falls through to auto.
    assert _resolve_notify_mode(None, "garbage", creds) == "pre"


def test_cli_pre_pipeline_fires_only_in_pre_or_both(tmp_path):
    from src.cli.main import _archive_run_pre_pipeline
    from src.core.archive import credentials as creds_mod

    notify_mod = mock.MagicMock()
    creds = creds_mod.Credentials()
    creds.discord.webhook_url = "https://h"

    base = dict(
        app_meta={"appid": 9, "name": "G", "buildid": "", "timeupdated": 0},
        previous_buildid="100",
        creds=creds, notify_mod=notify_mod,
    )
    _archive_run_pre_pipeline(notify_mode="delay", **base)
    notify_mod.send_discord_notification.assert_not_called()
    _archive_run_pre_pipeline(notify_mode="pre", **base)
    _archive_run_pre_pipeline(notify_mode="both", **base)
    assert notify_mod.send_discord_notification.call_count == 2

    # Pre-notify must NOT include upload links.
    for call in notify_mod.send_discord_notification.call_args_list:
        assert call.kwargs["upload_links"] is None


def test_cli_post_pipeline_skips_post_notify_when_mode_pre(tmp_path):
    """notify_mode='pre' means upload still runs (so links land in the
    paste/links file) but the *post* notify is suppressed — the pre-notify
    already fired, sending a second one would duplicate."""
    from src.cli.main import _archive_run_post_pipeline
    from src.core.archive import credentials as creds_mod

    upload_mod = mock.MagicMock()
    upload_mod.upload_archives.return_value = {"Game.1.windows.public": "https://u"}
    notify_mod = mock.MagicMock()

    creds = creds_mod.Credentials()
    creds.multiup.username    = "alice"
    creds.discord.webhook_url = "https://hook"

    _archive_run_post_pipeline(
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


def test_cli_apply_creds_overrides_merges_per_field():
    """CLI flag values overwrite the corresponding creds field; missing
    args leave the creds untouched."""
    from src.cli.main         import _apply_archive_creds_overrides
    from src.core.archive     import credentials as creds_mod

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


def test_log_tee_strips_ansi_to_file_passes_raw_to_stream(tmp_path):
    """Live-display ANSI codes must reach the terminal verbatim but the
    log file gets the plain-text version."""
    from src.cli.main import _LogTee
    import io

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


def test_cli_post_pipeline_forwards_upload_knobs(tmp_path):
    """description / max_concurrent / delete_archives must reach
    upload_archives()."""
    from src.cli.main import _archive_run_post_pipeline
    from src.core.archive import credentials as creds_mod

    upload_mod = mock.MagicMock()
    upload_mod.upload_archives.return_value = {}
    notify_mod = mock.MagicMock()
    creds = creds_mod.Credentials()
    creds.multiup.username = "u"

    _archive_run_post_pipeline(
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


def test_project_notify_mode_field_roundtrips(tmp_path):
    """ArchiveProject.notify_mode persists through save/load."""
    from src.core.archive import project as pm
    p = pm.new_project(name="t")
    p.notify_mode = "both"
    out = tmp_path / "p.xarchive"
    pm.save(p, out)
    loaded = pm.load(out)
    assert loaded.notify_mode == "both"


def test_project_run_time_knobs_roundtrip(tmp_path):
    """All persistent run-time knobs (workers, compression, language,
    upload_description, max_concurrent_uploads, delete_archives,
    experimental, archive_password, volume_size, max_retries, unstub.*)
    must survive save/load."""
    from src.core.archive import project as pm
    p = pm.new_project(name="t")
    p.workers                = 16
    p.compression            = 5
    p.language               = "russian"
    p.max_retries            = 3
    p.archive_password       = "hunter2"
    p.volume_size            = "4g"
    p.upload_description     = "weekly drop"
    p.max_concurrent_uploads = 2
    p.delete_archives        = True
    p.experimental           = True
    p.unstub.keepbind        = True
    p.unstub.recalcchecksum  = True

    out = tmp_path / "p.xarchive"
    pm.save(p, out)
    loaded = pm.load(out)
    assert loaded.workers                == 16
    assert loaded.compression            == 5
    assert loaded.language               == "russian"
    assert loaded.max_retries            == 3
    assert loaded.archive_password       == "hunter2"
    assert loaded.volume_size            == "4g"
    assert loaded.upload_description     == "weekly drop"
    assert loaded.max_concurrent_uploads == 2
    assert loaded.delete_archives        is True
    assert loaded.experimental           is True
    assert loaded.unstub.keepbind        is True
    assert loaded.unstub.recalcchecksum  is True


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
    """No project -> CLI values, then built-in defaults."""
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


def test_apply_archive_creds_overrides_returns_dirty_flag():
    """Caller relies on the returned bool to decide whether to save."""
    from src.cli.main         import _apply_archive_creds_overrides
    from src.core.archive     import credentials as creds_mod
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


def test_cli_post_pipeline_routes_upload_links_to_notify(tmp_path):
    """When upload + discord creds are set, the platform_links dict must
    be derived from the upload result and forwarded to send_discord."""
    from src.cli.main import _archive_run_post_pipeline
    from src.core.archive import credentials as creds_mod

    upload_mod = mock.MagicMock()
    upload_mod.upload_archives.return_value = {
        "Game.1.windows.public": "https://pb/win",
        "Game.1.linux.public":   "https://pb/lin",
    }
    notify_mod = mock.MagicMock()

    creds = creds_mod.Credentials()
    creds.multiup.username    = "alice"
    creds.discord.webhook_url = "https://hook"

    archive_paths = [tmp_path / "Game.1.windows.public.7z",
                     tmp_path / "Game.1.linux.public.7z"]
    _archive_run_post_pipeline(
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
