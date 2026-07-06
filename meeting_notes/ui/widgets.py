"""Small reusable UI building blocks: soft-shadowed cards, status chips and
separators. Keeps the pages declarative and the look consistent.
"""
from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import QDate, QEvent, QObject, QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCalendarWidget,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
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


class ElideLabel(QLabel):
    """A QLabel that elides with "…" instead of forcing the layout wide.

    A plain QLabel's minimumSizeHint is its full text width, so a long meeting
    title makes the whole page wider than a narrow window — content then gets
    clipped at the right edge (our pages disable horizontal scroll). This label
    reports a tiny minimum width and re-elides whenever it is resized; the full
    text goes in the tooltip when it doesn't fit."""

    def __init__(self, text: str = "", parent=None):
        super().__init__("", parent)
        self._full = text or ""
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        super().setText(self._full)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt override)
        self._full = text or ""
        self._elide()

    def fullText(self) -> str:  # noqa: N802 (Qt-style accessor)
        return self._full

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(48, super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        fm = self.fontMetrics()
        shown = fm.elidedText(self._full, Qt.TextElideMode.ElideRight, max(24, self.width()))
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


class _CalmWheelFilter(QObject):
    """Scrolling a settings page must never CHANGE a control it passes over.

    Without this, a wheel over any combo/slider/spinbox silently rewrites the
    value (model, overlay opacity, …) instead of scrolling the page. With it,
    an unfocused control forwards the gesture to the enclosing QScrollArea;
    the wheel only adjusts a control after the user clicks into it."""

    def eventFilter(self, obj, event):  # noqa: N802 (Qt override)
        if event.type() == QEvent.Type.Wheel and not obj.hasFocus():
            area = None
            p = obj.parentWidget()
            while p is not None:
                if isinstance(p, QScrollArea):
                    area = p
                    break
                p = p.parentWidget()
            if area is not None:
                bar = area.verticalScrollBar()
                # ~60px per wheel notch — close to native page scrolling
                bar.setValue(bar.value() - int(event.angleDelta().y() * 0.5))
            return True  # the control never sees the wheel
        return False


_calm_wheel = _CalmWheelFilter()


def calm_scroll(widget) -> None:
    """Make one control scroll-safe (see _CalmWheelFilter)."""
    widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    widget.installEventFilter(_calm_wheel)


def calm_scroll_children(root) -> None:
    """Sweep a page: every combo/slider/spinbox under `root` becomes scroll-safe."""
    for kind in (QComboBox, QSlider, QAbstractSpinBox):  # findChildren: one type per call
        for w in root.findChildren(kind):
            calm_scroll(w)


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


# Palette offered when creating/recolouring a folder — name + hex, Indigo is the
# app's own accent colour so it's the sensible default.
FOLDER_COLORS = [
    ("Red", "#EF4444"),
    ("Orange", "#F59E0B"),
    ("Green", "#22C55E"),
    ("Teal", "#14B8A6"),
    ("Blue", "#3B82F6"),
    ("Indigo", "#6366F1"),
    ("Purple", "#A855F7"),
    ("Pink", "#EC4899"),
]


class _DueDateDialog(QDialog):
    """A small themed popup: a calendar preselected to `current` (or today),
    plus Clear date / Cancel / Set date."""

    def __init__(self, parent, theme, current: str | None):
        super().__init__(parent)
        self.theme = theme
        self.setWindowTitle("Set due date")
        self.setMinimumWidth(320)

        v = QVBoxLayout(self)
        v.setSpacing(12)

        from ..util.dues import parse_due
        start = parse_due(current) or _dt.date.today()

        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(False)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar.setSelectedDate(QDate(start.year, start.month, start.day))
        self.calendar.setStyleSheet(self._calendar_qss())
        v.addWidget(self.calendar)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        clear_btn = QPushButton("Clear date")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._on_clear)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch(1)
        v.addLayout(btn_row)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Set date")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        v.addWidget(self.buttons)

        self._cleared = False

    def _on_clear(self) -> None:
        self._cleared = True
        self.accept()

    def result_iso(self) -> str | None:
        """None if the user hit Clear date; otherwise the selected date's ISO string."""
        if self._cleared:
            return None
        qd = self.calendar.selectedDate()
        return _dt.date(qd.year(), qd.month(), qd.day()).isoformat()

    def _calendar_qss(self) -> str:
        """Minimal theming so the calendar navigation bar and popup menus don't
        look alien against the app's dark/light surfaces — deliberately light-touch,
        just enough to keep contrast sane in both themes."""
        t = self.theme
        return f"""
QCalendarWidget QWidget {{ background-color: {t.color('surface')}; color: {t.color('text')}; }}
QCalendarWidget QToolButton {{
    background-color: transparent; color: {t.color('text')};
    border: none; border-radius: 8px; padding: 4px 8px; font-weight: 600;
}}
QCalendarWidget QToolButton:hover {{ background-color: {t.color('surface_hover')}; }}
QCalendarWidget QToolButton::menu-indicator {{ image: none; }}
QCalendarWidget QMenu {{
    background-color: {t.color('surface')}; color: {t.color('text')};
    border: 1px solid {t.color('border')}; border-radius: 10px;
}}
QCalendarWidget QSpinBox {{
    background-color: {t.color('surface')}; color: {t.color('text')};
    border: 1px solid {t.color('border_strong')}; border-radius: 6px; padding: 2px 4px;
}}
QCalendarWidget QAbstractItemView:enabled {{
    background-color: {t.color('surface')}; color: {t.color('text')};
    selection-background-color: {t.color('primary')}; selection-color: {t.color('on_primary')};
}}
QCalendarWidget QAbstractItemView:disabled {{ color: {t.color('text_faint')}; }}
QCalendarWidget QWidget#qt_calendar_navigationbar {{ background-color: {t.color('surface_hover')}; }}
"""


def pick_due_date(parent, theme, current: str | None) -> tuple[str | None, bool]:
    """Open a small themed date picker. Returns (iso_or_None, accepted_bool):
    - (None, False)      — user cancelled: caller must leave the due date untouched.
    - (None, True)       — user cleared the date.
    - ("YYYY-MM-DD", True) — user picked/kept a date.
    """
    dlg = _DueDateDialog(parent, theme, current)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None, False
    return dlg.result_iso(), True
