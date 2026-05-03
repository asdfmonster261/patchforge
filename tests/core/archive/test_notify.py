"""archive.notify — Telegram + Discord build-change post helpers, plus
the system-alert helper used by the runner's outage path.

External SDKs (origamibot, discord_webhook) are mocked at import time
so these tests don't need network or real bot credentials.
"""
from __future__ import annotations

import sys
from unittest import mock


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def test_notify_telegram_includes_buildids_and_links():
    """Caption must include app id, both buildids, and the platform-list
    URL pairs.  Caller passes upload_links per platform."""
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
    """force_download=True must add a visible warning to the caption so
    subscribers know the post may not represent a real update."""
    from src.core.archive import notify as nt
    fake_bot = mock.MagicMock()
    captured = {}
    fake_bot.send_photo = lambda chat_id, header, caption, parse_mode: \
        captured.setdefault("c", caption)
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


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

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
# System alerts (CM outage etc.) — plain-text, not a build-change event
# ---------------------------------------------------------------------------

def test_send_alert_uses_telegram_when_set():
    """send_alert routes to whichever channels are configured.  When
    only telegram is set, only the bot is invoked."""
    from src.core.archive import notify as nt
    from src.core.archive import credentials as creds_mod

    creds = creds_mod.Credentials()
    creds.telegram = creds_mod.TelegramCreds(token="t", chat_ids=["c1"])

    fake_bot = mock.MagicMock()
    sent = []
    fake_bot.send_message = lambda chat_id, text, parse_mode: sent.append(
        (chat_id, text))
    fake_origami = mock.MagicMock()
    fake_origami.OrigamiBot = mock.MagicMock(return_value=fake_bot)

    with mock.patch.dict(sys.modules, {"origamibot": fake_origami}):
        ok = nt.send_alert(creds, "subject", "body")
    assert ok is True
    assert sent and "subject" in sent[0][1] and "body" in sent[0][1]


def test_send_alert_uses_discord_when_set():
    from src.core.archive import notify as nt
    from src.core.archive import credentials as creds_mod

    creds = creds_mod.Credentials()
    creds.discord = creds_mod.DiscordCreds(webhook_url="https://hook",
                                           mention_role_ids=["123"])

    resp = mock.MagicMock(ok=True)
    fake_webhook = mock.MagicMock()
    fake_webhook.execute.return_value = resp
    fake_dw = mock.MagicMock()
    fake_dw.DiscordWebhook = mock.MagicMock(return_value=fake_webhook)

    with mock.patch.dict(sys.modules, {"discord_webhook": fake_dw}):
        ok = nt.send_alert(creds, "subject", "body")
    assert ok is True
    fake_dw.DiscordWebhook.assert_called_once()
    # Mention role must be present in the content payload.
    _args, kwargs = fake_dw.DiscordWebhook.call_args
    assert "<@&123>" in kwargs["content"]


def test_send_alert_returns_false_when_no_channels_configured():
    from src.core.archive import notify as nt
    from src.core.archive import credentials as creds_mod
    assert nt.send_alert(creds_mod.Credentials(), "s", "b") is False
