"""Application shell: a sidebar (logo, New-recording CTA, search, nav, meetings
list, theme toggle) plus a stacked content area. Coordinates navigation and
broadcasts theme changes to every page.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from . import icons, logo
from .page_detail import DetailPage
from .page_home import HomePage
from .page_record import RecordPage
from .page_settings import SettingsPage


class Shell(QMainWindow):
    def __init__(self, repo, cfg, theme):
        super().__init__()
        self.repo = repo
        self.cfg = cfg
        self.theme = theme
        self.setWindowTitle("Earshot")
        self.resize(1100, 720)
        self.setMinimumSize(880, 600)

        self.home = HomePage(self, repo, cfg, theme)
        self.record = RecordPage(self, repo, cfg, theme)
        self.detail = DetailPage(self, repo, cfg, theme)
        self.settings = SettingsPage(self, repo, cfg, theme)

        self._build()
        self.theme.changed.connect(self._on_theme_changed)
        self.notify_data_changed()
        self.show_home()

    # ---------- construction ----------
    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._sidebar())

        self.stack = QStackedWidget()
        for page in (self.home, self.record, self.detail, self.settings):
            self.stack.addWidget(page)
        row.addWidget(self.stack, 1)

    def _sidebar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Sidebar")
        bar.setFixedWidth(258)
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(16, 18, 16, 16)
        lay.setSpacing(12)

        # logo + title
        head = QHBoxLayout()
        head.setSpacing(10)
        self.logo = QLabel()
        self.logo.setFixedSize(34, 34)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Earshot")
        title.setObjectName("H2")
        head.addWidget(self.logo)
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)

        # new recording CTA
        self.new_btn = QPushButton("  New recording")
        self.new_btn.setProperty("variant", "danger")
        self.new_btn.setMinimumHeight(44)
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_btn.clicked.connect(self.show_record)
        lay.addWidget(self.new_btn)

        # search
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search meetings…")
        self.search.textChanged.connect(self._filter_list)
        self.search_action = self.search.addAction(
            icons.icon("search", self.theme.color("text_faint"), 16),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        lay.addWidget(self.search)

        # nav: home
        self.home_btn = self._nav_button("Home", self.show_home)
        lay.addWidget(self.home_btn)

        sect = QLabel("MEETING NOTES")
        sect.setObjectName("SectionLabel")
        lay.addWidget(sect)

        self.meeting_list = QListWidget()
        self.meeting_list.itemClicked.connect(self._on_list_click)
        lay.addWidget(self.meeting_list, 1)

        # bottom controls
        self.settings_btn = self._nav_button("Settings", self.show_settings)
        lay.addWidget(self.settings_btn)
        self.theme_btn = self._nav_button("Dark mode", self._toggle_theme)
        lay.addWidget(self.theme_btn)
        ver = QLabel(f"v{__version__}")
        ver.setObjectName("Faint")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(ver)

        self._refresh_sidebar_icons()
        return bar

    def _nav_button(self, text: str, slot) -> QPushButton:
        b = QPushButton("  " + text)
        b.setProperty("variant", "ghost")
        b.setCheckable(True)
        b.setMinimumHeight(40)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(slot)
        return b

    # ---------- navigation ----------
    def _set_active(self, which: str) -> None:
        self.home_btn.setChecked(which == "home")
        self.settings_btn.setChecked(which == "settings")

    def show_home(self) -> None:
        self.home.refresh()
        self.stack.setCurrentWidget(self.home)
        self._set_active("home")

    def show_record(self) -> None:
        self.record.on_shown()
        self.stack.setCurrentWidget(self.record)
        self._set_active("record")

    def show_settings(self) -> None:
        self.settings.apply_theme()
        self.stack.setCurrentWidget(self.settings)
        self._set_active("settings")

    def open_meeting(self, meeting_id: int) -> None:
        self.detail.load(int(meeting_id))
        self.stack.setCurrentWidget(self.detail)
        self._set_active("none")

    # ---------- data + theme ----------
    def notify_data_changed(self) -> None:
        self._rebuild_list()
        if self.stack.currentWidget() is self.home:
            self.home.refresh()

    def _rebuild_list(self) -> None:
        self.meeting_list.clear()
        for m in self.repo.list():
            item = QListWidgetItem(m.title or "Untitled meeting")
            item.setData(Qt.ItemDataRole.UserRole, m.id)
            item.setIcon(icons.icon("file", self.theme.color("text_muted"), 16))
            self.meeting_list.addItem(item)
        self._filter_list(self.search.text())

    def _filter_list(self, text: str) -> None:
        text = (text or "").lower()
        for i in range(self.meeting_list.count()):
            item = self.meeting_list.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())

    def _on_list_click(self, item: QListWidgetItem) -> None:
        mid = item.data(Qt.ItemDataRole.UserRole)
        if mid is not None:
            self.open_meeting(int(mid))

    def _toggle_theme(self) -> None:
        self.theme.toggle()

    def _on_theme_changed(self, _mode: str) -> None:
        self._refresh_sidebar_icons()
        self._rebuild_list()
        for page in (self.home, self.record, self.detail, self.settings):
            page.apply_theme()
        # rebuild colour-dependent content
        self.home.refresh()
        if self.detail.meeting_id is not None:
            self.detail.refresh()

    def _refresh_sidebar_icons(self) -> None:
        # brand mark (the indigo tile is part of the SVG, so no QSS background)
        self.logo.setPixmap(logo.logo_pixmap(34))
        self.logo.setStyleSheet("")
        self.new_btn.setIcon(icons.icon("record", self.theme.color("on_danger"), 16))
        self.home_btn.setIcon(icons.icon("home", self.theme.color("text_muted"), 18))
        self.settings_btn.setIcon(icons.icon("settings", self.theme.color("text_muted"), 18))
        dark = self.theme.mode == "dark"
        self.theme_btn.setText("  Light mode" if dark else "  Dark mode")
        self.theme_btn.setIcon(icons.icon("sun" if dark else "moon", self.theme.color("text_muted"), 18))
        if hasattr(self, "search_action"):
            self.search_action.setIcon(icons.icon("search", self.theme.color("text_faint"), 16))

    # ---------- close guard ----------
    def closeEvent(self, event):
        if self.record.is_busy():
            if QMessageBox.question(
                self, "Recording in progress",
                "A recording or processing is still running. Quit anyway?"
            ) != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.accept()
