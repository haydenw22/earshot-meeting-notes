"""Home / library page: a greeting, a New-recording CTA, and the meetings as
clickable cards (or a friendly empty state when there are none yet).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..storage.repository import Meeting
from . import icons
from .widgets import Card, add_shadow, status_chip


class MeetingCard(Card):
    def __init__(self, meeting: Meeting, theme, on_click):
        super().__init__(shadow=True)
        self._on_click = on_click
        self._mid = meeting.id
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(14)

        icon = QLabel()
        icon.setPixmap(icons.pixmap("file", theme.color("primary"), 22))
        icon.setFixedWidth(28)
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(icon)

        mid = QVBoxLayout()
        mid.setSpacing(4)
        title = QLabel(meeting.title or "Untitled meeting")
        title.setObjectName("H3")
        title.setWordWrap(True)
        mid.addWidget(title)
        bits = [meeting.date_text or meeting.date_iso]
        if meeting.attendees:
            bits.append(", ".join(meeting.attendees[:4]) + ("…" if len(meeting.attendees) > 4 else ""))
        if meeting.duration_secs:
            bits.append(f"{int(meeting.duration_secs // 60)}m")
        meta = QLabel("   ·   ".join(b for b in bits if b))
        meta.setObjectName("Faint")
        meta.setWordWrap(True)
        mid.addWidget(meta)
        lay.addLayout(mid, 1)

        lay.addWidget(status_chip(meeting.status, theme), alignment=Qt.AlignmentFlag.AlignVCenter)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self._on_click(self._mid)
        super().mouseReleaseEvent(event)


class HomePage(QWidget):
    def __init__(self, shell, repo, cfg, theme):
        super().__init__()
        self.shell = shell
        self.repo = repo
        self.theme = theme
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 32, 40, 24)
        root.setSpacing(18)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.h1 = QLabel("Your meetings")
        self.h1.setObjectName("H1")
        self.count = QLabel("")
        self.count.setObjectName("Muted")
        titles.addWidget(self.h1)
        titles.addWidget(self.count)
        header.addLayout(titles)
        header.addStretch(1)
        self.new_btn = QPushButton("  New recording")
        self.new_btn.setProperty("variant", "danger")
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_btn.setMinimumHeight(42)
        self.new_btn.clicked.connect(lambda: self.shell.show_record())
        header.addWidget(self.new_btn, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_host = QWidget()
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(2, 2, 2, 2)
        self.list_lay.setSpacing(12)
        self.scroll.setWidget(self.list_host)
        root.addWidget(self.scroll, 1)

        self.empty = self._empty_state()
        root.addWidget(self.empty)
        root.setStretchFactor(self.empty, 1)

    def _empty_state(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addStretch(1)
        self.empty_icon = QLabel()
        self.empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.empty_icon)
        t = QLabel("No meetings yet")
        t.setObjectName("H2")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        s = QLabel("Hit New recording to capture your first meeting.")
        s.setObjectName("Muted")
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(s)
        lay.addStretch(2)
        return w

    def refresh(self) -> None:
        # clear list — detach synchronously (deleteLater alone leaves orphan
        # widgets parented to the container until the event loop runs).
        while self.list_lay.count():
            item = self.list_lay.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        meetings = self.repo.list()
        self.count.setText(f"{len(meetings)} recorded" if meetings else "Nothing recorded yet")
        has = bool(meetings)
        self.scroll.setVisible(has)
        self.empty.setVisible(not has)
        if has:
            for m in meetings:
                self.list_lay.addWidget(MeetingCard(m, self.theme, self.shell.open_meeting))
            self.list_lay.addStretch(1)
        self.apply_theme()

    def apply_theme(self) -> None:
        self.new_btn.setIcon(self.theme.icon("record", "on_danger", 16))
        self.empty_icon.setPixmap(icons.pixmap("mic", self.theme.color("text_faint"), 56))
