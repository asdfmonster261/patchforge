"""Phase 1 archive-mode tests: credentials roundtrip, project roundtrip,
and the missing-extras error path.  These do NOT exercise the steam[client]
network paths."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# Ensure the repo root is importable when running pytest from any cwd.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------

def _redirect_credentials(tmp_path: Path):
    """Patch the credentials module to read/write inside tmp_path."""
    from src.core.archive import credentials as cm
    fake_path = tmp_path / "archive_credentials.json"
    return mock.patch.object(cm, "_CREDENTIALS_FILE", fake_path)


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

        # File was chmod 600 on POSIX
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
# JWT helpers
# ---------------------------------------------------------------------------

def _fake_jwt(exp_seconds_from_now: int) -> str:
    """Build a minimally-valid-looking JWT with a given exp claim."""
    import base64
    import time
    payload = {"exp": int(time.time()) + exp_seconds_from_now, "sub": "1"}
    enc = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"hdr.{enc}.sig"


def test_refresh_token_expiry_text_far_future(tmp_path):
    from src.core.archive import credentials as cm
    text, color = cm.refresh_token_expiry_text(_fake_jwt(60 * 86400))
    assert "expires in" in text
    assert color == "#6e6e87"   # green-ish


def test_refresh_token_expiry_text_soon(tmp_path):
    from src.core.archive import credentials as cm
    text, color = cm.refresh_token_expiry_text(_fake_jwt(3 * 86400))
    assert "expires in" in text
    assert color == "#e64646"   # red


def test_refresh_token_expiry_text_expired(tmp_path):
    from src.core.archive import credentials as cm
    text, color = cm.refresh_token_expiry_text(_fake_jwt(-3600))
    assert text == "Token expired"
    assert color == "#e64646"


def test_refresh_token_expiry_text_corrupt(tmp_path):
    from src.core.archive import credentials as cm
    text, color = cm.refresh_token_expiry_text("not.a.jwt")
    assert "corrupt" in text.lower()


def test_refresh_token_expiry_text_empty(tmp_path):
    from src.core.archive import credentials as cm
    text, color = cm.refresh_token_expiry_text("")
    assert text == "" and color == ""


# ---------------------------------------------------------------------------
# project
# ---------------------------------------------------------------------------

def test_project_roundtrip(tmp_path):
    from src.core.archive import project as pm
    p = pm.new_project("test")
    p.apps.append(pm.AppEntry(app_id=730, current_buildid="12345"))
    p.apps.append(pm.AppEntry(app_id=570, branch="beta", platform="linux"))
    p.crack.steam_id = 76561198000000000
    p.crack.username = "alice"
    p.crack.listen_port = 47585

    out = tmp_path / "x.xarchive"
    pm.save(p, out)
    loaded = pm.load(out)
    assert loaded.name == "test"
    assert len(loaded.apps) == 2
    assert loaded.apps[0].app_id == 730
    assert loaded.apps[0].current_buildid.buildid == "12345"
    assert loaded.apps[1].branch == "beta"
    assert loaded.crack.steam_id == 76561198000000000
    assert loaded.crack.username == "alice"
    assert loaded.crack.listen_port == 47585
    # Default BBCode template is non-empty
    assert "{APP_NAME}" in loaded.bbcode_template


def test_project_load_drops_unknown_fields(tmp_path):
    from src.core.archive import project as pm
    out = tmp_path / "x.xarchive"
    out.write_text(
        json.dumps({
            "schema_version": 1,
            "name": "x",
            "ghost_top_field": 1,
            "apps": [{"app_id": 1, "ghost_app_field": 1}],
            "crack": {"steam_id": 99, "ghost_crack_field": 1},
        }),
        encoding="utf-8",
    )
    p = pm.load(out)
    assert p.name == "x"
    assert p.apps[0].app_id == 1
    assert p.crack.steam_id == 99


def test_project_rejects_future_schema(tmp_path):
    from src.core.archive import project as pm
    out = tmp_path / "x.xarchive"
    out.write_text(json.dumps({"schema_version": 9999}), encoding="utf-8")
    with pytest.raises(ValueError, match="newer PatchForge"):
        pm.load(out)


def test_project_default_template_loads():
    from src.core.archive import project as pm
    s = pm.default_bbcode_template()
    assert "{APP_NAME}" in s
    assert "{BUILDID}" in s


def test_archive_project_does_not_carry_credentials():
    """Sanity check: ArchiveProject must not declare any field that smells like
    a credential (refresh tokens, API keys).  Catches future drift if someone
    forgets the per-project / global split."""
    from src.core.archive import project as pm
    forbidden = {"client_refresh_token", "refresh_token", "access_token",
                 "web_api_key", "api_key", "password"}
    fields = set(pm.ArchiveProject.__dataclass_fields__)
    fields |= set(pm.CrackIdentity.__dataclass_fields__)
    fields |= set(pm.AppEntry.__dataclass_fields__)
    leaks = forbidden.intersection(fields)
    # branch_password is allowed (Steam beta-branch passwords are not user
    # account credentials and were per-project in SteamArchiver too).
    leaks.discard("branch_password")
    assert not leaks, f"ArchiveProject contains credential-like fields: {leaks}"


# ---------------------------------------------------------------------------
# extras
# ---------------------------------------------------------------------------

def test_missing_extras_returns_list():
    """missing_extras() must always return a list (possibly empty)."""
    from src.core.archive._extras import missing_extras
    assert isinstance(missing_extras(), list)


def test_require_extras_raises_when_missing(monkeypatch):
    from src.core.archive import _extras
    from src.core.archive.errors import ExtrasNotInstalled
    monkeypatch.setattr(_extras, "missing_extras", lambda: ["fake-dist"])
    with pytest.raises(ExtrasNotInstalled, match="fake-dist"):
        _extras.require_extras()


def test_require_extras_silent_when_satisfied(monkeypatch):
    from src.core.archive import _extras
    monkeypatch.setattr(_extras, "missing_extras", lambda: [])
    _extras.require_extras()  # should not raise
