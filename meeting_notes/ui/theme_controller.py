"""Runtime theme state: applies the stylesheet, toggles light/dark, persists the
choice, and hands out theme-coloured icons. Widgets connect to `changed` to
re-tint their icons when the theme flips.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ..config import Config
from . import icons, theme


class ThemeController(QObject):
    changed = Signal(str)  # new mode

    def __init__(self, cfg: Config):
        super().__init__()
        self._cfg = cfg
        self._mode = "dark" if cfg.theme_mode == "dark" else "light"

    @property
    def mode(self) -> str:
        return self._mode

    def color(self, key: str) -> str:
        return theme.tokens(self._mode)[key]

    def apply(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.build_qss(self._mode, self._check_icon()))

    def _check_icon(self) -> str | None:
        """A white check PNG used as the checked-checkbox indicator (works on the
        indigo fill in both themes). Generated once and cached in the data dir."""
        from ..paths import app_data_dir

        path = app_data_dir() / "_check_white.png"
        if not path.exists():
            try:
                icons.pixmap("check", "#FFFFFF", 16).save(str(path), "PNG")
            except Exception:
                return None
        return str(path).replace("\\", "/")

    def set_mode(self, mode: str) -> None:
        mode = "dark" if mode == "dark" else "light"
        if mode == self._mode:
            return
        self._mode = mode
        self._cfg.theme_mode = mode
        self._cfg.save()
        self.apply()
        self.changed.emit(mode)

    def toggle(self) -> None:
        self.set_mode("light" if self._mode == "dark" else "dark")

    def icon(self, name: str, role: str = "text", size: int = 20) -> QIcon:
        return icons.icon(name, self.color(role), size)
