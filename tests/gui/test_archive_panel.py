"""gui.archive_panel — top-level archive-mode tab + page stack.

Tests run headless via QT_QPA_PLATFORM=offscreen (set in conftest).
"""
from __future__ import annotations

from src.core.archive import project as project_mod


def test_archive_panel_constructs(qapp):
    from src.gui.archive_panel import ArchivePanel
    p = ArchivePanel()
    assert p.stack.count()     == 7
    assert p.body_tabs.count() == 2


def test_archive_panel_save_load_roundtrip(qapp, tmp_path):
    """Create panel → mutate project → save → load into a fresh panel
    → state survived JSON roundtrip."""
    from src.gui.archive_panel import ArchivePanel
    p = ArchivePanel()
    p.project().apps.append(project_mod.AppEntry(app_id=730, branch="public"))
    p.project().bbcode_template = "[b]{APP_NAME}[/b]"
    # Refresh pages so widget state mirrors the in-memory project — the
    # panel's flush() pass during save reads from widgets, not the model.
    p._refresh_pages()
    target = tmp_path / "test.xarchive"
    p._save_to(target)
    assert target.exists()

    p2 = ArchivePanel()
    p2._project = project_mod.load(target)
    p2._refresh_pages()
    assert len(p2.project().apps) == 1
    assert p2.project().apps[0].app_id == 730
    assert p2.project().bbcode_template.startswith("[b]")


def test_archive_panel_run_crack_defaults_from_project(qapp):
    """The run-row crack picker initialises from project.crack_mode so
    a saved project's choice survives a panel reopen."""
    from src.gui.archive_panel import ArchivePanel
    p = ArchivePanel()
    p.project().crack_mode = "gse"
    p._refresh_pages()
    assert p.run_crack.currentData() == "gse"


def test_archive_panel_per_run_options_construct(qapp):
    """Smoke test: per-run controls (branch / crack / force / log) all
    exist on the panel after construction."""
    from src.gui.archive_panel import ArchivePanel
    p = ArchivePanel()
    assert p.run_branch.text() == "public"
    # Crack combo should expose three choices: (off) / coldclient / gse.
    items = [p.run_crack.itemText(i) for i in range(p.run_crack.count())]
    assert "coldclient" in items and "gse" in items
    assert p.run_crack.itemData(0) is None
    assert p.run_force.isChecked() is False
    assert p.run_log_path.text() == ""
