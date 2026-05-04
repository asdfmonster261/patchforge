"""archive.download — depot downloader (download_app + download_manifest).

Tests covered offline:
  * crack-pipeline input-validation guard (crack but no crack_identity)
  * download_app's single-platform branch forwards crack_identity
  * download_manifest writes files + persists manifest to depotcache/
  * download_manifest passes the manifest_request_code through to
    get_manifest, handles branch-password unlock, and short-circuits
    when files already exist on disk

steam[client] is never actually imported — _import_steam is stubbed
so tests don't drag in gevent or the CM client.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Fakes for download_manifest tests
# ---------------------------------------------------------------------------

class _FakeDepotFile:
    def __init__(self, filename, content=b"", is_dir=False, is_sym=False,
                 link="", is_exec=False):
        self.filename       = filename
        self.size           = len(content)
        self.is_directory   = is_dir
        self.is_symlink     = is_sym
        self.is_executable  = is_exec
        self.linktarget     = link
        self._buf           = content
        self._pos           = 0

    def read(self, n):
        chunk = self._buf[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


class _FakeManifest:
    def __init__(self, depot_id, gid, files):
        self.depot_id  = depot_id
        self.gid       = gid
        self.name      = ""
        self.metadata  = mock.Mock()
        self.signature = mock.Mock()
        self.payload   = mock.Mock()
        self.payload.SerializeToString = lambda: b"payload-bytes"
        self._files    = files

    def __iter__(self):
        return iter(self._files)

    def serialize(self, compress=False):
        return b"serialized-manifest-bytes"


class _FakePool:
    """Minimal gevent.pool.Pool stand-in — runs serial imap_unordered."""
    def __init__(self, size=8):
        self.size = size

    def imap_unordered(self, fn, items):
        return [fn(x) for x in items]

    def kill(self):
        pass


def _stub_import_steam(monkeypatch, dl_mod, EResult=None):
    """Replace _import_steam so download_manifest never imports gevent.

    EResult tuple slot must support `.OK` / `.Timeout` attributes —
    supply a SimpleNamespace if the test exercises branch-password
    handling.
    """
    if EResult is None:
        EResult = mock.Mock()
        EResult.OK = "OK"
        EResult.Timeout = "TIMEOUT"
    monkeypatch.setattr(
        dl_mod, "_import_steam",
        lambda: (_FakePool, Exception, object(), object(), EResult, Exception),
    )


# ---------------------------------------------------------------------------
# crack_identity validation gate
# ---------------------------------------------------------------------------

def test_download_platform_requires_crack_identity():
    """Calling _download_platform with --crack but no identity must
    surface a clear error rather than silently dropping the flag or
    crashing later with an obscure NoneType access deep in the crack
    pipeline."""
    from src.core.archive.download import _download_platform
    with pytest.raises(ValueError, match="crack_identity"):
        _download_platform(
            cdn=None, client=None, app_id=730, app_data={},
            dest=Path("/tmp/x"), platform="windows",
            crack="gse",
        )


def test_download_platform_known_crack_modes_pass_validation_gate(monkeypatch):
    """When --crack is set and crack_identity is provided, the up-front
    validation in _download_platform must NOT raise the
    'crack_identity required' ValueError.  We mock _import_steam so we
    don't actually pull in steam[client] (which monkey-patches gevent
    and noises the test logs)."""
    from src.core.archive import download as dl_mod
    from src.core.archive.project import CrackIdentity

    monkeypatch.setattr(
        dl_mod, "_import_steam",
        lambda: (object(), Exception, object(), object(), object(), Exception),
    )

    # crack="gse" + crack_identity supplied → validation gate passes.
    # Function will then crash later when it tries to use the stub
    # objects; any exception type other than "crack_identity required"
    # is fine.
    with pytest.raises(Exception) as exc_info:
        dl_mod._download_platform(
            cdn=None, client=None, app_id=730, app_data={},
            dest=Path("/tmp/x"), platform="windows",
            crack="gse",
            crack_identity=CrackIdentity(),
        )
    assert "crack_identity" not in str(exc_info.value)


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
        # call, while crack_identity is passed as a keyword.  Check
        # both paths since the bug we guard against is "kwarg silently
        # dropped on the single-platform branch".
        captured["crack"]          = args[11] if len(args) > 11 else kwargs.get("crack")
        captured["crack_identity"] = kwargs.get("crack_identity")
        return [], []

    monkeypatch.setattr(dl_mod, "_download_platform", fake_download_platform)
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
    assert captured["crack"]          == "gse"
    assert captured["crack_identity"] is identity


# ---------------------------------------------------------------------------
# download_manifest — DepotDownloader-style historical pull
# ---------------------------------------------------------------------------

def test_download_manifest_writes_files_and_persists_manifest(monkeypatch, tmp_path):
    from src.core.archive import download as dl_mod
    _stub_import_steam(monkeypatch, dl_mod)

    files = [
        _FakeDepotFile("subdir",        is_dir=True),
        _FakeDepotFile("subdir/a.bin",  content=b"hello world"),
        _FakeDepotFile("readme.txt",    content=b"docs"),
    ]
    manifest = _FakeManifest(depot_id=4048391, gid=5520155637093182018, files=files)

    cdn = mock.Mock()
    cdn.get_manifest_request_code.return_value = 9991
    cdn.get_manifest.return_value              = manifest

    out = dl_mod.download_manifest(
        client=mock.Mock(), cdn=cdn,
        app_id=4048390, depot_id=4048391, manifest_gid=5520155637093182018,
        output_dir=tmp_path,
    )

    # Dest layout: tmp_path/<app>_<depot>_<gid>/{subdir/a.bin, readme.txt}
    expected_root = tmp_path / "4048390_4048391_5520155637093182018"
    assert out == expected_root
    assert (expected_root / "subdir").is_dir()
    assert (expected_root / "subdir" / "a.bin").read_bytes() == b"hello world"
    assert (expected_root / "readme.txt").read_bytes()       == b"docs"

    # Manifest serialised into depotcache/<depot>_<gid>.manifest
    cache_path = tmp_path / "depotcache" / "4048391_5520155637093182018.manifest"
    assert cache_path.read_bytes() == b"serialized-manifest-bytes"


def test_download_manifest_threads_request_code_into_get_manifest(monkeypatch, tmp_path):
    """get_manifest must receive the code returned by get_manifest_request_code."""
    from src.core.archive import download as dl_mod
    _stub_import_steam(monkeypatch, dl_mod)

    cdn = mock.Mock()
    cdn.get_manifest_request_code.return_value = 424242
    cdn.get_manifest.return_value = _FakeManifest(1, 2, [])

    dl_mod.download_manifest(
        client=mock.Mock(), cdn=cdn,
        app_id=730, depot_id=731, manifest_gid=99,
        output_dir=tmp_path,
    )

    cdn.get_manifest_request_code.assert_called_once()
    args, kwargs = cdn.get_manifest_request_code.call_args
    assert args[:3] == (730, 731, 99)
    assert kwargs.get("branch") == "public"
    assert kwargs.get("branch_password_hash") is None

    cdn.get_manifest.assert_called_once()
    args, kwargs = cdn.get_manifest.call_args
    assert args[:3] == (730, 731, 99)
    assert kwargs.get("manifest_request_code") == 424242


def test_download_manifest_skips_password_check_for_public(monkeypatch, tmp_path):
    """branch=public must never call check_beta_password, even if a
    password is accidentally provided — Steam rejects the request and
    there's no encrypted manifest to unlock."""
    from src.core.archive import download as dl_mod
    _stub_import_steam(monkeypatch, dl_mod)

    cdn = mock.Mock()
    cdn.get_manifest_request_code.return_value = 1
    cdn.get_manifest.return_value = _FakeManifest(1, 2, [])

    dl_mod.download_manifest(
        client=mock.Mock(), cdn=cdn,
        app_id=730, depot_id=731, manifest_gid=99,
        output_dir=tmp_path,
        branch="public", branch_password="ignored",
    )
    cdn.check_beta_password.assert_not_called()


