"""PatchForge main window — dark-theme PySide6 GUI."""

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QObject, QUrl
from PySide6.QtGui import QTextCursor, QColor, QDesktopServices, QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGroupBox, QLabel, QLineEdit, QPushButton, QComboBox,
    QRadioButton, QButtonGroup, QProgressBar, QPlainTextEdit,
    QFileDialog, QSplitter, QFrame, QStatusBar,
    QCheckBox, QListWidget, QListWidgetItem, QScrollArea, QInputDialog,
    QSpinBox, QDoubleSpinBox, QTabWidget,
    QDialog, QDialogButtonBox, QFormLayout, QMenu, QMessageBox,
)

from .theme import QSS, SUCCESS, ERROR, WARN

from ..core.engines.hdiffpatch import (
    HDiffPatchEngine, THREAD_OPTIONS, DEFAULT_QUALITY,
)
from ..core.engines.jojodiff import JojoDiffEngine
from ..core.engines.xdelta3 import XDelta3Engine
from ..core.project import ProjectSettings, save as save_project, load as load_project
from ..core.patch_builder import build, BuildResult
from ..core.repack_project import RepackSettings, save as save_repack, load as load_repack
from ..core.repack_builder import build as build_repack, RepackResult
from ..core.xpack_archive import (
    LZMA_QUALITY_LABELS as REPACK_LZMA_QUALITY_LABELS,
    ZSTD_QUALITY_LABELS as REPACK_ZSTD_QUALITY_LABELS,
    THREAD_OPTIONS as REPACK_THREAD_OPTIONS,
)
from ..core import recent_files as _recent
from ..core import app_settings as _app_settings
from ..core.fmt import format_size as _fmt_size


# ---------------------------------------------------------------------------
# Background build worker
# ---------------------------------------------------------------------------

class BuildWorker(QObject):
    progress = Signal(int, str, str)  # pct, msg, kind ("phase"|"file")
    finished = Signal(object)   # BuildResult

    def __init__(self, settings: ProjectSettings):
        super().__init__()
        self._settings = settings

    def run(self):
        result = build(self._settings, progress=self.progress.emit)
        self.finished.emit(result)


class RepackWorker(QObject):
    progress        = Signal(int, str, str)  # pct, msg, kind ("phase"|"file")
    stream_progress = Signal(int, int, str, int, int, str)  # idx, n, label, done, total, file_size
    finished        = Signal(object)   # RepackResult

    def __init__(self, settings: RepackSettings):
        super().__init__()
        self._settings = settings

    def run(self):
        result = build_repack(
            self._settings,
            progress=self.progress.emit,
            stream_progress=self.stream_progress.emit,
        )
        self.finished.emit(result)


# ---------------------------------------------------------------------------
# Small reusable widgets
# ---------------------------------------------------------------------------

class FilePicker(QWidget):
    """Label + line edit + browse button row."""

    def __init__(self, label: str, mode: str = "open",
                 filter_str: str = "All files (*)", parent=None):
        super().__init__(parent)
        self._mode = mode          # "open" | "save" | "dir"
        self._filter = filter_str

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.setAcceptDrops(True)

        self.lbl = QLabel(label)
        self.lbl.setFixedWidth(90)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("(not set)")
        self.btn = QPushButton("…")
        self.btn.setObjectName("browse")
        self.btn.setToolTip("Browse")
        self.btn.clicked.connect(self._browse)

        layout.addWidget(self.lbl)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.btn)

    def _browse(self):
        if self._mode == "dir":
            path = QFileDialog.getExistingDirectory(self, "Select folder",
                                                    self.edit.text() or str(Path.home()))
        elif self._mode == "save":
            path, _ = QFileDialog.getSaveFileName(self, "Save as",
                                                   self.edit.text() or str(Path.home()),
                                                   self._filter)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Open file",
                                                   self.edit.text() or str(Path.home()),
                                                   self._filter)
        if path:
            self.edit.setText(path)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls:
                local = urls[0].toLocalFile()
                if self._mode == "dir":
                    if Path(local).is_dir():
                        event.acceptProposedAction()
                        return
                else:
                    if Path(local).is_file():
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self.edit.setText(urls[0].toLocalFile())
            event.acceptProposedAction()

    @property
    def path(self) -> str:
        return self.edit.text().strip()

    @path.setter
    def path(self, v: str):
        self.edit.setText(v)


class HSep(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.HLine)
        self.setFrameShadow(QFrame.Sunken)


# ---------------------------------------------------------------------------
# Widget ↔ settings-field binders
#
# Each helper returns a (collect, apply) closure pair. _build_*_bindings
# below assembles a {field_name: (collect, apply)} dict so _collect_settings
# and _apply_settings have one source of truth and don't drift apart.
# ---------------------------------------------------------------------------

def _bind_lineedit(widget):
    return (lambda: widget.text().strip(),
            lambda v: widget.setText(v or ""))


def _bind_filepicker(widget):
    def _set(v):
        widget.path = v or ""
    return (lambda: widget.path, _set)


def _bind_checkbox(widget):
    return (widget.isChecked, widget.setChecked)


def _bind_spin(widget):
    return (widget.value, widget.setValue)


def _bind_combo_data(widget, default=None):
    """Combo where each item carries a UserRole data value (currentData())."""
    def _get():
        v = widget.currentData()
        return v if v is not None else default

    def _set(value):
        for i in range(widget.count()):
            if widget.itemData(i) == value:
                widget.setCurrentIndex(i)
                return
        # Fallback: match by visible text — used when project file stores a
        # legacy preset name that no longer exists as a UserRole entry.
        for i in range(widget.count()):
            if widget.itemText(i) == value:
                widget.setCurrentIndex(i)
                return

    return (_get, _set)


