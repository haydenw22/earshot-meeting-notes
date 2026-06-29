"""Design system: semantic color tokens for light + dark themes and the Qt
stylesheet built from them.

One source of truth — every surface, text and accent colour is a token, so the
two themes stay consistent (the dark-mode-pairing rule). Code that needs a raw
colour (icons, shadows) reads `tokens()`; widgets get styled via `build_qss()`.
"""
from __future__ import annotations

from typing import Literal

Mode = Literal["light", "dark"]

FONT_FAMILY = '"Segoe UI Variable Display", "Segoe UI", system-ui, sans-serif'

LIGHT: dict[str, str] = {
    "bg": "#F5F6FA",          # app background
    "surface": "#FFFFFF",      # cards / panels
    "surface_alt": "#FBFBFE",  # sidebar / subtle panels
    "surface_hover": "#F2F3F9",
    "text": "#1B1C2A",         # primary text
    "text_muted": "#646579",   # secondary text
    "text_faint": "#9A9BAC",   # tertiary / placeholders
    "border": "#E8E8F0",
    "border_strong": "#D9DAE6",
    "primary": "#6366F1",      # indigo accent
    "primary_hover": "#5457E6",
    "primary_press": "#4A4DD4",
    "primary_soft": "#ECEDFE",  # selected nav / soft chips
    "on_primary": "#FFFFFF",
    "danger": "#F0483E",       # record / destructive (coral red)
    "danger_hover": "#DA362D",
    "danger_soft": "#FDECEA",
    "on_danger": "#FFFFFF",
    "warning": "#B45309",       # caution (amber) — e.g. "no input detected"
    "warning_soft": "#FEF3C7",
    "focus": "#6366F1",
    "scroll_thumb": "#D4D5E0",
    "shadow": "0,0,0",
}

DARK: dict[str, str] = {
    "bg": "#0E0F15",
    "surface": "#181A22",
    "surface_alt": "#13141B",
    "surface_hover": "#21232D",
    "text": "#EDEDF3",
    "text_muted": "#9A9BAC",
    "text_faint": "#6C6D7E",
    "border": "#262833",
    "border_strong": "#31333F",
    "primary": "#7C82F2",
    "primary_hover": "#8B90F5",
    "primary_press": "#9499F7",
    "primary_soft": "#22243A",
    "on_primary": "#FFFFFF",
    "danger": "#F26157",
    "danger_hover": "#F47169",
    "danger_soft": "#2A1B1C",
    "on_danger": "#FFFFFF",
    "warning": "#FBBF24",       # caution (amber) — e.g. "no input detected"
    "warning_soft": "#2A2410",
    "focus": "#7C82F2",
    "scroll_thumb": "#34364360",
}


def tokens(mode: Mode) -> dict[str, str]:
    return DARK if mode == "dark" else LIGHT