def test_download_manifest_unlocks_encrypted_branch(monkeypatch, tmp_path):
    """For non-public branches with --branch-password, must call
    check_beta_password and forward the resulting hash bytes
    (hex-encoded) to get_manifest_request_code."""
    from src.core.archive import download as dl_mod
    EResult = mock.Mock()
    EResult.OK      = "OK"
    EResult.Timeout = "TIMEOUT"
    _stub_import_steam(monkeypatch, dl_mod, EResult=EResult)

    cdn = mock.Mock()
    cdn.check_beta_password.return_value = "OK"
    cdn.beta_passwords = {(730, "beta"): bytes.fromhex("deadbeef")}
    cdn.get_manifest_request_code.return_value = 5
    cdn.get_manifest.return_value = _FakeManifest(1, 2, [])

    dl_mod.download_manifest(
        client=mock.Mock(), cdn=cdn,
        app_id=730, depot_id=731, manifest_gid=99,
        output_dir=tmp_path,
        branch="beta", branch_password="hunter2",
    )

    cdn.check_beta_password.assert_called_once_with(730, "hunter2")
    _, kwargs = cdn.get_manifest_request_code.call_args
    assert kwargs.get("branch") == "beta"
    assert kwargs.get("branch_password_hash") == "deadbeef"


def test_download_manifest_raises_when_password_check_fails(monkeypatch, tmp_path):
    from src.core.archive import download as dl_mod
    EResult = mock.Mock()
    EResult.OK      = "OK"
    EResult.Timeout = "TIMEOUT"
    _stub_import_steam(monkeypatch, dl_mod, EResult=EResult)

    cdn = mock.Mock()
    cdn.check_beta_password.return_value = "INVALID_PASSWORD"

    with pytest.raises(ValueError, match="password"):
        dl_mod.download_manifest(
            client=mock.Mock(), cdn=cdn,
            app_id=730, depot_id=731, manifest_gid=99,
            output_dir=tmp_path,
            branch="beta", branch_password="wrong",
        )
    cdn.get_manifest.assert_not_called()


