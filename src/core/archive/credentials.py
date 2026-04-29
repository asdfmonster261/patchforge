"""Persistent credential storage for archive-mode.

Holds Steam refresh tokens and the Steam Web API key in a single chmod-600
JSON file under PatchForge's user config dir (~/.config/patchforge/ on Linux,
%APPDATA%\\PatchForge\\ on Windows).

Design notes:
  - One file (archive_credentials.json) for all secrets so there is exactly
    one chmod surface to get right.
  - Plain JSON on disk; no OS keyring dependency.  The .env model in
    SteamArchiver was also plaintext, so this is no worse.
  - Credentials must NEVER be written into a .xarchive project file —
    .xarchive is meant to be shareable/version-controllable.  See
    src/core/archive/project.py for the per-project (non-secret) state.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _config_dir() -> Path:
    """Per-platform PatchForge config dir.  Mirrors src/core/app_settings.py."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "PatchForge"
    return Path.home() / ".config" / "patchforge"


_CREDENTIALS_FILE = _config_dir() / "archive_credentials.json"


def credentials_path() -> Path:
    """Return the absolute path to archive_credentials.json (may not exist)."""
    return _CREDENTIALS_FILE


@dataclass
class MultiUpCreds:
    """MultiUp.io upload account.  Anonymous uploads work too — leave both
    fields blank in that case (no login, no project grouping)."""
    username: str = ""
    password: str = ""

    def is_set(self) -> bool:
        # MultiUp accepts anonymous uploads, so a "set" multiup config just
        # needs *either* a username (logged-in upload) or an explicit
        # opt-in via username="" + password="" → handled at the call site.
        return bool(self.username)


@dataclass
class PrivateBinCreds:
    """PrivateBin paste service for grouping per-archive download links."""
    url:      str = ""
    password: str = ""

    def is_set(self) -> bool:
        return bool(self.url)


@dataclass
class TelegramCreds:
    token:    str       = ""
    chat_ids: list[str] = field(default_factory=list)

    def is_set(self) -> bool:
        return bool(self.token and self.chat_ids)


@dataclass
class DiscordCreds:
    webhook_url:      str       = ""
    mention_role_ids: list[str] = field(default_factory=list)

    def is_set(self) -> bool:
        return bool(self.webhook_url)


@dataclass
class Credentials:
    # Steam refresh-token login (CM)
    username:             str = ""
    steam_id:             int = 0
    client_refresh_token: str = ""

    # Steam Web API key (used by Goldberg/ColdClient config generation)
    web_api_key:          str = ""

    # Phase 4: archive upload + notify destinations.  Each block is opt-in;
    # when is_set() returns False the corresponding pipeline step is skipped.
    multiup:    MultiUpCreds    = field(default_factory=MultiUpCreds)
    privatebin: PrivateBinCreds = field(default_factory=PrivateBinCreds)
    telegram:   TelegramCreds   = field(default_factory=TelegramCreds)
    discord:    DiscordCreds    = field(default_factory=DiscordCreds)

    def has_login_tokens(self) -> bool:
        return bool(self.username and self.client_refresh_token)


# Map nested-block name -> dataclass.  Used by load() to reconstitute
# nested dicts from the on-disk JSON.
_NESTED: dict[str, type] = {
    "multiup":    MultiUpCreds,
    "privatebin": PrivateBinCreds,
    "telegram":   TelegramCreds,
    "discord":    DiscordCreds,
}


def load() -> Credentials:
    try:
        data = json.loads(_CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return Credentials()
    if not isinstance(data, dict):
        return Credentials()
    known = set(Credentials.__dataclass_fields__)
    filtered = {k: v for k, v in data.items() if k in known}
    # Coerce steam_id from string (some legacy migrations may have stored it
    # as a numeric string rather than an int).
    sid = filtered.get("steam_id")
    if isinstance(sid, str):
        filtered["steam_id"] = int(sid) if sid.isdigit() else 0
    # Reconstitute nested credential blocks.  Drop unknown keys so renamed
    # fields don't crash older files.
    for key, cls in _NESTED.items():
        raw = filtered.get(key)
        if isinstance(raw, dict):
            sub_known = set(cls.__dataclass_fields__)
            filtered[key] = cls(**{k: v for k, v in raw.items() if k in sub_known})
        else:
            filtered.pop(key, None)
    return Credentials(**filtered)


def save(creds: Credentials) -> None:
    _CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(creds), indent=2)
    _CREDENTIALS_FILE.write_text(payload, encoding="utf-8")
    # chmod 600 on POSIX.  Windows ACLs handled separately (the file lives
    # under %APPDATA% which is per-user already).
    if os.name == "posix":
        try:
            os.chmod(_CREDENTIALS_FILE, 0o600)
        except OSError:
            pass


def clear_login_tokens() -> None:
    """Remove only the Steam login fields, preserving the Web API key."""
    creds = load()
    creds.username = ""
    creds.steam_id = 0
    creds.client_refresh_token = ""
    save(creds)


def clear_all() -> None:
    """Delete the entire credentials file."""
    try:
        _CREDENTIALS_FILE.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# JWT helpers (used to inspect refresh-token expiry without contacting Steam)
# ---------------------------------------------------------------------------

def _jwt_claims(token: str) -> dict | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None


def jwt_expiry(token: str) -> int | None:
    """Return the `exp` claim of a JWT, or None if the token is malformed."""
    claims = _jwt_claims(token)
    if claims is None:
        return None
    exp = claims.get("exp")
    return int(exp) if exp is not None else 0


def refresh_token_expiry_text(token: str) -> tuple[str, str]:
    """Return (display_text, hex_color) describing a refresh token's expiry.

    Matches the SteamArchiver behaviour for parity with the GUI:
      green > 30 days, yellow 7-30 days, red < 7 days or expired.
    Returns ('', '') if no token is provided or the JWT lacks an exp claim.
    """
    if not token:
        return "", ""
    exp = jwt_expiry(token)
    if exp is None:
        return "Token file corrupt", "#e64646"
    if not exp:
        return "", ""
    remaining = int(exp - time.time())
    if remaining <= 0:
        return "Token expired", "#e64646"
    days  = remaining // 86400
    hours = (remaining % 86400) // 3600
    mins  = (remaining % 3600) // 60
    if days >= 1:
        text = f"Token expires in {days}d {hours}h"
    else:
        text = f"Token expires in {hours}h {mins}m"
    if remaining < 7 * 86400:
        color = "#e64646"
    elif remaining < 30 * 86400:
        color = "#e6b446"
    else:
        color = "#6e6e87"
    return text, color
