"""Dark theme stylesheet for PatchForge (palette from gui_colors.txt)."""

# ── Palette ────────────────────────────────────────────────────────────────
BG       = "#121218"   # main bg
BG_INPUT = "#20202c"   # input / surface bg
BG_LOG   = "#181820"   # log / table bg
HOVER    = "#2c2c3c"   # hover surface
PRESSED  = "#3a3a55"   # pressed surface
BORDER   = "#2a2a3a"   # all borders
SCROLLBAR= "#373753"   # scrollbar handle
ACCENT   = "#4287f5"   # accent blue
ACCENT_H = "#5897ff"   # accent hover
SUCCESS  = "#3cb969"   # green
ERROR    = "#e64646"   # red
WARN     = "#e6be32"   # yellow
TEXT     = "#d7d7e1"   # body text
TEXT_MUT = "#a0a0b9"   # muted text
TEXT_DIM = "#6e6e87"   # dim text
DISABLED = "#1a1a24"   # disabled bg
SEL      = "#2e3a55"   # selection highlight

QSS = f"""
/* ── Base ─────────────────────────────────────────────────────────────── */
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: "Segoe UI", "Liberation Sans", sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background-color: {BG};
}}

/* ── Group boxes ─────────────────────────────────────────────────────── */
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 14px;
    padding: 10px 8px 8px 8px;
    font-size: 11px;
    color: {TEXT_DIM};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    background-color: {BG};
    color: {TEXT_MUT};
}}

/* ── Labels ──────────────────────────────────────────────────────────── */
QLabel {{
    background: transparent;
    color: {TEXT};
}}
QLabel#dim {{
    color: {TEXT_DIM};
    font-size: 11px;
}}

/* ── Line edits ──────────────────────────────────────────────────────── */
QLineEdit {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 5px 8px;
    color: {TEXT};
    selection-background-color: {SEL};
    selection-color: {TEXT};
}}
QLineEdit:hover {{
    border-color: #3d3d50;
}}
QLineEdit:focus {{
    border-color: {ACCENT};
}}
QLineEdit:read-only {{
    color: {TEXT_MUT};
    background-color: {BG};
}}
QLineEdit:disabled {{
    color: {TEXT_DIM};
    background-color: {DISABLED};
    border-color: {BORDER};
}}

/* ── Combo boxes ─────────────────────────────────────────────────────── */
QComboBox {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 5px 8px;
    color: {TEXT};
    min-width: 120px;
}}
QComboBox:hover {{
    border-color: #3d3d50;
}}
QComboBox:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
    subcontrol-origin: padding;
    subcontrol-position: right center;
}}
QComboBox::down-arrow {{
    image: none;
    border-left:  5px solid transparent;
    border-right: 5px solid transparent;
    border-top:   5px solid {TEXT_DIM};
    width: 0;
    height: 0;
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    selection-background-color: {SEL};
    selection-color: {TEXT};
    color: {TEXT};
    outline: none;
    padding: 2px;
}}
QComboBox QAbstractItemView::item {{
    padding: 4px 8px;
    min-height: 22px;
}}
QComboBox QAbstractItemView::item:hover {{
    background-color: {HOVER};
}}

/* ── Push buttons ────────────────────────────────────────────────────── */
QPushButton {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 14px;
    color: {TEXT};
    min-width: 80px;
}}
QPushButton:hover {{
    background-color: {HOVER};
    border-color: #3d3d50;
}}
QPushButton:pressed {{
    background-color: {PRESSED};
    border-color: {ACCENT};
}}
QPushButton:disabled {{
    color: {TEXT_DIM};
    background-color: {DISABLED};
    border-color: {BORDER};
}}
QPushButton#accent {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    color: #ffffff;
    font-weight: bold;
    min-width: 120px;
    padding: 8px 22px;
}}
QPushButton#accent:hover {{
    background-color: {ACCENT_H};
    border-color: {ACCENT_H};
}}
QPushButton#accent:pressed {{
    background-color: #3070d8;
    border-color: #3070d8;
}}
QPushButton#accent:disabled {{
    background-color: {DISABLED};
    border-color: {BORDER};
    color: {TEXT_DIM};
}}
QPushButton#browse {{
    min-width: 28px;
    max-width: 28px;
    padding: 4px 0;
    font-size: 15px;
    border-radius: 5px;
}}

/* ── Progress bar ────────────────────────────────────────────────────── */
QProgressBar {{
    background-color: {DISABLED};
    border: 1px solid {BORDER};
    border-radius: 5px;
    text-align: center;
    color: {TEXT_MUT};
    height: 16px;
    font-size: 11px;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 4px;
    margin: 1px;
}}

/* ── Plain text edit (log) ───────────────────────────────────────────── */
QPlainTextEdit {{
    background-color: {BG_LOG};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {TEXT};
    font-family: "Cascadia Mono", "Consolas", "Fira Mono", monospace;
    font-size: 12px;
    padding: 6px;
    selection-background-color: {SEL};
}}

/* ── Checkboxes ──────────────────────────────────────────────────────── */
QCheckBox {{
    spacing: 8px;
    background: transparent;
    color: {TEXT};
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {BORDER};
    border-radius: 4px;
    background-color: {BG_INPUT};
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT};
    background-color: {HOVER};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}
QCheckBox::indicator:checked:hover {{
    background-color: {ACCENT_H};
    border-color: {ACCENT_H};
}}
QCheckBox::indicator:disabled {{
    background-color: {DISABLED};
    border-color: {BORDER};
}}

/* ── Radio buttons ───────────────────────────────────────────────────── */
QRadioButton {{
    spacing: 8px;
    background: transparent;
    color: {TEXT};
}}
QRadioButton::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {BORDER};
    border-radius: 8px;
    background-color: {BG_INPUT};
}}
QRadioButton::indicator:hover {{
    border-color: {ACCENT};
    background-color: {HOVER};
}}
QRadioButton::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}

/* ── List widget ─────────────────────────────────────────────────────── */
QListWidget {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {TEXT};
    outline: none;
    padding: 2px;
}}
QListWidget::item {{
    padding: 4px 8px;
    border-radius: 4px;
    color: {TEXT};
}}
QListWidget::item:hover {{
    background-color: {HOVER};
}}
QListWidget::item:selected {{
    background-color: {SEL};
    color: {TEXT};
}}

/* ── Scroll bars ─────────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    border: none;
    margin: 2px 1px;
}}
QScrollBar::handle:vertical {{
    background: {SCROLLBAR};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background: #4a4a68;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}

QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    border: none;
    margin: 1px 2px;
}}
QScrollBar::handle:horizontal {{
    background: {SCROLLBAR};
    border-radius: 4px;
    min-width: 20px;
}}
QScrollBar::handle:horizontal:hover {{
    background: #4a4a68;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}

/* ── Scroll area ─────────────────────────────────────────────────────── */
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}

/* ── Frame separator ─────────────────────────────────────────────────── */
QFrame[frameShape="4"] {{
    border: none;
    border-top: 1px solid {BORDER};
    max-height: 0px;
    background: transparent;
    color: transparent;
}}

/* ── Splitter ────────────────────────────────────────────────────────── */
QSplitter::handle {{
    background: {BORDER};
    width: 1px;
    height: 1px;
}}

/* ── Status bar ──────────────────────────────────────────────────────── */
QStatusBar {{
    background: {BG_INPUT};
    border-top: 1px solid {BORDER};
    color: {TEXT_DIM};
    font-size: 12px;
    padding: 2px 8px;
}}
"""
