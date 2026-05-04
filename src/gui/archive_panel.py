"""Archive-mode panel for the PatchForge main window.

Holds the .xarchive editor + run controls.  Sub-pages (Apps, Crack
identity, BBCode template, Run options, Polling, Credentials) live in
src/gui/archive_pages.py — this file just wires the sidebar nav, the
toolbar, and the project save/load loop.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)

from ..core.archive import project as project_mod
from . import archive_pages
from .archive_run_view import ArchiveRunView
from .archive_worker import ArchiveWorker, HistoricalPullWorker


SIDEBAR_ENTRIES: list[tuple[str, str]] = [
    # (label, page key — passed to archive_pages.build_page)
    ("Apps",             "apps"),
    ("Crack identity",   "crack"),
    ("BBCode template",  "bbcode"),
    ("Run options",      "run"),
    ("Polling",          "poll"),
    ("Manifest history", "history"),
    ("Credentials",      "creds"),
]


class ArchivePanel(QWidget):
    """Top-level Archive tab.

    Owns the in-memory ArchiveProject and the path it loaded from.
    Emits `project_changed()` when the loaded project is mutated by a
    sub-page so MainWindow can mark the title bar dirty / enable Save.
    """

    project_changed = Signal()
    run_requested   = Signal(object)   # emits ArchiveProject when user clicks Run

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._project: project_mod.ArchiveProject = project_mod.new_project()
        self._project_path: Optional[Path] = None
        self._dirty = False

        self._build_ui()
        self._refresh_pages()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # ── Toolbar (project ops) ───────────────────────────────────
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.btn_new      = QPushButton("New")
        self.btn_open     = QPushButton("Open…")
        self.btn_save     = QPushButton("Save")
        self.btn_save_as  = QPushButton("Save As…")
        for b in (self.btn_new, self.btn_open, self.btn_save, self.btn_save_as):
            bar.addWidget(b)
        bar.addSpacing(12)
        bar.addWidget(QLabel("Project:"))
        self.path_label = QLineEdit()
        self.path_label.setReadOnly(True)
        self.path_label.setPlaceholderText("(unsaved project)")
        bar.addWidget(self.path_label, 1)

        self.btn_new.clicked.connect(self._on_new)
        self.btn_open.clicked.connect(self._on_open)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_save_as.clicked.connect(self._on_save_as)

        root.addLayout(bar)

        # ── Tabbed body: Editor (sidebar+pages) | Live run ──────────
        self.body_tabs = QTabWidget()

        # Editor tab
        editor_tab = QWidget()
        body = QHBoxLayout(editor_tab)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)

        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(180)
        for label, key in SIDEBAR_ENTRIES:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, key)
            self.sidebar.addItem(it)
        self.sidebar.setCurrentRow(0)
        self.sidebar.currentRowChanged.connect(self._on_sidebar_changed)
        body.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self._pages: dict[str, archive_pages.ArchivePageBase] = {}
        for label, key in SIDEBAR_ENTRIES:
            page = archive_pages.build_page(key, panel=self)
            self._pages[key] = page
            self.stack.addWidget(page)
        body.addWidget(self.stack, 1)
        self.body_tabs.addTab(editor_tab, "Editor")

        # Live run tab
        self.run_view = ArchiveRunView()
        self.body_tabs.addTab(self.run_view, "Live run")
        root.addWidget(self.body_tabs, 1)

        # ── Per-run options + Run button ────────────────────────────
        # These mirror the CLI flags that aren't persisted in the .xarchive:
        #   --crack {coldclient,gse}, --force-download, --branch, --log
        run_row = QHBoxLayout()
        run_row.addWidget(QLabel("Branch:"))
        self.run_branch = QLineEdit("public")
        self.run_branch.setFixedWidth(100)
        run_row.addWidget(self.run_branch)

        run_row.addSpacing(12)
        run_row.addWidget(QLabel("Crack:"))
        self.run_crack = QComboBox()
        self.run_crack.addItem("(off)",       userData=None)
        self.run_crack.addItem("coldclient",  userData="coldclient")
        self.run_crack.addItem("gse",         userData="gse")
        self.run_crack.addItem("all",         userData="all")
        run_row.addWidget(self.run_crack)

        run_row.addSpacing(12)
        self.run_force = QCheckBox("Force download")
        self.run_force.setToolTip(
            "Download every tracked app's current build regardless of\n"
            "whether the buildid changed since the last run."
        )
        run_row.addWidget(self.run_force)

        run_row.addSpacing(12)
        run_row.addWidget(QLabel("Log file:"))
        self.run_log_path = QLineEdit()
        self.run_log_path.setPlaceholderText("(blank — no file log)")
        run_row.addWidget(self.run_log_path, 1)
        self.btn_log_browse = QPushButton("…")
        self.btn_log_browse.setObjectName("browse")
        self.btn_log_browse.clicked.connect(self._on_log_browse)
        run_row.addWidget(self.btn_log_browse)

        self.btn_run = QPushButton("Start Archive Run")
        self.btn_run.setObjectName("accent")
        self.btn_run.clicked.connect(self._on_run)
        run_row.addWidget(self.btn_run)
        root.addLayout(run_row)

        # worker state — created per run, cleaned up in _on_run_finished
        self._worker: ArchiveWorker | None = None
        self._worker_thread: QThread | None = None

    # ------------------------------------------------------------- public
    def project(self) -> project_mod.ArchiveProject:
        return self._project

    def project_path(self) -> Optional[Path]:
        return self._project_path

    def is_dirty(self) -> bool:
        return self._dirty

    def mark_dirty(self) -> None:
        """Sub-pages call this after editing project fields."""
        self._dirty = True
        self.project_changed.emit()

    # ---------------------------------------------------------- internal
    def _on_sidebar_changed(self, row: int) -> None:
        self.stack.setCurrentIndex(row)

    def _refresh_pages(self) -> None:
        for page in self._pages.values():
            page.refresh()
        self.path_label.setText(str(self._project_path or ""))
        # Default the per-run crack picker from the project's stored
        # crack_mode whenever a project is loaded / cleared.
        crack_keys = {"": 0, "coldclient": 1, "gse": 2, "all": 3}
        self.run_crack.setCurrentIndex(
            crack_keys.get(self._project.crack_mode, 0)
        )

    # ─── Project ops ────────────────────────────────────────────────
    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        ans = QMessageBox.question(
            self, "Discard changes?",
            "The current project has unsaved changes.  Discard them?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        return ans == QMessageBox.Yes

    def _on_new(self) -> None:
        if not self._confirm_discard():
            return
        self._project = project_mod.new_project()
        self._project_path = None
        self._dirty = False
        self._refresh_pages()
        self.project_changed.emit()

    def _on_open(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open .xarchive", "", "Archive projects (*.xarchive);;All files (*)",
        )
        if not path:
            return
        try:
            proj = project_mod.load(Path(path))
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", str(exc))
            return
        self._project = proj
        self._project_path = Path(path)
        self._dirty = False
        self._refresh_pages()
        self.project_changed.emit()

    def _on_save(self) -> None:
        if self._project_path is None:
            self._on_save_as()
            return
        self._save_to(self._project_path)

    def _on_save_as(self) -> None:
        suggested = str(self._project_path or "untitled.xarchive")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save .xarchive", suggested,
            "Archive projects (*.xarchive);;All files (*)",
        )
        if not path:
            return
        if not path.endswith(".xarchive"):
            path += ".xarchive"
        self._save_to(Path(path))

    def _save_to(self, path: Path) -> None:
        # Sub-pages may have edits buffered in widgets; flush before save.
        for page in self._pages.values():
            page.flush()
        try:
            project_mod.save(self._project, path)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self._project_path = path
        self._dirty = False
        self.path_label.setText(str(path))
        self.project_changed.emit()

    def _on_log_browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Choose log file", self.run_log_path.text() or "",
            "Log files (*.log *.txt);;All files (*)",
        )
        if path:
            self.run_log_path.setText(path)

    def _on_run(self) -> None:
        for page in self._pages.values():
            page.flush()
        if self._worker is not None:
            QMessageBox.information(self, "Run already in progress",
                                    "Stop the current run before starting another.")
            return

        app_ids = [e.app_id for e in self._project.apps if e.app_id]
        if not app_ids:
            QMessageBox.warning(self, "No apps",
                                "Add at least one app on the Apps page first.")
            return

        log_text = self.run_log_path.text().strip()
        log_path = Path(log_text) if log_text else None

        # Persist the per-run crack pick into the project so the next
        # load defaults to the same choice.  Mirrors the CLI's
        # _persist_archive_run_options behaviour for --crack.
        chosen_crack = self.run_crack.currentData() or ""
        if chosen_crack != self._project.crack_mode:
            self._project.crack_mode = chosen_crack
            self.mark_dirty()

        self._worker = ArchiveWorker(
            project_obj=self._project,
            project_path=self._project_path,
            app_ids=app_ids,
            platform=self._project.default_platform or None,
            branch=self.run_branch.text().strip() or "public",
            crack_mode=chosen_crack or None,
            force_download=self.run_force.isChecked(),
            log_file=log_path,
        )
        self._worker_thread = QThread(self)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_run_finished)
        self._worker.failed.connect(self._on_run_failed)
        self.run_view.attach(self._worker)
        self.body_tabs.setCurrentWidget(self.run_view)
        self.btn_run.setEnabled(False)
        self._worker_thread.start()
        self.run_requested.emit(self._project)

    def _on_run_finished(self, _result) -> None:
        self._cleanup_worker()
        # Keep the run view visible so user sees results.

    def _on_run_failed(self, _msg: str) -> None:
        self._cleanup_worker()

    # ─── Historical pull ────────────────────────────────────────────
    def start_historical_pull(self, params: dict) -> None:
        """Spawn a HistoricalPullWorker for one (app, depot, manifest)
        triple and route its events through the live-run view, the same
        way `_on_run` does for the full download pipeline.

        Called by the Manifest history page's "Pull selected" button.
        """
        if self._worker is not None:
            QMessageBox.information(
                self, "Run in progress",
                "Wait for the current run to finish before starting another.")
            return

        self._worker = HistoricalPullWorker(
            app_id          = params["app_id"],
            depot_id        = params["depot_id"],
            manifest_gid    = params["manifest_gid"],
            branch          = params.get("branch", "public"),
            branch_password = params.get("branch_password", ""),
            output_dir      = params["output_dir"],
        )
        self._worker_thread = QThread(self)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_run_finished)
        self._worker.failed.connect(self._on_run_failed)
        self.run_view.attach(self._worker)
        self.body_tabs.setCurrentWidget(self.run_view)
        self.btn_run.setEnabled(False)
        self._worker_thread.start()

    def _cleanup_worker(self) -> None:
        if self._worker_thread is not None:
            self._worker_thread.quit()
            self._worker_thread.wait(2000)
            self._worker_thread.deleteLater()
        if self._worker is not None:
            self._worker.deleteLater()
        self._worker = None
        self._worker_thread = None
        self.btn_run.setEnabled(True)
        # Pages may have been edited; reload from project so any
        # mid-run mutations (current_buildid bumps from the runner) show.
        self._refresh_pages()


__all__ = ["ArchivePanel"]
