"""Home / library page: a greeting, a New-recording CTA, and the meetings as
clickable cards (or a friendly empty state when there are none yet).
"""
from __future__ import annotations

import html as _html
import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
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
        self.cfg = cfg
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
            if self.cfg.show_dashboard:
                pending = self._gather_pending(meetings)
                if pending:
                    self.list_lay.addWidget(self._dashboard_card(pending))
            for m in meetings:
                self.list_lay.addWidget(MeetingCard(m, self.theme, self.shell.open_meeting))
            self.list_lay.addStretch(1)
        self.apply_theme()

    # ---------- pending action-items dashboard ----------
    def _gather_pending(self, meetings, limit: int = 40) -> list[dict]:
        out: list[dict] = []
        for m in meetings:
            notes = m.notes
            if not notes:
                continue
            for i, a in enumerate(notes.get("action_items") or []):
                if not a.get("done"):
                    out.append({"meeting_id": m.id, "idx": i, "task": a.get("task") or "",
                                "owner": a.get("owner"), "title": m.title or "Untitled"})
            if len(out) >= limit:
                break
        return out[:limit]

    def _dashboard_card(self, pending: list[dict]) -> QWidget:
        card = Card(shadow=True)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(8)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icons.pixmap("check-square", self.theme.color("primary"), 18))
        t = QLabel(f"To do — {len(pending)} pending action item" + ("s" if len(pending) != 1 else ""))
        t.setObjectName("H3")
        head.addWidget(ic)
        head.addWidget(t)
        head.addStretch(1)
        lay.addLayout(head)
        for p in pending:
            lay.addWidget(self._pending_row(p))
        return card

    def _pending_row(self, p: dict) -> QWidget:
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 1, 0, 1)
        rl.setSpacing(9)
        cb = QCheckBox()
        cb.setCursor(Qt.CursorShape.PointingHandCursor)
        cb.toggled.connect(lambda checked, mid=p["meeting_id"], idx=p["idx"]: self._mark_done(mid, idx, checked))
        lbl = QLabel()
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        owner = f' &middot; <b style="color:{self.theme.color("primary")};">{_html.escape(p["owner"])}</b>' if p["owner"] else ""
        src = f' <span style="color:{self.theme.color("text_faint")};">— {_html.escape(p["title"])}</span>'
        lbl.setText(_html.escape(p["task"]) + owner + src)
        open_btn = QPushButton("Open")
        open_btn.setProperty("variant", "ghost")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(lambda _=False, mid=p["meeting_id"]: self.shell.open_meeting(mid))
        rl.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)
        rl.addWidget(lbl, 1)
        rl.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignTop)
        return row

    def _mark_done(self, meeting_id: int, idx: int, checked: bool) -> None:
        if not checked:
            return
        m = self.repo.get(meeting_id)
        notes = m.notes or {}
        actions = notes.get("action_items") or []
        if 0 <= idx < len(actions):
            actions[idx]["done"] = True
            notes["action_items"] = actions
            self.repo.update(meeting_id, notes_json=json.dumps(notes))
        QTimer.singleShot(0, self.refresh)  # rebuild after the signal settles

    def apply_theme(self) -> None:
        self.new_btn.setIcon(self.theme.icon("record", "on_danger", 16))
        self.empty_icon.setPixmap(icons.pixmap("mic", self.theme.color("text_faint"), 56))
