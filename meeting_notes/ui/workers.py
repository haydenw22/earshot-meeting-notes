"""Background workers so audio finalisation, transcription and summarisation
never block the Qt UI thread.

A single generic FuncWorker runs any callable that takes a `progress(str)`
callback, emitting Qt signals for progress / done / failure.
"""
from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QThread, Signal


class FuncWorker(QThread):
    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[Callable[[str], None]], object], parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn(self.progress.emit)
            self.done.emit(result)
        except Exception as e:  # surface to the UI rather than dying silently
            traceback.print_exc()
            self.failed.emit(str(e))
