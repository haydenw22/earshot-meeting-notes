"""Design system: semantic color tokens for light + dark themes and the Qt
stylesheet built from them.

One source of truth — every surface, text and accent colour is a token, so the
two themes stay consistent (the dark-mode-pairing rule). Code that needs a raw
colour (icons, shadows) reads `tokens()`; widgets get styled via `build_qss()`.

Interaction states follow one matrix everywhere: rest → hover (surface shift) →
pressed (darker) → focus (accent border, keyboard-visible) → disabled (faint,
never invisible). Contrast targets: body text ≥ 4.5:1, secondary ≥ 3:1 in both
modes.
"""
from __future__ import annotations

import sys
from typing import Literal

Mode = Literal["light", "dark"]

if sys.platform == "darwin":
    # macOS: the hidden ".AppleSystemUIFont" face resolves to the system
    # San Francisco font; Helvetica Neue is the safety net.
    FONT_FAMILY = '".AppleSystemUIFont", "Helvetica Neue", system-ui, sans-serif'
    FONT_DISPLAY = FONT_FAMILY
else:
    # Windows 11 optical sizes: Text for body copy, Display for large headings.
    FONT_FAMILY = '"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif'
    FONT_DISPLAY = '"Segoe UI Variable Display", "Segoe UI", system-ui, sans-serif'

LIGHT: dict[str, str] = {
    "bg": "#F5F6FA",          # app background
    "surface": "#FFFFFF",      # cards / panels
    "surface_alt": "#FBFBFE",  # sidebar / subtle panels
    "surface_hover": "#F2F3F9",
    "surface_press": "#E9EAF4",
    "text": "#1B1C2A",         # primary text (≥ 12:1 on surface)
    "text_muted": "#5D5E72",   # secondary text (≥ 4.5:1 on surface)
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
    "danger_press": "#C42A21",
    "danger_soft": "#FDECEA",
    "on_danger": "#FFFFFF",
    "success": "#1B9E57",       # completed states (Done chip)
    "success_soft": "#E5F6EC",
    "warning": "#B45309",       # caution (amber) — e.g. "no input detected"
    "warning_soft": "#FEF3C7",
    "focus": "#6366F1",
    "scroll_thumb": "#D4D5E0",
    "scroll_thumb_hover": "#BDBECE",
    "shadow": "0,0,0",
}

DARK: dict[str, str] = {
    "bg": "#0E0F15",
    "surface": "#181A22",
    "surface_alt": "#13141B",
    "surface_hover": "#21232D",
    "surface_press": "#282A36",
    "text": "#EDEDF3",
    "text_muted": "#A6A7B8",   # ≥ 4.5:1 on dark surfaces
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
    "danger_press": "#F5837B",
    "danger_soft": "#2A1B1C",
    "on_danger": "#FFFFFF",
    "success": "#3ECF7A",
    "success_soft": "#132A1D",
    "warning": "#FBBF24",       # caution (amber) — e.g. "no input detected"
    "warning_soft": "#2A2410",
    "focus": "#7C82F2",
    "scroll_thumb": "#2E3140",
    "scroll_thumb_hover": "#3B3E4F",
    "shadow": "0,0,0",
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
/* top-level windows must paint their own background (they can't inherit) */
QDialog, QMessageBox {{ background-color: {t['surface']}; }}

/* ---------- cards / panels ---------- */
#Card {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 16px;
}}
#Card[clickable="true"]:hover {{
    border-color: {t['primary']};
    background-color: {t['surface']};
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
#H1 {{ font-family: {FONT_DISPLAY}; font-size: 26px; font-weight: 700; color: {t['text']}; }}
#H2 {{ font-family: {FONT_DISPLAY}; font-size: 20px; font-weight: 700; color: {t['text']}; }}
#H3 {{ font-size: 15px; font-weight: 600; color: {t['text']}; }}
#Muted {{ color: {t['text_muted']}; }}
#Faint {{ color: {t['text_faint']}; font-size: 12px; }}
#SectionLabel {{ color: {t['text_faint']}; font-size: 11px; font-weight: 700; }}

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
QPushButton:pressed {{ background-color: {t['surface_press']}; }}
QPushButton:focus {{ border: 1px solid {t['focus']}; }}
QPushButton:disabled {{
    color: {t['text_faint']};
    background-color: {t['surface_alt']};
    border-color: {t['border']};
}}

