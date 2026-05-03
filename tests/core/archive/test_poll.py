"""archive.poll — buildid-change detection driver.

Stubs `query_app_info_batch` so tests don't touch the network or
steam[client].  Each test seeds an apps_by_id dict, plugs in fake
product-info responses, and asserts which (app_id, prev, curr, info)
tuples come back.
"""
from __future__ import annotations

from unittest import mock


def _stub_qaib(infos: dict):
    """Return a fake query_app_info_batch that yields preset infos."""
    def fake(client, cdn, app_ids, max_retries=1, batch_size=None, quiet=False):
        for aid in app_ids:
            yield aid, infos.get(aid)
    return fake


# ---------------------------------------------------------------------------
# detect_changes
# ---------------------------------------------------------------------------

def test_detect_changes_returns_only_changed_apps():
    from src.core.archive import poll
    from src.core.archive.project import AppEntry

    apps_by_id = {
        100: AppEntry(app_id=100, current_buildid="111"),
        200: AppEntry(app_id=200, current_buildid="222"),
        300: AppEntry(app_id=300, current_buildid=""),
    }
    infos = {
        100: {"name": "A", "buildid": "111", "timeupdated": 1, "oslist": "windows", "installdir": "a"},
        200: {"name": "B", "buildid": "999", "timeupdated": 2, "oslist": "windows", "installdir": "b"},
        300: {"name": "C", "buildid": "333", "timeupdated": 3, "oslist": "windows", "installdir": "c"},
    }
    with mock.patch.object(poll, "query_app_info_batch", _stub_qaib(infos)):
        changes = poll.detect_changes(None, None, apps_by_id)

    by_id = {row[0]: row for row in changes}
    assert 100 not in by_id                              # unchanged
    assert by_id[200] == (200, "222", "999", infos[200]) # bumped
    # 300 was first-seen this cycle: seeded silently, no download.
    assert 300 not in by_id
    assert apps_by_id[300].current_buildid.buildid == "333"
    assert apps_by_id[300].name                    == "C"
    # Existing entries also get their name backfilled when missing.
    assert apps_by_id[100].name == "A"
    assert apps_by_id[200].name == "B"


def test_detect_changes_force_download_returns_all_with_valid_buildid():
    from src.core.archive import poll
    from src.core.archive.project import AppEntry

    apps_by_id = {
        100: AppEntry(app_id=100, current_buildid="111"),
        200: AppEntry(app_id=200, current_buildid="222"),
    }
    infos = {
        100: {"name": "A", "buildid": "111", "timeupdated": 1, "oslist": "", "installdir": ""},
        200: {"name": "B", "buildid": "222", "timeupdated": 2, "oslist": "", "installdir": ""},
    }
    with mock.patch.object(poll, "query_app_info_batch", _stub_qaib(infos)):
        changes = poll.detect_changes(None, None, apps_by_id, force_download=True)

    assert {row[0] for row in changes} == {100, 200}


def test_detect_changes_skips_apps_without_usable_buildid():
    from src.core.archive import poll
    from src.core.archive.project import AppEntry

    apps_by_id = {
        100: AppEntry(app_id=100, current_buildid=""),
        200: AppEntry(app_id=200, current_buildid=""),
        300: AppEntry(app_id=300, current_buildid=""),
    }
    infos = {
        100: None,                                              # no info
        200: {"name": "B", "buildid": "Unknown", "timeupdated": 0, "oslist": "", "installdir": ""},
        300: {"name": "C", "buildid": "999",     "timeupdated": 0, "oslist": "", "installdir": ""},
    }
    with mock.patch.object(poll, "query_app_info_batch", _stub_qaib(infos)):
        changes = poll.detect_changes(None, None, apps_by_id, force_download=True)

    # 300 has a usable buildid but is first-seen → seeded, not returned.
    # 100 / 200 have no usable buildid → skipped entirely.
    assert changes == []
    assert apps_by_id[300].current_buildid.buildid == "999"
    assert apps_by_id[300].name                    == "C"
    assert apps_by_id[100].current_buildid.buildid == ""    # untouched (no info)
    assert apps_by_id[200].current_buildid.buildid == ""    # untouched (Unknown)


def test_detect_changes_first_seen_not_returned_even_with_force_download():
    """Force-download bypasses change detection but never triggers a
    first-time download — that path is reserved for explicit
    `archive download <appid>`."""
    from src.core.archive import poll
    from src.core.archive.project import AppEntry

    apps_by_id = {
        500: AppEntry(app_id=500, current_buildid=""),
        600: AppEntry(app_id=600, current_buildid="123"),
    }
    infos = {
        500: {"name": "Newcomer", "buildid": "777", "timeupdated": 0, "oslist": "", "installdir": ""},
        600: {"name": "Old",      "buildid": "123", "timeupdated": 0, "oslist": "", "installdir": ""},
    }
    with mock.patch.object(poll, "query_app_info_batch", _stub_qaib(infos)):
        changes = poll.detect_changes(None, None, apps_by_id, force_download=True)

    # 600 has a known prior buildid → force_download includes it.
    # 500 is first-seen → seeded silently regardless of force_download.
    assert {row[0] for row in changes} == {600}
    assert apps_by_id[500].current_buildid.buildid == "777"
    assert apps_by_id[500].name                    == "Newcomer"


def test_detect_changes_passes_batch_size_through():
    from src.core.archive import poll
    from src.core.archive.project import AppEntry

    apps_by_id = {i: AppEntry(app_id=i, current_buildid="") for i in (1, 2, 3, 4, 5)}

    captured = {}
    def fake(client, cdn, app_ids, max_retries=1, batch_size=None, quiet=False):
        captured["batch_size"]  = batch_size
        captured["max_retries"] = max_retries
        captured["quiet"]       = quiet
        for aid in app_ids:
            yield aid, {"name": str(aid), "buildid": "1", "timeupdated": 0,
                        "oslist": "", "installdir": ""}

    with mock.patch.object(poll, "query_app_info_batch", fake):
        poll.detect_changes(None, None, apps_by_id, batch_size=2, max_retries=3)

    assert captured == {"batch_size": 2, "max_retries": 3, "quiet": True}


def test_detect_changes_empty_apps_returns_empty_list():
    from src.core.archive import poll
    assert poll.detect_changes(None, None, {}) == []


# ---------------------------------------------------------------------------
# Progress events
# ---------------------------------------------------------------------------

def test_detect_changes_emits_progress():
    """detect_changes must emit one app_info_progress per app probed,
    counting up to the total."""
    from src.core.archive import poll
    from src.core.archive.project import AppEntry

    apps_by_id = {
        100: AppEntry(app_id=100, current_buildid="111"),
        200: AppEntry(app_id=200, current_buildid="222"),
    }
    infos = {
        100: {"name": "A", "buildid": "111", "timeupdated": 1, "oslist": "", "installdir": ""},
        200: {"name": "B", "buildid": "999", "timeupdated": 2, "oslist": "", "installdir": ""},
    }

    def fake_qaib(client, cdn, app_ids, **kw):
        for aid in app_ids:
            yield aid, infos[aid]

    seen = []
    with mock.patch.object(poll, "query_app_info_batch", fake_qaib):
        poll.detect_changes(None, None, apps_by_id,
                            on_event=lambda ev: seen.append(ev))

    progress = [(e.name, e.done, e.total) for e in seen
                if e.kind == "app_info_progress"]
    assert progress == [("100", 1, 2), ("200", 2, 2)]
