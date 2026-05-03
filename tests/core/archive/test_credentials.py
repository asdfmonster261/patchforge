"""archive.credentials — JSON I/O, JWT-expiry helper, login-token clear."""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from unittest import mock


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _redirect_credentials(tmp_path: Path):
    """Patch the credentials module to read/write inside `tmp_path`.

    Used as a context manager so the module-level path patch is scoped
    to one test and doesn't leak into the next.
    """
    from src.core.archive import credentials as cm
    fake_path = tmp_path / "archive_credentials.json"
    return mock.patch.object(cm, "_CREDENTIALS_FILE", fake_path)


def _fake_jwt(exp_seconds_from_now: int) -> str:
    """Build a minimally-valid-looking JWT with a given exp claim."""
    payload = {"exp": int(time.time()) + exp_seconds_from_now, "sub": "1"}
    enc = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"hdr.{enc}.sig"


# ---------------------------------------------------------------------------
# Credentials (load / save / clear)
# ---------------------------------------------------------------------------

def test_credentials_roundtrip(tmp_path):
    from src.core.archive import credentials as cm
    with _redirect_credentials(tmp_path):
        c = cm.Credentials(
            username="alice", steam_id=76561,
            client_refresh_token="aaa.bbb.ccc", web_api_key="APIKEY",
        )
        cm.save(c)
        loaded = cm.load()
        assert loaded == c

        # File must be chmod 600 on POSIX so multi-user systems don't leak it.
        if os.name == "posix":
            mode = os.stat(cm.credentials_path()).st_mode & 0o777
            assert mode == 0o600


def test_credentials_clear_login_preserves_api_key(tmp_path):
    from src.core.archive import credentials as cm
    with _redirect_credentials(tmp_path):
        cm.save(cm.Credentials(
            username="alice", steam_id=76561,
            client_refresh_token="tok", web_api_key="APIKEY",
        ))
        cm.clear_login_tokens()
        c = cm.load()
        assert c.username == "" and c.steam_id == 0
        assert c.client_refresh_token == ""
        assert c.web_api_key == "APIKEY"


def test_credentials_clear_all(tmp_path):
    from src.core.archive import credentials as cm
    with _redirect_credentials(tmp_path):
        cm.save(cm.Credentials(username="x", web_api_key="y"))
        assert cm.credentials_path().exists()
        cm.clear_all()
        assert not cm.credentials_path().exists()


def test_credentials_load_missing_file_returns_default(tmp_path):
    from src.core.archive import credentials as cm
    with _redirect_credentials(tmp_path):
        c = cm.load()
        assert c == cm.Credentials()
        assert not c.has_login_tokens()


def test_credentials_load_corrupt_json_returns_default(tmp_path):
    from src.core.archive import credentials as cm
    with _redirect_credentials(tmp_path):
        cm._CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        cm._CREDENTIALS_FILE.write_text("{not json", encoding="utf-8")
        assert cm.load() == cm.Credentials()


def test_credentials_drops_unknown_fields(tmp_path):
    from src.core.archive import credentials as cm
    with _redirect_credentials(tmp_path):
        cm._CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        cm._CREDENTIALS_FILE.write_text(
            json.dumps({"username": "u", "ghost_field": "ignored"}),
            encoding="utf-8",
        )
        loaded = cm.load()
        assert loaded.username == "u"
        assert not hasattr(loaded, "ghost_field")


def test_credentials_string_steam_id_coerces(tmp_path):
    from src.core.archive import credentials as cm
    with _redirect_credentials(tmp_path):
        cm._CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        cm._CREDENTIALS_FILE.write_text(
            json.dumps({"username": "u", "steam_id": "76561"}),
            encoding="utf-8",
        )
        assert cm.load().steam_id == 76561


# ---------------------------------------------------------------------------
# Nested-block credentials (multiup / privatebin / telegram / discord)
# ---------------------------------------------------------------------------

def test_credentials_nested_blocks_roundtrip(tmp_path, monkeypatch):
    from src.core.archive import credentials as cm
    monkeypatch.setattr(cm, "_CREDENTIALS_FILE",
                        tmp_path / "archive_credentials.json")
    c = cm.Credentials()
    c.multiup    = cm.MultiUpCreds(username="user", password="pass")
    c.privatebin = cm.PrivateBinCreds(url="https://pb", password="pw")
    c.telegram   = cm.TelegramCreds(token="t", chat_ids=["1", "2"])
    c.discord    = cm.DiscordCreds(webhook_url="https://wh",
                                   mention_role_ids=["100"])
    cm.save(c)
    loaded = cm.load()
    assert loaded.multiup.username == "user"
    assert loaded.privatebin.url    == "https://pb"
    assert loaded.telegram.chat_ids == ["1", "2"]
    assert loaded.discord.mention_role_ids == ["100"]


def test_credentials_load_drops_unknown_nested_keys(tmp_path, monkeypatch):
    """Nested credential blocks ignore fields they don't know about
    so an older PatchForge can read a newer credentials file."""
    from src.core.archive import credentials as cm
    monkeypatch.setattr(cm, "_CREDENTIALS_FILE",
                        tmp_path / "archive_credentials.json")
    cm._CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    cm._CREDENTIALS_FILE.write_text(json.dumps({
        "username": "u",
        "multiup": {"username": "x", "password": "y", "ghost_field": 1},
        "ghost_top": 2,
    }), encoding="utf-8")
    loaded = cm.load()
    assert loaded.username == "u"
    assert loaded.multiup.username == "x"
    assert not hasattr(loaded, "ghost_top")
    assert not hasattr(loaded.multiup, "ghost_field")


# ---------------------------------------------------------------------------
# JWT expiry helper (refresh_token_expiry_text)
# ---------------------------------------------------------------------------

def test_refresh_token_expiry_text_far_future():
    from src.core.archive import credentials as cm
    text, color = cm.refresh_token_expiry_text(_fake_jwt(60 * 86400))
    assert "expires in" in text
    assert color == "#6e6e87"   # green-ish


def test_refresh_token_expiry_text_soon():
    from src.core.archive import credentials as cm
    text, color = cm.refresh_token_expiry_text(_fake_jwt(3 * 86400))
    assert "expires in" in text
    assert color == "#e64646"   # red


def test_refresh_token_expiry_text_expired():
    from src.core.archive import credentials as cm
    text, color = cm.refresh_token_expiry_text(_fake_jwt(-3600))
    assert text == "Token expired"
    assert color == "#e64646"


def test_refresh_token_expiry_text_corrupt():
    from src.core.archive import credentials as cm
    text, color = cm.refresh_token_expiry_text("not.a.jwt")
    assert "corrupt" in text.lower()


def test_refresh_token_expiry_text_empty():
    from src.core.archive import credentials as cm
    text, color = cm.refresh_token_expiry_text("")
    assert text == "" and color == ""
