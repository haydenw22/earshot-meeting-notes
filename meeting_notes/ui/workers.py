"""Background workers so audio finalisation, transcription and summarisation
never block the Qt UI thread.

A single generic FuncWorker runs any callable that takes a `progress(str)`
callback, emitting Qt signals for progress / done / failure.
"""
from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import Qt, QThread, Signal

# Strong references to every running worker. Callers keep workers in plain
# attributes (self.worker = FuncWorker(...)); if that attribute is overwritten
# while the old thread is still running, Python would GC a live QThread — which
# aborts the process ("QThread: Destroyed while thread is still running").
# Holding each worker here until it has truly finished removes that crash class
# app-wide without changing any call site.
_ACTIVE: set["FuncWorker"] = set()


def active_count() -> int:
    """Number of background workers still running (used by the close guard)."""
    return len(_ACTIVE)


def join_all(timeout_ms: int = 5000) -> None:
    """Block until every active worker has finished (or the timeout elapses),
    called on app close so the interpreter never exits with a live QThread —
    which Qt aborts with STATUS_STACK_BUFFER_OVERRUN (0xC0000409). Best-effort:
    a wedged worker is terminated as a last resort so quit still completes."""
    for w in list(_ACTIVE):
        try:
            if w.isRunning() and not w.wait(timeout_ms):
                w.terminate()
                w.wait(1000)
        except RuntimeError:
            pass  # already deleted — fine


class FuncWorker(QThread):
    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[Callable[[str], None]], object], parent=None):
        super().__init__(parent)
        self._fn = fn
        # Queued => the release runs on the main event loop after the thread has
        # fully finished, so the GC can never collect a still-running thread.
        self.finished.connect(self._release, Qt.ConnectionType.QueuedConnection)

    def start(self, *args) -> None:
        _ACTIVE.add(self)
        super().start(*args)

    def _release(self) -> None:
        _ACTIVE.discard(self)

    def run(self) -> None:
        try:
            result = self._fn(self.progress.emit)
            self.done.emit(result)
        except Exception as e:  # surface to the UI rather than dying silently
            traceback.print_exc()
            self.failed.emit(str(e))
