"""PatchForge main window — dark-theme PySide6 GUI."""

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QFont, QTextCursor, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGroupBox, QLabel, QLineEdit, QPushButton, QComboBox,
    QRadioButton, QButtonGroup, QProgressBar, QPlainTextEdit,
    QFileDialog, QSplitter, QSizePolicy, QFrame, QStatusBar,
    QCheckBox, QListWidget, QListWidgetItem, QScrollArea, QInputDialog,
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
from ..core import verification


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
        self._thread: Optional[QThread] = None
        self._current_project_path: Optional[Path] = None

        self._build_ui()
        self._connect_signals()
        self._on_engine_changed()  # set initial compression list

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Splitter: left settings / right log ──
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        splitter.addWidget(self._build_settings_panel())
        splitter.addWidget(self._build_output_panel())
        splitter.setSizes([580, 480])

        # ── Bottom button bar ──
        root.addWidget(HSep())
        root.addLayout(self._build_button_bar())

        # ── Status bar ──
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def _build_settings_panel(self) -> QWidget:
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

        mg.addWidget(QLabel("Version:"),     0, 2)
        self.version_edit = QLineEdit()
        self.version_edit.setPlaceholderText("1.0.0")
        mg.addWidget(self.version_edit, 0, 3)

        mg.addWidget(QLabel("Description:"), 1, 0)
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("Optional description shown in patcher")
        mg.addWidget(self.desc_edit, 1, 1, 1, 3)

        mg.addWidget(QLabel("Icon (.ico):"), 2, 0)
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
        mg.addWidget(icon_container, 2, 1, 1, 3)

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

        # Backdrop image
        ag.addWidget(QLabel("Backdrop:"), 6, 0)
        bd_row = QHBoxLayout()
        bd_row.setSpacing(4)
        self.backdrop_edit = QLineEdit()
        self.backdrop_edit.setPlaceholderText(
            "Optional background image (PNG/JPEG/BMP)")
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
        ag.addWidget(bd_w, 6, 1, 1, 2)

        layout.addWidget(adv_grp)
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

    def _on_build(self):
        if self._thread and self._thread.isRunning():
            return

        settings = self._collect_settings()
        if not settings:
            return

        self.build_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.log.clear()
        self._log("Starting build…")

        self._thread = QThread()
        self._worker = BuildWorker(settings)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_build_done)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._on_thread_done)
        self._thread.start()

    def _on_progress(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        self.status_lbl.setText(msg)
        if msg:
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
        else:
            self._log(f"\n✗  Build failed: {result.error}", color=ERROR)
            self.status_bar.showMessage("Build failed")
            self.status_lbl.setText("Failed")

    def _on_thread_done(self):
        self.build_btn.setEnabled(True)

    def _on_new_project(self):
        self._clear_fields()
        self._current_project_path = None
        self.setWindowTitle("PatchForge")
        self.status_bar.showMessage("New project")

    def _on_load_project(self):
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
        except Exception as exc:
            self._log(f"Failed to load project: {exc}", color=ERROR)

    def _on_save_project(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project",
            str(self._current_project_path or Path.home() / "patch.xpm"),
            "PatchForge Projects (*.xpm);;All files (*)")
        if not path:
            return
        try:
            save_project(self._collect_settings(validate=False), Path(path))
            self._current_project_path = Path(path)
            self.setWindowTitle(f"PatchForge — {Path(path).name}")
            self.status_bar.showMessage(f"Saved: {path}")
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

    def _collect_settings(self, validate: bool = True) -> Optional[ProjectSettings]:
        s = ProjectSettings(
            app_name      = self.app_name_edit.text().strip(),
            version       = self.version_edit.text().strip(),
            description   = self.desc_edit.text().strip(),
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
            if errors:
                for e in errors:
                    self._log(f"✗  {e}", color=ERROR)
                return None
        return s

    def _apply_settings(self, s: ProjectSettings):
        self.app_name_edit.setText(s.app_name)
        self.version_edit.setText(s.version)
        self.desc_edit.setText(s.description)
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
