"""BBCode template editor with format toolbar, colour picker and live preview.

Layout:
  Top     — format toolbar (B/I/U/S, lists, links, sizes, spoilers, …)
            + placeholder toolbar + Reset button
  Body    — horizontal split: editor | preview | 120-swatch colour bar

Format / colour buttons wrap the current selection in BBCode tags
(no selection → tags inserted at cursor with caret between them).
Placeholder buttons insert the literal {TOKEN}.  Preview re-renders
against the same _SAMPLE data on a 150 ms debounce.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, QRect, QSize, QPoint
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget,
)

from ..core.archive import bbcode as bbcode_mod
from ..core.archive import project as project_mod
from .archive_pages import ArchivePageBase


# 120 colours from the typical phpBB-style colour picker (5 cols × 24 rows).
_COLORS = [
    '#000000','#000040','#000080','#0000BF','#0000FF',
    '#004000','#004040','#004080','#0040BF','#0040FF',
    '#008000','#008040','#008080','#0080BF','#0080FF',
    '#00BF00','#00BF40','#00BF80','#00BFBF','#00BFFF',
    '#00FF00','#00FF40','#00FF80','#00FFBF','#00FFFF',
    '#400000','#400040','#400080','#4000BF','#4000FF',
    '#404000','#404040','#404080','#4040BF','#4040FF',
    '#408000','#408040','#408080','#4080BF','#4080FF',
    '#40BF00','#40BF40','#40BF80','#40BFBF','#40BFFF',
    '#40FF00','#40FF40','#40FF80','#40FFBF','#40FFFF',
    '#800000','#800040','#800080','#8000BF','#8000FF',
    '#804000','#804040','#804080','#8040BF','#8040FF',
    '#808000','#808040','#808080','#8080BF','#8080FF',
    '#80BF00','#80BF40','#80BF80','#80BFBF','#80BFFF',
    '#80FF00','#80FF40','#80FF80','#80FFBF','#80FFFF',
    '#BF0000','#BF0040','#BF0080','#BF00BF','#BF00FF',
    '#BF4000','#BF4040','#BF4080','#BF40BF','#BF40FF',
    '#BF8000','#BF8040','#BF8080','#BF80BF','#BF80FF',
    '#BFBF00','#BFBF40','#BFBF80','#BFBFBF','#BFBFFF',
    '#BFFF00','#BFFF40','#BFFF80','#BFFFBF','#BFFFFF',
    '#FF0000','#FF0040','#FF0080','#FF00BF','#FF00FF',
    '#FF4000','#FF4040','#FF4080','#FF40BF','#FF40FF',
    '#FF8000','#FF8040','#FF8080','#FF80BF','#FF80FF',
    '#FFBF00','#FFBF40','#FFBF80','#FFBFBF','#FFBFFF',
]

# (token, label-on-button)
_PLACEHOLDERS = [
    ('{APP_NAME}',         'App Name'),
    ('{APPID}',            'App ID'),
    ('{BUILDID}',          'Build ID'),
    ('{PREVIOUS_BUILDID}', 'Prev Build'),
    ('{DATE}',             'Date'),
    ('{DATETIME}',         'DateTime'),
    ('{STEAMDB_URL}',      'SteamDB URL'),
    ('{ALL_LINKS}',        'All Links'),
    ('{PLATFORMS}',        'Platforms'),
    ('{MANIFESTS}',        'Manifests'),
    ('{WINDOWS_LINK}',     'Win Link'),
    ('{LINUX_LINK}',       'Linux Link'),
    ('{MACOS_LINK}',       'macOS Link'),
    ('{HEADER_IMAGE}',     'Header Image'),
]

_SIZES = [('Tiny', '50'), ('Small', '85'), ('Normal', '100'),
          ('Large', '150'), ('Huge', '200')]

# Colour bar geometry
_COLOR_COLS    = 5
_COLOR_SWATCH  = 16
_COLOR_SPACING = 2
_COLOR_MARGIN  = 4
_COLOR_BAR_W   = (
    _COLOR_COLS * _COLOR_SWATCH
    + (_COLOR_COLS - 1) * _COLOR_SPACING
    + 2 * _COLOR_MARGIN
    + 8
)

# Sample data so the preview always renders something.
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


class _FlowLayout(QLayout):
    """Wrapping flow layout — items reflow when row is full."""

    def __init__(self, parent=None, spacing: int = 3) -> None:
        super().__init__(parent)
        self._items: list = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 2, 0, 4)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout(QRect(0, 0, width, 0), dry_run=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._layout(rect, dry_run=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _layout(self, rect: QRect, dry_run: bool) -> int:
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y = eff.x(), eff.y()
        line_height = 0
        sp = self.spacing()

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + sp
            if next_x - sp > eff.right() and line_height > 0:
                x = eff.x()
                y += line_height + sp
                next_x = x + hint.width() + sp
                line_height = 0
            if not dry_run:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + m.bottom()


class _FlowContainer(QWidget):
    """Propagates hasHeightForWidth from _FlowLayout to parent layout."""

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        lo = self.layout()
        return lo.heightForWidth(w) if lo else super().heightForWidth(w)

    def sizeHint(self) -> QSize:
        lo = self.layout()
        w = self.width() or 800
        h = lo.heightForWidth(w) if lo else 0
        return QSize(w, h)


class BBCodePage(ArchivePageBase):

    def __init__(self, panel):
        super().__init__(panel)
        self._building = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(5)

        outer.addWidget(self._build_format_toolbar())
        outer.addWidget(self._build_placeholder_toolbar())

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        outer.addWidget(sep)

        body = QHBoxLayout()
        body.setSpacing(6)

        split = QSplitter(Qt.Horizontal)
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont('Monospace', 10))
        self.editor.setPlaceholderText("(empty template)")
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("(preview)")
        split.addWidget(self.editor)
        split.addWidget(self.preview)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        body.addWidget(split, 1)

        body.addWidget(self._build_color_bar(), 0)
        outer.addLayout(body, 1)

        # Debounced preview refresh.
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(150)
        self._preview_timer.timeout.connect(self._refresh_preview)
        self.editor.textChanged.connect(self._on_text_changed)

    # ── toolbars ────────────────────────────────────────────────────────

    def _build_format_toolbar(self) -> _FlowContainer:
        container = _FlowContainer()
        flow = _FlowLayout(container, spacing=3)

        def _btn(label: str, open_tag: str, close_tag: str,
                 w: int = 28, **css) -> QPushButton:
            b = QPushButton(label)
            b.setFixedHeight(26)
            b.setFixedWidth(w)
            props = {'padding': '2px 4px'}
            props.update(css)
            rules = '; '.join(f'{k.replace("_", "-")}: {v}' for k, v in props.items())
            b.setStyleSheet(f'QPushButton {{ {rules} }}')
            b.clicked.connect(lambda _=False, o=open_tag, c=close_tag: self._wrap(o, c))
            return b

        def _sep() -> QFrame:
            f = QFrame()
            f.setFrameShape(QFrame.Shape.VLine)
            f.setFixedSize(6, 26)
            f.setStyleSheet('color: #444;')
            return f

        lbl = QLabel('BBCode:')
        flow.addWidget(lbl)

        flow.addWidget(_btn('B', '[b]', '[/b]', font_weight='bold'))
        flow.addWidget(_btn('I', '[i]', '[/i]', font_style='italic'))
        flow.addWidget(_btn('U', '[u]', '[/u]', text_decoration='underline'))
        flow.addWidget(_btn('S', '[s]', '[/s]'))
        flow.addWidget(_sep())
        flow.addWidget(_btn('Quote', '[quote]',  '[/quote]', w=46))
        flow.addWidget(_btn('Code',  '[code]',   '[/code]',  w=40))
        flow.addWidget(_sep())
        flow.addWidget(_btn('List',  '[list]\n', '\n[/list]',  w=36))
        flow.addWidget(_btn('List=', '[list=1]\n', '\n[/list]', w=42))

        li_btn = QPushButton('[*]')
        li_btn.setFixedSize(36, 26)
        li_btn.setStyleSheet('QPushButton { padding: 2px 4px; }')
        li_btn.clicked.connect(lambda: self._insert('[*]'))
        flow.addWidget(li_btn)

        flow.addWidget(_sep())
        flow.addWidget(_btn('Img', '[img]', '[/img]', w=34))
        flow.addWidget(_btn('URL', '[url]', '[/url]', w=34))
        flow.addWidget(_sep())

        size_menu = QMenu(self)
        for label, val in _SIZES:
            size_menu.addAction(label, lambda v=val: self._wrap(f'[size={v}]', '[/size]'))

        size_btn = QPushButton('Size ▾')
        size_btn.setFixedHeight(26)
        size_btn.setStyleSheet('QPushButton { padding: 2px 6px; }')
        size_btn.clicked.connect(
            lambda: size_menu.exec(size_btn.mapToGlobal(size_btn.rect().bottomLeft()))
        )
        flow.addWidget(size_btn)

        flow.addWidget(_sep())
        flow.addWidget(_btn('Spoiler', '[spoiler]', '[/spoiler]', w=56))

        sp_eq = QPushButton('Spoiler=')
        sp_eq.setFixedHeight(26)
        sp_eq.setStyleSheet('QPushButton { padding: 2px 8px; }')
        sp_eq.clicked.connect(self._insert_named_spoiler)
        flow.addWidget(sp_eq)

        flow.addWidget(_btn('YouTube', '[youtube]', '[/youtube]', w=60))
        return container

    def _build_placeholder_toolbar(self) -> _FlowContainer:
        container = _FlowContainer()
        flow = _FlowLayout(container, spacing=3)

        flow.addWidget(QLabel('Placeholders:'))

        for placeholder, label in _PLACEHOLDERS:
            btn = QPushButton(label)
            btn.setFixedHeight(24)
            btn.setStyleSheet('QPushButton { padding: 2px 6px; }')
            btn.setToolTip(placeholder)
            btn.clicked.connect(lambda _=False, p=placeholder: self._insert(p))
            flow.addWidget(btn)

        # Trailing Reset button.
        self.btn_reset = QPushButton("Reset to default")
        self.btn_reset.setFixedHeight(24)
        self.btn_reset.setStyleSheet('QPushButton { padding: 2px 8px; }')
        self.btn_reset.clicked.connect(self._on_reset)
        flow.addWidget(self.btn_reset)

        return container

    def _build_color_bar(self) -> QScrollArea:
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setSpacing(_COLOR_SPACING)
        grid.setContentsMargins(_COLOR_MARGIN, _COLOR_MARGIN,
                                _COLOR_MARGIN, _COLOR_MARGIN)

        for i, color in enumerate(_COLORS):
            btn = QPushButton()
            btn.setFixedSize(_COLOR_SWATCH, _COLOR_SWATCH)
            btn.setToolTip(color)
            btn.setStyleSheet(
                f'QPushButton {{ background-color: {color}; border: 1px solid #555;'
                f' border-radius: 1px; padding: 0; }}'
                f'QPushButton:hover {{ border: 2px solid #fff; }}'
            )
            btn.clicked.connect(lambda _=False, c=color: self._wrap(f'[color={c}]', '[/color]'))
            grid.addWidget(btn, i // _COLOR_COLS, i % _COLOR_COLS)

        rows = len(_COLORS) // _COLOR_COLS
        nw = _COLOR_COLS * _COLOR_SWATCH + (_COLOR_COLS - 1) * _COLOR_SPACING + 2 * _COLOR_MARGIN
        nh = rows * _COLOR_SWATCH + (rows - 1) * _COLOR_SPACING + 2 * _COLOR_MARGIN
        grid_w.setFixedSize(nw, nh)

        scroll = QScrollArea()
        scroll.setWidget(grid_w)
        scroll.setWidgetResizable(False)
        scroll.setFixedWidth(_COLOR_BAR_W)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.Shape.Box)
        return scroll

    # ── Editing helpers ────────────────────────────────────────────────

    def _wrap(self, open_tag: str, close_tag: str) -> None:
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            cursor.insertText(f'{open_tag}{cursor.selectedText()}{close_tag}')
        else:
            cursor.insertText(f'{open_tag}{close_tag}')
            cursor.setPosition(cursor.position() - len(close_tag))
            self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    def _insert(self, text: str) -> None:
        self.editor.textCursor().insertText(text)
        self.editor.setFocus()

    def _insert_named_spoiler(self) -> None:
        cursor = self.editor.textCursor()
        inner = cursor.selectedText() if cursor.hasSelection() else ''
        cursor.insertText(f'[spoiler="Title"]{inner}[/spoiler]')
        anchor = cursor.position() - len(inner) - len('[/spoiler]') - len('Title"')
        cursor.setPosition(anchor)
        cursor.setPosition(anchor + len('Title'), cursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    # ── Reset / preview ────────────────────────────────────────────────

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

    # ── ArchivePageBase protocol ───────────────────────────────────────

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
