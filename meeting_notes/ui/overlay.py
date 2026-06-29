"""Always-on-top recording overlay.

A small frameless pill that floats above every other app while a recording is in
progress: a pulsing REC dot, an elapsed timer, and two "lights" that glow with
your microphone and the system (their) audio. Drag it anywhere — including onto a
second monitor — and the position is remembered. Opacity and whether it shows at
all are configurable. Kept parentless on purpose so it stays visible even when the
main Earshot window is minimised.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..config import OVERLAY_AUTO_POS

_ACTIVE = 0.04          # level above which a light counts as "lit"
_MARGIN = 24            # gap from the screen edge when auto-placing


class _Light(QWidget):
    """A round indicator that glows brighter as the audio level rises."""

    def __init__(self, color: QColor):
        super().__init__()
        self._color = color
        self._level = 0.0
        self.setFixedSize(18, 18)

    def set_level(self, level: float) -> None:
        level = max(0.0, min(1.0, float(level)))
        if abs(level - self._level) > 0.015:  # avoid needless repaints
            self._level = level
            self.update()

    @property
    def level(self) -> float:
        return self._level

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPoint(9, 9)
        if self._level >= _ACTIVE:
            strength = min(1.0, self._level * 1.6)
            halo = QColor(self._color)
            halo.setAlpha(int(70 * strength))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            p.drawEllipse(center, 9, 9)          # outer glow
            dot = QColor(self._color)
            dot.setAlpha(int(150 + 105 * strength))
            p.setBrush(dot)
            p.drawEllipse(center, 5, 5)
        else:
            off = QColor(96, 98, 112)
            off.setAlpha(150)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(off)
            p.drawEllipse(center, 5, 5)
        p.end()


class _RecDot(QWidget):
    """A red record dot that gently pulses."""

    def __init__(self):
        super().__init__()
        self._phase = 0.0
        self.setFixedSize(16, 16)

    def pulse(self, step: float = 0.05) -> None:
        self._phase = (self._phase + step) % 1.0
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        amp = 0.5 + 0.5 * math.sin(self._phase * 2 * math.pi)
        c = QColor(0xF0, 0x48, 0x3E)
        c.setAlpha(int(150 + 105 * amp))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPoint(8, 8), 5, 5)
        p.end()


class RecordingOverlay(QWidget):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowOpacity(self._clamp_opacity(cfg.overlay_opacity))
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._drag_offset: QPoint | None = None
        self._build()
        self._pulse = QTimer(self)
        self._pulse.setInterval(55)
        self._pulse.timeout.connect(self._rec.pulse)

    # ---------- construction ----------
    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.pill = QFrame()
        self.pill.setObjectName("OverlayPill")
        self.pill.setStyleSheet(
            "#OverlayPill{background:rgba(18,19,27,238); border:1px solid rgba(255,255,255,30);"
            "border-radius:17px;} QLabel{background:transparent; color:#EDEDF3;}"
        )
        row = QHBoxLayout(self.pill)
        row.setContentsMargins(14, 9, 16, 9)
        row.setSpacing(12)

        self._rec = _RecDot()
        row.addWidget(self._rec)
        self.timer_label = QLabel("00:00")
        self.timer_label.setStyleSheet("color:#FFFFFF; font-size:15px; font-weight:700; letter-spacing:0.5px;")
        row.addWidget(self.timer_label)

        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setStyleSheet("background:rgba(255,255,255,28);")
        row.addWidget(sep)

        self.mic_light = _Light(QColor(0x53, 0xE0, 0xA8))   # mint = you
        self.sys_light = _Light(QColor(0x7C, 0x82, 0xF2))   # indigo = them
        row.addLayout(self._light_col("You", self.mic_light))
        row.addLayout(self._light_col("Them", self.sys_light))

        outer.addWidget(self.pill)
        self.adjustSize()

    def _light_col(self, label: str, light: _Light) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        col.addWidget(light, 0, Qt.AlignmentFlag.AlignHCenter)
        lbl = QLabel(label)
        lbl.setStyleSheet("color:#9A9BAC; font-size:9px; font-weight:700;")
        col.addWidget(lbl, 0, Qt.AlignmentFlag.AlignHCenter)
        return col

    # ---------- public API ----------
    def update_levels(self, mic_level: float, them_level: float) -> None:
        self.mic_light.set_level(mic_level)
        self.sys_light.set_level(them_level)

    def update_time(self, secs: float) -> None:
        secs = max(0, int(secs))
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        self.timer_label.setText(f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}")

    def set_opacity(self, opacity: float) -> None:
        self.setWindowOpacity(self._clamp_opacity(opacity))

    def show_overlay(self) -> None:
        self._place()
        self.show()
        self.raise_()
        self._pulse.start()

    def close_overlay(self) -> None:
        self._pulse.stop()
        self.close()

    # ---------- placement (multi-monitor aware) ----------
    def _place(self) -> None:
        self.adjustSize()
        screens = [s.availableGeometry() for s in QGuiApplication.screens()]
        ps = QGuiApplication.primaryScreen()
        primary = ps.availableGeometry() if ps else QRect(0, 0, 1920, 1080)
        x, y = self.place(self.cfg.overlay_pos_x, self.cfg.overlay_pos_y,
                          (self.width(), self.height()), screens, primary)
        self.move(x, y)

    @staticmethod
    def place(saved_x: int, saved_y: int, size, screens, primary, margin: int = _MARGIN):
        """Return an on-screen top-left for the overlay.

        Auto-places (top-right of the primary screen) when unset; otherwise keeps
        the saved spot but clamps it fully onto whichever connected screen it most
        overlaps — so a position saved on a now-disconnected monitor falls back
        gracefully instead of vanishing off-screen.
        """
        w, h = size
        auto = (saved_x == OVERLAY_AUTO_POS or saved_y == OVERLAY_AUTO_POS or not screens)
        if auto:
            return (primary.right() - w - margin, primary.top() + margin)
        rect = QRect(int(saved_x), int(saved_y), w, h)
        best, best_area = None, 0
        for sr in screens:
            inter = sr.intersected(rect)
            area = max(0, inter.width()) * max(0, inter.height())
            if area > best_area:
                best, best_area = sr, area
        if best is None or best_area <= 0:
            return (primary.right() - w - margin, primary.top() + margin)
        x = min(max(int(saved_x), best.left()), best.right() - w + 1)
        y = min(max(int(saved_y), best.top()), best.bottom() - h + 1)
        return (x, y)

    @staticmethod
    def _clamp_opacity(opacity) -> float:
        try:
            opacity = float(opacity)
        except (TypeError, ValueError):
            opacity = 0.95
        return max(0.4, min(1.0, opacity))

    # ---------- dragging (repositionable) ----------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.cfg.overlay_pos_x = self.x()
            self.cfg.overlay_pos_y = self.y()
            self.cfg.save()
            event.accept()
