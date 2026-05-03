"""archive.appinfo — wrapper around SteamClient.get_product_info that
streams responses per-app instead of buffering the whole batch.
"""
from __future__ import annotations

from unittest import mock


def test_query_app_info_batch_quiet_does_not_print(capsys, monkeypatch):
    """quiet=True is the polling path: emit only the structured info
    dict, no per-app summary, no licensed_app_ids fetch (that probe is
    expensive and only useful in the human-readable branch)."""
    from src.core.archive import appinfo

    def fake_stream(client, app_ids, max_retries, timeout=15):
        for aid in app_ids:
            yield aid, {
                "common":  {"name": "Q", "oslist": ""},
                "config":  {"installdir": "Q"},
                "depots":  {"branches": {"public": {"buildid": "9",
                                                    "timeupdated": 7}}},
            }
    monkeypatch.setattr(appinfo, "_streaming_product_info", fake_stream)

    fake_client = mock.Mock()
    fake_cdn = mock.Mock()
    type(fake_cdn).licensed_app_ids = mock.PropertyMock(side_effect=AssertionError(
        "quiet=True must not request licensed_app_ids"
    ))

    rows = list(appinfo.query_app_info_batch(fake_client, fake_cdn, [42],
                                             quiet=True))
    captured = capsys.readouterr()
    assert captured.out == ""             # no per-app summary
    assert rows == [(42, {
        "name": "Q", "buildid": "9", "oslist": "",
        "timeupdated": 7, "installdir": "Q",
    })]
