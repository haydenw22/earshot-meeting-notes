"""Call auto-detection: notice when another app starts using the microphone
(Zoom, Teams, a Meet tab…) and offer to record — the "I forgot to hit record"
killer for a tool with no cloud bot to fall back on.

Detection polls the Windows mic consent store (see util/mic_usage.py). Edge
logic lives here: prompt once per call on the idle→active transition, snooze
after a dismissal until the mic goes idle again, and offer to stop when the
call ends while a recording is running.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..util import mic_usage

_POLL_MS = 4000
_TOAST_TIMEOUT_MS = 30_000
_MARGIN = 24

# How many consecutive polls a signal must persist before we believe it.
# Known meeting apps (Zoom/Teams/Webex/Slack) are a strong signal → quick.
# A browser using the mic could be a Meet tab — or a game, a voice note,
# anything — so it must be SUSTAINED before we dare interrupt.
_MEETING_TICKS = 2   # ~8 s
_BROWSER_TICKS = 6   # ~24 s
_END_TICKS = 2       # ~8 s of silence before we call it ended


class CallWatcher(QObject):
    """Emits call_started(apps) when a known meeting app (or, much more
    conservatively, a browser) has been using the mic for long enough, and
    call_ended() when that stops. Unknown apps — games, dictation, assistants —
    never trigger anything. Snoozable so a dismissed prompt stays dismissed for
    that call."""

    call_started = Signal(list)
    call_ended = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._snoozed = False
        self._streak = 0       # consecutive polls with a qualifying app
        self._quiet = 0        # consecutive polls without one (for end detection)
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def snooze_until_idle(self) -> None:
        self._snoozed = True

    def _tick(self) -> None:
        classified = mic_usage.apps_using_microphone_classified()
        meeting = [name for name, cat in classified if cat == "meeting"]
        browser = [name for name, cat in classified if cat == "browser"]
        self._evaluate(meeting, browser)

    def _evaluate(self, meeting: list, browser: list) -> None:
        """Edge logic, separated from polling so it's directly testable."""
        qualifying = meeting or browser
        if qualifying:
            self._streak += 1
            self._quiet = 0
            # meeting apps confirm fast; browser-only signals need sustained use
            need = _MEETING_TICKS if meeting else _BROWSER_TICKS
            if not self._active and self._streak >= need:
                self._active = True
                if not self._snoozed:
                    self.call_started.emit(meeting or browser)
        else:
            self._streak = 0
            if self._active:
                self._quiet += 1
                if self._quiet >= _END_TICKS:
                    self._active = False
                    self._snoozed = False  # next call may prompt again
                    self._quiet = 0
                    self.call_ended.emit()


class CallToast(QWidget):
    """A small always-on-top prompt in the bottom-right corner. Doesn't steal
    focus from the meeting app; auto-dismisses after 30 s."""

    def __init__(self, message: str, *, accept_text: str, on_accept, on_dismiss=None):
        super().__init__(None)
        self._on_accept = on_accept
        self._on_dismiss = on_dismiss
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        pill = QFrame()
        pill.setObjectName("CallToast")
        pill.setStyleSheet(
            "#CallToast{background:rgba(18,19,27,242); border:1px solid rgba(255,255,255,32);"
            "border-radius:14px;} QLabel{background:transparent; color:#EDEDF3;}"
        )
        v = QVBoxLayout(pill)
        v.setContentsMargins(16, 13, 16, 13)
        v.setSpacing(10)
        lbl = QLabel(message)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("font-size:13px; font-weight:600; color:#FFFFFF;")
        lbl.setMaximumWidth(340)
        v.addWidget(lbl)
        row = QHBoxLayout()
        row.setSpacing(8)
        ok = QPushButton(accept_text)
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(
            "QPushButton{background:#F0483E; color:white; border:none; border-radius:9px;"
            "padding:7px 14px; font-weight:700; font-size:13px;}"
            "QPushButton:hover{background:#DA362D;}"
        )
        ok.clicked.connect(self._accept)
        no = QPushButton("Dismiss")
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(
            "QPushButton{background:transparent; color:#9A9BAC; border:1px solid rgba(255,255,255,40);"
            "border-radius:9px; padding:7px 14px; font-weight:600; font-size:13px;}"
            "QPushButton:hover{color:#EDEDF3;}"
        )
        no.clicked.connect(self._dismiss)
        row.addWidget(ok)
        row.addWidget(no)
        row.addStretch(1)
        v.addLayout(row)
        outer.addWidget(pill)

        QTimer.singleShot(_TOAST_TIMEOUT_MS, self._timeout)

    def show_toast(self) -> None:
        self.adjustSize()
        ps = QGuiApplication.primaryScreen()
        if ps is not None:
            g = ps.availableGeometry()
            self.move(QPoint(g.right() - self.width() - _MARGIN,
                             g.bottom() - self.height() - _MARGIN))
        self.show()
        self.raise_()

    def _accept(self) -> None:
        cb = self._on_accept
        self.close()
        if cb:
            cb()

    def _dismiss(self) -> None:
        cb = self._on_dismiss
        self.close()
        if cb:
            cb()

    def _timeout(self) -> None:
        if self.isVisible():
            self._dismiss()