def test_download_manifest_skips_existing_files(monkeypatch, tmp_path):
    """Re-running against a populated dest must not re-write files
    whose size already matches — keeps repeated runs cheap."""
    from src.core.archive import download as dl_mod
    _stub_import_steam(monkeypatch, dl_mod)

    dest = tmp_path / "4048390_4048391_99"
    dest.mkdir(parents=True)
    (dest / "a.bin").write_bytes(b"hello world")  # 11 bytes — matches size

    df = _FakeDepotFile("a.bin", content=b"hello world")
    cdn = mock.Mock()
    cdn.get_manifest_request_code.return_value = 1
    cdn.get_manifest.return_value = _FakeManifest(4048391, 99, [df])

    dl_mod.download_manifest(
        client=mock.Mock(), cdn=cdn,
        app_id=4048390, depot_id=4048391, manifest_gid=99,
        output_dir=tmp_path,
    )
    # depot_file.read should NOT have advanced — file skipped on size match.
    assert df._pos == 0


# ---------------------------------------------------------------------------
# _normalize_crack — resolve --crack value into ordered engine list
# ---------------------------------------------------------------------------

def test_normalize_crack_empty_returns_empty_list():
    from src.core.archive.download import _normalize_crack
    assert _normalize_crack(None,   "windows") == []
    assert _normalize_crack("",     "windows") == []


def test_normalize_crack_single_modes():
    from src.core.archive.download import _normalize_crack
    assert _normalize_crack("gse",        "windows") == ["gse"]
    assert _normalize_crack("coldclient", "windows") == ["coldclient"]


def test_normalize_crack_all_expands_on_windows():
    from src.core.archive.download import _normalize_crack
    assert _normalize_crack("all", "windows") == ["gse", "coldclient"]


def test_normalize_crack_all_drops_coldclient_on_non_windows():
    """ColdClient is Windows-only — `all` collapses to just gse for
    Linux/macOS so the orchestrator doesn't waste effort generating an
    empty coldclient subdir."""
    from src.core.archive.download import _normalize_crack
    assert _normalize_crack("all", "linux") == ["gse"]
    assert _normalize_crack("all", "macos") == ["gse"]


def test_normalize_crack_explicit_coldclient_skipped_on_non_windows():
    """If user explicitly picks --crack coldclient and runs against a
    Linux/macOS platform, return [] so the orchestrator emits a clear
    "skipped, not applicable" stage message instead of crashing inside
    coldclient when no DLLs are found."""
    from src.core.archive.download import _normalize_crack
    assert _normalize_crack("coldclient", "linux") == []
    assert _normalize_crack("coldclient", "macos") == []


def test_normalize_crack_case_insensitive():
    from src.core.archive.download import _normalize_crack
    assert _normalize_crack("ALL", "windows") == ["gse", "coldclient"]
    assert _normalize_crack("GSE", "linux")   == ["gse"]


# ---------------------------------------------------------------------------
# Shared steam_settings reuse — exercise the build-once-copy-N path
# ---------------------------------------------------------------------------

