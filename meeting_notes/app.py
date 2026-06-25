"""Application bootstrap: theme, config, repository and the main shell window."""
from __future__ import annotations

import sys

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from .config import Config
from .storage.repository import MeetingRepository
from .ui import logo
from .ui.shell import Shell
from .ui.theme_controller import ThemeController


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Earshot")
    app.setOrganizationName("Whittle")
    app.setFont(QFont("Segoe UI", 10))
    app.setWindowIcon(QIcon(logo.logo_pixmap(256)))

    cfg = Config.load()
    repo = MeetingRepository()

    theme = ThemeController(cfg)
    theme.apply()

    window = Shell(repo, cfg, theme)
    window.setWindowIcon(QIcon(logo.logo_pixmap(256)))
    window.show()
    return app.exec()
