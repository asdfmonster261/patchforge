"""archive.download — depot downloader.

Most of download.py's surface needs live Steam state (manifests, CDN,
chunk fetches).  Unit tests here cover the offline-checkable bits:
the crack-pipeline input-validation guard and the crack_identity
plumbing through download_app's branches.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest


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
