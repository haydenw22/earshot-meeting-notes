"""Account page: local display name today, a "coming soon" pitch for Earshot
Cloud (hosted sync — no network calls made from here), and a reminder that
everything currently lives on this PC.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
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

from . import icons
from .widgets import Card


class AccountPage(QWidget):
    def __init__(self, shell, repo, cfg, theme):
        super().__init__()
        self.shell = shell
        self.repo = repo
        self.cfg = cfg
        self.theme = theme
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 28, 40, 28)
        root.setSpacing(16)

        head = QLabel("Account")
        head.setObjectName("H1")
        root.addWidget(head)

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

        lay.addWidget(self._profile_card())
        lay.addWidget(self._cloud_card())
        lay.addWidget(self._data_card())
        lay.addStretch(1)

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

    # ---------- Profile ----------
    def _profile_card(self) -> Card:
        card, cl = self._card("Profile")

        row = QHBoxLayout()
        row.setSpacing(14)
        self.avatar = QLabel()
        self.avatar.setFixedSize(56, 56)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.avatar)

        form = QVBoxLayout()
        form.setSpacing(4)
        name_lbl = QLabel("Display name")
        name_lbl.setObjectName("Muted")
        form.addWidget(name_lbl)
        self.name_edit = QLineEdit(self.cfg.account_name)
        self.name_edit.setPlaceholderText("Guest")
        self.name_edit.editingFinished.connect(self._on_name_changed)
        form.addWidget(self.name_edit)
        row.addLayout(form, 1)
        cl.addLayout(row)

        helper = QLabel("Used for the account card and future sharing features.")
        helper.setObjectName("Faint")
        helper.setWordWrap(True)
        cl.addWidget(helper)

        self._update_avatar()
        return card

    def _on_name_changed(self) -> None:
        self.cfg.account_name = self.name_edit.text().strip()
        self.cfg.save()
        self._update_avatar()
        self.shell.refresh_account_card()

    def _update_avatar(self) -> None:
        name = (self.cfg.account_name or "").strip() or "Guest"
        initial = name[0].upper()
        self.avatar.setStyleSheet(
            f"background:{self.theme.color('primary_soft')}; color:{self.theme.color('primary')};"
            f"border-radius:28px; font-size:22px; font-weight:700;"
        )
        self.avatar.setText(initial)

    # ---------- Earshot Cloud (coming soon — no network calls) ----------
    def _cloud_card(self) -> Card:
        card, cl = self._card(
            "Earshot Cloud — coming soon",
            "Sync your meetings across devices, offload transcription to the cloud, and share "
            "notes with your team — all optional, all on top of the local-first app you have today.",
        )

        row = QHBoxLayout()
        row.setSpacing(10)
        self.cloud_email = QLineEdit()
        self.cloud_email.setPlaceholderText("you@example.com")
        self.signin_btn = QPushButton("  Sign in")
        self.signin_btn.setProperty("variant", "primary")
        self.signin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.signin_btn.clicked.connect(self._on_sign_in)
        row.addWidget(self.cloud_email, 1)
        row.addWidget(self.signin_btn)
        cl.addLayout(row)

        caption = QLabel("No account required — Earshot works 100% locally.")
        caption.setObjectName("Faint")
        caption.setWordWrap(True)
        cl.addWidget(caption)
        return card

    def _on_sign_in(self) -> None:
        # Deliberately does NOT touch the network — accounts/sync don't exist yet.
        QMessageBox.information(
            self, "Coming soon",
            "Accounts and sync are coming in a future release. Earshot stays fully local until then.",
        )

    # ---------- Your data ----------
    def _data_card(self) -> Card:
        card, cl = self._card("Your data")
        line = QLabel("Everything — recordings, transcripts, notes and settings — is stored locally on this PC.")
        line.setObjectName("Muted")
        line.setWordWrap(True)
        cl.addWidget(line)
        open_btn = QPushButton("Open storage folder")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(self._open_storage_folder)
        cl.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    def _open_storage_folder(self) -> None:
        import os

        from ..paths import recordings_dir
        folder = str(recordings_dir())
        if os.path.isdir(folder):
            os.startfile(folder)  # noqa: S606

    def apply_theme(self) -> None:
        self._update_avatar()
        if hasattr(self, "signin_btn"):
            self.signin_btn.setIcon(icons.icon("cloud", self.theme.color("on_primary"), 15))
