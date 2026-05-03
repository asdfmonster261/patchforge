"""archive.acf — Steam .acf VDF emit + manifest aggregation.

The CDNClient's manifest objects only expose a handful of attributes
the .acf builder reads (depot_id, gid, metadata.cb_disk_*).  Tests use
a lightweight `_FakeManifest` so we don't pull in steam[client] just
to run a unit test.
"""
from __future__ import annotations

from unittest import mock


class _FakeManifest:
    """Stand-in for steam[client]'s manifest object — only carries the
    attributes the acf builder actually reads."""
    def __init__(self, depot_id, gid, cb_disk_original, cb_disk_compressed):
        self.depot_id = depot_id
        self.gid      = gid
        self.metadata = mock.Mock(
            cb_disk_original=cb_disk_original,
            cb_disk_compressed=cb_disk_compressed,
        )


# ---------------------------------------------------------------------------
# write_acf — VDF emit shape
# ---------------------------------------------------------------------------

def test_acf_vdf_dumps_basic_shape(tmp_path):
    from src.core.archive.acf import write_acf
    out = tmp_path / "test.acf"
    write_acf(out, {
        "appid": "730", "name": "Test",
        "InstalledDepots": {"731": {"manifest": "abc"}},
    })
    text = out.read_text()
    assert text.startswith('"AppState"\n{\n')
    assert text.rstrip().endswith("}")
    assert '"appid"' in text and '"730"' in text
    assert '"name"' in text and '"Test"' in text
    # Nested dict gets its own VDF block.
    assert '"InstalledDepots"' in text
    assert '"731"' in text and '"manifest"' in text and '"abc"' in text


# ---------------------------------------------------------------------------
# build_app_acf
# ---------------------------------------------------------------------------

def test_acf_build_app_acf_minimal():
    from src.core.archive.acf import build_app_acf
    app_data = {
        "common": {"name": "Game"},
        "config": {"installdir": "Game"},
        "depots": {"branches": {"public": {"buildid": "12345"}}},
    }
    manifests = [_FakeManifest(731, "g1", 100, 50)]
    acf = build_app_acf(730, app_data, manifests, {"731": {}})
    assert acf["appid"]      == "730"
    assert acf["name"]       == "Game"
    assert acf["installdir"] == "Game"
    assert acf["buildid"]    == "12345"
    assert acf["SizeOnDisk"]      == "100"
    assert acf["BytesToDownload"] == "50"
    assert acf["InstalledDepots"]["731"]["manifest"] == "g1"
    assert acf["InstalledDepots"]["731"]["size"]     == "100"
    assert acf["DownloadType"] == "0"   # no DLC


def test_acf_build_app_acf_with_dlc():
    from src.core.archive.acf import build_app_acf
    app_data = {
        "common": {"name": "Game"},
        "config": {"installdir": "Game"},
        "depots": {"branches": {"public": {"buildid": "12345"}}},
    }
    base   = [_FakeManifest(731, "g1", 100, 50)]
    dlc_ms = [_FakeManifest(900, "d1",  20, 10)]
    acf = build_app_acf(730, app_data, base, {"731": {}, "900": {}},
                        dlc_data=[(800, dlc_ms)])
    # Both base and DLC depots are folded into InstalledDepots.
    assert "731" in acf["InstalledDepots"]
    assert "900" in acf["InstalledDepots"]
    assert acf["InstalledDepots"]["900"]["dlcappid"] == "800"
    # SizeOnDisk includes DLC.
    assert acf["SizeOnDisk"] == "120"
    # DownloadType bumps to 3 when DLC depots are present.
    assert acf["DownloadType"] == "3"


def test_acf_build_app_acf_includes_shared_depots():
    from src.core.archive.acf import build_app_acf
    app_data = {
        "common": {"name": "Game"},
        "config": {"installdir": "Game"},
        "depots": {"branches": {"public": {"buildid": "12345"}}},
    }
    base = [_FakeManifest(731, "g1", 100, 50)]
    depots_info = {"731": {}, "228984": {"depotfromapp": "228980"}}
    acf = build_app_acf(730, app_data, base, depots_info)
    assert "SharedDepots" in acf
    assert acf["SharedDepots"]["228984"] == "228980"


# ---------------------------------------------------------------------------
# build_shared_acf — Steamworks Common etc.
# ---------------------------------------------------------------------------

def test_acf_build_shared_acf():
    from src.core.archive.acf import build_shared_acf
    app_data = {
        "common": {"name": "Steamworks Common"},
        "config": {"installdir": "Steamworks Common"},
        "depots": {"branches": {"public": {"buildid": "0"}}},
    }
    manifests = [_FakeManifest(228984, "abc", 50, 25)]
    acf = build_shared_acf(228980, app_data, manifests, {})
    assert acf["appid"]        == "228980"
    assert acf["DownloadType"] == "0"
    assert acf["InstalledDepots"]["228984"]["manifest"] == "abc"
