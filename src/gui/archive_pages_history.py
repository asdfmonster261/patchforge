"""Manifest history sub-page — browse + replay past builds.

Flattens project.apps[*].manifest_history into one sortable table.
A "Pull selected" button fires the equivalent of `archive depot
--app X --depot Y --manifest Z` for the highlighted row, routed
through ArchivePanel.start_historical_pull so the existing live-run
view picks it up the same way `archive download` does.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from ..core.archive import project as project_mod
from .archive_pages import ArchivePageBase


COLS = [
    ("App ID",       80),
    ("Name",         180),
    ("Build ID",     100),
    ("Branch",       80),
    ("Platform",     80),
    ("Depot ID",     90),
    ("Depot name",   180),
    ("Manifest GID", 180),
    ("Time",         150),
]


def _fmt_ts(ts: int) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC")
    except (OSError, ValueError):
        return str(ts)


class ManifestHistoryPage(ArchivePageBase):

    def __init__(self, panel):
        super().__init__(panel)
        layout = QVBoxLayout(self)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("App:"))
        self.app_filter = QComboBox()
        self.app_filter.setMinimumWidth(220)
        self.app_filter.currentIndexChanged.connect(self._on_filter_changed)
        bar.addWidget(self.app_filter)

        bar.addSpacing(12)
        self.btn_pull = QPushButton("Pull selected build")
        self.btn_pull.setObjectName("accent")
        self.btn_pull.clicked.connect(self._on_pull)
        bar.addWidget(self.btn_pull)

        bar.addStretch(1)
        self.count_label = QLabel("0 records")
        self.count_label.setObjectName("dim")
        bar.addWidget(self.count_label)
        layout.addLayout(bar)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels([c[0] for c in COLS])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        for i, (_, w) in enumerate(COLS):
            self.table.setColumnWidth(i, w)
        self.table.horizontalHeader().setSectionResizeMode(
            len(COLS) - 1, QHeaderView.Stretch
        )
        layout.addWidget(self.table, 1)

        hint = QLabel(
            "Manifest history accumulates as the runner downloads builds.\n"
            "Pull selected build fires the equivalent of\n"
            "`archive depot --app X --depot Y --manifest Z` and writes the\n"
            "depot tree under the project's output dir."
        )
        hint.setObjectName("dim")
        layout.addWidget(hint)

    # ----------------------------------------------------------- helpers
    def _records(self) -> list[tuple[project_mod.AppEntry,
                                      project_mod.ManifestRecord]]:
        rows: list = []
        for entry in self._panel.project().apps:
            for rec in entry.manifest_history:
                rows.append((entry, rec))
        return rows

    def _populate_filter(self) -> None:
        # Preserve the user's current pick across refreshes.
        prev = self.app_filter.currentData()
        self.app_filter.blockSignals(True)
        try:
            self.app_filter.clear()
            self.app_filter.addItem("(all apps)", userData=None)
            for entry in self._panel.project().apps:
                label = f"{entry.app_id} — {entry.name or '(unnamed)'}"
                self.app_filter.addItem(label, userData=entry.app_id)
            # Restore selection if the same app_id is still around.
            for i in range(self.app_filter.count()):
                if self.app_filter.itemData(i) == prev:
                    self.app_filter.setCurrentIndex(i)
                    break
        finally:
            self.app_filter.blockSignals(False)

    def _selected_filter(self) -> int | None:
        return self.app_filter.currentData()

    def _populate_table(self) -> None:
        self.table.setSortingEnabled(False)
        try:
            filter_app = self._selected_filter()
            rows = [
                (e, r) for e, r in self._records()
                if filter_app is None or e.app_id == filter_app
            ]
            self.table.setRowCount(len(rows))
            for row, (entry, rec) in enumerate(rows):
                values = [
                    str(entry.app_id),
                    entry.name,
                    rec.buildid,
                    rec.branch,
                    rec.platform,
                    str(rec.depot_id) if rec.depot_id else "",
                    rec.depot_name,
                    rec.manifest_gid,
                    _fmt_ts(rec.timeupdated),
                ]
                for col, val in enumerate(values):
                    item = QTableWidgetItem(val)
                    # Stash the (entry, rec) pair on the first column for
                    # _on_pull to recover without re-walking history.
                    if col == 0:
                        item.setData(Qt.UserRole, (entry, rec))
                    self.table.setItem(row, col, item)
            total = sum(len(e.manifest_history) for e in self._panel.project().apps)
            shown = len(rows)
            self.count_label.setText(
                f"{shown} of {total} record{'s' if total != 1 else ''}"
            )
        finally:
            self.table.setSortingEnabled(True)

    # ----------------------------------------------------------- handlers
    def _on_filter_changed(self, _idx) -> None:
        self._populate_table()

    def _on_pull(self) -> None:
        rows = self.table.selectedItems()
        if not rows:
            QMessageBox.information(self, "Pull build",
                                    "Select a row first.")
            return
        item = self.table.item(rows[0].row(), 0)
        data = item.data(Qt.UserRole) if item is not None else None
        if not data:
            return
        entry, rec = data
        if not rec.depot_id or not rec.manifest_gid:
            QMessageBox.warning(self, "Pull build",
                                "Row is missing depot_id or manifest_gid.")
            return

        # Default output dir: project setting → fall back to home/Downloads.
        proj = self._panel.project()
        suggested = proj.output_dir or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Output directory for historical pull", suggested,
        )
        if not chosen:
            return

        params = {
            "app_id":          int(entry.app_id),
            "depot_id":        int(rec.depot_id),
            "manifest_gid":    str(rec.manifest_gid),
            "branch":          rec.branch or "public",
            "branch_password": entry.branch_password or "",
            "output_dir":      Path(chosen),
        }
        self._panel.start_historical_pull(params)

    # ----------------------------------------------------------- protocol
    def refresh(self) -> None:
        self._populate_filter()
        self._populate_table()

    def flush(self) -> None:
        # Read-only view; nothing to write back.
        pass


__all__ = ["ManifestHistoryPage"]