def _bind_radio_pair(primary_widget, primary_value, fallback_value):
    """Two mutually-exclusive radios → a string field (e.g. arch x64/x86).
    primary_widget is the radio whose checked-state means primary_value."""
    def _set(value):
        primary_widget.setChecked(value == primary_value)
    return (lambda: primary_value if primary_widget.isChecked() else fallback_value,
            _set)


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PatchForge")
        self.setMinimumSize(960, 680)
        # Window size is restored from app_settings (G5) — but only if it
        # honours the current minimum.  splitter_sizes / mode_tab_index are
        # applied below in _build_ui after the widgets exist.
        self._app_settings = _app_settings.load()
        w = max(self._app_settings.window_width,  960)
        h = max(self._app_settings.window_height, 680)
        self.resize(w, h)
        # Stylesheet is set once on QApplication in run_gui() so the
        # cascade resolves once across the whole widget tree instead of
        # per-widget on each show. Anything that needs targeted styling
        # uses object names + selectors in theme.QSS.

        self._worker: Optional[BuildWorker] = None
        self._repack_worker: Optional[RepackWorker] = None
        self._thread: Optional[QThread] = None
        self._current_project_path: Optional[Path] = None
        self._current_repack_path: Optional[Path] = None
        self._output_dir: str = ""
        # Log batching: accumulate _log() calls and flush once per event-loop
        # tick.  Stops chatty repack runs from triggering a layout pass per
        # message; everything queued in the same tick gets one cursor + one
        # ensureCursorVisible call.
        self._log_queue: list[tuple[str, str]] = []
        self._log_flush_pending: bool = False

        self._build_ui()
        self._build_patch_bindings()
        self._build_repack_bindings()
        self._connect_signals()
        self._on_engine_changed()  # set initial compression list

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        self._build_menu_bar()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Mode tab widget ──
        self.mode_tabs = QTabWidget()
        self.mode_tabs.addTab(self._build_patch_panel(), "Update Patch")
        self.mode_tabs.addTab(self._build_repack_panel(), "Repack")
        # Restore the last-used mode tab (G5).  Bounded to valid range in
        # case the saved value is stale after a UI change.
        self.mode_tabs.setCurrentIndex(
            max(0, min(self._app_settings.mode_tab_index, self.mode_tabs.count() - 1))
        )

        # ── Splitter: left tabs / right log ──
        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        root.addWidget(self._splitter, 1)

        self._splitter.addWidget(self.mode_tabs)
        self._splitter.addWidget(self._build_output_panel())
        # Restore splitter ratio (G5).  Sanity-check we got two ints summing
        # to something positive; fall back to defaults otherwise.
        sizes = self._app_settings.splitter_sizes
        if isinstance(sizes, list) and len(sizes) == 2 and all(isinstance(s, int) and s > 0 for s in sizes):
            self._splitter.setSizes(sizes)
        else:
            self._splitter.setSizes([580, 480])

        # ── Bottom button bar ──
        root.addWidget(HSep())
        root.addLayout(self._build_button_bar())

        # ── Status bar ──
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def _build_menu_bar(self) -> None:
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")

        self._recent_menu = QMenu("Open &Recent", self)
        self._recent_menu.aboutToShow.connect(self._rebuild_recent_menu)
        file_menu.addMenu(self._recent_menu)

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        entries = _recent.load()
        if not entries:
            act = QAction("(no recent files)", self)
            act.setEnabled(False)
            self._recent_menu.addAction(act)
            return
        for entry in entries:
            p = Path(entry["path"])
            kind = entry["kind"]
            label = f"{p.name}  [{kind}]  —  {p.parent}"
            act = QAction(label, self)
            act.setData(entry)
            act.triggered.connect(self._on_open_recent)
            self._recent_menu.addAction(act)
        self._recent_menu.addSeparator()
        clear_act = QAction("Clear Recent", self)
        clear_act.triggered.connect(self._on_clear_recent)
        self._recent_menu.addAction(clear_act)

    def _on_open_recent(self) -> None:
        entry = self.sender().data()
        path = Path(entry["path"])
        kind = entry["kind"]
        if not path.exists():
            _recent.remove(path)
            self.status_bar.showMessage(f"File not found: {path}")
            return
        if kind == "repack":
            self.mode_tabs.setCurrentIndex(1)
            try:
                s = load_repack(path)
                self._apply_repack_settings(s)
                self._current_repack_path = path
                self._set_project_title(path)
                self.status_bar.showMessage(f"Loaded: {path}")
                _recent.add(path, "repack")
            except Exception as exc:
                self._log(f"Failed to load repack project: {exc}", color=ERROR)
        else:
            self.mode_tabs.setCurrentIndex(0)
            try:
                s = load_project(path)
                self._apply_settings(s)
                self._current_project_path = path
                self._set_project_title(path)
                self.status_bar.showMessage(f"Loaded: {path}")
                _recent.add(path, "patch")
            except Exception as exc:
                self._log(f"Failed to load project: {exc}", color=ERROR)

    def _on_clear_recent(self) -> None:
        _recent.clear()

    def _grid_lineedit(self, grid, row, col, label, placeholder="",
                       colspan=1, readonly=False):
        """Add a label + QLineEdit pair to a QGridLayout. Returns the edit.
        Used to collapse the recurring 4-line "addWidget(QLabel),
        self.X = QLineEdit(), setPlaceholderText, addWidget(self.X)" pattern
        in the panel builders below."""
        grid.addWidget(QLabel(label), row, col)
        edit = QLineEdit()
        if placeholder:
            edit.setPlaceholderText(placeholder)
        if readonly:
            edit.setReadOnly(True)
        grid.addWidget(edit, row, col + 1, 1, colspan)
        return edit

    def _grid_browse_row(self, grid, row, col, label, placeholder,
                         browse_cb, colspan=3):
        """Add a label + read-only QLineEdit + Browse + clear-button row.
        Used by the icon-picker and backdrop-picker rows in both panels.
        Returns (edit, browse_button, clear_button)."""
        grid.addWidget(QLabel(label), row, col)
        row_layout = QHBoxLayout()
        row_layout.setSpacing(4)
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setReadOnly(True)
        row_layout.addWidget(edit)
        browse = QPushButton("Browse…")
        browse.setFixedWidth(70)
        browse.clicked.connect(browse_cb)
        row_layout.addWidget(browse)
        clear = QPushButton("✕")
        clear.setFixedWidth(24)
        clear.setToolTip(f"Clear {label.rstrip(':').lower()}")
        clear.clicked.connect(lambda: edit.setText(""))
        row_layout.addWidget(clear)
        container = QWidget()
        container.setLayout(row_layout)
        grid.addWidget(container, row, col + 1, 1, colspan)
        return edit, browse, clear

    def _build_patch_panel(self) -> QWidget:
        # Wrap everything in a scroll area so the panel doesn't get clipped
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(8)

        # ── Directories ──────────────────────────────────────────────────
        files_grp = QGroupBox("Directories")
        fg = QVBoxLayout(files_grp)
        fg.setSpacing(5)
        self.src_picker = FilePicker("Source dir:", "dir")
        self.tgt_picker = FilePicker("Target dir:", "dir")
        self.out_picker = FilePicker("Output dir:", "dir")
        fg.addWidget(self.src_picker)
        fg.addWidget(self.tgt_picker)
        fg.addWidget(self.out_picker)
        layout.addWidget(files_grp)

        # ── Patch metadata ───────────────────────────────────────────────
        meta_grp = QGroupBox("Patch Info")
        mg = QGridLayout(meta_grp)
        mg.setSpacing(6)
        mg.setColumnStretch(1, 1)
        mg.setColumnStretch(3, 1)

        self.app_name_edit          = self._grid_lineedit(mg, 0, 0, "App name:",     "My Application")
        self.app_note_edit          = self._grid_lineedit(mg, 0, 2, "App note:",     "Short subtitle (optional)")
        self.version_edit           = self._grid_lineedit(mg, 1, 0, "Version:",      "1.0.0")
        self.patch_exe_version_edit = self._grid_lineedit(mg, 1, 2, "Exe version:",  "1.0.0.0  (informational)")
        self.desc_edit              = self._grid_lineedit(mg, 2, 0, "Description:",  "Optional description shown in patcher", colspan=3)
        self.copyright_edit         = self._grid_lineedit(mg, 3, 0, "Copyright:",    "© 2025 My Company")
        self.company_info_edit      = self._grid_lineedit(mg, 3, 2, "Company:",      "Publisher / company name")
        self.contact_edit           = self._grid_lineedit(mg, 4, 0, "Contact:",      "support@example.com or URL")
        self.window_title_edit      = self._grid_lineedit(mg, 4, 2, "Window title:", "Patcher title bar (defaults to app name)")
        self.patch_exe_name_edit    = self._grid_lineedit(
            mg, 5, 0, "Exe name:",
            "Output exe filename stem — blank = auto (AppName_version_patch_x64.exe)",
            colspan=3,
        )

        self.icon_edit, self.icon_browse_btn, self.icon_clear_btn = self._grid_browse_row(
            mg, 6, 0, "Icon (.ico):",
            "Optional — leave blank for default icon",
            self._on_icon_browse,
        )

        self.backdrop_edit, self.backdrop_browse_btn, self.backdrop_clear_btn = self._grid_browse_row(
            mg, 7, 0, "Backdrop:",
            "Optional background image (PNG/JPEG/BMP)",
            self._on_backdrop_browse,
        )

        layout.addWidget(meta_grp)

        # ── Engine + compression + verify ────────────────────────────────
        eng_grp = QGroupBox("Engine & Compression")
        eg = QGridLayout(eng_grp)
        eg.setSpacing(6)
        eg.setColumnStretch(1, 1)
        eg.setColumnStretch(3, 1)

        eg.addWidget(QLabel("Engine:"),      0, 0)
        self.engine_combo = QComboBox()
        # Items carry their settings key as UserRole data so callers can
        # use currentData() / findData() instead of position-based indexing.
        self.engine_combo.addItem("HDiffPatch 4.12.2", userData="hdiffpatch")
        self.engine_combo.addItem("xdelta3 3.0.8",     userData="xdelta3")
        self.engine_combo.addItem("JojoDiff 0.8.1",    userData="jojodiff")
        eg.addWidget(self.engine_combo, 0, 1)

        eg.addWidget(QLabel("Compression:"), 0, 2)
        self.comp_combo = QComboBox()
        eg.addWidget(self.comp_combo, 0, 3)

        eg.addWidget(QLabel("Threads:"),     1, 0)
        self.threads_combo = QComboBox()
        for t in THREAD_OPTIONS:
            self.threads_combo.addItem(str(t), userData=t)
        eg.addWidget(self.threads_combo, 1, 1)

        self.quality_lbl = QLabel("Quality:")
        eg.addWidget(self.quality_lbl, 1, 2)
        self.quality_combo = QComboBox()
        eg.addWidget(self.quality_combo, 1, 3)

        eg.addWidget(QLabel("Verify:"),      2, 0)
        self.verify_combo = QComboBox()
        self.verify_combo.addItems(["CRC32C SUM", "MD5 HASH", "FILESIZE"])
        eg.addWidget(self.verify_combo, 2, 1)

        eg.addWidget(QLabel("Architecture:"), 2, 2)
        arch_widget = QWidget()
        arch_layout = QHBoxLayout(arch_widget)
        arch_layout.setContentsMargins(0, 0, 0, 0)
        self.arch_x64 = QRadioButton("x64")
        self.arch_x86 = QRadioButton("x86")
        self.arch_x64.setChecked(True)
        self._arch_group = QButtonGroup()
        self._arch_group.addButton(self.arch_x64)
        self._arch_group.addButton(self.arch_x86)
        arch_layout.addWidget(self.arch_x64)
        arch_layout.addWidget(self.arch_x86)
        arch_layout.addStretch()
        eg.addWidget(arch_widget, 2, 3)

        eg.addWidget(QLabel("Extra diff args:"), 3, 0)
        self.extra_diff_args_edit = QLineEdit()
        self.extra_diff_args_edit.setPlaceholderText(
            "Optional — extra flags passed to the engine CLI")
        eg.addWidget(self.extra_diff_args_edit, 3, 1, 1, 3)

        # Stub warning label
        self.stub_warn_lbl = QLabel()
        self.stub_warn_lbl.setObjectName("dim")
        self.stub_warn_lbl.setWordWrap(True)
        self.stub_warn_lbl.hide()
        eg.addWidget(self.stub_warn_lbl, 4, 0, 1, 4)

        layout.addWidget(eng_grp)

        # ── Target file discovery ────────────────────────────────────────
        find_grp = QGroupBox("Target File Detection")
        find_outer = QVBoxLayout(find_grp)
        find_outer.setSpacing(5)

        method_row = QHBoxLayout()
        method_row.setSpacing(12)
        self.find_manual   = QRadioButton("Manual (user browses)")
        self.find_registry = QRadioButton("Registry")
        self.find_ini      = QRadioButton("INI file")
        self.find_manual.setChecked(True)
        self._find_group = QButtonGroup()
        for r in (self.find_manual, self.find_registry, self.find_ini):
            self._find_group.addButton(r)
            method_row.addWidget(r)
        method_row.addStretch()
        find_outer.addLayout(method_row)

        # Registry sub-panel
        self.reg_panel = QWidget()
        rg = QGridLayout(self.reg_panel)
        rg.setContentsMargins(0, 0, 0, 0)
        rg.setSpacing(5)
        rg.setColumnStretch(1, 1)
        self.reg_key_edit = self._grid_lineedit(rg, 0, 0, "Key:",
            r"SOFTWARE\MyCompany\MyApp")
        self.reg_val_edit = self._grid_lineedit(rg, 1, 0, "Value:",
            "InstallPath  (leave blank for default)")
        self.reg_panel.hide()
        find_outer.addWidget(self.reg_panel)

        # INI sub-panel
        self.ini_panel = QWidget()
        ig = QGridLayout(self.ini_panel)
        ig.setContentsMargins(0, 0, 0, 0)
        ig.setSpacing(5)
        ig.setColumnStretch(1, 1)
        self.ini_path_picker = FilePicker("INI file:", "open", "INI files (*.ini);;All files (*)")
        ig.addWidget(self.ini_path_picker, 0, 0, 1, 2)
        self.ini_section_edit = self._grid_lineedit(ig, 1, 0, "Section:", "Settings")
        self.ini_key_edit     = self._grid_lineedit(ig, 2, 0, "Key:",     "InstallPath")
        self.ini_panel.hide()
        find_outer.addWidget(self.ini_panel)

        layout.addWidget(find_grp)

        # ── Advanced Patching ────────────────────────────────────────────
        adv_grp = QGroupBox("Advanced Patching")
        ag = QGridLayout(adv_grp)
        ag.setSpacing(6)
        ag.setColumnStretch(1, 1)

        # delete_extra_files checkbox
        self.delete_extra_chk = QCheckBox("Delete extra files from game folder")
        self.delete_extra_chk.setChecked(True)
        self.delete_extra_chk.setToolTip(
            "If enabled, files present in the game folder but absent from the\n"
            "target version will be deleted during patching."
        )
        ag.addWidget(self.delete_extra_chk, 0, 0, 1, 3)

        # Extra files list
        ag.addWidget(QLabel("Extra files:"), 1, 0, Qt.AlignTop)
        self.extra_files_list = QListWidget()
        self.extra_files_list.setFixedHeight(72)
        self.extra_files_list.setToolTip(
            "Files to copy into the game folder after patching.\n"
            "Format: dest_path ← src_path"
        )
        ag.addWidget(self.extra_files_list, 1, 1)
        ef_btn_col = QVBoxLayout()
        ef_btn_col.setSpacing(4)
        self.ef_add_btn = QPushButton("Add…")
        self.ef_add_btn.setFixedWidth(60)
        self.ef_add_btn.clicked.connect(self._on_ef_add)
        self.ef_remove_btn = QPushButton("Remove")
        self.ef_remove_btn.setFixedWidth(60)
        self.ef_remove_btn.clicked.connect(self._on_ef_remove)
        ef_btn_col.addWidget(self.ef_add_btn)
        ef_btn_col.addWidget(self.ef_remove_btn)
        ef_btn_col.addStretch()
        ef_btn_w = QWidget()
        ef_btn_w.setLayout(ef_btn_col)
        ag.addWidget(ef_btn_w, 1, 2)

        # run_before / run_after
        self.run_before_edit = self._grid_lineedit(ag, 2, 0, "Run before:",
            "Command to run before patching (optional)", colspan=2)
        self.run_after_edit  = self._grid_lineedit(ag, 3, 0, "Run after:",
            "Command to run after patching (optional)", colspan=2)

        # Backup
        ag.addWidget(QLabel("Backup:"), 4, 0)
        self.backup_combo = QComboBox()
        self.backup_combo.addItem("Same folder (sibling directory)", userData="same_folder")
        self.backup_combo.addItem("Custom location",                userData="custom")
        self.backup_combo.addItem("Disabled",                       userData="disabled")
        ag.addWidget(self.backup_combo, 4, 1, 1, 2)

        self.backup_path_lbl = QLabel("Backup path:")
        ag.addWidget(self.backup_path_lbl, 5, 0)
        self.backup_path_edit = QLineEdit()
        self.backup_path_edit.setPlaceholderText("Backup directory path")
        bp_row = QHBoxLayout()
        bp_row.setSpacing(4)
        bp_row.addWidget(self.backup_path_edit)
        self.backup_path_browse = QPushButton("…")
        self.backup_path_browse.setFixedWidth(26)
        self.backup_path_browse.clicked.connect(self._on_backup_path_browse)
        bp_row.addWidget(self.backup_path_browse)
        bp_w = QWidget()
        bp_w.setLayout(bp_row)
        ag.addWidget(bp_w, 5, 1, 1, 2)
        # hide backup path row initially (same_folder is default)
        self.backup_path_lbl.hide()
        bp_w.hide()
        self._backup_path_widget = bp_w

        # Preserve timestamps
        self.preserve_timestamps_chk = QCheckBox("Preserve original file timestamps after patching")
        self.preserve_timestamps_chk.setToolTip(
            "Before patching, snapshot the modification time of every file in the game\n"
            "folder. After patching succeeds, restore those timestamps. Useful for\n"
            "games that check file dates for integrity or DRM purposes."
        )
        ag.addWidget(self.preserve_timestamps_chk, 6, 0, 1, 3)

        # Required free space
        ag.addWidget(QLabel("Min. free space:"), 7, 0)
        free_space_row = QHBoxLayout()
        free_space_row.setSpacing(6)
        self.free_space_spin = QDoubleSpinBox()
        self.free_space_spin.setRange(0.0, 999.0)
        self.free_space_spin.setSingleStep(0.5)
        self.free_space_spin.setDecimals(1)
        self.free_space_spin.setValue(0.0)
        self.free_space_spin.setFixedWidth(80)
        self.free_space_spin.setToolTip("Warn if available disk space is below this threshold (0 = disabled)")
        free_space_row.addWidget(self.free_space_spin)
        free_space_row.addWidget(QLabel("GB  (0 = no check)"))
        free_space_row.addStretch()
        fs_w = QWidget()
        fs_w.setLayout(free_space_row)
        ag.addWidget(fs_w, 7, 1, 1, 2)

        # Close delay
        ag.addWidget(QLabel("Auto-close delay:"), 8, 0)
        close_delay_row = QHBoxLayout()
        close_delay_row.setSpacing(6)
        self.close_delay_spin = QSpinBox()
        self.close_delay_spin.setRange(0, 3600)
        self.close_delay_spin.setSingleStep(1)
        self.close_delay_spin.setValue(0)
        self.close_delay_spin.setFixedWidth(70)
        self.close_delay_spin.setToolTip("Seconds before auto-closing after a successful patch (0 = stay open)")
        close_delay_row.addWidget(self.close_delay_spin)
        close_delay_row.addWidget(QLabel("seconds  (0 = stay open)"))
        close_delay_row.addStretch()
        cd_w = QWidget()
        cd_w.setLayout(close_delay_row)
        ag.addWidget(cd_w, 8, 1, 1, 2)

        # Detect running exe
        self.detect_running_edit = self._grid_lineedit(ag, 9, 0, "Detect running:",
            "e.g. GameApp.exe — warn if running before patching", colspan=2)
        self.detect_running_edit.setToolTip(
            "If the specified process is running when the user clicks Patch,\n"
            "a warning dialog will appear asking whether to continue."
        )

        # Run on startup / Run on finish
        self.run_on_startup_edit = self._grid_lineedit(ag, 10, 0, "Run on startup:",
            "Command to run when the patcher window opens (optional)", colspan=2)
        self.run_on_finish_edit  = self._grid_lineedit(ag, 11, 0, "Run on finish:",
            "Command to run after successful patch + dialog (optional)", colspan=2)

        layout.addWidget(adv_grp)
        layout.addStretch()

        scroll.setWidget(inner)
        return scroll

    def _build_repack_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(8)

        # ── Directories ──────────────────────────────────────────────────
        dirs_grp = QGroupBox("Directories")
        dg = QVBoxLayout(dirs_grp)
        dg.setSpacing(5)
        self.rp_game_picker  = FilePicker("Game dir:", "dir")
        self.rp_out_picker   = FilePicker("Output dir:", "dir")
        dg.addWidget(self.rp_game_picker)
        dg.addWidget(self.rp_out_picker)
        layout.addWidget(dirs_grp)

        # ── Optional Components ──────────────────────────────────────────
        comp_grp = QGroupBox("Optional Components")
        comp_grp.setToolTip(
            "Extra folders to offer during install.\n"
            "Each becomes a checkbox (or radio button if in a group) that the user can toggle."
        )
        comp_layout = QVBoxLayout(comp_grp)
        comp_layout.setSpacing(4)

        comp_note = QLabel(
            "Each component is a separate folder that will be merged on top of the base game. "
            "Components in the same group are mutually exclusive (radio buttons); "
            "others are independent checkboxes."
        )
        comp_note.setWordWrap(True)
        comp_note.setObjectName("dim")
        comp_layout.addWidget(comp_note)

        self.rp_comp_list = QListWidget()
        self.rp_comp_list.setFixedHeight(110)
        self.rp_comp_list.setSelectionMode(QListWidget.SingleSelection)
        comp_layout.addWidget(self.rp_comp_list)

        comp_btn_row = QHBoxLayout()
        comp_add_btn    = QPushButton("Add…")
        comp_edit_btn   = QPushButton("Edit…")
        comp_remove_btn = QPushButton("Remove")
        comp_add_btn.setFixedWidth(70)
        comp_edit_btn.setFixedWidth(70)
        comp_remove_btn.setFixedWidth(70)
        comp_btn_row.addWidget(comp_add_btn)
        comp_btn_row.addWidget(comp_edit_btn)
        comp_btn_row.addWidget(comp_remove_btn)
        comp_btn_row.addStretch()
        comp_layout.addLayout(comp_btn_row)

        comp_add_btn.clicked.connect(self._on_rp_comp_add)
        comp_edit_btn.clicked.connect(self._on_rp_comp_edit)
        comp_remove_btn.clicked.connect(self._on_rp_comp_remove)
        self.rp_comp_list.itemDoubleClicked.connect(self._on_rp_comp_edit)

        layout.addWidget(comp_grp)

        # ── Installer Info ───────────────────────────────────────────────
        info_grp = QGroupBox("Installer Info")
        ig = QGridLayout(info_grp)
        ig.setSpacing(6)
        ig.setColumnStretch(1, 1)
        ig.setColumnStretch(3, 1)

        self.rp_app_name_edit     = self._grid_lineedit(ig, 0, 0, "App name:",     "My Game")
        self.rp_app_note_edit     = self._grid_lineedit(ig, 0, 2, "App note:",     "Short subtitle (optional)")
        self.rp_version_edit      = self._grid_lineedit(ig, 1, 0, "Version:",      "1.0.0")
        self.rp_exe_version_edit  = self._grid_lineedit(ig, 1, 2, "Exe version:",  "1.0.0.0  (informational)")
        self.rp_desc_edit         = self._grid_lineedit(ig, 2, 0, "Description:",  "Optional description shown in installer", colspan=3)
        self.rp_copyright_edit    = self._grid_lineedit(ig, 3, 0, "Copyright:",    "© 2025 My Company")
        self.rp_company_edit      = self._grid_lineedit(ig, 3, 2, "Company:",      "Publisher / company name")
        self.rp_contact_edit      = self._grid_lineedit(ig, 4, 0, "Contact:",      "support@example.com or URL")
        self.rp_window_title_edit = self._grid_lineedit(ig, 4, 2, "Window title:", "Installer title bar (defaults to app name)")
        self.rp_exe_name_edit     = self._grid_lineedit(
            ig, 5, 0, "Exe name:",
            "Output exe filename stem — blank = auto (AppName_version_installer_x64.exe)",
            colspan=3,
        )

        self.rp_icon_edit, _, _ = self._grid_browse_row(
            ig, 6, 0, "Icon (.ico):",
            "Optional — leave blank for default icon",
            self._on_rp_icon_browse,
        )
        self.rp_backdrop_edit, _, _ = self._grid_browse_row(
            ig, 7, 0, "Backdrop:",
            "Optional background image (PNG/JPEG/BMP)",
            self._on_rp_backdrop_browse,
        )

        layout.addWidget(info_grp)

        # ── Compression & Architecture ───────────────────────────────────
        comp_grp = QGroupBox("Compression & Architecture")
        cg = QGridLayout(comp_grp)
        cg.setSpacing(6)
        cg.setColumnStretch(1, 1)
        cg.setColumnStretch(3, 1)

        cg.addWidget(QLabel("Codec:"), 0, 0)
        rp_codec_w = QWidget()
        rp_codec_l = QHBoxLayout(rp_codec_w)
        rp_codec_l.setContentsMargins(0, 0, 0, 0)
        self.rp_codec_lzma = QRadioButton("LZMA")
        self.rp_codec_zstd = QRadioButton("Zstd")
        self.rp_codec_lzma.setChecked(True)
        self._rp_codec_group = QButtonGroup()
        self._rp_codec_group.addButton(self.rp_codec_lzma)
        self._rp_codec_group.addButton(self.rp_codec_zstd)
        rp_codec_l.addWidget(self.rp_codec_lzma)
        rp_codec_l.addWidget(self.rp_codec_zstd)
        rp_codec_l.addStretch()
        cg.addWidget(rp_codec_w, 0, 1)

        cg.addWidget(QLabel("Architecture:"), 0, 2)
        rp_arch_w = QWidget()
        rp_arch_l = QHBoxLayout(rp_arch_w)
        rp_arch_l.setContentsMargins(0, 0, 0, 0)
        self.rp_arch_x64 = QRadioButton("x64")
        self.rp_arch_x86 = QRadioButton("x86")
        self.rp_arch_x64.setChecked(True)
        self._rp_arch_group = QButtonGroup()
        self._rp_arch_group.addButton(self.rp_arch_x64)
        self._rp_arch_group.addButton(self.rp_arch_x86)
        rp_arch_l.addWidget(self.rp_arch_x64)
        rp_arch_l.addWidget(self.rp_arch_x86)
        rp_arch_l.addStretch()
        cg.addWidget(rp_arch_w, 0, 3)

        cg.addWidget(QLabel("Quality:"), 1, 0)
        self.rp_comp_combo = QComboBox()
        cg.addWidget(self.rp_comp_combo, 1, 1)

        cg.addWidget(QLabel("Threads:"), 1, 2)
        self.rp_threads_combo = QComboBox()
        for t in REPACK_THREAD_OPTIONS:
            self.rp_threads_combo.addItem(str(t), userData=t)
        cg.addWidget(self.rp_threads_combo, 1, 3)

        layout.addWidget(comp_grp)

        # ── Post-Install Options ─────────────────────────────────────────
        post_grp = QGroupBox("Post-Install Options")
        pg = QGridLayout(post_grp)
        pg.setSpacing(6)
        pg.setColumnStretch(1, 1)

        self.rp_registry_key_edit   = self._grid_lineedit(pg, 0, 0, "Registry key:",
            r"SOFTWARE\MyCompany\MyGame  — written to HKCU after install (for patch detection)")
        self.rp_run_after_edit      = self._grid_lineedit(pg, 1, 0, "Run after install:",
            "Command to run after successful install (optional)")
        self.rp_detect_running_edit = self._grid_lineedit(pg, 2, 0, "Detect running:",
            "e.g. GameApp.exe — warn if running before install")

        pg.addWidget(QLabel("Min. free space:"), 3, 0)
        rp_fs_row = QHBoxLayout()
        rp_fs_row.setSpacing(6)
        self.rp_free_space_spin = QDoubleSpinBox()
        self.rp_free_space_spin.setRange(0.0, 9999.0)
        self.rp_free_space_spin.setSingleStep(0.5)
        self.rp_free_space_spin.setDecimals(1)
        self.rp_free_space_spin.setValue(0.0)
        self.rp_free_space_spin.setFixedWidth(90)
        self.rp_free_space_spin.setToolTip("Warn if available disk space is below this threshold (0 = disabled)")
        rp_fs_row.addWidget(self.rp_free_space_spin)
        rp_fs_row.addWidget(QLabel("GB  (0 = no check)"))
        rp_fs_row.addStretch()
        rp_fs_w = QWidget(); rp_fs_w.setLayout(rp_fs_row)
        pg.addWidget(rp_fs_w, 3, 1)

        pg.addWidget(QLabel("Auto-close delay:"), 4, 0)
        rp_cd_row = QHBoxLayout()
        rp_cd_row.setSpacing(6)
        self.rp_close_delay_spin = QSpinBox()
        self.rp_close_delay_spin.setRange(0, 3600)
        self.rp_close_delay_spin.setValue(0)
        self.rp_close_delay_spin.setFixedWidth(70)
        rp_cd_row.addWidget(self.rp_close_delay_spin)
        rp_cd_row.addWidget(QLabel("seconds  (0 = stay open)"))
        rp_cd_row.addStretch()
        rp_cd_w = QWidget(); rp_cd_w.setLayout(rp_cd_row)
        pg.addWidget(rp_cd_w, 4, 1)

        self.rp_include_uninstaller_chk = QCheckBox(
            "Include uninstaller (uninstall.exe + Add/Remove Programs entry)")
        self.rp_include_uninstaller_chk.setChecked(True)
        pg.addWidget(self.rp_include_uninstaller_chk, 5, 0, 1, 2)

        self.rp_verify_crc32_chk = QCheckBox(
            "Verify file integrity after installation (CRC32) — end user can skip with /NOVERIFY")
        self.rp_verify_crc32_chk.setChecked(True)
        pg.addWidget(self.rp_verify_crc32_chk, 6, 0, 1, 2)

        self.rp_split_bin_chk = QCheckBox(
            "Write base game data to separate base_game.bin  (required for games > 3.5 GB; "
            "both files must be distributed together)")
        self.rp_split_bin_chk.setChecked(False)
        self.rp_split_bin_chk.setToolTip(
            "Forces the compressed game data into a base_game.bin sidecar file.\n"
            "This is applied automatically when the data exceeds the threshold\n"
            "set in ~/.config/patchforge/app_settings.json (default 3.5 GB)."
        )
        pg.addWidget(self.rp_split_bin_chk, 7, 0, 1, 2)

        pg.addWidget(QLabel("Max part size (MB):"), 8, 0)
        self.rp_max_part_size_spin = QSpinBox()
        self.rp_max_part_size_spin.setRange(0, 100_000)
        self.rp_max_part_size_spin.setValue(0)
        self.rp_max_part_size_spin.setSpecialValueText("Off")
        self.rp_max_part_size_spin.setToolTip(
            "If set, splits base_game.bin into <name>.bin.001, .002, ... parts of this size.\n"
            "Useful for distribution on file hosts with upload size caps.\n"
            "0 (Off) = no multi-part split."
        )
        pg.addWidget(self.rp_max_part_size_spin, 8, 1)

        self.rp_shortcut_target_edit = self._grid_lineedit(pg, 9, 0, "Shortcut target:",
            "Relative path to game exe within install dir  (e.g. Game.exe)")
        self.rp_shortcut_name_edit   = self._grid_lineedit(pg, 10, 0, "Shortcut name:",
            "Display name  (blank = use App Name)")

        self.rp_shortcut_startmenu_chk = QCheckBox("Create Start Menu shortcut")
        self.rp_shortcut_startmenu_chk.setChecked(True)
        pg.addWidget(self.rp_shortcut_startmenu_chk, 11, 0, 1, 2)

        self.rp_shortcut_desktop_chk = QCheckBox("Create Desktop shortcut")
        self.rp_shortcut_desktop_chk.setChecked(False)
        pg.addWidget(self.rp_shortcut_desktop_chk, 12, 0, 1, 2)

        layout.addWidget(post_grp)
        layout.addStretch()

        scroll.setWidget(inner)
        return scroll

    def _build_output_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 0, 0, 0)
        layout.setSpacing(6)

        out_grp = QGroupBox("Build Output")
        og = QVBoxLayout(out_grp)
        og.setSpacing(6)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%  %v / 100")
        og.addWidget(self.progress_bar)

        self.status_lbl = QLabel("Idle")
        self.status_lbl.setAlignment(Qt.AlignCenter)
        self.status_lbl.setObjectName("dim")
        og.addWidget(self.status_lbl)

        # Per-stream compression status (repack builds only)
        self.stream_widget = QWidget()
        sw_layout = QVBoxLayout(self.stream_widget)
        sw_layout.setContentsMargins(0, 0, 0, 0)
        sw_layout.setSpacing(2)
        self.stream_label = QLabel()
        self.stream_bar = QProgressBar()
        self.stream_bar.setRange(0, 100)
        self.stream_bar.setValue(0)
        self.stream_bar.setFixedHeight(10)
        self.stream_bar.setTextVisible(False)
        sw_layout.addWidget(self.stream_label)
        sw_layout.addWidget(self.stream_bar)
        self.stream_widget.setVisible(False)
        og.addWidget(self.stream_widget)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Build output will appear here…")
        og.addWidget(self.log, 1)

        log_btn_row = QHBoxLayout()
        log_btn_row.setContentsMargins(0, 0, 0, 0)
        log_btn_row.setSpacing(6)
        self.clear_log_btn = QPushButton("Clear Log")
        self.clear_log_btn.clicked.connect(self._on_clear_log)
        log_btn_row.addWidget(self.clear_log_btn)
        log_btn_row.addStretch()
        og.addLayout(log_btn_row)

        self.open_folder_btn = QPushButton("Open Output Folder")
        self.open_folder_btn.setVisible(False)
        og.addWidget(self.open_folder_btn)

        layout.addWidget(out_grp, 1)
        return w

    def _build_button_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.build_btn = QPushButton("⚡  Build Patch")
        self.build_btn.setObjectName("accent")
        self.build_btn.setToolTip("Build the output exe (Ctrl+B)")

        self.new_btn   = QPushButton("New Project")
        self.new_btn.setToolTip("Reset all fields to defaults (Ctrl+N)")
        self.load_btn  = QPushButton("Load Project")
        self.load_btn.setToolTip("Open a .xpm or .xpr project file (Ctrl+O)")
        self.save_btn  = QPushButton("Save Project")
        self.save_btn.setToolTip("Save current settings to a project file (Ctrl+S)")

        bar.addWidget(self.build_btn)
        bar.addStretch()
        bar.addWidget(self.new_btn)
        bar.addWidget(self.load_btn)
        bar.addWidget(self.save_btn)
        return bar

    def _is_repack_mode(self) -> bool:
        return self.mode_tabs.currentIndex() == 1

    # ------------------------------------------------------------------ #
    # Signal wiring                                                        #
    # ------------------------------------------------------------------ #

    def _connect_signals(self):
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        self.comp_combo.currentIndexChanged.connect(self._on_compression_changed)
        self.rp_codec_lzma.toggled.connect(self._on_rp_codec_changed)
        self.rp_codec_zstd.toggled.connect(self._on_rp_codec_changed)
        self._on_rp_codec_changed()  # populate quality combo on startup

        self.find_manual.toggled.connect(self._on_find_method_changed)
        self.find_registry.toggled.connect(self._on_find_method_changed)
        self.find_ini.toggled.connect(self._on_find_method_changed)

        self.backup_combo.currentIndexChanged.connect(self._on_backup_changed)

        self.mode_tabs.currentChanged.connect(self._on_mode_changed)
        self.build_btn.clicked.connect(self._on_build)
        self.new_btn.clicked.connect(self._on_new_project)
        self.load_btn.clicked.connect(self._on_load_project)
        self.save_btn.clicked.connect(self._on_save_project)
        self.open_folder_btn.clicked.connect(self._on_open_output_folder)

        # G6: keyboard shortcuts.  Bound at the window level so they fire
        # regardless of which child widget has focus.  Each routes through
        # the button's clicked signal so disabled-state and connected
        # handlers all behave identically to a click.
        for keyseq, btn in (
            ("Ctrl+B", self.build_btn),
            ("Ctrl+N", self.new_btn),
            ("Ctrl+O", self.load_btn),
            ("Ctrl+S", self.save_btn),
        ):
            sc = QShortcut(QKeySequence(keyseq), self)
            sc.activated.connect(btn.click)

    def _on_open_output_folder(self) -> None:
        if self._output_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._output_dir))

    # ------------------------------------------------------------------ #
    # Slot handlers                                                        #
    # ------------------------------------------------------------------ #

    def _on_engine_changed(self):
        engine = self._engine_key()
        self.comp_combo.blockSignals(True)
        self.comp_combo.clear()

        if engine == "hdiffpatch":
            for key, lbl in HDiffPatchEngine.presets().items():
                self.comp_combo.addItem(lbl, userData=key)
            for i in range(self.comp_combo.count()):
                if self.comp_combo.itemData(i) == HDiffPatchEngine.default_preset():
                    self.comp_combo.setCurrentIndex(i)
                    break
            self.comp_combo.setEnabled(True)
        elif engine == "jojodiff":
            for key, lbl in JojoDiffEngine.presets().items():
                self.comp_combo.addItem(lbl, userData=key)
            for i in range(self.comp_combo.count()):
                if self.comp_combo.itemData(i) == JojoDiffEngine.default_preset():
                    self.comp_combo.setCurrentIndex(i)
                    break
            self.comp_combo.setEnabled(True)
        else:  # xdelta3
            for key, lbl in XDelta3Engine.presets().items():
                self.comp_combo.addItem(lbl, userData=key)
            for i in range(self.comp_combo.count()):
                if self.comp_combo.itemData(i) == XDelta3Engine.default_preset():
                    self.comp_combo.setCurrentIndex(i)
                    break
            self.comp_combo.setEnabled(True)

        is_hdiff = (engine == "hdiffpatch")
        self.quality_lbl.setVisible(is_hdiff)
        self.quality_combo.setVisible(is_hdiff)

        self.comp_combo.blockSignals(False)
        self._on_compression_changed()

    def _on_rp_codec_changed(self):
        codec = "zstd" if self.rp_codec_zstd.isChecked() else "lzma"
        quality_map = REPACK_ZSTD_QUALITY_LABELS if codec == "zstd" else REPACK_LZMA_QUALITY_LABELS
        default_key = "max"
        current = self.rp_comp_combo.currentData()
        self.rp_comp_combo.blockSignals(True)
        self.rp_comp_combo.clear()
        for key, label in quality_map.items():
            self.rp_comp_combo.addItem(label, userData=key)
        restore = current if current in quality_map else default_key
        for i in range(self.rp_comp_combo.count()):
            if self.rp_comp_combo.itemData(i) == restore:
                self.rp_comp_combo.setCurrentIndex(i)
                break
        self.rp_comp_combo.blockSignals(False)

    def _on_compression_changed(self):
        self.stub_warn_lbl.hide()
        if self._engine_key() != "hdiffpatch":
            return

        preset_key = self._compression_key()
        qualities = HDiffPatchEngine.qualities_for_preset(preset_key)

        current_quality = self.quality_combo.currentData()

        self.quality_combo.blockSignals(True)
        self.quality_combo.clear()
        for key, (lbl, _flag) in qualities.items():
            self.quality_combo.addItem(lbl, userData=key)
        restore = current_quality if current_quality in qualities else DEFAULT_QUALITY
        for i in range(self.quality_combo.count()):
            if self.quality_combo.itemData(i) == restore:
                self.quality_combo.setCurrentIndex(i)
                break
        self.quality_combo.blockSignals(False)

    def _on_icon_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Icon", "", "Icon files (*.ico)"
        )
        if path:
            self.icon_edit.setText(path)

    def _on_rp_icon_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Icon", "", "Icon files (*.ico)"
        )
        if path:
            self.rp_icon_edit.setText(path)

    def _on_rp_backdrop_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Backdrop Image", "",
            "Image files (*.png *.jpg *.jpeg *.bmp);;All files (*)"
        )
        if path:
            self.rp_backdrop_edit.setText(path)

    # ------------------------------------------------------------------ #
    # Optional component dialog helpers                                    #
    # ------------------------------------------------------------------ #

    def _component_dialog(self, title: str, data: dict | None = None) -> dict | None:
        """Show a dialog to add or edit a component. Returns dict or None if cancelled."""
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(420)

        form = QFormLayout(dlg)
        form.setSpacing(8)

        label_edit = QLineEdit(data.get("label", "") if data else "")
        label_edit.setPlaceholderText("e.g. Crack, DLC Pack 1")
        form.addRow("Label:", label_edit)

        folder_row = QHBoxLayout()
        folder_edit = QLineEdit(data.get("folder", "") if data else "")
        folder_edit.setPlaceholderText("Folder containing files for this component")
        folder_edit.setReadOnly(True)
        folder_browse = QPushButton("Browse…")
        folder_browse.setFixedWidth(70)
        def _browse_folder():
            path = QFileDialog.getExistingDirectory(
                dlg, "Select Component Folder",
                folder_edit.text() or str(Path.home())
            )
            if path:
                folder_edit.setText(path)
        folder_browse.clicked.connect(_browse_folder)
        folder_row.addWidget(folder_edit)
        folder_row.addWidget(folder_browse)
        folder_w = QWidget(); folder_w.setLayout(folder_row)
        form.addRow("Folder:", folder_w)

        default_chk = QCheckBox("Selected by default")
        default_chk.setChecked(data.get("default_checked", True) if data else True)
        form.addRow("", default_chk)

        group_edit = QLineEdit(data.get("group", "") if data else "")
        group_edit.setPlaceholderText(
            "Leave blank for checkbox  —  same name = mutually exclusive radio buttons"
        )
        form.addRow("Group:", group_edit)

        existing_requires = data.get("requires", []) if data else []
        requires_edit = QLineEdit(", ".join(str(r) for r in existing_requires))
        requires_edit.setPlaceholderText(
            "Comma-separated component numbers that must be selected  (e.g. 1, 2)"
        )
        form.addRow("Requires:", requires_edit)

        shortcut_target_edit = QLineEdit(data.get("shortcut_target", "") if data else "")
        shortcut_target_edit.setPlaceholderText(
            "Override shortcut target for this component  (e.g. Crack\\Game.exe)"
        )
        form.addRow("Shortcut target:", shortcut_target_edit)

        sac_warn_chk = QCheckBox("Show antivirus / Smart App Control warning when selected")
        sac_warn_chk.setChecked(bool(data.get("sac_warning", False)) if data else False)
        form.addRow("", sac_warn_chk)

        ext_chk = QCheckBox(
            "Store in separate .bin file  (both files must be distributed together)")
        ext_chk.setChecked(bool(data.get("external", False)) if data else False)
        ext_chk.setToolTip(
            "When checked the compressed stream for this component is written to\n"
            "<group_or_label>.bin alongside the installer instead of embedded inside it.\n"
            "Useful for large or optional DLC that not all users need to download."
        )
        form.addRow("", ext_chk)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() != QDialog.Accepted:
            return None
        lbl = label_edit.text().strip()
        fld = folder_edit.text().strip()
        if not lbl or not fld:
            return None
        requires = []
        for tok in requires_edit.text().split(","):
            tok = tok.strip()
            if tok.isdigit() and int(tok) > 0:
                requires.append(int(tok))
        return {
            "label":            lbl,
            "folder":           fld,
            "default_checked":  default_chk.isChecked(),
            "group":            group_edit.text().strip(),
            "requires":         requires,
            "shortcut_target":  shortcut_target_edit.text().strip(),
            "sac_warning":      sac_warn_chk.isChecked(),
            "external":         ext_chk.isChecked(),
        }

    @staticmethod
    def _comp_item_text(c: dict) -> str:
        chk = "✓" if c.get("default_checked", True) else "○"
        grp = f"  [group: {c['group']}]" if c.get("group") else ""
        req = c.get("requires", [])
        req_str = f"  [requires: {', '.join(str(r) for r in req)}]" if req else ""
        folder_name = Path(c["folder"]).name if c.get("folder") else ""
        sc = f"  [→ {c['shortcut_target']}]" if c.get("shortcut_target") else ""
        sac = "  [! SAC warning]" if c.get("sac_warning") else ""
        ext = "  [.bin]" if c.get("external") else ""
        return f"[{chk}]  {c['label']}  ({folder_name}){grp}{req_str}{sc}{sac}{ext}"

    def _on_rp_comp_add(self):
        comp = self._component_dialog("Add Optional Component")
        if comp is None:
            return
        item = QListWidgetItem(self._comp_item_text(comp))
        item.setData(Qt.UserRole, comp)
        self.rp_comp_list.addItem(item)

    def _on_rp_comp_edit(self, _item=None):
        row = self.rp_comp_list.currentRow()
        if row < 0:
            return
        item = self.rp_comp_list.item(row)
        comp = self._component_dialog("Edit Optional Component", item.data(Qt.UserRole))
        if comp is None:
            return
        item.setText(self._comp_item_text(comp))
        item.setData(Qt.UserRole, comp)

    def _on_rp_comp_remove(self):
        row = self.rp_comp_list.currentRow()
        if row >= 0:
            self.rp_comp_list.takeItem(row)

    def _on_find_method_changed(self):
        self.reg_panel.setVisible(self.find_registry.isChecked())
        self.ini_panel.setVisible(self.find_ini.isChecked())

    def _on_backup_changed(self):
        is_custom = (self.backup_combo.currentData() == "custom")
        self.backup_path_lbl.setVisible(is_custom)
        self._backup_path_widget.setVisible(is_custom)

    def _on_backup_path_browse(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select Backup Folder",
            self.backup_path_edit.text() or str(Path.home())
        )
        if path:
            self.backup_path_edit.setText(path)

    def _on_backdrop_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Backdrop Image", "",
            "Image files (*.png *.jpg *.jpeg *.bmp);;All files (*)"
        )
        if path:
            self.backdrop_edit.setText(path)

    def _on_ef_add(self):
        src_path, _ = QFileDialog.getOpenFileName(
            self, "Select file to bundle", str(Path.home()), "All files (*)"
        )
        if not src_path:
            return
        dest, ok = QInputDialog.getText(
            self, "Destination path",
            "Destination path inside game folder\n(e.g. DLC\\pack1.pak):",
            text=Path(src_path).name,
        )
        if not ok or not dest.strip():
            return
        dest = dest.strip()
        item = QListWidgetItem(f"{dest}  ←  {src_path}")
        item.setData(Qt.UserRole, {"src": src_path, "dest": dest})
        self.extra_files_list.addItem(item)

    def _on_ef_remove(self):
        for item in self.extra_files_list.selectedItems():
            self.extra_files_list.takeItem(self.extra_files_list.row(item))

    def _on_mode_changed(self, index: int):
        if index == 0:
            self.build_btn.setText("⚡  Build Patch")
        else:
            self.build_btn.setText("⚡  Build Repack")

    def _on_build(self):
        if self._thread and self._thread.isRunning():
            return
        if self._is_repack_mode():
            self._on_build_repack()
        else:
            self._on_build_patch()

    def _on_build_patch(self):
        settings = self._collect_settings()
        if not settings:
            return

        self.build_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        # Don't clear the log automatically — the user can use the
        # Clear Log button if they want to start fresh.  Insert a thin
        # separator so the new run is visually distinct from the prior.
        if self.log.toPlainText():
            self._log("─" * 60)
        self._log("Starting patch build…")

        if self._thread:
            self._thread.deleteLater()
        self._thread = QThread()
        self._worker = BuildWorker(settings)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_build_done)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._on_thread_done)
        self._thread.start()

    def _on_build_repack(self):
        settings = self._collect_repack_settings()
        if not settings:
            return

        self.build_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.stream_widget.setVisible(False)
        if self.log.toPlainText():
            self._log("─" * 60)
        self._log("Starting repack build…")

        if self._thread:
            self._thread.deleteLater()
        self._thread = QThread()
        self._repack_worker = RepackWorker(settings)
        self._repack_worker.moveToThread(self._thread)
        self._thread.started.connect(self._repack_worker.run)
        self._repack_worker.progress.connect(self._on_progress)
        self._repack_worker.stream_progress.connect(self._on_stream_progress)
        self._repack_worker.finished.connect(self._on_repack_done)
        self._repack_worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._on_thread_done)
        self._thread.start()

    def _on_progress(self, pct: int, msg: str, kind: str):
        self.progress_bar.setValue(pct)
        self.status_lbl.setText(msg)
        # Per-file stream messages are shown in the stream widget; skip
        # logging them. Tag-based filter replaces the prior string-match
        # heuristic so renaming a status message can't silently break it.
        if msg and kind != "file":
            self._log(msg)

    def _on_stream_progress(self, stream_idx: int, num_streams: int,
                            label: str, done: int, total: int, file_size: str):
        self.stream_widget.setVisible(True)
        size_str = f"  ({file_size})" if file_size else ""
        self.stream_label.setText(
            f"Stream {stream_idx + 1} / {num_streams}: {label} — "
            f"{done:,} / {total:,} files{size_str}"
        )
        self.stream_bar.setValue(done * 100 // total if total else 0)

    def _on_build_done(self, result: BuildResult):
        if result.success:
            self.progress_bar.setValue(100)
            self._log("\n✓  Done!", color=SUCCESS)
            self._log(f"   Output:      {result.output_path}",          color=SUCCESS)
            self._log(f"   Patch size:  {_fmt_size(result.patch_size)}", color=SUCCESS)
            self._log(f"   Output size: {_fmt_size(result.output_size)}", color=SUCCESS)
            self.status_bar.showMessage(f"Built: {Path(result.output_path).name}")
            self.status_lbl.setText("Build complete")
            self._output_dir = str(Path(result.output_path).parent)
            self.open_folder_btn.setVisible(True)
        else:
            self._log(f"\n✗  Build failed: {result.error}", color=ERROR)
            self.status_bar.showMessage("Build failed")
            self.status_lbl.setText("Failed")
            self.open_folder_btn.setVisible(False)

    def _on_repack_done(self, result: RepackResult):
        self.stream_widget.setVisible(False)
        if result.success:
            self.progress_bar.setValue(100)
            self._log("\n✓  Done!", color=SUCCESS)
            self._log(f"   Output:       {result.output_path}",                  color=SUCCESS)
            self._log(f"   Files packed: {result.total_files}",                  color=SUCCESS)
            self._log(f"   Game size:    {_fmt_size(result.uncompressed_size)}", color=SUCCESS)
            self._log(f"   Installer:    {_fmt_size(result.output_size)}",       color=SUCCESS)
            ratio = result.output_size / result.uncompressed_size * 100 if result.uncompressed_size else 0
            self._log(f"   Compression:  {ratio:.1f}% of original", color=SUCCESS)
            self.status_bar.showMessage(f"Built: {Path(result.output_path).name}")
            self.status_lbl.setText("Build complete")
            self._output_dir = str(Path(result.output_path).parent)
            self.open_folder_btn.setVisible(True)
        else:
            self._log(f"\n✗  Repack failed: {result.error}", color=ERROR)
            self.status_bar.showMessage("Repack failed")
            self.status_lbl.setText("Failed")
            self.open_folder_btn.setVisible(False)

    def closeEvent(self, event):
        # G4: if a build is in progress, ask before exiting.  We don't have
        # a clean cancellation channel into core (engines run as
        # subprocesses), so the choice is "let it run to completion" or
        # "abandon it and let the OS reap subprocesses on exit".
        if self._thread and self._thread.isRunning():
            reply = QMessageBox.question(
                self,
                "Build in progress",
                "A build is still running.\n\n"
                "Closing now will abandon the in-progress build (any\n"
                "partial output may be incomplete).  Close anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self._thread.quit()
            self._thread.wait(3000)

        # G5: persist current window/splitter/tab state for next launch.
        self._app_settings.window_width    = self.width()
        self._app_settings.window_height   = self.height()
        self._app_settings.splitter_sizes  = list(self._splitter.sizes())
        self._app_settings.mode_tab_index  = self.mode_tabs.currentIndex()
        try:
            _app_settings.save(self._app_settings)
        except Exception:
            # Best-effort — don't block close on a settings write failure.
            pass
        event.accept()

    def _on_thread_done(self):
        self.progress_bar.setRange(0, 100)
        self.build_btn.setEnabled(True)

    def _on_new_project(self):
        if self._is_repack_mode():
            self._apply_repack_settings(RepackSettings())
            self._current_repack_path = None
        else:
            self._clear_fields()
            self._current_project_path = None
        self._set_project_title()
        self.status_bar.showMessage("New project")

    def _on_load_project(self):
        if self._is_repack_mode():
            path, _ = QFileDialog.getOpenFileName(
                self, "Load Repack Project", str(Path.home()),
                "PatchForge Repack Projects (*.xpr);;All files (*)")
            if not path:
                return
            try:
                s = load_repack(Path(path))
                self._apply_repack_settings(s)
                self._current_repack_path = Path(path)
                self._set_project_title(path)
                self.status_bar.showMessage(f"Loaded: {path}")
                _recent.add(path, "repack")
            except Exception as exc:
                self._log(f"Failed to load repack project: {exc}", color=ERROR)
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "Load Project", str(Path.home()),
                "PatchForge Projects (*.xpm);;All files (*)")
            if not path:
                return
            try:
                s = load_project(Path(path))
                self._apply_settings(s)
                self._current_project_path = Path(path)
                self._set_project_title(path)
                self.status_bar.showMessage(f"Loaded: {path}")
                _recent.add(path, "patch")
            except Exception as exc:
                self._log(f"Failed to load project: {exc}", color=ERROR)

    def _on_save_project(self):
        if self._is_repack_mode():
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Repack Project",
                str(self._current_repack_path or Path.home() / "repack.xpr"),
                "PatchForge Repack Projects (*.xpr);;All files (*)")
            if not path:
                return
            try:
                s = self._collect_repack_settings(validate=False)
                save_repack(s, Path(path))
                self._current_repack_path = Path(path)
                self._set_project_title(path)
                self.status_bar.showMessage(f"Saved: {path}")
                _recent.add(path, "repack")
            except Exception as exc:
                self._log(f"Failed to save repack project: {exc}", color=ERROR)
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Project",
                str(self._current_project_path or Path.home() / "patch.xpm"),
                "PatchForge Projects (*.xpm);;All files (*)")
            if not path:
                return
            try:
                s = self._collect_settings(validate=False)
                save_project(s, Path(path))
                self._current_project_path = Path(path)
                self._set_project_title(path)
                self.status_bar.showMessage(f"Saved: {path}")
                _recent.add(path, "patch")
                missing = [f for f, v in [("app name", s.app_name),
                                           ("source dir", s.source_dir),
                                           ("target dir", s.target_dir)] if not v]
                if missing:
                    self._log(f"⚠  Project saved with missing required fields: {', '.join(missing)}",
                              color=WARN)
            except Exception as exc:
                self._log(f"Failed to save project: {exc}", color=ERROR)

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _engine_key(self) -> str:
        return self.engine_combo.currentData() or "hdiffpatch"

    def _set_project_title(self, path=None) -> None:
        """Set the window title to "PatchForge" (no file open) or
        "PatchForge — <basename>" when a project file is current."""
        if path is None:
            self.setWindowTitle("PatchForge")
        else:
            self.setWindowTitle(f"PatchForge — {Path(path).name}")

    def _compression_key(self) -> str:
        data = self.comp_combo.currentData()
        return data if data else self.comp_combo.currentText()

    def _verify_key(self) -> str:
        return ["crc32c", "md5", "filesize"][self.verify_combo.currentIndex()]

    def _find_method_key(self) -> str:
        if self.find_registry.isChecked():
            return "registry"
        if self.find_ini.isChecked():
            return "ini"
        return "manual"

    def _collect_extra_files(self) -> list:
        result = []
        for i in range(self.extra_files_list.count()):
            data = self.extra_files_list.item(i).data(Qt.UserRole)
            if data:
                result.append(data)
        return result

    def _collect_repack_settings(self, validate: bool = True) -> Optional[RepackSettings]:
        components = []
        for i in range(self.rp_comp_list.count()):
            d = self.rp_comp_list.item(i).data(Qt.UserRole)
            if d:
                components.append(d)

        kwargs = {f: collect() for f, (collect, _) in self._repack_bindings.items()}
        kwargs["components"] = components
        s = RepackSettings(**kwargs)
        if validate:
            errors = []
            if not s.app_name:
                errors.append("App name is required")
            if not s.game_dir:
                errors.append("Game directory is required")
            for i, c in enumerate(s.components):
                if not c.get("folder") or not Path(c["folder"]).is_dir():
                    errors.append(
                        f"Component {i + 1} ({c.get('label', '?')}): "
                        f"folder not found: {c.get('folder', '')}"
                    )
            if errors:
                for e in errors:
                    self._log(f"✗  {e}", color=ERROR)
                return None
        return s

    def _apply_repack_settings(self, s: RepackSettings):
        # Codec radio binder fires before _on_rp_codec_changed which
        # repopulates the quality combo for the codec — but the quality
        # binder needs to run AFTER repopulation to find the right item.
        # So: apply codec first, refresh, then apply the rest.
        self._repack_bindings["codec"][1](s.codec)
        self._on_rp_codec_changed()
        for f, (_, apply) in self._repack_bindings.items():
            if f == "codec":
                continue
            apply(getattr(s, f))

        # Components — list widget needs explicit population.
        self.rp_comp_list.clear()
        for c in (s.components or []):
            item = QListWidgetItem(self._comp_item_text(c))
            item.setData(Qt.UserRole, c)
            self.rp_comp_list.addItem(item)

    def _build_patch_bindings(self) -> None:
        """Map ProjectSettings fields → (collect, apply) closures for the
        patch-mode form.  Used by _collect_settings / _apply_settings.
        Special cases (engine combo without UserRole data, three-way
        find-method radio group, extra_files list, dependent combos that
        need on-change handlers re-fired) are handled inline in the
        collect/apply methods rather than via a binder."""
        self._patch_bindings = {
            "app_name":         _bind_lineedit(self.app_name_edit),
            "app_note":         _bind_lineedit(self.app_note_edit),
            "version":          _bind_lineedit(self.version_edit),
            "description":      _bind_lineedit(self.desc_edit),
            "copyright":        _bind_lineedit(self.copyright_edit),
            "contact":          _bind_lineedit(self.contact_edit),
            "company_info":     _bind_lineedit(self.company_info_edit),
            "window_title":     _bind_lineedit(self.window_title_edit),
            "patch_exe_name":   _bind_lineedit(self.patch_exe_name_edit),
            "patch_exe_version":_bind_lineedit(self.patch_exe_version_edit),
            "source_dir":       _bind_filepicker(self.src_picker),
            "target_dir":       _bind_filepicker(self.tgt_picker),
            "output_dir":       _bind_filepicker(self.out_picker),
            "registry_key":     _bind_lineedit(self.reg_key_edit),
            "registry_value":   _bind_lineedit(self.reg_val_edit),
            "ini_path":         _bind_filepicker(self.ini_path_picker),
            "ini_section":      _bind_lineedit(self.ini_section_edit),
            "ini_key":          _bind_lineedit(self.ini_key_edit),
            "arch":             _bind_radio_pair(self.arch_x64, "x64", "x86"),
            "threads":          _bind_combo_data(self.threads_combo, default=1),
            "compressor_quality": _bind_combo_data(self.quality_combo, default=DEFAULT_QUALITY),
            "icon_path":        _bind_lineedit(self.icon_edit),
            "extra_diff_args":  _bind_lineedit(self.extra_diff_args_edit),
            "delete_extra_files": _bind_checkbox(self.delete_extra_chk),
            "run_before":       _bind_lineedit(self.run_before_edit),
            "run_after":        _bind_lineedit(self.run_after_edit),
            "backup_at":        _bind_combo_data(self.backup_combo, default="same_folder"),
            "backup_path":      _bind_lineedit(self.backup_path_edit),
            "backdrop_path":    _bind_lineedit(self.backdrop_edit),
            "close_delay":      _bind_spin(self.close_delay_spin),
            "required_free_space_gb": _bind_spin(self.free_space_spin),
            "preserve_timestamps":    _bind_checkbox(self.preserve_timestamps_chk),
            "detect_running_exe":     _bind_lineedit(self.detect_running_edit),
            "run_on_startup":   _bind_lineedit(self.run_on_startup_edit),
            "run_on_finish":    _bind_lineedit(self.run_on_finish_edit),
        }

    def _build_repack_bindings(self) -> None:
        """Map RepackSettings fields → (collect, apply) closures for the
        repack-mode form.  See _build_patch_bindings for the conventions."""
        self._repack_bindings = {
            "app_name":             _bind_lineedit(self.rp_app_name_edit),
            "app_note":             _bind_lineedit(self.rp_app_note_edit),
            "version":              _bind_lineedit(self.rp_version_edit),
            "description":          _bind_lineedit(self.rp_desc_edit),
            "copyright":            _bind_lineedit(self.rp_copyright_edit),
            "contact":              _bind_lineedit(self.rp_contact_edit),
            "company_info":         _bind_lineedit(self.rp_company_edit),
            "window_title":         _bind_lineedit(self.rp_window_title_edit),
            "installer_exe_name":   _bind_lineedit(self.rp_exe_name_edit),
            "installer_exe_version":_bind_lineedit(self.rp_exe_version_edit),
            "game_dir":             _bind_filepicker(self.rp_game_picker),
            "output_dir":           _bind_filepicker(self.rp_out_picker),
            "arch":                 _bind_radio_pair(self.rp_arch_x64, "x64", "x86"),
            "codec":                _bind_radio_pair(self.rp_codec_zstd, "zstd", "lzma"),
            "compression":          _bind_combo_data(self.rp_comp_combo, default="max"),
            "threads":              _bind_combo_data(self.rp_threads_combo, default=1),
            "icon_path":            _bind_lineedit(self.rp_icon_edit),
            "backdrop_path":        _bind_lineedit(self.rp_backdrop_edit),
            "install_registry_key": _bind_lineedit(self.rp_registry_key_edit),
            "run_after_install":    _bind_lineedit(self.rp_run_after_edit),
            "detect_running_exe":   _bind_lineedit(self.rp_detect_running_edit),
            "required_free_space_gb": _bind_spin(self.rp_free_space_spin),
            "close_delay":          _bind_spin(self.rp_close_delay_spin),
            "include_uninstaller":  _bind_checkbox(self.rp_include_uninstaller_chk),
            "verify_crc32":         _bind_checkbox(self.rp_verify_crc32_chk),
            "split_bin":            _bind_checkbox(self.rp_split_bin_chk),
            "max_part_size_mb":     _bind_spin(self.rp_max_part_size_spin),
            "shortcut_target":          _bind_lineedit(self.rp_shortcut_target_edit),
            "shortcut_name":            _bind_lineedit(self.rp_shortcut_name_edit),
            "shortcut_create_startmenu":_bind_checkbox(self.rp_shortcut_startmenu_chk),
            "shortcut_create_desktop":  _bind_checkbox(self.rp_shortcut_desktop_chk),
        }

    def _collect_settings(self, validate: bool = True) -> Optional[ProjectSettings]:
        kwargs = {f: collect() for f, (collect, _) in self._patch_bindings.items()}
        # Special cases not covered by the binder dict:
        kwargs["engine"]        = self._engine_key()
        kwargs["compression"]   = self._compression_key()
        kwargs["verify_method"] = self._verify_key()
        kwargs["find_method"]   = self._find_method_key()
        kwargs["extra_files"]   = self._collect_extra_files()
        s = ProjectSettings(**kwargs)
        if validate:
            errors = []
            if not s.app_name:
                errors.append("App name is required")
            if not s.source_dir:
                errors.append("Source directory is required")
            if not s.target_dir:
                errors.append("Target directory is required")
            if s.find_method == "registry" and not s.registry_key:
                errors.append("Registry key is required when find method is Registry")
            if s.find_method == "ini":
                if not s.ini_path:
                    errors.append("INI file path is required when find method is INI")
                if not s.ini_section:
                    errors.append("INI section is required when find method is INI")
                if not s.ini_key:
                    errors.append("INI key is required when find method is INI")
            if s.backup_at == "custom" and not s.backup_path:
                errors.append("Backup path is required when backup method is Custom")
            if errors:
                for e in errors:
                    self._log(f"✗  {e}", color=ERROR)
                return None
        return s

    def _apply_settings(self, s: ProjectSettings):
        # Engine combo carries the settings key as UserRole data; look it
        # up by data so reordering the combo can't break apply().  Then
        # fire the on-change handler so the compression combo repopulates
        # before the compression binder applies the saved value.
        idx = self.engine_combo.findData(s.engine)
        self.engine_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._on_engine_changed()

        # Backup combo's on-change handler enables/disables the path field;
        # the binder below sets the index, but we still need the side effect.
        for f, (_, apply) in self._patch_bindings.items():
            apply(getattr(s, f))
        self._on_backup_changed()

        # Compression combo lookup — falls back to text match if the saved
        # preset key isn't present as UserRole data (legacy projects).
        for i in range(self.comp_combo.count()):
            if self.comp_combo.itemData(i) == s.compression or \
               self.comp_combo.itemText(i) == s.compression:
                self.comp_combo.setCurrentIndex(i)
                break

        # Combos without UserRole data — set by index from a known map.
        verify_map = {"crc32c": 0, "md5": 1, "filesize": 2}
        self.verify_combo.setCurrentIndex(verify_map.get(s.verify_method, 0))

        # Three-way radio group.
        self.find_manual.setChecked(s.find_method == "manual")
        self.find_registry.setChecked(s.find_method == "registry")
        self.find_ini.setChecked(s.find_method == "ini")

        # Extra files — list widget needs explicit population.
        self.extra_files_list.clear()
        for ef in (s.extra_files or []):
            src  = ef.get("src", "")
            dest = ef.get("dest", "")
            if src or dest:
                item = QListWidgetItem(f"{dest}  ←  {src}")
                item.setData(Qt.UserRole, ef)
                self.extra_files_list.addItem(item)

    def _clear_fields(self):
        self._apply_settings(ProjectSettings())

    def _log(self, msg: str, color: str = ""):
        # Queue the message and schedule a flush at the next event-loop
        # tick.  Multiple _log calls within the same tick share one cursor
        # move + ensureCursorVisible call; the layout cost no longer scales
        # with message count.
        self._log_queue.append((msg, color))
        if not self._log_flush_pending:
            self._log_flush_pending = True
            QTimer.singleShot(0, self._flush_log)

    def _flush_log(self):
        self._log_flush_pending = False
        if not self._log_queue:
            return
        pending, self._log_queue = self._log_queue, []
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.End)
        default_fmt = cursor.charFormat()
        for msg, color in pending:
            if color:
                fmt = cursor.charFormat()
                fmt.setForeground(QColor(color))
                cursor.setCharFormat(fmt)
                cursor.insertText(msg + "\n")
                cursor.setCharFormat(default_fmt)
            else:
                cursor.insertText(msg + "\n")
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()

    def _on_clear_log(self) -> None:
        # Drop any queued messages too — they belong to the run the user
        # is dismissing, and would otherwise reappear after the next event-
        # loop tick fires _flush_log.
        self._log_queue.clear()
        self._log_flush_pending = False
        self.log.clear()


def run_gui():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("PatchForge")
    app.setStyleSheet(QSS)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
