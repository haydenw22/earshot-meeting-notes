"""Small dialogs for creating/renaming a folder: a name field plus a row of
checkable colour swatches for new folders, and a plain text prompt for renames.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .widgets import FOLDER_COLORS

_DEFAULT_COLOR = "#6366F1"  # Indigo — the app's own accent


class _NewFolderDialog(QDialog):
    def __init__(self, parent, theme):
        super().__init__(parent)
        self.theme = theme
        self._color = _DEFAULT_COLOR
        self._swatches: list[QPushButton] = []
        self.setWindowTitle("New project")
        self.setMinimumWidth(320)

        v = QVBoxLayout(self)
        v.setSpacing(12)

        name_lbl = QLabel("Name")
        name_lbl.setObjectName("H3")
        v.addWidget(name_lbl)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Acme Corp")
        self.name_edit.textChanged.connect(self._update_ok_enabled)
        v.addWidget(self.name_edit)

        color_lbl = QLabel("Colour")
        color_lbl.setObjectName("H3")
        v.addWidget(color_lbl)
        swatch_row = QHBoxLayout()
        swatch_row.setSpacing(8)
        for name, hexcolor in FOLDER_COLORS:
            btn = self._make_swatch(name, hexcolor)
            swatch_row.addWidget(btn)
            self._swatches.append(btn)
        swatch_row.addStretch(1)
        v.addLayout(swatch_row)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        v.addWidget(self.buttons)

        self._select_color(_DEFAULT_COLOR)
        self._update_ok_enabled()
        self.name_edit.setFocus()

    def _make_swatch(self, name: str, hexcolor: str) -> QPushButton:
        btn = QPushButton()
        btn.setCheckable(True)
        btn.setFixedSize(24, 24)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(name)
        btn.setProperty("hexcolor", hexcolor)
        btn.clicked.connect(lambda _=False, c=hexcolor: self._select_color(c))
        self._style_swatch(btn, hexcolor, checked=False)
        return btn

    def _style_swatch(self, btn: QPushButton, hexcolor: str, *, checked: bool) -> None:
        border = self.theme.color("text") if checked else "transparent"
        btn.setStyleSheet(
            f"QPushButton{{background:{hexcolor}; border-radius:12px; border:2px solid {border};}}"
        )

    def _select_color(self, hexcolor: str) -> None:
        self._color = hexcolor
        for btn in self._swatches:
            is_this = btn.property("hexcolor") == hexcolor
            btn.setChecked(is_this)
            self._style_swatch(btn, btn.property("hexcolor"), checked=is_this)

    def _update_ok_enabled(self) -> None:
        ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setEnabled(bool(self.name_edit.text().strip()))

    def result_tuple(self) -> tuple[str, str]:
        return self.name_edit.text().strip(), self._color


def ask_new_folder(parent, theme) -> tuple[str, str] | None:
    """Prompt for a new folder's name + colour. Returns (name, color-hex) or
    None if cancelled."""
    dlg = _NewFolderDialog(parent, theme)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        name, color = dlg.result_tuple()
        if name:
            return name, color
    return None


def ask_rename_folder(parent, theme, current_name: str) -> str | None:
    """Prompt for a new name for an existing folder. Returns the new name, or
    None if cancelled/unchanged/blank."""
    text, ok = QInputDialog.getText(parent, "Rename project", "Name", QLineEdit.EchoMode.Normal, current_name)
    if not ok:
        return None
    text = text.strip()
    if not text or text == current_name:
        return None
    return text
