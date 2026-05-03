"""Telegram + Discord build-change notifications.

One-shot — no progress events, no event subscriber integration.  Both
senders take an `app_data` dict (name, appid, previous_buildid,
current_buildid, timeupdated) and an optional `upload_links` dict mapping
platform -> URL, and post a single message.

Lazy-imports `origamibot` and `discord_webhook` so importing this module
without the `archive` extra installed does not raise.  Each `send_*`
function returns True iff every chat/webhook accepted the message.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Iterable


# ---------------------------------------------------------------------------
# Header-image lookup
# ---------------------------------------------------------------------------

@lru_cache(maxsize=256)
def _steam_image_url(appid: int | str) -> str:
    """Best-effort Steam header URL for `appid`.

    Tries the legacy CDN flat path first (one HEAD request, no API key).
    Newer titles use content-addressed paths and 404 there — fall back to
    the public Store appdetails API and read header_image.
    """
    import requests
    flat = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"
    try:
        if requests.head(flat, timeout=5).ok:
            return flat
    except Exception:
        return flat

    try:
        r = requests.get(
            f"https://store.steampowered.com/api/appdetails?appids={appid}&filters=basic",
            timeout=8,
        )
        if r.ok:
            payload = r.json().get(str(appid), {})
            if payload.get("success"):
                url = payload["data"].get("header_image", "")
                if url:
                    return url
    except Exception:
        pass

    return flat  # last resort, may 404


def _format_time(timeupdated: int | str) -> str:
    """Format a Unix timestamp as `Month DD, YYYY - HH:MM:SS UTC`."""
    try:
        ts = int(timeupdated)
    except (TypeError, ValueError):
        return str(timeupdated)
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%B %d, %Y - %H:%M:%S UTC")


def _link_parts(upload_links: dict | None) -> list[str]:
    """Return platform-ordered `[Label](url)` link snippets, or []."""
    if not upload_links:
        return []
    out: list[str] = []
    for plat, label in (("linux", "Linux"), ("macos", "macOS"), ("windows", "Windows")):
        url = upload_links.get(plat)
        if url:
            out.append(f"[{label}]({url})")
    return out


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram_notification(token: str,
                               chat_ids: Iterable[str],
                               app_data: dict,
                               upload_links: dict | None = None,
                               force_download: bool = False) -> bool:
    """Send a Telegram photo + Markdown caption to each chat in `chat_ids`.

    Returns True iff every chat accepted the message.  Per-chat failures
    are recoverable — they don't abort the loop.
    """
    from origamibot import OrigamiBot as Bot

    appid     = app_data["appid"]
    name      = app_data["name"]
    prev_bid  = app_data["previous_buildid"]
    curr_bid  = app_data["current_buildid"]
    time_str  = _format_time(app_data["timeupdated"])
    patch_url = f"https://steamdb.info/patchnotes/{curr_bid}/"
    header    = _steam_image_url(appid)

    lines = [
        f"\U0001f4e2 *{name}*",
        f"\U0001f539 *App ID:* `{appid}`",
        f"\U0001f539 *Previous Build ID:* `{prev_bid}`",
        f"\U0001f539 *Current Build ID:* `{curr_bid}`",
        f"\U0001f539 *Updated On:* `{time_str}`",
    ]

    parts = _link_parts(upload_links)
    if parts:
        lines.append(f"\U0001f517 *Downloads:* {', '.join(parts)}")
    lines.append(f"\U0001f517 [Patch Notes]({patch_url})")

    if force_download:
        lines.append(
            "⚠️ *Warning:* Forced download mode was enabled.  "
            "This may not represent an actual update."
        )

    caption = "\n".join(lines)

    ok = True
    bot = Bot(token)
    for chat_id in chat_ids:
        try:
            bot.send_photo(chat_id, header, caption=caption, parse_mode="Markdown")
        except Exception:
            ok = False
    return ok


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

def send_discord_notification(webhook_url: str,
                              app_data: dict,
                              mention_role_ids: Iterable[str] | None = None,
                              upload_links: dict | None = None,
                              force_download: bool = False) -> bool:
    """Post a Discord embed to `webhook_url` describing the build change."""
    from discord_webhook import DiscordEmbed, DiscordWebhook

    appid    = app_data["appid"]
    name     = app_data["name"]
    prev_bid = app_data["previous_buildid"]
    curr_bid = app_data["current_buildid"]
    patch_url = f"https://steamdb.info/patchnotes/{curr_bid}/"
    # Cache-bust the header image so Discord refetches between notifications.
    header_url = f"{_steam_image_url(appid)}?{int(time.time())}"

    embed = DiscordEmbed(title=name, url=patch_url, color="242424")
    if mention_role_ids:
        embed.add_embed_field(
            name="",
            value=" ".join(f"<@{rid}>" for rid in mention_role_ids),
            inline=False,
        )
    embed.set_image(url=header_url)
    embed.set_footer(text="Steam",
                     icon_url="https://i.imgur.com/oYkhH6s.png")
    embed.add_embed_field(name=">>> Previous Version",
                          value=f"```{prev_bid}```", inline=True)
    embed.add_embed_field(name=">>> Current Version",
                          value=f"```{curr_bid}```", inline=True)
    embed.add_embed_field(name=">>> AppID",
                          value=f"```{appid}```", inline=False)

    if force_download:
        embed.add_embed_field(
            name=">>> Warning",
            value=(
                "```Forced download mode was enabled. "
                "This post may not represent an actual update.```"
            ),
            inline=False,
        )

    parts = _link_parts(upload_links)
    if parts:
        embed.add_embed_field(name=">>> Downloads",
                              value=", ".join(parts), inline=True)

    webhook = DiscordWebhook(url=webhook_url)
    webhook.add_embed(embed)
    try:
        response = webhook.execute()
        return bool(getattr(response, "ok", False))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# System alerts (CM outage etc.) — plain-text, not a build-change event
# ---------------------------------------------------------------------------

def send_alert(creds, subject: str, body: str) -> bool:
    """Send a plain-text alert to whichever notify channels are configured.

    Used for "Steam CM still unreachable" / extended-outage warnings —
    distinct from the per-build send_*_notification functions which want
    structured app_data.  Returns True iff at least one channel accepted
    the message.
    """
    delivered = False

    if creds.telegram.is_set():
        try:
            from origamibot import OrigamiBot as Bot
            bot = Bot(creds.telegram.token)
            text = f"⚠️ *{subject}*\n{body}"
            for chat_id in creds.telegram.chat_ids:
                try:
                    bot.send_message(chat_id, text=text, parse_mode="Markdown")
                    delivered = True
                except Exception:
                    pass
        except Exception:
            pass

    if creds.discord.is_set():
        try:
            from discord_webhook import DiscordWebhook
            content = f"⚠️ **{subject}**\n{body}"
            mention_ids = getattr(creds.discord, "mention_role_ids", None) or []
            if mention_ids:
                content = " ".join(f"<@&{rid}>" for rid in mention_ids) + "\n" + content
            webhook = DiscordWebhook(url=creds.discord.webhook_url, content=content)
            response = webhook.execute()
            if getattr(response, "ok", False):
                delivered = True
        except Exception:
            pass

    return delivered
