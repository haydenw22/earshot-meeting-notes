"""The "Ask" page — a chat over your past meetings.

Type a natural-language question; the answer comes back with clickable citation
chips that jump to the source meeting. Runs off-thread via FuncWorker.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
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
from .widgets import Card
from .workers import FuncWorker


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

        head = QLabel("Ask Earshot")
        head.setObjectName("H1")
        sub = QLabel("Ask anything about your past meetings — answers cite the source.")
        sub.setObjectName("Muted")
        root.addWidget(head)
        root.addWidget(sub)

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

    # ---------- send / receive ----------
    def _send(self) -> None:
        q = self.input.text().strip()
        if not q or (self.worker and self.worker.isRunning()):
            return
        if not self.cfg.resolved_anthropic_key():
            QMessageBox.warning(self, "No API key", "Add an Anthropic API key in Settings → Notes first.")
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

        def job(_progress):
            from ..qa import ask
            return ask.answer(q, meetings=repo.list(), api_key=cfg.resolved_anthropic_key(),
                              model=cfg.anthropic_model, today=today)

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

    # ---------- bubbles ----------
    def _add_bubble(self, text: str, *, role: str) -> QWidget:
        card = Card(shadow=(role != "you"))
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
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
