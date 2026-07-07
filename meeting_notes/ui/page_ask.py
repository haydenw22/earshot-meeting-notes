"""The "Ask" page — a chat over your past meetings.

Type a natural-language question; the answer comes back with clickable citation
chips that jump to the source meeting. Runs off-thread via FuncWorker.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..util.dates import iso_date
from . import icons
from .widgets import Card
from .workers import FuncWorker

_RECENT_MEETINGS_LIMIT = 15


class AskPage(QWidget):
    def __init__(self, shell, repo, cfg, theme):
        super().__init__()
        self.shell = shell
        self.repo = repo
        self.cfg = cfg
        self.theme = theme
        self.worker: FuncWorker | None = None
        self._empty: QWidget | None = None
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 28, 40, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        head = QLabel("Ask Earshot")
        head.setObjectName("H1")
        sub = QLabel("Ask anything about your past meetings — answers cite the source.")
        sub.setObjectName("Muted")
        titles.addWidget(head)
        titles.addWidget(sub)
        header.addLayout(titles)
        header.addStretch(1)
        self.new_chat_btn = QPushButton("New chat")
        self.new_chat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_chat_btn.clicked.connect(self._new_chat)
        header.addWidget(self.new_chat_btn, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat_host = QWidget()
        self.chat_lay = QVBoxLayout(self.chat_host)
        self.chat_lay.setContentsMargins(2, 2, 2, 2)
        self.chat_lay.setSpacing(12)
        self.chat_lay.addStretch(1)
        self.scroll.setWidget(self.chat_host)
        root.addWidget(self.scroll, 1)

        self._empty = self._empty_state()
        self.chat_lay.insertWidget(0, self._empty)

        scope_row = QHBoxLayout()
        scope_row.setSpacing(8)
        scope_lbl = QLabel("Search:")
        scope_lbl.setObjectName("Muted")
        self.scope_combo = QComboBox()
        self.scope_combo.setMinimumWidth(220)
        scope_row.addWidget(scope_lbl)
        scope_row.addWidget(self.scope_combo)
        scope_row.addStretch(1)
        root.addLayout(scope_row)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.input = QLineEdit()
        self.input.setPlaceholderText("e.g.  What did we decide with Scott about pricing last week?")
        self.input.setMinimumHeight(44)
        self.input.returnPressed.connect(self._send)
        self.send_btn = QPushButton("Ask")
        self.send_btn.setProperty("variant", "primary")
        self.send_btn.setMinimumHeight(44)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.clicked.connect(self._send)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn)
        root.addLayout(row)

    def _empty_state(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 40, 0, 0)
        t = QLabel("Ask a question to get started")
        t.setObjectName("H3")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ex = QLabel('Try: "What were the action items from my budget meeting?"  ·  '
                    '"Did Sam agree to the Friday deadline?"')
        ex.setObjectName("Faint")
        ex.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ex.setWordWrap(True)
        v.addWidget(t)
        v.addWidget(ex)
        return w

    def on_shown(self) -> None:
        self.input.setFocus()
        self._populate_scope_combo()

    # ---------- scope (which meetings to search) ----------
    def _add_scope_header(self, text: str) -> None:
        """A non-selectable section header inside the scope dropdown, so the
        project list and the single-meeting list read as separate groups (a
        bare separator was invisible enough that recent meetings looked like
        they were filed under whatever row sat above them)."""
        from PySide6.QtGui import QColor

        idx = self.scope_combo.count()
        self.scope_combo.addItem(text)
        item = self.scope_combo.model().item(idx)
        if item is not None:
            item.setEnabled(False)
            item.setForeground(QColor(self.theme.color("text_faint")))

    def _populate_scope_combo(self) -> None:
        current = self.scope_combo.currentData() if self.scope_combo.count() else None
        self.scope_combo.blockSignals(True)
        self.scope_combo.clear()
        self.scope_combo.addItem("All meetings", None)

        folders = self.repo.list_folders()
        folders_by_id = {f.id: f for f in folders}
        if folders:
            self._add_scope_header("Projects")
            for f in folders:
                idx = self.scope_combo.count()
                self.scope_combo.addItem(f.name, ("folder", f.id))
                self.scope_combo.setItemIcon(idx, icons.icon("folder", f.color, 16))
        self.scope_combo.addItem("Uncategorized", "unfiled")

        recent = self.repo.list(limit=_RECENT_MEETINGS_LIMIT)
        if recent:
            self._add_scope_header("A single meeting")
            for m in recent:
                folder = folders_by_id.get(m.folder_id)
                # each meeting names its project so nothing looks "uncategorized"
                label = m.title or "Untitled meeting"
                if folder is not None:
                    label = f"{label}   ·  {folder.name}"
                idx = self.scope_combo.count()
                self.scope_combo.addItem(label, ("meeting", m.id))
                color = folder.color if folder is not None else self.theme.color("text_muted")
                self.scope_combo.setItemIcon(idx, icons.icon("file", color, 16))

        if current is not None:
            found = self.scope_combo.findData(current)
            if found >= 0:
                self.scope_combo.setCurrentIndex(found)
        self.scope_combo.blockSignals(False)

    @staticmethod
    def scoped_meetings(meetings: list, scope) -> list:
        """Filter `meetings` (from repo.list()) down to the ones matching a
        scope-combo value: None = all, ('folder', id), 'unfiled', or ('meeting', id)."""
        if scope is None:
            return list(meetings)
        if scope == "unfiled":
            return [m for m in meetings if m.folder_id is None]
        if isinstance(scope, tuple) and len(scope) == 2:
            kind, value = scope
            if kind == "folder":
                return [m for m in meetings if m.folder_id == value]
            if kind == "meeting":
                return [m for m in meetings if m.id == value]
        return list(meetings)

    # ---------- send / receive ----------
    def _send(self) -> None:
        q = self.input.text().strip()
        if not q or (self.worker and self.worker.isRunning()):
            return
        from ..notes import service as notes_service
        if not notes_service.ready(self.cfg):
            QMessageBox.warning(self, "AI not configured", notes_service.missing_hint(self.cfg))
            return
        if self._empty is not None:
            self._empty.setParent(None)
            self._empty.deleteLater()
            self._empty = None
        self.input.clear()
        self._add_bubble(q, role="you")
        thinking = self._add_bubble("Thinking…", role="thinking")
        self._set_busy(True)

        repo, cfg, today = self.repo, self.cfg, iso_date()
        scope = self.scope_combo.currentData() if self.scope_combo.count() else None

        def job(_progress):
            from ..qa import ask
            meetings = AskPage.scoped_meetings(repo.list(), scope)
            return ask.answer(q, meetings=meetings, cfg=cfg, today=today)

        self.worker = FuncWorker(job)
        self.worker.done.connect(lambda ans, t=thinking: self._on_answer(ans, t))
        self.worker.failed.connect(lambda msg, t=thinking: self._on_failed(msg, t))
        self.worker.start()

    def _on_answer(self, ans, thinking: QWidget) -> None:
        thinking.setParent(None)
        thinking.deleteLater()
        self._add_answer(ans)
        self._set_busy(False)

    def _on_failed(self, msg: str, thinking: QWidget) -> None:
        thinking.setParent(None)
        thinking.deleteLater()
        self._add_bubble(f"Sorry — {msg}", role="error")
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self.send_btn.setEnabled(not busy)
        self.send_btn.setText("…" if busy else "Ask")
        self.input.setEnabled(not busy)
        self.new_chat_btn.setEnabled(not busy)

    def _new_chat(self) -> None:
        i = 0
        while i < self.chat_lay.count():
            w = self.chat_lay.itemAt(i).widget()
            if w:
                self.chat_lay.takeAt(i)
                w.setParent(None)
                w.deleteLater()
            else:
                i += 1  # keep the trailing stretch
        self._empty = self._empty_state()
        self.chat_lay.insertWidget(0, self._empty)
        self.input.clear()
        self.input.setFocus()

    # ---------- bubbles ----------
    def _add_bubble(self, text: str, *, role: str) -> QWidget:
        card = Card(shadow=(role != "you"))
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.PlainText)  # never render AI/user text as rich text
        if role == "thinking" or role == "error":
            lbl.setObjectName("Muted")
        lay.addWidget(lbl)

        wrap = QHBoxLayout()
        if role == "you":
            card.setStyleSheet(
                f"#Card {{ background:{self.theme.color('primary_soft')}; border:none; }}"
            )
            wrap.addStretch(1)
            wrap.addWidget(card, 4)
        else:
            wrap.addWidget(card, 5)
            wrap.addStretch(1)
        holder = QWidget()
        holder.setLayout(wrap)
        self.chat_lay.insertWidget(self.chat_lay.count() - 1, holder)
        self._scroll_to_bottom()
        return holder

    def _add_answer(self, ans) -> None:
        card = Card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        body = QLabel(ans.text)
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.PlainText)  # AI answer is untrusted → no rich text
        lay.addWidget(body)
        if ans.citations:
            chips = QHBoxLayout()
            chips.setSpacing(6)
            seen = set()
            for c in ans.citations:
                key = (c["meeting_id"], c.get("timestamp"))
                if key in seen:
                    continue
                seen.add(key)
                ts = f" · {c['timestamp']}" if c.get("timestamp") else ""
                btn = QPushButton(f"{c['title']} ({c['date_text']}){ts}")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setToolTip(c.get("quote", ""))
                btn.setStyleSheet(
                    f"QPushButton{{background:{self.theme.color('surface_hover')}; color:{self.theme.color('primary')};"
                    f"border:none; border-radius:9px; padding:4px 10px; font-size:12px; font-weight:600;}}"
                    f"QPushButton:hover{{background:{self.theme.color('primary_soft')};}}"
                )
                btn.clicked.connect(lambda _=False, mid=c["meeting_id"]: self.shell.open_meeting(mid))
                chips.addWidget(btn)
            chips.addStretch(1)
            lay.addLayout(chips)
        if ans.scope:
            sc = QLabel(ans.scope)
            sc.setObjectName("Faint")
            sc.setWordWrap(True)
            lay.addWidget(sc)

        wrap = QHBoxLayout()
        wrap.addWidget(card, 5)
        wrap.addStretch(1)
        holder = QWidget()
        holder.setLayout(wrap)
        self.chat_lay.insertWidget(self.chat_lay.count() - 1, holder)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()))

    def apply_theme(self) -> None:
        pass
