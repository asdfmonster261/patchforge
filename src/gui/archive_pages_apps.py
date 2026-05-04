"""Apps sub-page — table editor for project.apps.

Columns:
    App ID | Branch | Branch password | Platform | current_buildid |
    previous_buildid

Rows are editable in place (Qt.ItemIsEditable).  Add / Remove buttons
mutate project.apps.  All edits flip panel.mark_dirty().
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ..core.archive import project as project_mod
from .archive_pages import ArchivePageBase


COLS = [
    ("App ID",            80),
    ("Name",              200),
    ("Branch",            100),
    ("Branch password",   140),
    ("Platform",          90),
    ("Crack",             110),
    ("current_buildid",   140),
    ("previous_buildid",  140),
]

# Empty string in AppEntry.crack_mode means "inherit project default";
# the "(default)" label is the user-facing rendering of that.
_CRACK_LABELS  = ["(default)", "off", "gse", "coldclient", "all"]
_CRACK_VALUES  = ["",           "off", "gse", "coldclient", "all"]


class AppsPage(ArchivePageBase):

    def __init__(self, panel):
        super().__init__(panel)
        self._building = False
        layout = QVBoxLayout(self)

        # toolbar
        bar = QHBoxLayout()
        self.btn_add    = QPushButton("Add app")
        self.btn_remove = QPushButton("Remove selected")
        bar.addWidget(self.btn_add)
        bar.addWidget(self.btn_remove)
        bar.addStretch(1)
        self.count_label = QLabel("0 apps")
        self.count_label.setObjectName("dim")
        bar.addWidget(self.count_label)
        layout.addLayout(bar)

        # table
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels([c[0] for c in COLS])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.SelectedClicked
        )
        for i, (_, w) in enumerate(COLS):
            self.table.setColumnWidth(i, w)
        self.table.horizontalHeader().setSectionResizeMode(
            len(COLS) - 1, QHeaderView.Stretch
        )
        layout.addWidget(self.table, 1)

        hint = QLabel(
            "Name + current_buildid + previous_buildid update automatically\n"
            "each successful download (and Name auto-fills the first time a\n"
            "poll cycle sees the app).  First-time observations seed the\n"
            "buildid silently — they don't trigger a download."
        )
        hint.setObjectName("dim")
        layout.addWidget(hint)

        self.btn_add.clicked.connect(self._on_add)
        self.btn_remove.clicked.connect(self._on_remove)
        self.table.itemChanged.connect(self._on_item_changed)

    # ---------------------------------------------------------- helpers
    def _make_crack_combo(self, current: str) -> QComboBox:
        cb = QComboBox()
        cb.addItems(_CRACK_LABELS)
        try:
            idx = _CRACK_VALUES.index((current or "").strip().lower())
        except ValueError:
            idx = 0
        cb.setCurrentIndex(idx)
        cb.currentIndexChanged.connect(self._on_combo_changed)
        return cb

    def _read_crack_combo(self, row: int) -> str:
        cb = self.table.cellWidget(row, 5)
        if cb is None or not isinstance(cb, QComboBox):
            return ""
        return _CRACK_VALUES[cb.currentIndex()]

    def _set_row(self, row: int, entry: project_mod.AppEntry) -> None:
        text_values = [
            (0, str(entry.app_id) if entry.app_id else ""),
            (1, entry.name),
            (2, entry.branch or "public"),
            (3, entry.branch_password),
            (4, entry.platform),
            (6, entry.current_buildid.buildid),
            (7, entry.previous_buildid.buildid),
        ]
        for col, val in text_values:
            self.table.setItem(row, col, QTableWidgetItem(val))
        self.table.setCellWidget(row, 5, self._make_crack_combo(entry.crack_mode))

    def _read_row(self, row: int) -> project_mod.AppEntry:
        def _t(c: int) -> str:
            it = self.table.item(row, c)
            return it.text() if it is not None else ""
        try:
            app_id = int(_t(0) or 0)
        except ValueError:
            app_id = 0
        return project_mod.AppEntry(
            app_id           = app_id,
            name             = _t(1),
            branch           = _t(2) or "public",
            branch_password  = _t(3),
            platform         = _t(4),
            crack_mode       = self._read_crack_combo(row),
            current_buildid  = project_mod.BuildIdRecord(buildid=_t(6)),
            previous_buildid = project_mod.BuildIdRecord(buildid=_t(7)),
        )

    # ---------------------------------------------------------- buttons
    def _on_add(self):
        p = self._panel.project()
        p.apps.append(project_mod.AppEntry(branch="public"))
        self._panel.mark_dirty()
        self.refresh()
        self.table.setCurrentCell(len(p.apps) - 1, 0)
        self.table.editItem(self.table.item(len(p.apps) - 1, 0))

    def _on_remove(self):
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        if not rows:
            return
        p = self._panel.project()
        for r in rows:
            if 0 <= r < len(p.apps):
                p.apps.pop(r)
        self._panel.mark_dirty()
        self.refresh()

    def _on_item_changed(self, _item):
        if self._building:
            return
        self._panel.mark_dirty()

    def _on_combo_changed(self, _idx):
        if self._building:
            return
        self._panel.mark_dirty()

    # ---------------------------------------------------------- protocol
    def refresh(self):
        self._building = True
        try:
            apps = self._panel.project().apps
            self.table.setRowCount(len(apps))
            for row, entry in enumerate(apps):
                self._set_row(row, entry)
            self.count_label.setText(f"{len(apps)} app{'s' if len(apps) != 1 else ''}")
        finally:
            self._building = False

    def flush(self):
        p = self._panel.project()
        # Preserve fields not surfaced in the table (manifest_history,
        # timeupdated nested in BuildIdRecord) by merging row values into
        # the existing AppEntry keyed on app_id.  Without this, editing
        # any visible cell would silently wipe historical state.
        prior = {e.app_id: e for e in p.apps if e.app_id}
        new_apps = []
        for r in range(self.table.rowCount()):
            row_entry = self._read_row(r)
            existing  = prior.get(row_entry.app_id)
            if existing is not None:
                existing.name             = row_entry.name
                existing.branch           = row_entry.branch
                existing.branch_password  = row_entry.branch_password
                existing.platform         = row_entry.platform
                existing.crack_mode       = row_entry.crack_mode
                existing.current_buildid.buildid  = row_entry.current_buildid.buildid
                existing.previous_buildid.buildid = row_entry.previous_buildid.buildid
                new_apps.append(existing)
            else:
                new_apps.append(row_entry)
        p.apps = new_apps


__all__ = ["AppsPage"]