QPushButton[variant="primary"] {{
    background-color: {t['primary']};
    color: {t['on_primary']};
    border: none;
    padding: 9px 18px;
}}
QPushButton[variant="primary"]:hover {{ background-color: {t['primary_hover']}; }}
QPushButton[variant="primary"]:pressed {{ background-color: {t['primary_press']}; }}
QPushButton[variant="primary"]:focus {{ background-color: {t['primary_hover']}; }}
QPushButton[variant="primary"]:disabled {{ background-color: {t['primary_soft']}; color: {t['text_faint']}; }}

QPushButton[variant="danger"] {{
    background-color: {t['danger']};
    color: {t['on_danger']};
    border: none;
    padding: 9px 18px;
}}
QPushButton[variant="danger"]:hover {{ background-color: {t['danger_hover']}; }}
QPushButton[variant="danger"]:pressed {{ background-color: {t['danger_press']}; }}
QPushButton[variant="danger"]:focus {{ background-color: {t['danger_hover']}; }}
QPushButton[variant="danger"]:disabled {{ background-color: {t['danger_soft']}; color: {t['text_faint']}; }}

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
QPushButton[variant="ghost"]:pressed {{ background-color: {t['surface_press']}; }}
QPushButton[variant="ghost"]:checked {{ background-color: {t['primary_soft']}; color: {t['primary']}; }}
QPushButton[variant="ghost"]:disabled {{ color: {t['text_faint']}; background-color: transparent; }}
/* small fixed-size icon buttons (card-header kebabs / collapse chevrons): the
   ghost variant left-aligns + pads for nav rows, which shoves a lone icon into
   a corner — centre it in its tap target. Placed AFTER ghost so it wins. */
QPushButton[iconbtn="true"] {{ padding: 0px; text-align: center; }}

/* round icon-only buttons (rail) */
QToolButton {{
    background-color: transparent;
    border: none;
    border-radius: 12px;
    padding: 8px;
}}
QToolButton:hover {{ background-color: {t['surface_hover']}; }}
QToolButton:pressed {{ background-color: {t['surface_press']}; }}
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
QLineEdit:hover, QComboBox:hover, QPlainTextEdit:hover {{ border-color: {t['text_faint']}; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {t['focus']}; }}
QLineEdit:disabled, QComboBox:disabled, QPlainTextEdit:disabled {{
    color: {t['text_faint']};
    background-color: {t['surface_alt']};
    border-color: {t['border']};
}}
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

/* ---------- checkbox ---------- */
QCheckBox {{ color: {t['text']}; spacing: 8px; }}
QCheckBox:disabled {{ color: {t['text_faint']}; }}
QCheckBox::indicator {{
    width: 18px; height: 18px;
    border: 1px solid {t['border_strong']};
    border-radius: 6px;
    background: {t['surface']};
}}
QCheckBox::indicator:hover {{ border-color: {t['primary']}; }}
QCheckBox::indicator:checked {{ background: {t['primary']}; border-color: {t['primary']}; {check_rule} }}
QCheckBox::indicator:checked:hover {{ background: {t['primary_hover']}; border-color: {t['primary_hover']}; }}
QCheckBox::indicator:disabled {{ background: {t['surface_alt']}; border-color: {t['border']}; }}

/* ---------- sliders (settings) ---------- */
QSlider::groove:horizontal {{
    height: 6px;
    background: {t['surface_hover']};
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{ background: {t['primary']}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    width: 16px; height: 16px;
    margin: -5px 0;
    border-radius: 8px;
    background: {t['primary']};
    border: 2px solid {t['surface']};
}}
QSlider::handle:horizontal:hover {{ background: {t['primary_hover']}; }}
QSlider::handle:horizontal:pressed {{ background: {t['primary_press']}; }}

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
    padding: 9px 16px;
    margin-right: 4px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 600;
}}
QTabBar::tab:selected {{ color: {t['primary']}; border-bottom: 2px solid {t['primary']}; }}
QTabBar::tab:hover {{ color: {t['text']}; }}
QTabBar::tab:disabled {{ color: {t['text_faint']}; }}

/* ---------- settings: left nav rail + panes ---------- */
#SettingsNav {{
    background-color: {t['surface_alt']};
    border-right: 1px solid {t['border']};
}}
#SettingsNav QPushButton[variant="ghost"] {{
    padding: 8px 12px;
    font-size: 13px;
}}

/* ---------- sidebar status card (trial / renew prompt) ---------- */
#StatusCard {{
    background-color: {t['primary_soft']};
    border: 1px solid {t['border']};
    border-radius: 14px;
}}

/* ---------- plans & billing ---------- */
#PlanBanner {{
    background-color: {t['primary_soft']};
    border: 1px solid {t['border']};
    border-radius: 14px;
}}
#PlanCard {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 16px;
}}
#PlanCard[current="true"] {{ border: 1px solid {t['primary']}; }}
#PlanPrice {{ font-family: {FONT_DISPLAY}; font-size: 22px; font-weight: 700; color: {t['text']}; }}
#PlanKicker {{ color: {t['text_faint']}; font-size: 12px; font-weight: 600; }}

