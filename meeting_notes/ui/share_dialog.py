"""The Share dialog: save a standalone HTML file, or (Earshot Plus) publish a
public link hosted on tryearshot.app.

The dialog only collects the user's choice — page_detail performs the export /
publish / unshare. The public-link option is explicit about what it means:
the notes leave this computer and anyone with the link can view the page.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


class ShareDialog(QDialog):
    """Result: `choice` is one of "file" | "link" | "unshare" | None (cancel);
    `include_transcript` applies to file and link alike."""

    def __init__(self, parent, theme, *, title: str, has_transcript: bool,
                 cloud: bool, share_url: str = ""):
        super().__init__(parent)
        self.theme = theme
        self.choice: str | None = None
        self.setWindowTitle("Share meeting")
        self.setMinimumWidth(430)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        head = QLabel("Share meeting")
        head.setObjectName("H3")
        lay.addWidget(head)
        sub = QLabel(title or "Untitled meeting")
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        lay.addSpacing(4)

        # ---- option 1: file (always available, always local) ----
        file_btn = QPushButton("Save as file")
        file_btn.setProperty("variant", "primary" if not cloud else "")
        file_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        file_btn.clicked.connect(lambda _=False: self._pick("file"))
        lay.addWidget(file_btn)
        file_note = QLabel("A standalone HTML file saved on this computer. "
                           "Nothing is uploaded anywhere.")
        file_note.setObjectName("Faint")
        file_note.setWordWrap(True)
        lay.addWidget(file_note)
        lay.addSpacing(6)

        # ---- option 2: public link (Earshot Plus) ----
        self.link_btn = QPushButton("Update public link" if share_url else "Create public link")
        self.link_btn.setProperty("variant", "primary" if cloud else "")
        self.link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.link_btn.setEnabled(cloud)
        self.link_btn.clicked.connect(lambda _=False: self._pick("link"))
        lay.addWidget(self.link_btn)
        if cloud:
            link_note = QLabel("Hosts these notes as a public web page on tryearshot.app. "
                               "The notes LEAVE THIS COMPUTER, and anyone who has the link "
                               "can view them. The link is unguessable and never listed "
                               "anywhere; you can stop sharing at any time.")
        else:
            link_note = QLabel("Requires Earshot Plus — hosts the notes as a web page you "
                               "can share with a link. Sign in from Settings → Account.")
        link_note.setObjectName("Faint")
        link_note.setWordWrap(True)
        lay.addWidget(link_note)

        # ---- include transcript (applies to both) ----
        lay.addSpacing(4)
        self.tr_check = QCheckBox("Include the full transcript")
        self.tr_check.setEnabled(has_transcript)
        if not has_transcript:
            self.tr_check.setToolTip("This meeting has no transcript")
        lay.addWidget(self.tr_check)

        # ---- existing link management ----
        if share_url:
            lay.addSpacing(6)
            cur = QLabel("This meeting is currently shared at:")
            cur.setObjectName("Muted")
            lay.addWidget(cur)
            self.url_box = QLineEdit(share_url)
            self.url_box.setReadOnly(True)
            self.url_box.setCursorPosition(0)
            lay.addWidget(self.url_box)
            row = QHBoxLayout()
            row.setSpacing(8)
            self.copy_btn = QPushButton("Copy link")
            self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.copy_btn.clicked.connect(lambda _=False, u=share_url: self._copy(u))
            row.addWidget(self.copy_btn)
            unshare_btn = QPushButton("Stop sharing")
            unshare_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            unshare_btn.clicked.connect(lambda _=False: self._pick("unshare"))
            row.addWidget(unshare_btn)
            row.addStretch(1)
            lay.addLayout(row)

        lay.addSpacing(8)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        lay.addLayout(bottom)

    @property
    def include_transcript(self) -> bool:
        return self.tr_check.isChecked() and self.tr_check.isEnabled()

    def _pick(self, choice: str) -> None:
        self.choice = choice
        self.accept()

    def _copy(self, url: str) -> None:
        QApplication.clipboard().setText(url)
        self.copy_btn.setText("Copied ✓")
