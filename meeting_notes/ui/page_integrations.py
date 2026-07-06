"""Integrations page: connect Earshot to the tools you already use.

Houses the Todoist and Webhook cards (moved here from Settings -> General in
Phase D) plus a "More coming soon" card previewing future connectors. Same
save-button pattern as Settings: fields hold live edits, "Save changes" commits
them to cfg.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .widgets import Card, calm_scroll_children, make_chip

# Connectors previewed in the "More coming soon" card: (icon name, display name,
# one-line description). Kept short and honest — nothing here is wired up yet.
_COMING_SOON = [
    ("message", "Slack", "Post finished meeting notes straight into a channel."),
    ("file", "Notion", "Sync meeting notes into a Notion database."),
    ("calendar", "Calendar", "Auto-detect meetings from your calendar and pre-fill attendees."),
]


class IntegrationsPage(QWidget):
    def __init__(self, shell, repo, cfg, theme):
        super().__init__()
        self.shell = shell
        self.repo = repo
        self.cfg = cfg
        self.theme = theme
        self._build()
        # scrolling the page must never change a control it passes over
        calm_scroll_children(self)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 28, 40, 28)
        root.setSpacing(16)

        head = QLabel("Integrations")
        head.setObjectName("H1")
        root.addWidget(head)
        sub = QLabel("Connect Earshot to the tools you already use.")
        sub.setObjectName("Muted")
        root.addWidget(sub)

        outer_w = QWidget()
        outer = QVBoxLayout(outer_w)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(2, 16, 2, 2)
        lay.setSpacing(16)
        scroll.setWidget(host)
        outer.addWidget(scroll)
        root.addWidget(outer_w, 1)

        lay.addWidget(self._webhook_card())
        lay.addWidget(self._todoist_card())
        lay.addWidget(self._coming_soon_card())
        lay.addStretch(1)

        bar = QHBoxLayout()
        bar.addStretch(1)
        self.save_btn = QPushButton("Save changes")
        self.save_btn.setProperty("variant", "primary")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.clicked.connect(self._save)
        bar.addWidget(self.save_btn)
        root.addLayout(bar)

        self.apply_theme()

    def _card(self, title: str, subtitle: str = "") -> tuple[Card, QVBoxLayout]:
        card = Card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(22, 18, 22, 20)
        cl.setSpacing(12)
        t = QLabel(title)
        t.setObjectName("H3")
        cl.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("Muted")
            s.setWordWrap(True)
            cl.addWidget(s)
        return card, cl

    # ---------- Webhook (moved from Settings -> General) ----------
    def _webhook_card(self) -> Card:
        card, wcl = self._card(
            "Webhook",
            "POST each finished meeting (as JSON) to your own automation — Slack, Notion, "
            "Zapier, n8n, a CRM, anything. Leave blank to disable. Note: this sends data off "
            "your machine.",
        )
        wform = QFormLayout()
        wform.setSpacing(10)
        self.webhook_url = QLineEdit(self.cfg.webhook_url)
        self.webhook_url.setPlaceholderText("https://…  (blank = off)")
        self.webhook_when = QComboBox()
        self.webhook_when.addItem("After the AI summary is done", "summary")
        self.webhook_when.addItem("After transcription (raw transcript)", "transcript")
        self.webhook_when.setCurrentIndex(1 if self.cfg.webhook_when == "transcript" else 0)
        wform.addRow("URL", self.webhook_url)
        wform.addRow("Send", self.webhook_when)
        wcl.addLayout(wform)
        return card

    # ---------- Todoist (moved from Settings -> General) ----------
    def _todoist_card(self) -> Card:
        card, tdl = self._card(
            "Todoist",
            "Send open action items to Todoist from a meeting's page (the \"To Todoist\" button). "
            "Get your token from Todoist → Settings → Integrations → Developer.",
        )
        tdform = QFormLayout()
        tdform.setSpacing(10)
        self.todoist_token = QLineEdit(self.cfg.todoist_token)
        self.todoist_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.todoist_token.setPlaceholderText("Todoist API token  (blank = off)")
        tdform.addRow("API token", self.todoist_token)
        tdl.addLayout(tdform)
        td_row = QHBoxLayout()
        self.todoist_test_btn = QPushButton("Test connection")
        self.todoist_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.todoist_test_btn.clicked.connect(self._test_todoist)
        self.todoist_test_label = QLabel("")
        td_row.addWidget(self.todoist_test_btn)
        td_row.addWidget(self.todoist_test_label)
        td_row.addStretch(1)
        tdl.addLayout(td_row)
        return card

    def _test_todoist(self) -> None:
        self.todoist_test_label.setText("Testing…")
        self.todoist_test_label.repaint()
        from ..integrations import todoist
        ok = todoist.ping(self.todoist_token.text().strip())
        self.todoist_test_label.setText("✓ Connected" if ok else "✗ Could not connect")
        self.todoist_test_label.setStyleSheet(
            f"color:{self.theme.color('primary' if ok else 'danger')}; font-weight:600;"
        )

    # ---------- more coming soon ----------
    def _coming_soon_card(self) -> Card:
        card, cl = self._card(
            "More coming soon",
            "Zapier and Make already work today — point either at the webhook above.",
        )
        for icon_name, name, desc in _COMING_SOON:
            cl.addWidget(self._coming_soon_row(icon_name, name, desc))
        return card

    def _coming_soon_row(self, icon_name: str, name: str, desc: str) -> QWidget:
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 4, 0, 4)
        rl.setSpacing(12)

        tile = QLabel()
        tile.setFixedSize(34, 34)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setStyleSheet(f"background:{self.theme.color('surface_hover')}; border-radius:9px;")
        icon_lbl = QLabel(tile)
        icon_lbl.setPixmap(icons.pixmap(icon_name, self.theme.color("text_muted"), 16))
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setGeometry(0, 0, 34, 34)
        rl.addWidget(tile)

        texts = QVBoxLayout()
        texts.setSpacing(1)
        name_lbl = QLabel(name)
        name_lbl.setObjectName("H3")
        texts.addWidget(name_lbl)
        desc_lbl = QLabel(desc)
        desc_lbl.setObjectName("Faint")
        desc_lbl.setWordWrap(True)
        texts.addWidget(desc_lbl)
        rl.addLayout(texts, 1)

        chip = make_chip("Coming soon", fg=self.theme.color("text_faint"), bg=self.theme.color("surface_hover"))
        chip.setEnabled(False)
        rl.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    # ---------- save ----------
    def _save(self) -> None:
        self.cfg.webhook_url = self.webhook_url.text().strip()
        self.cfg.webhook_when = self.webhook_when.currentData() or "summary"
        self.cfg.todoist_token = self.todoist_token.text().strip()
        self.cfg.save()
        self.save_btn.setText("Saved ✓")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1400, lambda: self.save_btn.setText("Save changes"))

    def apply_theme(self) -> None:
        pass
