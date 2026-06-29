"""A tiny editor for a list of {name, <text>} items — used in Settings for note
templates and saved AI actions. Pick from a dropdown, edit name + text, save/delete.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class NamedListManager(QWidget):
    def __init__(self, items, *, text_key="instructions", name_ph="Name", text_ph="", text_lines=4):
        super().__init__()
        self._items = [dict(i) for i in (items or [])]
        self._text_key = text_key

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        top = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._on_select)
        self.new_btn = QPushButton("New")
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_btn.clicked.connect(self._new)
        self.del_btn = QPushButton("Delete")
        self.del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.del_btn.clicked.connect(self._delete)
        top.addWidget(self.combo, 1)
        top.addWidget(self.new_btn)
        top.addWidget(self.del_btn)
        v.addLayout(top)

        self.name = QLineEdit()
        self.name.setPlaceholderText(name_ph)
        v.addWidget(self.name)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(text_ph)
        self.text.setMinimumHeight(text_lines * 22)
        v.addWidget(self.text)

        row = QHBoxLayout()
        row.addStretch(1)
        self.save_btn = QPushButton("Save this one")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.clicked.connect(self._save_current)
        row.addWidget(self.save_btn)
        v.addLayout(row)

        self._reload(0)

    def items(self) -> list:
        return [dict(i) for i in self._items]

    def _reload(self, select: int = 0) -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        for it in self._items:
            self.combo.addItem(it.get("name") or "(unnamed)")
        self.combo.blockSignals(False)
        if self._items:
            self.combo.setCurrentIndex(max(0, min(select, len(self._items) - 1)))
            self._on_select(self.combo.currentIndex())
        else:
            self._new()

    def _on_select(self, idx: int) -> None:
        if 0 <= idx < len(self._items):
            it = self._items[idx]
            self.name.setText(it.get("name", ""))
            self.text.setPlainText(it.get(self._text_key, ""))

    def _new(self) -> None:
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(-1)
        self.combo.blockSignals(False)
        self.name.clear()
        self.text.clear()
        self.name.setFocus()

    def _save_current(self) -> None:
        name = self.name.text().strip()
        if not name:
            return
        text = self.text.toPlainText().strip()
        for it in self._items:
            if it.get("name") == name:
                it[self._text_key] = text
                break
        else:
            self._items.append({"name": name, self._text_key: text})
        names = [i.get("name") for i in self._items]
        self._reload(names.index(name))

    def _delete(self) -> None:
        idx = self.combo.currentIndex()
        if 0 <= idx < len(self._items):
            self._items.pop(idx)
            self._reload(idx)
