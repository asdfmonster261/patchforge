"""Dark theme stylesheet for PatchForge."""

# Palette
BG       = "#1e1e1e"
BG_LIGHT = "#2d2d2d"
BG_INPUT = "#252526"
BORDER   = "#3c3c3c"
ACCENT   = "#007acc"
ACCENT_H = "#1a8adc"
TEXT     = "#d4d4d4"
TEXT_DIM = "#888888"
SUCCESS  = "#4ec9b0"
ERROR    = "#f44747"
WARN     = "#ce9178"

QSS = f"""
/* ── Base ── */
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: "Segoe UI", "Liberation Sans", sans-serif;
    font-size: 13px;
}}

QMainWindow, QDialog {{
    background-color: {BG};
}}

/* ── Group boxes ── */
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 6px;
    font-size: 12px;
    color: {TEXT_DIM};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    padding: 0 4px;
}}

/* ── Labels ── */
QLabel {{
    background: transparent;
    color: {TEXT};
}}
QLabel#dim {{
    color: {TEXT_DIM};
    font-size: 11px;
}}

/* ── Line edits ── */
QLineEdit {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 4px 6px;
    color: {TEXT};
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{
    border-color: {ACCENT};
}}
QLineEdit:disabled {{
    color: {TEXT_DIM};
    background-color: {BG};
}}

/* ── Combo boxes ── */
QComboBox {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 4px 6px;
    color: {TEXT};
    min-width: 120px;
}}
QComboBox:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {TEXT_DIM};
    width: 0;
    height: 0;
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_LIGHT};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    color: {TEXT};
    outline: none;
}}

/* ── Push buttons ── */
QPushButton {{
    background-color: {BG_LIGHT};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 6px 14px;
    color: {TEXT};
    min-width: 80px;
}}
QPushButton:hover {{
    background-color: #383838;
    border-color: #555;
}}
QPushButton:pressed {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    color: #fff;
}}
QPushButton:disabled {{
    color: {TEXT_DIM};
    border-color: {BORDER};
}}
QPushButton#accent {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    color: #fff;
    font-weight: bold;
    min-width: 110px;
    padding: 7px 20px;
}}
QPushButton#accent:hover {{
    background-color: {ACCENT_H};
}}
QPushButton#accent:disabled {{
    background-color: #2a4a66;
    border-color: #2a4a66;
    color: {TEXT_DIM};
}}
QPushButton#browse {{
    min-width: 28px;
    max-width: 28px;
    padding: 4px 0;
    font-size: 14px;
    border-radius: 3px;
}}

/* ── Progress bar ── */
QProgressBar {{
    background-color: {BG_LIGHT};
    border: 1px solid {BORDER};
    border-radius: 3px;
    text-align: center;
    color: {TEXT};
    height: 18px;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 2px;
}}

/* ── Text edit (log) ── */
QPlainTextEdit {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 3px;
    color: {TEXT};
    font-family: "Consolas", "Fira Mono", monospace;
    font-size: 12px;
    padding: 4px;
}}

/* ── Radio buttons ── */
QRadioButton {{
    spacing: 6px;
    background: transparent;
}}
QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {BORDER};
    border-radius: 7px;
    background: {BG_INPUT};
}}
QRadioButton::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}

/* ── Scroll bars ── */
QScrollBar:vertical {{
    background: {BG};
    width: 8px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: #555;
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

/* ── Splitter ── */
QSplitter::handle {{
    background: {BORDER};
}}

/* ── Status bar ── */
QStatusBar {{
    background: {BG_LIGHT};
    border-top: 1px solid {BORDER};
    color: {TEXT_DIM};
    font-size: 12px;
}}
"""
