"""BBCode template editor with live preview.

Left pane: source QPlainTextEdit (the project's bbcode_template).
Right pane: rendered preview against fake sample data, refreshed on
text-change with a small debounce so typing stays smooth.
Top toolbar: token chips that insert at cursor + Reset to default button.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

from ..core.archive import bbcode as bbcode_mod
from ..core.archive import project as project_mod
from .archive_pages import ArchivePageBase


# Token chip labels — clicking inserts the literal text at cursor.
TOKEN_CHIPS = [
    "{APP_NAME}", "{APPID}", "{BUILDID}", "{PREVIOUS_BUILDID}",
    "{DATE}", "{DATETIME}", "{STEAMDB_URL}",
    "{WINDOWS_LINK}", "{LINUX_LINK}", "{MACOS_LINK}",
    "{ALL_LINKS}", "{PLATFORMS}", "{MANIFESTS}", "{HEADER_IMAGE}",
]

# Sample data so the preview always renders something — tests of the
# token expansion without needing a real run.
_SAMPLE = dict(
    name="Counter-Strike 2",
    appid=730,
    buildid="16203481",
    previous_buildid="16187234",
    timeupdated=1714500000,
    upload_links={
        "windows": "https://multiup.io/abc123",
        "linux":   "https://multiup.io/def456",
        "macos":   "https://multiup.io/ghi789",
    },
    manifests={
        "windows": [(1234, "main_content", "999111222333")],
        "linux":   [(5678, "linux_binaries", "888777666555")],
    },
    header_image="https://cdn.cloudflare.steamstatic.com/steam/apps/730/header.jpg",
)


class BBCodePage(ArchivePageBase):

    def __init__(self, panel):
        super().__init__(panel)
        self._building = False
        layout = QVBoxLayout(self)

        # ── chip + reset toolbar ─────────────────────────────────────
        bar = QHBoxLayout()
        bar.setSpacing(4)
        bar.addWidget(QLabel("Tokens:"))
        for chip in TOKEN_CHIPS:
            btn = QPushButton(chip)
            btn.setFlat(True)
            btn.setStyleSheet("padding: 2px 8px; min-width: 0;")
            btn.clicked.connect(lambda _=False, t=chip: self._insert_token(t))
            bar.addWidget(btn)
        bar.addStretch(1)
        self.btn_reset = QPushButton("Reset to default")
        self.btn_reset.clicked.connect(self._on_reset)
        bar.addWidget(self.btn_reset)
        layout.addLayout(bar)

        # ── split editor / preview ───────────────────────────────────
        split = QSplitter(Qt.Horizontal)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("(empty template)")
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("(preview)")
        split.addWidget(self.editor)
        split.addWidget(self.preview)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)

        # debounce preview refresh
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(150)
        self._preview_timer.timeout.connect(self._refresh_preview)
        self.editor.textChanged.connect(self._on_text_changed)

    def _insert_token(self, token: str):
        self.editor.insertPlainText(token)
        self.editor.setFocus()

    def _on_reset(self):
        ans = QMessageBox.question(
            self, "Reset template?",
            "Replace the current BBCode template with the vendored default?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        try:
            tpl = bbcode_mod.load_default_template()
        except Exception:
            tpl = project_mod.default_bbcode_template()
        self.editor.setPlainText(tpl)
        self._panel.mark_dirty()

    def _on_text_changed(self):
        if self._building:
            return
        self._panel.mark_dirty()
        self._preview_timer.start()

    def _refresh_preview(self):
        try:
            data = bbcode_mod.build_data(**_SAMPLE)
            rendered = bbcode_mod.render(self.editor.toPlainText(), data)
        except Exception as exc:
            rendered = f"(preview error: {exc})"
        self.preview.setPlainText(rendered)

    # ---------------------------------------------------------- protocol
    def refresh(self):
        self._building = True
        try:
            self.editor.setPlainText(self._panel.project().bbcode_template or "")
        finally:
            self._building = False
        self._refresh_preview()

    def flush(self):
        self._panel.project().bbcode_template = self.editor.toPlainText()


__all__ = ["BBCodePage"]