def build_qss(mode: Mode, check_icon: str | None = None) -> str:
    t = tokens(mode)
    # quote the path so usernames with spaces (e.g. C:/Users/John Doe/…) work
    check_rule = f'image: url("{check_icon}");' if check_icon else ""
    return f"""
* {{
    font-family: {FONT_FAMILY};
    font-size: 14px;
    color: {t['text']};
    outline: none;
}}
QWidget {{ background: transparent; }}
QMainWindow, #Root {{ background-color: {t['bg']}; }}

/* ---------- cards / panels ---------- */
#Card {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 16px;
}}
#Sidebar {{
    background-color: {t['surface_alt']};
    border-right: 1px solid {t['border']};
}}
#Sidebar[side="right"] {{
    border-right: none;
    border-left: 1px solid {t['border']};
}}
QSplitter#MainSplitter::handle {{ background: transparent; }}
QSplitter#MainSplitter::handle:hover {{ background: {t['primary_soft']}; }}
#Topbar {{ background-color: transparent; }}

/* ---------- text roles ---------- */
#H1 {{ font-size: 26px; font-weight: 700; color: {t['text']}; }}
#H2 {{ font-size: 19px; font-weight: 700; color: {t['text']}; }}
#H3 {{ font-size: 15px; font-weight: 600; color: {t['text']}; }}
#Muted {{ color: {t['text_muted']}; }}
#Faint {{ color: {t['text_faint']}; font-size: 12px; }}
#SectionLabel {{ color: {t['text_faint']}; font-size: 11px; font-weight: 600; }}

/* ---------- buttons ---------- */
QPushButton {{
    background-color: {t['surface']};
    color: {t['text']};
    border: 1px solid {t['border_strong']};
    border-radius: 10px;
    padding: 8px 16px;
    font-size: 14px;
    font-weight: 600;
}}
QPushButton:hover {{ background-color: {t['surface_hover']}; }}
QPushButton:disabled {{ color: {t['text_faint']}; border-color: {t['border']}; }}

QPushButton[variant="primary"] {{
    background-color: {t['primary']};
    color: {t['on_primary']};
    border: none;
    padding: 9px 18px;
}}
QPushButton[variant="primary"]:hover {{ background-color: {t['primary_hover']}; }}
QPushButton[variant="primary"]:pressed {{ background-color: {t['primary_press']}; }}
QPushButton[variant="primary"]:disabled {{ background-color: {t['primary_soft']}; color: {t['text_faint']}; }}

QPushButton[variant="danger"] {{
    background-color: {t['danger']};
    color: {t['on_danger']};
    border: none;
    padding: 9px 18px;
}}
QPushButton[variant="danger"]:hover {{ background-color: {t['danger_hover']}; }}

QPushButton[variant="ghost"] {{
    background-color: transparent;
    border: none;
    color: {t['text_muted']};
    font-weight: 600;
    text-align: left;
    padding: 9px 12px;
    border-radius: 10px;
}}
QPushButton[variant="ghost"]:hover {{ background-color: {t['surface_hover']}; color: {t['text']}; }}
QPushButton[variant="ghost"]:checked {{ background-color: {t['primary_soft']}; color: {t['primary']}; }}

/* round icon-only buttons (rail) */
QToolButton {{
    background-color: transparent;
    border: none;
    border-radius: 12px;
    padding: 8px;
}}
QToolButton:hover {{ background-color: {t['surface_hover']}; }}
QToolButton:checked {{ background-color: {t['primary_soft']}; }}

/* ---------- inputs ---------- */
QLineEdit, QComboBox, QPlainTextEdit {{
    background-color: {t['surface']};
    border: 1px solid {t['border_strong']};
    border-radius: 10px;
    padding: 8px 12px;
    color: {t['text']};
    selection-background-color: {t['primary']};
    selection-color: {t['on_primary']};
}}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {t['focus']}; }}
QLineEdit::placeholder {{ color: {t['text_faint']}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 10px;
    padding: 4px;
    selection-background-color: {t['primary_soft']};
    selection-color: {t['text']};
    outline: none;
}}

/* ---------- checkbox / switch-ish ---------- */
QCheckBox {{ color: {t['text']}; spacing: 8px; }}
QCheckBox::indicator {{
    width: 18px; height: 18px;
    border: 1px solid {t['border_strong']};
    border-radius: 6px;
    background: {t['surface']};
}}
QCheckBox::indicator:hover {{ border-color: {t['primary']}; }}
QCheckBox::indicator:checked {{ background: {t['primary']}; border-color: {t['primary']}; {check_rule} }}

/* ---------- progress / level meters ---------- */
QProgressBar {{
    background-color: {t['surface_hover']};
    border: none;
    border-radius: 5px;
    height: 8px;
    text-align: center;
}}
QProgressBar::chunk {{ background-color: {t['primary']}; border-radius: 5px; }}
QProgressBar[meter="them"]::chunk {{ background-color: {t['danger']}; }}

/* ---------- tabs (settings) ---------- */
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{
    background: transparent;
    color: {t['text_muted']};
    padding: 8px 16px;
    margin-right: 4px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 600;
}}
QTabBar::tab:selected {{ color: {t['primary']}; border-bottom: 2px solid {t['primary']}; }}
QTabBar::tab:hover {{ color: {t['text']}; }}

/* ---------- text views ---------- */
QTextBrowser, QTextEdit {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 12px;
    padding: 8px 12px;
    color: {t['text']};
}}

/* ---------- meeting list ---------- */
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{
    color: {t['text']};
    border-radius: 10px;
    padding: 10px 10px;
    margin: 2px 0;
}}
QListWidget::item:hover {{ background-color: {t['surface_hover']}; }}
QListWidget::item:selected {{ background-color: {t['primary_soft']}; color: {t['text']}; }}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['scroll_thumb']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t['scroll_thumb']}; border-radius: 5px; min-width: 30px; }}

QToolTip {{
    background-color: {t['surface']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 8px;
    padding: 6px 10px;
}}
"""
