"""Phase 3 archive-mode tests: crack identity wiring, vendored unstub
import surface, gse helpers that are exercisable without hitting Steam /
GitHub / a real DLL.

The actual SteamStub unpacking is delegated to vendored binary-surgery code
that needs real protected .exe samples to test meaningfully — that's a
manual smoke gate, not CI work.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path
from unittest import mock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Vendored unstub tree imports cleanly + registers all four variants
# ---------------------------------------------------------------------------

def test_unstub_unpackers_register():
    from src.core.archive.crack.unstub.unpackers import get_unpackers
    classes = get_unpackers()
    names = sorted(c.__name__ for c in classes)
    assert names == [
        "Variant20Unpacker", "Variant21Unpacker",
        "Variant30Unpacker", "Variant31Unpacker",
    ]


def test_unstub_base_unpacker_importable():
    """Sanity check that the vendored base class imports without dragging in
    anything from the SteamArchiver package."""
    from src.core.archive.crack.unstub.base_unpacker import BaseUnpacker
    assert hasattr(BaseUnpacker, "process")
    assert hasattr(BaseUnpacker, "can_process")


# ---------------------------------------------------------------------------
# gse — pure helpers (no network)
# ---------------------------------------------------------------------------

def test_is_linux_so():
    from src.core.archive.crack.gse import is_linux_so
    assert is_linux_so(Path("libsteam_api.so"))
    assert not is_linux_so(Path("steam_api.dll"))
    assert not is_linux_so(Path("steam_api64.dll"))


def test_detect_binary_arch_pe_x64(tmp_path):
    from src.core.archive.crack.gse import detect_binary_arch
    # Minimal MZ + e_lfanew + PE\0\0 + Machine = 0x8664 (AMD64)
    pe = tmp_path / "fake.dll"
    buf = bytearray(0x100)
    buf[0:2] = b"MZ"
    pe_offset = 0x80
    struct.pack_into("<I", buf, 0x3C, pe_offset)
    struct.pack_into("<4sH", buf, pe_offset, b"PE\x00\x00", 0x8664)
    pe.write_bytes(bytes(buf))
    assert detect_binary_arch(pe) == "x64"


def test_detect_binary_arch_pe_x32(tmp_path):
    from src.core.archive.crack.gse import detect_binary_arch
    pe = tmp_path / "fake.dll"
    buf = bytearray(0x100)
    buf[0:2] = b"MZ"
    pe_offset = 0x80
    struct.pack_into("<I", buf, 0x3C, pe_offset)
    struct.pack_into("<4sH", buf, pe_offset, b"PE\x00\x00", 0x014C)
    pe.write_bytes(bytes(buf))
    assert detect_binary_arch(pe) == "x32"


def test_detect_binary_arch_elf64(tmp_path):
    from src.core.archive.crack.gse import detect_binary_arch
    so = tmp_path / "libsteam_api.so"
    so.write_bytes(b"\x7fELF\x02" + b"\x00" * 64)
    assert detect_binary_arch(so) == "x64"


def test_detect_binary_arch_elf32(tmp_path):
    from src.core.archive.crack.gse import detect_binary_arch
    so = tmp_path / "libsteam_api.so"
    so.write_bytes(b"\x7fELF\x01" + b"\x00" * 64)
    assert detect_binary_arch(so) == "x32"


def test_extract_steam_interfaces_finds_versions(tmp_path):
    from src.core.archive.crack.gse import extract_steam_interfaces
    dll = tmp_path / "steam_api64.dll"
    payload = (
        b"random padding "
        b"SteamUser023"             b"\x00"
        b"STEAMAPPS_INTERFACE_VERSION008" b"\x00"
        b"more padding"
    )
    dll.write_bytes(payload)
    result = extract_steam_interfaces(dll)
    assert "SteamUser023" in result
    assert "STEAMAPPS_INTERFACE_VERSION008" in result


def test_find_steam_apis_priority(tmp_path):
    """find_steam_apis prefers steam_api64.dll over steam_api.dll over .so,
    and ranks closer-to-root copies first within each kind."""
    from src.core.archive.crack.gse import find_steam_apis
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    shallow = tmp_path / "shallow.dll"   # not a valid match — wrong name
    (deep / "steam_api.dll").write_bytes(b"\x00")
    (tmp_path / "steam_api64.dll").write_bytes(b"\x00")
    (deep / "libsteam_api.so").write_bytes(b"\x00")
    shallow.write_bytes(b"\x00")
    result = find_steam_apis(tmp_path)
    # steam_api64 first, then steam_api.dll, then libsteam_api.so
    names_only = [p.name for p in result]
    assert names_only.index("steam_api64.dll") < names_only.index("steam_api.dll")
    assert names_only.index("steam_api.dll") < names_only.index("libsteam_api.so")


def test_has_lan_multiplayer_yes():
    from src.core.archive.crack.gse import _has_lan_multiplayer
    app_data = {"common": {"categories": {
        "0": {"categoryid": 1}, "1": {"categoryid": 7},
    }}}
    assert _has_lan_multiplayer(app_data) is True


def test_has_lan_multiplayer_no():
    from src.core.archive.crack.gse import _has_lan_multiplayer
    app_data = {"common": {"categories": {
        "0": {"categoryid": 2}, "1": {"categoryid": 9},
    }}}
    assert _has_lan_multiplayer(app_data) is False


# ---------------------------------------------------------------------------
# DLC name database
# ---------------------------------------------------------------------------

def _redirect_dlc_db(tmp_path):
    from src.core.archive import utils
    return mock.patch.object(utils, "dlc_db_dir", lambda: tmp_path / "dlc_db")


def test_db_save_load_roundtrip(tmp_path):
    from src.core.archive.crack.gse import db_load, db_save
    with _redirect_dlc_db(tmp_path):
        db_save("730", {"731": "Counter-Strike DLC", "732": "Map Pack"})
        loaded = db_load("730")
        assert loaded == {"731": "Counter-Strike DLC", "732": "Map Pack"}


def test_db_save_merges_with_existing(tmp_path):
    from src.core.archive.crack.gse import db_load, db_save
    with _redirect_dlc_db(tmp_path):
        db_save("730", {"731": "Old name"})
        db_save("730", {"731": "New name", "732": "Brand new"})
        assert db_load("730") == {"731": "New name", "732": "Brand new"}


def test_db_load_missing_returns_empty(tmp_path):
    from src.core.archive.crack.gse import db_load
    with _redirect_dlc_db(tmp_path):
        assert db_load("nonexistent") == {}


# ---------------------------------------------------------------------------
# Web API key resolution
# ---------------------------------------------------------------------------

def _redirect_credentials(tmp_path):
    from src.core.archive import credentials as cm
    fake = tmp_path / "archive_credentials.json"
    return mock.patch.object(cm, "_CREDENTIALS_FILE", fake)


def test_resolve_api_key_uses_saved_value(tmp_path):
    from src.core.archive import credentials as cm
    from src.core.archive.crack.gse import _resolve_api_key
    with _redirect_credentials(tmp_path):
        cm.save(cm.Credentials(web_api_key="ABCDEF1234567890"))
        assert _resolve_api_key() == "ABCDEF1234567890"


def test_resolve_api_key_prompts_and_saves_when_missing(tmp_path, monkeypatch):
    from src.core.archive import credentials as cm
    from src.core.archive.crack import gse
    with _redirect_credentials(tmp_path):
        monkeypatch.setattr("builtins.input", lambda *a, **kw: "NEWKEY42")
        key = gse._resolve_api_key()
        assert key == "NEWKEY42"
        # Saved to disk so subsequent runs don't re-prompt.
        assert cm.load().web_api_key == "NEWKEY42"


def test_resolve_api_key_blank_input_returns_empty(tmp_path, monkeypatch):
    from src.core.archive import credentials as cm
    from src.core.archive.crack import gse
    with _redirect_credentials(tmp_path):
        monkeypatch.setattr("builtins.input", lambda *a, **kw: "")
        assert gse._resolve_api_key() == ""
        # Empty input must NOT clobber any other credential the user has set.
        assert cm.load().web_api_key == ""


# ---------------------------------------------------------------------------
# CrackIdentity prompting
# ---------------------------------------------------------------------------

def test_resolve_user_config_uses_filled_identity(tmp_path):
    from src.core.archive.crack.gse import _resolve_user_config
    from src.core.archive.project import CrackIdentity
    identity = CrackIdentity(
        steam_id=76561198000000000, username="alice",
        language="english", listen_port=47584,
    )
    cfg = _resolve_user_config(identity, ["english", "french"], has_lan=True)
    assert cfg["steam_id"]    == "76561198000000000"
    assert cfg["username"]    == "alice"
    assert cfg["language"]    == "english"
    assert cfg["listen_port"] == "47584"


def test_resolve_user_config_prompts_for_missing_username(monkeypatch):
    from src.core.archive.crack.gse import _resolve_user_config
    from src.core.archive.project import CrackIdentity
    identity = CrackIdentity(
        steam_id=76561198000000000, username="",
        language="english", listen_port=47584,
    )
    answers = iter(["bob"])
    monkeypatch.setattr("builtins.input", lambda *a, **kw: next(answers))
    cfg = _resolve_user_config(identity, ["english"], has_lan=True)
    assert identity.username == "bob"
    assert cfg["username"] == "bob"


def test_resolve_user_config_skips_listen_port_without_lan():
    from src.core.archive.crack.gse import _resolve_user_config
    from src.core.archive.project import CrackIdentity
    identity = CrackIdentity(
        steam_id=76561198000000000, username="alice",
        language="english", listen_port=47584,
    )
    cfg = _resolve_user_config(identity, ["english"], has_lan=False)
    assert cfg["listen_port"] is None


# ---------------------------------------------------------------------------
# parse_size sanity (for --volume-size)  — covered by phase 2; skipped here
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# download_app crack plumbing
# ---------------------------------------------------------------------------

def test_download_app_forwards_crack_identity_on_single_platform(monkeypatch):
    """Regression: download_app's single-platform branch (the default
    --platform != 'all' path) must forward crack_identity to
    _download_platform.  When this kwarg goes missing, every download
    with --crack fails up front with 'crack=... requires crack_identity'."""
    from src.core.archive import download as dl_mod
    from src.core.archive.project import CrackIdentity

    captured: dict = {}

    def fake_download_platform(*args, **kwargs):
        # crack is the 12th positional arg (index 11) in download.py's
        # call, while crack_identity is passed as a keyword.  Check both
        # paths since the bug we're guarding against is "kwarg silently
        # dropped on the single-platform branch".
        captured["crack"]          = args[11] if len(args) > 11 else kwargs.get("crack")
        captured["crack_identity"] = kwargs.get("crack_identity")
        return [], []

    monkeypatch.setattr(dl_mod, "_download_platform", fake_download_platform)

    # Stub _import_steam so download_app gets past the gevent monkey-patch
    # call.  Only GeventTimeout is unpacked at top-level — supply Exception
    # so the retry loop's `except GeventTimeout` clause is well-formed.
    monkeypatch.setattr(
        dl_mod, "_import_steam",
        lambda: (object(), Exception, object(), object(), object(), Exception),
    )

    fake_client = mock.Mock()
    fake_client.get_product_info.return_value = {
        "apps": {
            730: {
                "common": {"name": "T", "oslist": "windows"},
                "config": {"installdir": "T"},
                "depots": {"branches": {"public": {"buildid": "1"}}},
            },
        },
    }

    identity = CrackIdentity(steam_id=42, username="alice")
    dl_mod.download_app(
        fake_client, mock.Mock(), 730, Path("/tmp/x"),
        platform="windows",
        crack="gse",
        crack_identity=identity,
    )
    assert captured["crack"] == "gse"
    assert captured["crack_identity"] is identity


def test_download_platform_known_crack_modes_pass_validation_gate(monkeypatch):
    """When --crack is set and crack_identity is provided, the up-front
    validation in _download_platform must NOT raise the
    'crack_identity required' ValueError.  We mock _import_steam so we
    don't actually pull in steam[client] (which monkey-patches gevent
    and noises the test logs)."""
    from src.core.archive import download as dl_mod
    from src.core.archive.project import CrackIdentity

    # Stub _import_steam so the function bails on its first real use of
    # the returned tuple instead of dragging in gevent.
    monkeypatch.setattr(
        dl_mod, "_import_steam",
        lambda: (object(), Exception, object(), object(), object(), Exception),
    )

    # crack="gse" + crack_identity supplied -> validation gate passes.
    # Function will then crash later when it tries to use the stub objects;
    # any exception type other than "crack_identity required" is fine.
    with pytest.raises(Exception) as exc_info:
        dl_mod._download_platform(
            cdn=None, client=None, app_id=730, app_data={},
            dest=Path("/tmp/x"), platform="windows",
            crack="gse",
            crack_identity=CrackIdentity(),
        )
    assert "crack_identity" not in str(exc_info.value)
