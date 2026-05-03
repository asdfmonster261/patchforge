"""archive.crack.gse — Goldberg Steam Emulator pure-helper coverage.

Network-touching paths (the actual emu deploy + DLL surgery) need real
protected binaries and live Steamworks API calls — manual smoke only.
Tests here exercise the offline pieces: arch detection, interface
extraction, DLL location heuristics, DLC name DB roundtrip, and the
prompt-for-config helpers.
"""
from __future__ import annotations

import struct
from pathlib import Path
from unittest import mock


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _redirect_dlc_db(tmp_path):
    from src.core.archive import utils
    return mock.patch.object(utils, "dlc_db_dir", lambda: tmp_path / "dlc_db")


def _redirect_credentials(tmp_path):
    from src.core.archive import credentials as cm
    fake = tmp_path / "archive_credentials.json"
    return mock.patch.object(cm, "_CREDENTIALS_FILE", fake)


# ---------------------------------------------------------------------------
# is_linux_so + detect_binary_arch
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


# ---------------------------------------------------------------------------
# extract_steam_interfaces + find_steam_apis
# ---------------------------------------------------------------------------

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
    """find_steam_apis prefers steam_api64.dll over steam_api.dll over
    .so, and ranks closer-to-root copies first within each kind."""
    from src.core.archive.crack.gse import find_steam_apis
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    (deep / "steam_api.dll").write_bytes(b"\x00")
    (tmp_path / "steam_api64.dll").write_bytes(b"\x00")
    (deep / "libsteam_api.so").write_bytes(b"\x00")
    (tmp_path / "shallow.dll").write_bytes(b"\x00")  # unrelated
    result = find_steam_apis(tmp_path)
    names_only = [p.name for p in result]
    assert names_only.index("steam_api64.dll") < names_only.index("steam_api.dll")
    assert names_only.index("steam_api.dll")   < names_only.index("libsteam_api.so")


# ---------------------------------------------------------------------------
# _has_lan_multiplayer
# ---------------------------------------------------------------------------

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
# Web API key resolution (gse._resolve_api_key)
# ---------------------------------------------------------------------------

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
# CrackIdentity prompting (_resolve_user_config)
# ---------------------------------------------------------------------------

def test_resolve_user_config_uses_filled_identity():
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
    assert cfg["username"]   == "bob"


def test_resolve_user_config_skips_listen_port_without_lan():
    from src.core.archive.crack.gse import _resolve_user_config
    from src.core.archive.project import CrackIdentity
    identity = CrackIdentity(
        steam_id=76561198000000000, username="alice",
        language="english", listen_port=47584,
    )
    cfg = _resolve_user_config(identity, ["english"], has_lan=False)
    assert cfg["listen_port"] is None