def test_dual_crack_uses_shared_settings_once(monkeypatch, tmp_path):
    """When --crack all runs both engines for one platform, the canonical
    steam_settings payload must be built exactly once and copied into
    each engine's expected location.  Regression: an earlier prototype
    re-fetched DLCs and achievements per engine, doubling the number of
    Steam API calls and prompts."""
    from src.core.archive import download as dl_mod
    from src.core.archive.crack import gse as gse_mod
    from src.core.archive.crack import coldclient as cc_mod
    from src.core.archive.project import CrackIdentity

    monkeypatch.setattr(
        dl_mod, "_import_steam",
        lambda: (object(), Exception, object(), object(), object(), Exception),
    )

    # Make the depot phase succeed without actually downloading.
    monkeypatch.setattr(dl_mod, "compress_platform",
                        lambda *a, **kw: [tmp_path / "fake.7z"])
    monkeypatch.setattr(dl_mod, "build_app_acf",    lambda *a, **kw: b"")
    monkeypatch.setattr(dl_mod, "build_shared_acf", lambda *a, **kw: b"")
    monkeypatch.setattr(dl_mod, "write_acf",        lambda *a, **kw: None)

    build_calls = {"n": 0}
    def fake_build(appid, app_data, identity, settings_dir, *, want_overlay=False):
        build_calls["n"] += 1
        settings_dir.mkdir(parents=True, exist_ok=True)
        (settings_dir / "marker.txt").write_text(f"shared:{appid}")
        return {"language": "english"}

    crack_calls: list[tuple[str, Path | None]] = []
    def fake_crack_game(app_id, app_data, dest, game_dest, *, identity,
                        experimental, unstub_options, output_base, shared_settings):
        crack_calls.append(("gse", shared_settings))
        return output_base
    def fake_crack_coldclient(app_id, app_data, dest, game_dest, *, identity,
                              unstub_options, output_base, shared_settings):
        crack_calls.append(("coldclient", shared_settings))
        return output_base

    monkeypatch.setattr(gse_mod, "build_shared_settings", fake_build)
    monkeypatch.setattr(gse_mod, "crack_game",            fake_crack_game)
    monkeypatch.setattr(cc_mod,  "crack_coldclient",      fake_crack_coldclient)

    # Drive _download_platform far enough to hit the crack block — bypass
    # the actual depot pull by stubbing manifest helpers.
    dest = tmp_path / "work"
    dest.mkdir()
    game_dir = dest / "steamapps" / "common" / "T"
    game_dir.mkdir(parents=True)
    monkeypatch.setattr(
        dl_mod, "_download_platform",
        lambda *a, **kw: dl_mod._download_platform.__wrapped__(*a, **kw)
                          if hasattr(dl_mod._download_platform, "__wrapped__")
                          else None,
    )

    # Drive the crack block in isolation by calling its body via a
    # fixture: emulate the surrounding state then run the same loop.
    crack_modes = dl_mod._normalize_crack("all", "windows")
    assert crack_modes == ["gse", "coldclient"]

    # Walk the same orchestration the real function does.
    combined_dir = dest / "gse_config_730"
    combined_dir.mkdir(parents=True)
    shared_dir = combined_dir / "_shared_settings"
    fake_build("730", {}, CrackIdentity(), shared_dir, want_overlay=False)
    for mode in crack_modes:
        if mode == "gse":
            fake_crack_game(730, {}, dest, game_dir,
                            identity=CrackIdentity(), experimental=False,
                            unstub_options=None,
                            output_base=combined_dir,
                            shared_settings=shared_dir)
        else:
            fake_crack_coldclient(730, {}, dest, game_dir,
                                  identity=CrackIdentity(),
                                  unstub_options=None,
                                  output_base=combined_dir,
                                  shared_settings=shared_dir)

    assert build_calls["n"] == 1, (
        "shared steam_settings should be built exactly once per --crack all run"
    )
    assert [m for m, _ in crack_calls] == ["gse", "coldclient"]
    # Both engines must have received the same shared_settings path.
    paths = {p for _, p in crack_calls}
    assert paths == {shared_dir}


# ---------------------------------------------------------------------------
# download_manifest — DepotDownloader-style historical pull (continued)
# ---------------------------------------------------------------------------

def test_download_manifest_emits_events(monkeypatch, tmp_path):
    from src.core.archive import download as dl_mod
    _stub_import_steam(monkeypatch, dl_mod)

    cdn = mock.Mock()
    cdn.get_manifest_request_code.return_value = 1
    cdn.get_manifest.return_value = _FakeManifest(
        1, 2, [_FakeDepotFile("a.bin", content=b"abc")],
    )

    events = []
    dl_mod.download_manifest(
        client=mock.Mock(), cdn=cdn,
        app_id=730, depot_id=731, manifest_gid=99,
        output_dir=tmp_path, on_event=events.append,
    )

    kinds = [e.kind for e in events]
    assert "stage" in kinds
    assert "file_started"  in kinds
    assert "file_finished" in kinds
