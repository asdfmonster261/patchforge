"""PatchForge main window — dark-theme PySide6 GUI."""

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QObject, QUrl
from PySide6.QtGui import QFont, QTextCursor, QColor, QDesktopServices, QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGroupBox, QLabel, QLineEdit, QPushButton, QComboBox,
    QRadioButton, QButtonGroup, QProgressBar, QPlainTextEdit,
    QFileDialog, QSplitter, QSizePolicy, QFrame, QStatusBar,
    QCheckBox, QListWidget, QListWidgetItem, QScrollArea, QInputDialog,
    QSpinBox, QDoubleSpinBox, QTabWidget,
    QDialog, QDialogButtonBox, QFormLayout, QMenu,
)

from .theme import QSS, ACCENT, SUCCESS, ERROR, WARN, TEXT_DIM

from ..core.engines.hdiffpatch import (
    HDiffPatchEngine, THREAD_OPTIONS,
    LZMA2_QUALITIES, BZIP2_QUALITIES, DEFAULT_QUALITY, preset_compressor,
)
from ..core.engines.jojodiff import JojoDiffEngine
from ..core.engines.xdelta3 import XDelta3Engine
from ..core.project import ProjectSettings, save as save_project, load as load_project
from ..core.patch_builder import build, BuildResult
from ..core.repack_project import RepackSettings, save as save_repack, load as load_repack
from ..core.repack_builder import build as build_repack, RepackResult
from ..core.xpack_archive import QUALITY_LABELS as REPACK_QUALITY_LABELS, THREAD_OPTIONS as REPACK_THREAD_OPTIONS
from ..core import verification
from ..core import recent_files as _recent


# ---------------------------------------------------------------------------
# Background build worker
# ---------------------------------------------------------------------------

class BuildWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(object)   # BuildResult

    def __init__(self, settings: ProjectSettings):
        super().__init__()
        self._settings = settings

    def run(self):
        result = build(self._settings, progress=self.progress.emit)
        self.finished.emit(result)


class RepackWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(object)   # RepackResult

    def __init__(self, settings: RepackSettings):
        super().__init__()
        self._settings = settings

    def run(self):
        result = build_repack(self._settings, progress=self.progress.emit)
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
# Main Window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PatchForge")
        self.setMinimumSize(960, 680)
        self.resize(1100, 780)
        self.setStyleSheet(QSS)

        self._worker: Optional[BuildWorker] = None
        self._repack_worker: Optional[RepackWorker] = None
        self._thread: Optional[QThread] = None
        self._current_project_path: Optional[Path] = None
        self._current_repack_path: Optional[Path] = None

        self._build_ui()
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

        # ── Splitter: left tabs / right log ──
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        splitter.addWidget(self.mode_tabs)
        splitter.addWidget(self._build_output_panel())
        splitter.setSizes([580, 480])

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
                self.setWindowTitle(f"PatchForge — {path.name}")
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
                self.setWindowTitle(f"PatchForge — {path.name}")
                self.status_bar.showMessage(f"Loaded: {path}")
                _recent.add(path, "patch")
            except Exception as exc:
                self._log(f"Failed to load project: {exc}", color=ERROR)

    def _on_clear_recent(self) -> None:
        _recent.clear()

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

        mg.addWidget(QLabel("App name:"),    0, 0)
        self.app_name_edit = QLineEdit()
        self.app_name_edit.setPlaceholderText("My Application")
        mg.addWidget(self.app_name_edit, 0, 1)

        mg.addWidget(QLabel("App note:"),    0, 2)
        self.app_note_edit = QLineEdit()
        self.app_note_edit.setPlaceholderText("Short subtitle (optional)")
        mg.addWidget(self.app_note_edit, 0, 3)

        mg.addWidget(QLabel("Version:"),     1, 0)
        self.version_edit = QLineEdit()
        self.version_edit.setPlaceholderText("1.0.0")
        mg.addWidget(self.version_edit, 1, 1)

        mg.addWidget(QLabel("Exe version:"), 1, 2)
        self.patch_exe_version_edit = QLineEdit()
        self.patch_exe_version_edit.setPlaceholderText("1.0.0.0  (informational)")
        mg.addWidget(self.patch_exe_version_edit, 1, 3)

        mg.addWidget(QLabel("Description:"), 2, 0)
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("Optional description shown in patcher")
        mg.addWidget(self.desc_edit, 2, 1, 1, 3)

        mg.addWidget(QLabel("Copyright:"),   3, 0)
        self.copyright_edit = QLineEdit()
        self.copyright_edit.setPlaceholderText("© 2025 My Company")
        mg.addWidget(self.copyright_edit, 3, 1)

        mg.addWidget(QLabel("Company:"),     3, 2)
        self.company_info_edit = QLineEdit()
        self.company_info_edit.setPlaceholderText("Publisher / company name")
        mg.addWidget(self.company_info_edit, 3, 3)

        mg.addWidget(QLabel("Contact:"),     4, 0)
        self.contact_edit = QLineEdit()
        self.contact_edit.setPlaceholderText("support@example.com or URL")
        mg.addWidget(self.contact_edit, 4, 1)

        mg.addWidget(QLabel("Window title:"), 4, 2)
        self.window_title_edit = QLineEdit()
        self.window_title_edit.setPlaceholderText("Patcher title bar (defaults to app name)")
        mg.addWidget(self.window_title_edit, 4, 3)

        mg.addWidget(QLabel("Exe name:"),    5, 0)
        self.patch_exe_name_edit = QLineEdit()
        self.patch_exe_name_edit.setPlaceholderText(
            "Output exe filename stem — blank = auto (AppName_version_patch_x64.exe)")
        mg.addWidget(self.patch_exe_name_edit, 5, 1, 1, 3)

        mg.addWidget(QLabel("Icon (.ico):"), 6, 0)
        icon_row = QHBoxLayout()
        icon_row.setSpacing(4)
        self.icon_edit = QLineEdit()
        self.icon_edit.setPlaceholderText("Optional — leave blank for default icon")
        self.icon_edit.setReadOnly(True)
        icon_row.addWidget(self.icon_edit)
        self.icon_browse_btn = QPushButton("Browse…")
        self.icon_browse_btn.setFixedWidth(70)
        self.icon_browse_btn.clicked.connect(self._on_icon_browse)
        icon_row.addWidget(self.icon_browse_btn)
        self.icon_clear_btn = QPushButton("✕")
        self.icon_clear_btn.setFixedWidth(24)
        self.icon_clear_btn.setToolTip("Clear icon")
        self.icon_clear_btn.clicked.connect(lambda: self.icon_edit.setText(""))
        icon_row.addWidget(self.icon_clear_btn)
        icon_container = QWidget()
        icon_container.setLayout(icon_row)
        mg.addWidget(icon_container, 6, 1, 1, 3)

        mg.addWidget(QLabel("Backdrop:"), 7, 0)
        bd_row = QHBoxLayout()
        bd_row.setSpacing(4)
        self.backdrop_edit = QLineEdit()
        self.backdrop_edit.setPlaceholderText("Optional background image (PNG/JPEG/BMP)")
        self.backdrop_edit.setReadOnly(True)
        bd_row.addWidget(self.backdrop_edit)
        self.backdrop_browse_btn = QPushButton("Browse…")
        self.backdrop_browse_btn.setFixedWidth(70)
        self.backdrop_browse_btn.clicked.connect(self._on_backdrop_browse)
        bd_row.addWidget(self.backdrop_browse_btn)
        self.backdrop_clear_btn = QPushButton("✕")
        self.backdrop_clear_btn.setFixedWidth(24)
        self.backdrop_clear_btn.setToolTip("Clear backdrop")
        self.backdrop_clear_btn.clicked.connect(lambda: self.backdrop_edit.setText(""))
        bd_row.addWidget(self.backdrop_clear_btn)
        bd_w = QWidget()
        bd_w.setLayout(bd_row)
        mg.addWidget(bd_w, 7, 1, 1, 3)

        layout.addWidget(meta_grp)

        # ── Engine + compression + verify ────────────────────────────────
        eng_grp = QGroupBox("Engine & Compression")
        eg = QGridLayout(eng_grp)
        eg.setSpacing(6)
        eg.setColumnStretch(1, 1)
        eg.setColumnStretch(3, 1)

        eg.addWidget(QLabel("Engine:"),      0, 0)
        self.engine_combo = QComboBox()
        self.engine_combo.addItems([
            "HDiffPatch 4.12.2",
            "xdelta3 3.0.8",
            "JojoDiff 0.8.1",
        ])
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
        rg.addWidget(QLabel("Key:"),   0, 0)
        self.reg_key_edit = QLineEdit()
        self.reg_key_edit.setPlaceholderText(r"SOFTWARE\MyCompany\MyApp")
        rg.addWidget(self.reg_key_edit, 0, 1)
        rg.addWidget(QLabel("Value:"), 1, 0)
        self.reg_val_edit = QLineEdit()
        self.reg_val_edit.setPlaceholderText("InstallPath  (leave blank for default)")
        rg.addWidget(self.reg_val_edit, 1, 1)
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
        ig.addWidget(QLabel("Section:"), 1, 0)
        self.ini_section_edit = QLineEdit()
        self.ini_section_edit.setPlaceholderText("Settings")
        ig.addWidget(self.ini_section_edit, 1, 1)
        ig.addWidget(QLabel("Key:"),     2, 0)
        self.ini_key_edit = QLineEdit()
        self.ini_key_edit.setPlaceholderText("InstallPath")
        ig.addWidget(self.ini_key_edit, 2, 1)
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
        ag.addWidget(QLabel("Run before:"), 2, 0)
        self.run_before_edit = QLineEdit()
        self.run_before_edit.setPlaceholderText("Command to run before patching (optional)")
        ag.addWidget(self.run_before_edit, 2, 1, 1, 2)

        ag.addWidget(QLabel("Run after:"), 3, 0)
        self.run_after_edit = QLineEdit()
        self.run_after_edit.setPlaceholderText("Command to run after patching (optional)")
        ag.addWidget(self.run_after_edit, 3, 1, 1, 2)

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
        ag.addWidget(QLabel("Detect running:"), 9, 0)
        self.detect_running_edit = QLineEdit()
        self.detect_running_edit.setPlaceholderText("e.g. GameApp.exe — warn if running before patching")
        self.detect_running_edit.setToolTip(
            "If the specified process is running when the user clicks Patch,\n"
            "a warning dialog will appear asking whether to continue."
        )
        ag.addWidget(self.detect_running_edit, 9, 1, 1, 2)

        # Run on startup
        ag.addWidget(QLabel("Run on startup:"), 10, 0)
        self.run_on_startup_edit = QLineEdit()
        self.run_on_startup_edit.setPlaceholderText("Command to run when the patcher window opens (optional)")
        ag.addWidget(self.run_on_startup_edit, 10, 1, 1, 2)

        # Run on finish
        ag.addWidget(QLabel("Run on finish:"), 11, 0)
        self.run_on_finish_edit = QLineEdit()
        self.run_on_finish_edit.setPlaceholderText("Command to run after successful patch + dialog (optional)")
        ag.addWidget(self.run_on_finish_edit, 11, 1, 1, 2)

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

        ig.addWidget(QLabel("App name:"),   0, 0)
        self.rp_app_name_edit = QLineEdit()
        self.rp_app_name_edit.setPlaceholderText("My Game")
        ig.addWidget(self.rp_app_name_edit, 0, 1)

        ig.addWidget(QLabel("App note:"),   0, 2)
        self.rp_app_note_edit = QLineEdit()
        self.rp_app_note_edit.setPlaceholderText("Short subtitle (optional)")
        ig.addWidget(self.rp_app_note_edit, 0, 3)

        ig.addWidget(QLabel("Version:"),    1, 0)
        self.rp_version_edit = QLineEdit()
        self.rp_version_edit.setPlaceholderText("1.0.0")
        ig.addWidget(self.rp_version_edit, 1, 1)

        ig.addWidget(QLabel("Exe version:"), 1, 2)
        self.rp_exe_version_edit = QLineEdit()
        self.rp_exe_version_edit.setPlaceholderText("1.0.0.0  (informational)")
        ig.addWidget(self.rp_exe_version_edit, 1, 3)

        ig.addWidget(QLabel("Description:"), 2, 0)
        self.rp_desc_edit = QLineEdit()
        self.rp_desc_edit.setPlaceholderText("Optional description shown in installer")
        ig.addWidget(self.rp_desc_edit, 2, 1, 1, 3)

        ig.addWidget(QLabel("Copyright:"),  3, 0)
        self.rp_copyright_edit = QLineEdit()
        self.rp_copyright_edit.setPlaceholderText("© 2025 My Company")
        ig.addWidget(self.rp_copyright_edit, 3, 1)

        ig.addWidget(QLabel("Company:"),    3, 2)
        self.rp_company_edit = QLineEdit()
        self.rp_company_edit.setPlaceholderText("Publisher / company name")
        ig.addWidget(self.rp_company_edit, 3, 3)

        ig.addWidget(QLabel("Contact:"),    4, 0)
        self.rp_contact_edit = QLineEdit()
        self.rp_contact_edit.setPlaceholderText("support@example.com or URL")
        ig.addWidget(self.rp_contact_edit, 4, 1)

        ig.addWidget(QLabel("Window title:"), 4, 2)
        self.rp_window_title_edit = QLineEdit()
        self.rp_window_title_edit.setPlaceholderText("Installer title bar (defaults to app name)")
        ig.addWidget(self.rp_window_title_edit, 4, 3)

        ig.addWidget(QLabel("Exe name:"),   5, 0)
        self.rp_exe_name_edit = QLineEdit()
        self.rp_exe_name_edit.setPlaceholderText(
            "Output exe filename stem — blank = auto (AppName_version_installer_x64.exe)")
        ig.addWidget(self.rp_exe_name_edit, 5, 1, 1, 3)

        ig.addWidget(QLabel("Icon (.ico):"), 6, 0)
        rp_icon_row = QHBoxLayout()
        rp_icon_row.setSpacing(4)
        self.rp_icon_edit = QLineEdit()
        self.rp_icon_edit.setPlaceholderText("Optional — leave blank for default icon")
        self.rp_icon_edit.setReadOnly(True)
        rp_icon_row.addWidget(self.rp_icon_edit)
        rp_icon_btn = QPushButton("Browse…")
        rp_icon_btn.setFixedWidth(70)
        rp_icon_btn.clicked.connect(self._on_rp_icon_browse)
        rp_icon_row.addWidget(rp_icon_btn)
        rp_icon_clr = QPushButton("✕")
        rp_icon_clr.setFixedWidth(24)
        rp_icon_clr.clicked.connect(lambda: self.rp_icon_edit.setText(""))
        rp_icon_row.addWidget(rp_icon_clr)
        rp_icon_w = QWidget(); rp_icon_w.setLayout(rp_icon_row)
        ig.addWidget(rp_icon_w, 6, 1, 1, 3)

        ig.addWidget(QLabel("Backdrop:"),   7, 0)
        rp_bd_row = QHBoxLayout()
        rp_bd_row.setSpacing(4)
        self.rp_backdrop_edit = QLineEdit()
        self.rp_backdrop_edit.setPlaceholderText("Optional background image (PNG/JPEG/BMP)")
        self.rp_backdrop_edit.setReadOnly(True)
        rp_bd_row.addWidget(self.rp_backdrop_edit)
        rp_bd_btn = QPushButton("Browse…")
        rp_bd_btn.setFixedWidth(70)
        rp_bd_btn.clicked.connect(self._on_rp_backdrop_browse)
        rp_bd_row.addWidget(rp_bd_btn)
        rp_bd_clr = QPushButton("✕")
        rp_bd_clr.setFixedWidth(24)
        rp_bd_clr.clicked.connect(lambda: self.rp_backdrop_edit.setText(""))
        rp_bd_row.addWidget(rp_bd_clr)
        rp_bd_w = QWidget(); rp_bd_w.setLayout(rp_bd_row)
        ig.addWidget(rp_bd_w, 7, 1, 1, 3)

        layout.addWidget(info_grp)

        # ── Compression & Architecture ───────────────────────────────────
        comp_grp = QGroupBox("Compression & Architecture")
        cg = QGridLayout(comp_grp)
        cg.setSpacing(6)
        cg.setColumnStretch(1, 1)
        cg.setColumnStretch(3, 1)

        cg.addWidget(QLabel("Compression:"), 0, 0)
        self.rp_comp_combo = QComboBox()
        for key, label in REPACK_QUALITY_LABELS.items():
            self.rp_comp_combo.addItem(label, userData=key)
        # Default to "max"
        for i in range(self.rp_comp_combo.count()):
            if self.rp_comp_combo.itemData(i) == "max":
                self.rp_comp_combo.setCurrentIndex(i)
                break
        cg.addWidget(self.rp_comp_combo, 0, 1)

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

        cg.addWidget(QLabel("Threads:"), 1, 0)
        self.rp_threads_combo = QComboBox()
        for t in REPACK_THREAD_OPTIONS:
            self.rp_threads_combo.addItem(str(t), userData=t)
        cg.addWidget(self.rp_threads_combo, 1, 1)

        layout.addWidget(comp_grp)

        # ── Post-Install Options ─────────────────────────────────────────
        post_grp = QGroupBox("Post-Install Options")
        pg = QGridLayout(post_grp)
        pg.setSpacing(6)
        pg.setColumnStretch(1, 1)

        pg.addWidget(QLabel("Registry key:"), 0, 0)
        self.rp_registry_key_edit = QLineEdit()
        self.rp_registry_key_edit.setPlaceholderText(
            r"SOFTWARE\MyCompany\MyGame  — written to HKCU after install (for patch detection)")
        pg.addWidget(self.rp_registry_key_edit, 0, 1)

        pg.addWidget(QLabel("Run after install:"), 1, 0)
        self.rp_run_after_edit = QLineEdit()
        self.rp_run_after_edit.setPlaceholderText("Command to run after successful install (optional)")
        pg.addWidget(self.rp_run_after_edit, 1, 1)

        pg.addWidget(QLabel("Detect running:"), 2, 0)
        self.rp_detect_running_edit = QLineEdit()
        self.rp_detect_running_edit.setPlaceholderText("e.g. GameApp.exe — warn if running before install")
        pg.addWidget(self.rp_detect_running_edit, 2, 1)

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

        pg.addWidget(QLabel("Shortcut target:"), 7, 0)
        self.rp_shortcut_target_edit = QLineEdit()
        self.rp_shortcut_target_edit.setPlaceholderText(
            "Relative path to game exe within install dir  (e.g. Game.exe)")
        pg.addWidget(self.rp_shortcut_target_edit, 7, 1)

        pg.addWidget(QLabel("Shortcut name:"), 8, 0)
        self.rp_shortcut_name_edit = QLineEdit()
        self.rp_shortcut_name_edit.setPlaceholderText("Display name  (blank = use App Name)")
        pg.addWidget(self.rp_shortcut_name_edit, 8, 1)

        self.rp_shortcut_startmenu_chk = QCheckBox("Create Start Menu shortcut")
        self.rp_shortcut_startmenu_chk.setChecked(True)
        pg.addWidget(self.rp_shortcut_startmenu_chk, 9, 0, 1, 2)

        self.rp_shortcut_desktop_chk = QCheckBox("Create Desktop shortcut")
        self.rp_shortcut_desktop_chk.setChecked(False)
        pg.addWidget(self.rp_shortcut_desktop_chk, 10, 0, 1, 2)

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

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Build output will appear here…")
        og.addWidget(self.log, 1)

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

        self.new_btn   = QPushButton("New Project")
        self.load_btn  = QPushButton("Load Project")
        self.save_btn  = QPushButton("Save Project")

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

        self.find_manual.toggled.connect(self._on_find_method_changed)
        self.find_registry.toggled.connect(self._on_find_method_changed)
        self.find_ini.toggled.connect(self._on_find_method_changed)

        self.backup_combo.currentIndexChanged.connect(self._on_backup_changed)

        self.mode_tabs.currentChanged.connect(self._on_mode_changed)
        self.build_btn.clicked.connect(self._on_build)
        self.new_btn.clicked.connect(self._on_new_project)
        self.load_btn.clicked.connect(self._on_load_project)
        self.save_btn.clicked.connect(self._on_save_project)

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
            "label":           lbl,
            "folder":          fld,
            "default_checked": default_chk.isChecked(),
            "group":           group_edit.text().strip(),
            "requires":        requires,
        }

    @staticmethod
    def _comp_item_text(c: dict) -> str:
        chk = "✓" if c.get("default_checked", True) else "○"
        grp = f"  [group: {c['group']}]" if c.get("group") else ""
        req = c.get("requires", [])
        req_str = f"  [requires: {', '.join(str(r) for r in req)}]" if req else ""
        folder_name = Path(c["folder"]).name if c.get("folder") else ""
        return f"[{chk}]  {c['label']}  ({folder_name}){grp}{req_str}"

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
        self.log.clear()
        self._log("Starting patch build…")

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
        self.log.clear()
        self._log("Starting repack build…")

        self._thread = QThread()
        self._repack_worker = RepackWorker(settings)
        self._repack_worker.moveToThread(self._thread)
        self._thread.started.connect(self._repack_worker.run)
        self._repack_worker.progress.connect(self._on_progress)
        self._repack_worker.finished.connect(self._on_repack_done)
        self._repack_worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._on_thread_done)
        self._thread.start()

    def _on_progress(self, pct: int, msg: str):
        compressing = bool(msg and ": compressing " in msg and "done" not in msg)
        # In parallel mode multiple streams run simultaneously — a "reading"
        # update from stream N must not stop the indeterminate bar started by
        # stream M. Only exit indeterminate mode at a terminal stage (≥95% or
        # "Archive complete"), not on every non-compressing message.
        terminal = pct >= 95 or (msg and "Archive complete" in msg)
        indeterminate = self.progress_bar.maximum() == 0

        if compressing:
            if not indeterminate:
                self.progress_bar.setRange(0, 0)
        elif indeterminate:
            if terminal:
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(pct)
            # else: keep pulsing — another stream is still compressing
        else:
            self.progress_bar.setValue(pct)

        self.status_lbl.setText(msg)
        if msg and ": reading " not in msg and ": compressing " not in msg:
            self._log(msg)

    def _on_build_done(self, result: BuildResult):
        if result.success:
            self.progress_bar.setValue(100)
            self._log(f"\n✓  Done!", color=SUCCESS)
            self._log(f"   Output:      {result.output_path}")
            self._log(f"   Patch size:  {_fmt_size(result.patch_size)}")
            self._log(f"   Output size: {_fmt_size(result.output_size)}")
            self.status_bar.showMessage(f"Built: {Path(result.output_path).name}")
            self.status_lbl.setText("Build complete")
            out_dir = str(Path(result.output_path).parent)
            try:
                self.open_folder_btn.clicked.disconnect()
            except RuntimeError:
                pass
            self.open_folder_btn.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(out_dir))
            )
            self.open_folder_btn.setVisible(True)
        else:
            self._log(f"\n✗  Build failed: {result.error}", color=ERROR)
            self.status_bar.showMessage("Build failed")
            self.status_lbl.setText("Failed")
            self.open_folder_btn.setVisible(False)

    def _on_repack_done(self, result: RepackResult):
        if result.success:
            self.progress_bar.setValue(100)
            self._log(f"\n✓  Done!", color=SUCCESS)
            self._log(f"   Output:       {result.output_path}")
            self._log(f"   Files packed: {result.total_files}")
            self._log(f"   Game size:    {_fmt_size(result.uncompressed_size)}")
            self._log(f"   Installer:    {_fmt_size(result.output_size)}")
            ratio = result.output_size / result.uncompressed_size * 100 if result.uncompressed_size else 0
            self._log(f"   Compression:  {ratio:.1f}% of original")
            self.status_bar.showMessage(f"Built: {Path(result.output_path).name}")
            self.status_lbl.setText("Build complete")
            out_dir = str(Path(result.output_path).parent)
            try:
                self.open_folder_btn.clicked.disconnect()
            except RuntimeError:
                pass
            self.open_folder_btn.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(out_dir))
            )
            self.open_folder_btn.setVisible(True)
        else:
            self._log(f"\n✗  Repack failed: {result.error}", color=ERROR)
            self.status_bar.showMessage("Repack failed")
            self.status_lbl.setText("Failed")
            self.open_folder_btn.setVisible(False)

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
        self.setWindowTitle("PatchForge")
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
                self.setWindowTitle(f"PatchForge — {Path(path).name}")
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
                self.setWindowTitle(f"PatchForge — {Path(path).name}")
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
                self.setWindowTitle(f"PatchForge — {Path(path).name}")
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
                self.setWindowTitle(f"PatchForge — {Path(path).name}")
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
        idx = self.engine_combo.currentIndex()
        return ["hdiffpatch", "xdelta3", "jojodiff"][idx]

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

        s = RepackSettings(
            app_name             = self.rp_app_name_edit.text().strip(),
            app_note             = self.rp_app_note_edit.text().strip(),
            version              = self.rp_version_edit.text().strip(),
            description          = self.rp_desc_edit.text().strip(),
            copyright            = self.rp_copyright_edit.text().strip(),
            contact              = self.rp_contact_edit.text().strip(),
            company_info         = self.rp_company_edit.text().strip(),
            window_title         = self.rp_window_title_edit.text().strip(),
            installer_exe_name   = self.rp_exe_name_edit.text().strip(),
            installer_exe_version= self.rp_exe_version_edit.text().strip(),
            game_dir             = self.rp_game_picker.path,
            output_dir           = self.rp_out_picker.path,
            arch                 = "x64" if self.rp_arch_x64.isChecked() else "x86",
            compression          = self.rp_comp_combo.currentData() or "max",
            threads              = self.rp_threads_combo.currentData() or 1,
            icon_path            = self.rp_icon_edit.text().strip(),
            backdrop_path        = self.rp_backdrop_edit.text().strip(),
            install_registry_key = self.rp_registry_key_edit.text().strip(),
            run_after_install    = self.rp_run_after_edit.text().strip(),
            detect_running_exe   = self.rp_detect_running_edit.text().strip(),
            required_free_space_gb = self.rp_free_space_spin.value(),
            close_delay          = self.rp_close_delay_spin.value(),
            include_uninstaller  = self.rp_include_uninstaller_chk.isChecked(),
            verify_crc32         = self.rp_verify_crc32_chk.isChecked(),
            shortcut_target           = self.rp_shortcut_target_edit.text().strip(),
            shortcut_name             = self.rp_shortcut_name_edit.text().strip(),
            shortcut_create_startmenu = self.rp_shortcut_startmenu_chk.isChecked(),
            shortcut_create_desktop   = self.rp_shortcut_desktop_chk.isChecked(),
            components           = components,
        )
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
        self.rp_app_name_edit.setText(s.app_name)
        self.rp_app_note_edit.setText(s.app_note)
        self.rp_version_edit.setText(s.version)
        self.rp_desc_edit.setText(s.description)
        self.rp_copyright_edit.setText(s.copyright)
        self.rp_contact_edit.setText(s.contact)
        self.rp_company_edit.setText(s.company_info)
        self.rp_window_title_edit.setText(s.window_title)
        self.rp_exe_name_edit.setText(s.installer_exe_name)
        self.rp_exe_version_edit.setText(s.installer_exe_version)
        self.rp_game_picker.path = s.game_dir
        self.rp_out_picker.path  = s.output_dir
        self.rp_arch_x64.setChecked(s.arch == "x64")
        self.rp_arch_x86.setChecked(s.arch == "x86")
        for i in range(self.rp_comp_combo.count()):
            if self.rp_comp_combo.itemData(i) == s.compression:
                self.rp_comp_combo.setCurrentIndex(i)
                break
        for i in range(self.rp_threads_combo.count()):
            if self.rp_threads_combo.itemData(i) == s.threads:
                self.rp_threads_combo.setCurrentIndex(i)
                break
        self.rp_icon_edit.setText(s.icon_path)
        self.rp_backdrop_edit.setText(s.backdrop_path)
        self.rp_registry_key_edit.setText(s.install_registry_key)
        self.rp_run_after_edit.setText(s.run_after_install)
        self.rp_detect_running_edit.setText(s.detect_running_exe)
        self.rp_free_space_spin.setValue(s.required_free_space_gb)
        self.rp_close_delay_spin.setValue(s.close_delay)
        self.rp_include_uninstaller_chk.setChecked(s.include_uninstaller)
        self.rp_verify_crc32_chk.setChecked(s.verify_crc32)
        self.rp_shortcut_target_edit.setText(s.shortcut_target)
        self.rp_shortcut_name_edit.setText(s.shortcut_name)
        self.rp_shortcut_startmenu_chk.setChecked(s.shortcut_create_startmenu)
        self.rp_shortcut_desktop_chk.setChecked(s.shortcut_create_desktop)
        self.rp_comp_list.clear()
        for c in (s.components or []):
            item = QListWidgetItem(self._comp_item_text(c))
            item.setData(Qt.UserRole, c)
            self.rp_comp_list.addItem(item)

    def _collect_settings(self, validate: bool = True) -> Optional[ProjectSettings]:
        s = ProjectSettings(
            app_name      = self.app_name_edit.text().strip(),
            app_note      = self.app_note_edit.text().strip(),
            version       = self.version_edit.text().strip(),
            description   = self.desc_edit.text().strip(),
            copyright     = self.copyright_edit.text().strip(),
            contact       = self.contact_edit.text().strip(),
            company_info  = self.company_info_edit.text().strip(),
            window_title  = self.window_title_edit.text().strip(),
            patch_exe_name    = self.patch_exe_name_edit.text().strip(),
            patch_exe_version = self.patch_exe_version_edit.text().strip(),
            source_dir    = self.src_picker.path,
            target_dir    = self.tgt_picker.path,
            output_dir    = self.out_picker.path,
            engine        = self._engine_key(),
            compression   = self._compression_key(),
            verify_method = self._verify_key(),
            find_method   = self._find_method_key(),
            registry_key  = self.reg_key_edit.text().strip(),
            registry_value= self.reg_val_edit.text().strip(),
            ini_path      = self.ini_path_picker.path,
            ini_section   = self.ini_section_edit.text().strip(),
            ini_key       = self.ini_key_edit.text().strip(),
            arch               = "x64" if self.arch_x64.isChecked() else "x86",
            threads            = self.threads_combo.currentData(),
            compressor_quality = self.quality_combo.currentData() or DEFAULT_QUALITY,
            icon_path          = self.icon_edit.text().strip(),
            # New fields
            extra_diff_args    = self.extra_diff_args_edit.text().strip(),
            delete_extra_files = self.delete_extra_chk.isChecked(),
            run_before         = self.run_before_edit.text().strip(),
            run_after          = self.run_after_edit.text().strip(),
            backup_at          = self.backup_combo.currentData() or "same_folder",
            backup_path        = self.backup_path_edit.text().strip(),
            backdrop_path      = self.backdrop_edit.text().strip(),
            close_delay            = self.close_delay_spin.value(),
            required_free_space_gb = self.free_space_spin.value(),
            preserve_timestamps    = self.preserve_timestamps_chk.isChecked(),
            detect_running_exe     = self.detect_running_edit.text().strip(),
            run_on_startup         = self.run_on_startup_edit.text().strip(),
            run_on_finish          = self.run_on_finish_edit.text().strip(),
            extra_files        = self._collect_extra_files(),
        )
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
        self.app_name_edit.setText(s.app_name)
        self.app_note_edit.setText(s.app_note)
        self.version_edit.setText(s.version)
        self.patch_exe_version_edit.setText(s.patch_exe_version)
        self.desc_edit.setText(s.description)
        self.copyright_edit.setText(s.copyright)
        self.contact_edit.setText(s.contact)
        self.company_info_edit.setText(s.company_info)
        self.window_title_edit.setText(s.window_title)
        self.patch_exe_name_edit.setText(s.patch_exe_name)
        self.src_picker.path = s.source_dir
        self.tgt_picker.path = s.target_dir
        self.out_picker.path = s.output_dir

        engine_map = {"hdiffpatch": 0, "xdelta3": 1, "jojodiff": 2}
        self.engine_combo.setCurrentIndex(engine_map.get(s.engine, 0))
        self._on_engine_changed()

        for i in range(self.comp_combo.count()):
            if self.comp_combo.itemData(i) == s.compression or \
               self.comp_combo.itemText(i) == s.compression:
                self.comp_combo.setCurrentIndex(i)
                break

        verify_map = {"crc32c": 0, "md5": 1, "filesize": 2}
        self.verify_combo.setCurrentIndex(verify_map.get(s.verify_method, 0))

        self.find_manual.setChecked(s.find_method == "manual")
        self.find_registry.setChecked(s.find_method == "registry")
        self.find_ini.setChecked(s.find_method == "ini")

        self.reg_key_edit.setText(s.registry_key)
        self.reg_val_edit.setText(s.registry_value)
        self.ini_path_picker.path = s.ini_path
        self.ini_section_edit.setText(s.ini_section)
        self.ini_key_edit.setText(s.ini_key)

        self.arch_x64.setChecked(s.arch == "x64")
        self.arch_x86.setChecked(s.arch == "x86")

        self.icon_edit.setText(s.icon_path)

        for i in range(self.threads_combo.count()):
            if self.threads_combo.itemData(i) == s.threads:
                self.threads_combo.setCurrentIndex(i)
                break

        for i in range(self.quality_combo.count()):
            if self.quality_combo.itemData(i) == s.compressor_quality:
                self.quality_combo.setCurrentIndex(i)
                break

        # New fields
        self.extra_diff_args_edit.setText(s.extra_diff_args)
        self.delete_extra_chk.setChecked(s.delete_extra_files)
        self.run_before_edit.setText(s.run_before)
        self.run_after_edit.setText(s.run_after)

        backup_map = {"same_folder": 0, "custom": 1, "disabled": 2}
        self.backup_combo.setCurrentIndex(backup_map.get(s.backup_at, 0))
        self._on_backup_changed()
        self.backup_path_edit.setText(s.backup_path)

        self.backdrop_edit.setText(s.backdrop_path)
        self.preserve_timestamps_chk.setChecked(s.preserve_timestamps)
        self.free_space_spin.setValue(s.required_free_space_gb)
        self.close_delay_spin.setValue(s.close_delay)
        self.detect_running_edit.setText(s.detect_running_exe)
        self.run_on_startup_edit.setText(s.run_on_startup)
        self.run_on_finish_edit.setText(s.run_on_finish)

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
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.End)
        if color:
            fmt = cursor.charFormat()
            fmt.setForeground(QColor(color))
            cursor.setCharFormat(fmt)
        cursor.insertText(msg + "\n")
        if color:
            fmt = cursor.charFormat()
            fmt.setForeground(QColor("#d4d4d4"))
            cursor.setCharFormat(fmt)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()


# ---------------------------------------------------------------------------

def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def run_gui():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("PatchForge")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