/* collapsed icon rail: the record button keeps its danger colour */
QToolButton#RailRecord {{
    background-color: {t['danger']};
    border-radius: 12px;
}}
QToolButton#RailRecord:hover {{ background-color: {t['danger_hover']}; }}
QToolButton#RailRecord:pressed {{ background-color: {t['danger_press']}; }}

/* ---------- text views ---------- */
QTextBrowser, QTextEdit {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 12px;
    padding: 8px 12px;
    color: {t['text']};
}}

/* ---------- sidebar middle scroll column ---------- */
/* invisible chrome: the PROJECTS + MEETING NOTES sections scroll as one */
QScrollArea#SidebarScroll {{ background: transparent; border: none; }}
QScrollArea#SidebarScroll > QWidget > QWidget {{ background: transparent; }}

/* ---------- sidebar projects tree ---------- */
/* borderless like the meeting list below it — the tree flows in the sidebar
   (mockup), not a boxed panel. No ::branch rule: styling it would replace the
   platform expand arrows, which we want to keep. */
QTreeWidget#SidebarTree {{ background: transparent; border: none; outline: none; }}
QTreeWidget#SidebarTree::item {{
    color: {t['text']};
    border-radius: 10px;
    padding: 9px 10px;   /* match the MEETING NOTES list rows exactly */
    margin: 2px 0;
    border-left: 3px solid transparent;
}}
QTreeWidget#SidebarTree::item:hover {{ background-color: {t['surface_hover']}; }}
QTreeWidget#SidebarTree::item:selected {{
    background-color: {t['primary_soft']};
    color: {t['text']};
    border-left: 3px solid {t['primary']};
}}

/* ---------- meeting list ---------- */
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{
    color: {t['text']};
    border-radius: 10px;
    padding: 9px 10px;
    margin: 2px 0;
    border-left: 3px solid transparent;
}}
QListWidget::item:hover {{ background-color: {t['surface_hover']}; }}
QListWidget::item:selected {{
    background-color: {t['primary_soft']};
    color: {t['text']};
    border-left: 3px solid {t['primary']};
}}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['scroll_thumb']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t['scroll_thumb_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t['scroll_thumb']}; border-radius: 5px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {t['scroll_thumb_hover']}; }}

QToolTip {{
    background-color: {t['surface']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 12px;
}}

/* ---------- menus (e.g. detail page "More") ---------- */
/* ---------- menus & dropdown lists (rounded, sidebar-like pills) ---------- */
QMenu {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 12px;
    padding: 6px;
}}
QMenu::item {{
    background: transparent;
    padding: 8px 26px 8px 12px;
    margin: 2px 4px;
    border-radius: 9px;
    color: {t['text']};
    font-weight: 600;
}}
QMenu::item:selected {{ background-color: {t['primary_soft']}; color: {t['primary']}; }}
QMenu::item:disabled {{ color: {t['text_faint']}; background: transparent; }}
QMenu::separator {{ height: 1px; background: {t['border']}; margin: 6px 12px; }}
QMenu::icon {{ margin-left: 8px; }}
/* checkable menu items (e.g. the current folder) — match the app's checkboxes
   and keep the box vertically centred in the pill */
QMenu::indicator {{
    width: 16px; height: 16px;
    margin-left: 8px;
    border: 1px solid {t['border_strong']};
    border-radius: 5px;
    background: {t['surface']};
}}
QMenu::indicator:checked {{ background: {t['primary']}; border-color: {t['primary']}; {check_rule} }}
QMenu::right-arrow {{ margin-right: 10px; }}

/* combo dropdown lists get the same rounded-pill rows */
QComboBox QAbstractItemView::item {{
    padding: 7px 10px;
    margin: 2px 4px;
    border-radius: 8px;
    min-height: 22px;
}}
QComboBox QAbstractItemView::item:selected {{
    background-color: {t['primary_soft']};
    color: {t['primary']};
}}

/* multi-select lists (e.g. the brief meeting picker) — same checkbox look */
QListView::indicator, QTreeView::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {t['border_strong']};
    border-radius: 5px;
    background: {t['surface']};
}}
QListView::indicator:checked, QTreeView::indicator:checked {{
    background: {t['primary']}; border-color: {t['primary']}; {check_rule}
}}

/* buttons that open a QMenu: our themed chevron icon is the indicator — kill
   Qt's built-in second arrow */
QPushButton::menu-indicator {{ image: none; width: 0px; }}
"""
