"""gui.archive_pages_history — Manifest history sub-page.

Tests run headless via QT_QPA_PLATFORM=offscreen (set in conftest).
"""
from __future__ import annotations

from pathlib import Path

from src.core.archive import project as project_mod


def _build_panel_with_history(qapp, *, with_filter_app=True):
    """Construct an ArchivePanel pre-populated with two apps and a few
    manifest_history records each, then return (panel, history_page)."""
    from src.gui.archive_panel import ArchivePanel

    panel = ArchivePanel()
    proj = panel.project()
    proj.apps = [
        project_mod.AppEntry(app_id=730,    name="A"),
        project_mod.AppEntry(app_id=4048390, name="B"),
    ]
    proj.apps[0].manifest_history = [
        project_mod.ManifestRecord(
            buildid="100", branch="public", platform="windows",
            depot_id=731, depot_name="A Win",
            manifest_gid="111", timeupdated=1_700_000_000,
        ),
        project_mod.ManifestRecord(
            buildid="200", branch="public", platform="windows",
            depot_id=731, depot_name="A Win",
            manifest_gid="222", timeupdated=1_700_010_000,
        ),
    ]
    proj.apps[1].manifest_history = [
        project_mod.ManifestRecord(
            buildid="X", branch="beta", platform="linux",
            depot_id=4048391, depot_name="B Linux",
            manifest_gid="999", timeupdated=1_700_020_000,
        ),
    ]
    panel._refresh_pages()
    return panel, panel._pages["history"]


def test_history_page_flattens_records_across_apps(qapp):
    """All manifest records from every AppEntry should appear in the
    flat table — that's the whole purpose of the page."""
    _, page = _build_panel_with_history(qapp)
    # 2 (app 730) + 1 (app 4048390) = 3
    assert page.table.rowCount() == 3


def test_history_page_filter_dropdown_narrows_rows(qapp):
    """Selecting a single app from the filter must reduce the table to
    just that app's history."""
    _, page = _build_panel_with_history(qapp)

    # Select app 730 (entry index 1 — 0 is the (all apps) sentinel).
    for i in range(page.app_filter.count()):
        if page.app_filter.itemData(i) == 730:
            page.app_filter.setCurrentIndex(i)
            break
    assert page.table.rowCount() == 2

    # Select app 4048390.
    for i in range(page.app_filter.count()):
        if page.app_filter.itemData(i) == 4048390:
            page.app_filter.setCurrentIndex(i)
            break
    assert page.table.rowCount() == 1


def test_history_page_count_label_reflects_filter(qapp):
    """The "X of Y records" footer must update with the filter so the
    user can see at a glance how many records are hidden."""
    _, page = _build_panel_with_history(qapp)
    assert "3 of 3" in page.count_label.text()

    for i in range(page.app_filter.count()):
        if page.app_filter.itemData(i) == 730:
            page.app_filter.setCurrentIndex(i)
            break
    assert "2 of 3" in page.count_label.text()


def test_history_page_user_role_carries_entry_and_record(qapp):
    """Each row's first column must stash the (entry, record) pair so
    the Pull button can reconstruct download_manifest args without
    re-walking project.apps[*].manifest_history."""
    from PySide6.QtCore import Qt
    _, page = _build_panel_with_history(qapp)
    item = page.table.item(0, 0)
    data = item.data(Qt.UserRole)
    assert isinstance(data, tuple)
    entry, rec = data
    assert isinstance(entry, project_mod.AppEntry)
    assert isinstance(rec, project_mod.ManifestRecord)


def test_history_page_pull_button_calls_panel(qapp, monkeypatch, tmp_path):
    """Clicking Pull must hand the row's (app_id, depot_id, manifest_gid,
    branch, branch_password, output_dir) bundle to
    panel.start_historical_pull — and to nothing else.  Regression
    against future refactors that route the call through some
    intermediate signal layer that drops fields."""
    panel, page = _build_panel_with_history(qapp)

    captured: dict = {}
    def fake_start(params):
        captured.update(params)
    panel.start_historical_pull = fake_start  # type: ignore[assignment]

    # Bypass the QFileDialog by patching getExistingDirectory.
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory",
        staticmethod(lambda *a, **kw: str(tmp_path)),
    )
    page.table.selectRow(0)
    page._on_pull()

    assert captured.get("app_id") in (730, 4048390)
    assert "depot_id" in captured
    assert "manifest_gid" in captured
    assert "branch" in captured
    assert captured.get("output_dir") == Path(tmp_path)


def test_history_page_pull_no_selection_skips(qapp, monkeypatch):
    """No row selected → no panel call.  The user gets an info dialog."""
    panel, page = _build_panel_with_history(qapp)

    called = {"start": 0, "info": 0}
    def fake_start(params):
        called["start"] += 1
    panel.start_historical_pull = fake_start  # type: ignore[assignment]

    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **kw: called.__setitem__(
                            "info", called["info"] + 1) or 0))
    page._on_pull()
    assert called == {"start": 0, "info": 1}


def test_history_page_pull_skips_when_record_missing_ids(qapp,
                                                         monkeypatch, tmp_path):
    """Rows lacking depot_id or manifest_gid (legacy projects) must not
    fire the worker — the call would just crash later inside
    download_manifest."""
    from PySide6.QtCore import Qt
    panel, page = _build_panel_with_history(qapp)

    # Mutate the underlying record to drop depot_id, then simulate the
    # row's UserRole payload still pointing at it.
    proj = panel.project()
    proj.apps[0].manifest_history[0].depot_id = 0
    page.refresh()

    called = {"start": 0, "warn": 0}
    panel.start_historical_pull = lambda p: called.__setitem__(
        "start", called["start"] + 1)  # type: ignore[assignment]
    from PySide6.QtWidgets import QMessageBox, QFileDialog
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **kw: called.__setitem__(
                            "warn", called["warn"] + 1) or 0))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **kw: str(tmp_path)))

    # Pick the row we just blanked.
    for r in range(page.table.rowCount()):
        item = page.table.item(r, 0)
        _, rec = item.data(Qt.UserRole)
        if rec.depot_id == 0:
            page.table.selectRow(r)
            break
    page._on_pull()
    assert called["start"] == 0
    assert called["warn"]  == 1
