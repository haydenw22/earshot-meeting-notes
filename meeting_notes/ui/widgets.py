"""Small reusable UI building blocks: soft-shadowed cards, status chips and
separators. Keeps the pages declarative and the look consistent.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QWidget,
)


def add_shadow(widget: QWidget, *, blur: int = 34, dy: int = 8, alpha: int = 26) -> None:
    """Apply a soft drop shadow (the 'elevation' of a card)."""
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setXOffset(0)
    eff.setYOffset(dy)
    eff.setColor(QColor(20, 22, 40, alpha))
    widget.setGraphicsEffect(eff)


class Card(QFrame):
    def __init__(self, parent=None, *, shadow: bool = True):
        super().__init__(parent)
        self.setObjectName("Card")
        if shadow:
            add_shadow(self)


def make_chip(text: str, *, fg: str, bg: str) -> QLabel:
    """A small rounded pill — used for status / meta tags."""
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"background:{bg}; color:{fg}; border-radius:9px; padding:3px 10px;"
        f"font-size:12px; font-weight:600;"
    )
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lbl


# Maps a meeting status to (foreground, background-token) roles resolved later.
# Semantics: green = finished, indigo = ready-for-AI, red = live/destructive,
# neutral = in-flight. The chip text always carries the meaning too (never
# color-only).
STATUS_ROLES = {
    "Done": ("success", "success_soft"),
    "Transcribed": ("primary", "primary_soft"),
    "Recording": ("danger", "danger_soft"),
    "Transcribing": ("text_muted", "surface_hover"),
    "Summarizing": ("text_muted", "surface_hover"),
    "Recorded": ("text_muted", "surface_hover"),
    "Error": ("danger", "danger_soft"),
    "New": ("text_muted", "surface_hover"),
}


def status_chip(status: str, theme) -> QLabel:
    fg_role, bg_role = STATUS_ROLES.get(status, ("text_muted", "surface_hover"))
    return make_chip(status, fg=theme.color(fg_role), bg=theme.color(bg_role))


def hline(theme) -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background:{theme.color('border')}; border:none;")
    return line
