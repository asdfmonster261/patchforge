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

import pytest

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
