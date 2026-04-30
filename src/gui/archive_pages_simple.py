"""Form-based archive sub-pages: Crack identity, Run options, Polling.

Each page binds widgets to an ArchiveProject section via refresh() →
write project values into widgets and flush() → write widgets back into
project.  Editing also flips panel.mark_dirty() so the title bar shows
unsaved-changes state.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QSpinBox, QVBoxLayout, QWidget,
)

from .archive_pages import ArchivePageBase


# ---------------------------------------------------------------------------
# Crack identity
# ---------------------------------------------------------------------------

class CrackIdentityPage(ArchivePageBase):
    """Editor for project.crack (Goldberg / ColdClient identity).

    NOT credentials — Steam64 + username are public.  See
    src/core/archive/credentials.py for the secret-side companion.
    """

    LANGUAGES = [
        "english", "french", "german", "spanish", "italian", "polish",
        "russian", "japanese", "koreana", "schinese", "tchinese",
        "brazilian", "portuguese", "turkish", "thai", "ukrainian",
    ]

    def __init__(self, panel):
        super().__init__(panel)
        self._building = False
        layout = QVBoxLayout(self)

        grp = QGroupBox("Crack identity (Goldberg / ColdClient)")
        form = QFormLayout(grp)
        form.setContentsMargins(12, 14, 12, 14)
        form.setSpacing(8)

        self.steam_id = QLineEdit()
        self.steam_id.setPlaceholderText("76561198000000000")
        self.username = QLineEdit()
        self.username.setPlaceholderText("DisplayName")
        self.listen_port = QSpinBox()
        self.listen_port.setRange(1024, 65535)
        self.language = QComboBox()
        self.language.addItems(self.LANGUAGES)
        self.ach_language = QComboBox()
        self.ach_language.addItems(self.LANGUAGES)

        form.addRow("Steam64 ID:",            self.steam_id)
        form.addRow("Display username:",      self.username)
        form.addRow("Listen port:",           self.listen_port)
        form.addRow("Language:",              self.language)
        form.addRow("Achievement language:",  self.ach_language)

        layout.addWidget(grp)
        hint = QLabel(
            "Stored in plaintext inside the .xarchive — safe to commit / share.\n"
            "Steam Web API key (achievement schema) lives in archive_credentials.json."
        )
        hint.setObjectName("dim")
        layout.addWidget(hint)
        layout.addStretch(1)

        for w in (self.steam_id, self.username):
            w.editingFinished.connect(self._on_changed)
        self.listen_port.valueChanged.connect(self._on_changed)
        self.language.currentTextChanged.connect(self._on_changed)
        self.ach_language.currentTextChanged.connect(self._on_changed)

    def _on_changed(self, *_):
        if self._building:
            return
        self._panel.mark_dirty()

    def refresh(self):
        c = self._panel.project().crack
        self._building = True
        try:
            self.steam_id.setText(str(c.steam_id) if c.steam_id else "")
            self.username.setText(c.username)
            self.listen_port.setValue(c.listen_port or 47584)
            self._set_combo(self.language,     c.language or "english")
            self._set_combo(self.ach_language, c.achievement_language or "english")
        finally:
            self._building = False

    def flush(self):
        c = self._panel.project().crack
        try:
            c.steam_id = int(self.steam_id.text() or 0)
        except ValueError:
            c.steam_id = 0
        c.username             = self.username.text().strip()
        c.listen_port          = self.listen_port.value()
        c.language             = self.language.currentText()
        c.achievement_language = self.ach_language.currentText()

    @staticmethod
    def _set_combo(combo: QComboBox, value: str) -> None:
        idx = combo.findText(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.addItem(value)
            combo.setCurrentIndex(combo.count() - 1)


# ---------------------------------------------------------------------------
# Run options
# ---------------------------------------------------------------------------

class RunOptionsPage(ArchivePageBase):
    """Edits the project's persistent run-time knobs — defaults the CLI
    or the GUI run uses when the user doesn't override per invocation."""

    PLATFORMS    = ["windows", "linux", "macos", "all"]
    NOTIFY_MODES = [
        ("",      "auto (delay if MultiUp creds, else pre)"),
        ("pre",   "pre — fire before download, no upload links"),
        ("delay", "delay — fire after upload, with links"),
        ("both",  "both — pre + delay"),
        ("none",  "none — disable notifications"),
    ]

    def __init__(self, panel):
        super().__init__(panel)
        self._building = False
        layout = QVBoxLayout(self)

        # ── Output / shape ────────────────────────────────────────────
        shape_grp = QGroupBox("Archive shape")
        sf = QFormLayout(shape_grp)
        sf.setContentsMargins(12, 14, 12, 14); sf.setSpacing(8)
        self.platform = QComboBox(); self.platform.addItems(self.PLATFORMS)
        self.workers = QSpinBox(); self.workers.setRange(1, 64)
        self.compression = QSpinBox(); self.compression.setRange(0, 9)
        self.archive_password = QLineEdit()
        self.archive_password.setPlaceholderText("(plain — written to .xarchive)")
        self.volume_size = QLineEdit()
        self.volume_size.setPlaceholderText("e.g. 2G, 500M, 0 = no split")
        self.language = QLineEdit()
        self.max_retries = QSpinBox(); self.max_retries.setRange(0, 10)
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText("(blank = use app_settings.archive_output_dir)")

        sf.addRow("Default platform:",    self.platform)
        sf.addRow("Workers:",             self.workers)
        sf.addRow("Compression level:",   self.compression)
        sf.addRow("Archive password:",    self.archive_password)
        sf.addRow("Volume size:",         self.volume_size)
        sf.addRow("Steam language:",      self.language)
        sf.addRow("Max retries:",         self.max_retries)
        sf.addRow("Output dir:",          self.output_dir)
        layout.addWidget(shape_grp)

        # ── Upload ────────────────────────────────────────────────────
        up_grp = QGroupBox("Upload")
        uf = QFormLayout(up_grp)
        uf.setContentsMargins(12, 14, 12, 14); uf.setSpacing(8)
        self.upload_description = QLineEdit()
        self.upload_description.setPlaceholderText("(blank = use app name)")
        self.max_concurrent_uploads = QSpinBox()
        self.max_concurrent_uploads.setRange(1, 16)
        self.delete_archives = QCheckBox("Delete archives after upload")
        uf.addRow("Description:",      self.upload_description)
        uf.addRow("Concurrent uploads:", self.max_concurrent_uploads)
        uf.addRow(self.delete_archives)
        layout.addWidget(up_grp)

        # ── Notify + crack ───────────────────────────────────────────
        misc_grp = QGroupBox("Notify + crack")
        mf = QFormLayout(misc_grp)
        mf.setContentsMargins(12, 14, 12, 14); mf.setSpacing(8)
        self.notify_mode = QComboBox()
        for _key, label in self.NOTIFY_MODES:
            self.notify_mode.addItem(label)
        self.crack_mode = QComboBox()
        self.crack_mode.addItem("(off)",      userData="")
        self.crack_mode.addItem("coldclient", userData="coldclient")
        self.crack_mode.addItem("gse",        userData="gse")
        self.experimental = QCheckBox("Experimental download path")
        self.unstub_keepbind   = QCheckBox("unstub: keep bind section")
        self.unstub_keepstub   = QCheckBox("unstub: keep DOS stub")
        self.unstub_dumppayload= QCheckBox("unstub: dump payload")
        self.unstub_dumpdrmp   = QCheckBox("unstub: dump DRMP")
        self.unstub_realign    = QCheckBox("unstub: realign sections")
        self.unstub_recalc     = QCheckBox("unstub: recalc checksum")
        mf.addRow("Notify mode:",  self.notify_mode)
        mf.addRow("Crack mode:",   self.crack_mode)
        mf.addRow(self.experimental)
        for cb in (self.unstub_keepbind, self.unstub_keepstub,
                   self.unstub_dumppayload, self.unstub_dumpdrmp,
                   self.unstub_realign, self.unstub_recalc):
            mf.addRow(cb)
        layout.addWidget(misc_grp)

        layout.addStretch(1)

        # wire dirty flag
        for w in (self.archive_password, self.volume_size, self.language,
                  self.output_dir, self.upload_description):
            w.editingFinished.connect(self._on_changed)
        for w in (self.platform, self.notify_mode, self.crack_mode):
            w.currentIndexChanged.connect(self._on_changed)
        for w in (self.workers, self.compression, self.max_retries,
                  self.max_concurrent_uploads):
            w.valueChanged.connect(self._on_changed)
        for cb in (self.delete_archives, self.experimental,
                   self.unstub_keepbind, self.unstub_keepstub,
                   self.unstub_dumppayload, self.unstub_dumpdrmp,
                   self.unstub_realign, self.unstub_recalc):
            cb.toggled.connect(self._on_changed)

    def _on_changed(self, *_):
        if self._building:
            return
        self._panel.mark_dirty()

    def refresh(self):
        p = self._panel.project()
        self._building = True
        try:
            self._set_combo(self.platform, p.default_platform or "windows")
            self.workers.setValue(p.workers or 8)
            self.compression.setValue(p.compression if p.compression is not None else 9)
            self.archive_password.setText(p.archive_password)
            self.volume_size.setText(p.volume_size)
            self.language.setText(p.language or "english")
            self.max_retries.setValue(p.max_retries or 1)
            self.output_dir.setText(p.output_dir)
            self.upload_description.setText(p.upload_description)
            self.max_concurrent_uploads.setValue(p.max_concurrent_uploads or 1)
            self.delete_archives.setChecked(p.delete_archives)
            self.experimental.setChecked(p.experimental)
            keys = [k for k, _ in self.NOTIFY_MODES]
            self.notify_mode.setCurrentIndex(
                keys.index(p.notify_mode) if p.notify_mode in keys else 0
            )
            crack_keys = ["", "coldclient", "gse"]
            self.crack_mode.setCurrentIndex(
                crack_keys.index(p.crack_mode) if p.crack_mode in crack_keys else 0
            )
            u = p.unstub
            self.unstub_keepbind.setChecked(u.keepbind)
            self.unstub_keepstub.setChecked(u.keepstub)
            self.unstub_dumppayload.setChecked(u.dumppayload)
            self.unstub_dumpdrmp.setChecked(u.dumpdrmp)
            self.unstub_realign.setChecked(u.realign)
            self.unstub_recalc.setChecked(u.recalcchecksum)
        finally:
            self._building = False

    def flush(self):
        p = self._panel.project()
        p.default_platform = self.platform.currentText()
        p.workers          = self.workers.value()
        p.compression      = self.compression.value()
        p.archive_password = self.archive_password.text()
        p.volume_size      = self.volume_size.text()
        p.language         = self.language.text() or "english"
        p.max_retries      = self.max_retries.value()
        p.output_dir       = self.output_dir.text()
        p.upload_description     = self.upload_description.text()
        p.max_concurrent_uploads = self.max_concurrent_uploads.value()
        p.delete_archives  = self.delete_archives.isChecked()
        p.experimental     = self.experimental.isChecked()
        p.notify_mode      = self.NOTIFY_MODES[self.notify_mode.currentIndex()][0]
        p.crack_mode       = self.crack_mode.currentData() or ""
        u = p.unstub
        u.keepbind       = self.unstub_keepbind.isChecked()
        u.keepstub       = self.unstub_keepstub.isChecked()
        u.dumppayload    = self.unstub_dumppayload.isChecked()
        u.dumpdrmp       = self.unstub_dumpdrmp.isChecked()
        u.realign        = self.unstub_realign.isChecked()
        u.recalcchecksum = self.unstub_recalc.isChecked()

    @staticmethod
    def _set_combo(combo: QComboBox, value: str) -> None:
        idx = combo.findText(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------

class PollingPage(ArchivePageBase):
    """Editor for restart_delay + batch_size (Phase 5 polling-mode knobs)."""

    def __init__(self, panel):
        super().__init__(panel)
        self._building = False
        layout = QVBoxLayout(self)

        grp = QGroupBox("Polling")
        form = QFormLayout(grp)
        form.setContentsMargins(12, 14, 12, 14); form.setSpacing(8)
        self.restart_delay = QSpinBox()
        self.restart_delay.setRange(0, 24*60*60)
        self.restart_delay.setSuffix(" sec")
        self.batch_size = QSpinBox()
        self.batch_size.setRange(0, 10_000)
        self.batch_size.setSpecialValueText("(single batch)")

        form.addRow("Restart delay:", self.restart_delay)
        form.addRow("Batch size:",    self.batch_size)
        layout.addWidget(grp)

        hint = QLabel(
            "restart_delay > 0 enables poll-on-change mode.  Each cycle the\n"
            "GUI fetches product-info, then triggers a download for any app\n"
            "whose Steam buildid moved since the .xarchive last persisted.\n"
            "batch_size chunks the product-info RPC; 0 = single batch."
        )
        hint.setObjectName("dim")
        layout.addWidget(hint)
        layout.addStretch(1)

        self.restart_delay.valueChanged.connect(self._on_changed)
        self.batch_size.valueChanged.connect(self._on_changed)

    def _on_changed(self, *_):
        if self._building:
            return
        self._panel.mark_dirty()

    def refresh(self):
        p = self._panel.project()
        self._building = True
        try:
            self.restart_delay.setValue(p.restart_delay or 0)
            self.batch_size.setValue(p.batch_size or 0)
        finally:
            self._building = False

    def flush(self):
        p = self._panel.project()
        p.restart_delay = self.restart_delay.value()
        p.batch_size    = self.batch_size.value()


__all__ = ["CrackIdentityPage", "RunOptionsPage", "PollingPage"]
