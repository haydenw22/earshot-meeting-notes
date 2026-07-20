"""'Update available' dialog + the startup check that raises it.

Mirrors cloud_link.py: a QThread does the network work (checking, then
downloading) so the GUI never blocks, and the dialog owns the worker's lifecycle.

Closing the dialog mid-download is safe: the download is cancelled
cooperatively (checked between streamed chunks), its signals are disconnected
so a straggler can never launch the installer after the dialog is gone, and the
thread is detached + kept alive in _LINGERING until it exits — Qt aborts the
whole process if a running QThread is destroyed, so we never let that happen.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from .. import __version__, updater
from ..updater import UpdateInfo

# Cancelled download threads that haven't fully exited yet. Holding a strong
# reference stops Python GC destroying a live QThread (process abort); shutdown()
# joins them at app close.
_LINGERING: set[QThread] = set()


def shutdown(timeout_ms: int = 5000) -> None:
    """Join any cancelled download threads at app close. They touch nothing but
    their own private temp files, so terminate() is an acceptable last resort
    here (unlike workers that write the database)."""
    for w in list(_LINGERING):
        try:
            if w.isRunning() and not w.wait(timeout_ms):
                w.terminate()
                w.wait(1000)
        except RuntimeError:
            pass  # already deleted — fine
        _LINGERING.discard(w)


class _CheckWorker(QThread):
    """Ask GitHub for a newer release; emit it if there is one."""

    found = Signal(object)  # UpdateInfo

    def run(self) -> None:
        try:
            info = updater.check_for_update(__version__)
        except Exception:
            return
        if info is not None:
            self.found.emit(info)


class _DownloadWorker(QThread):
    progress = Signal(float)   # 0..1
    done = Signal(str)         # verified installer path
    failed = Signal(str)

    def __init__(self, url: str, digest_url: str, dest: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.digest_url = digest_url
        self.dest = dest
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            expected = updater.fetch_expected_sha256(self.digest_url)
            path = updater.download_installer(
                self.url, self.dest, progress_cb=self.progress.emit,
                expected_sha256=expected, cancelled=lambda: self._cancelled)
        except updater.DownloadCancelled:
            updater.cleanup_download(self.dest)
            return
        except Exception as e:  # noqa: BLE001 — surface any failure to the user
            updater.cleanup_download(self.dest)
            if not self._cancelled:
                self.failed.emit(f"Download failed: {e}")
            return
        if self._cancelled:  # finished in the same instant the user closed — don't install
            updater.cleanup_download(path)
            return
        self.done.emit(path)


class UpdateDialog(QDialog):
    """Shows what's new and downloads + runs the installer on request."""

    def __init__(self, parent, theme, info: UpdateInfo):
        super().__init__(parent)
        self.theme = theme
        self.info = info
        self.worker: _DownloadWorker | None = None
        self.setWindowTitle("Update available")
        self.setMinimumWidth(500)
        self._build()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(28, 24, 28, 22)
        v.setSpacing(12)

        title = QLabel("A new version of Earshot is available")
        title.setObjectName("H2")
        v.addWidget(title)

        sub = QLabel(f"You're on {__version__}. Update to {self.info.version}.")
        sub.setObjectName("Muted")
        v.addWidget(sub)

        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(self.info.notes)
        notes.setMinimumHeight(230)
        v.addWidget(notes, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(True)
        self.progress.setVisible(False)
        v.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setObjectName("Faint")
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        row = QHBoxLayout()
        self.install_btn = QPushButton("Download & install")
        self.install_btn.setProperty("variant", "primary")
        self.install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.install_btn.clicked.connect(self._start)
        row.addWidget(self.install_btn)
        row.addStretch(1)
        self.later_btn = QPushButton("Later")
        self.later_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.later_btn.clicked.connect(self.reject)
        row.addWidget(self.later_btn)
        v.addLayout(row)

    def _start(self) -> None:
        self.install_btn.setEnabled(False)
        self.later_btn.setText("Cancel")  # closing now cancels the download
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.status.setStyleSheet("")
        self.status.setText("Downloading update…")
        self.worker = _DownloadWorker(self.info.download_url, self.info.digest_url,
                                      updater.default_download_path(), self)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_progress(self, f: float) -> None:
        self.progress.setValue(int(f * 100))

    def _on_done(self, path: str) -> None:
        self.worker = None
        if sys.platform == "darwin":
            from . import workers

            window = self.parentWidget()
            record = getattr(window, "record", None)
            if ((record is not None and record.is_busy()) or workers.active_count()):
                updater.cleanup_download(path)
                self._on_failed(
                    "Finish the active recording or background task before installing the update."
                )
                return
        if sys.platform == "darwin":
            self.status.setText("Installing the update. Earshot will close and reopen. "
                                "Your recordings and notes are kept.")
        else:
            self.status.setText("Starting the installer. Earshot will close and reopen. "
                                "Your recordings and notes are kept.")
        try:
            updater.run_installer(path)
        except Exception as e:  # noqa: BLE001
            updater.cleanup_download(path)
            self._on_failed(f"Couldn't start the installer: {e}")
            return
        # Close the main window so its closeEvent performs the normal worker,
        # overlay and update-thread cleanup. QApplication.quit() bypassed that
        # guard and could interrupt an active recording.
        window = self.parentWidget()
        if window is not None:
            window.close()
        else:
            app = QApplication.instance()
            if app is not None:
                app.quit()

    def _on_failed(self, msg: str) -> None:
        self.worker = None
        self.progress.setVisible(False)
        self.status.setStyleSheet(f"color:{self.theme.color('danger')};")
        self.status.setText(msg + "  You can also download it from tryearshot.app.")
        self.install_btn.setEnabled(True)
        self.later_btn.setText("Later")
        self.later_btn.setEnabled(True)

    def _stop_worker(self) -> None:
        w, self.worker = self.worker, None
        if w is None:
            return
        w.cancel()
        if not w.isRunning():
            return
        # Disconnect everything so the straggler can never touch this dialog or
        # launch the installer, then detach it from the dialog: destroying a
        # QDialog that still parents a running QThread aborts the process.
        for sig in (w.progress, w.done, w.failed):
            try:
                sig.disconnect()
            except (TypeError, RuntimeError):
                pass
        w.setParent(None)
        _LINGERING.add(w)
        w.finished.connect(lambda: _LINGERING.discard(w))
        # A cancelled download exits within one chunk read; give it a moment but
        # never block the UI for long — _LINGERING keeps it alive either way.
        w.wait(250)

    def reject(self) -> None:  # noqa: N802 (Qt override)
        self._stop_worker()
        super().reject()

    def closeEvent(self, event):  # noqa: N802 (Qt override)
        self._stop_worker()
        super().closeEvent(event)


def schedule_update_check(window, theme):
    """Kick off a background update check (packaged Windows build only). Returns
    the worker so the caller can keep a reference (else it'd be garbage-collected
    mid-flight); None when auto-update doesn't apply."""
    if not updater.is_supported():
        return None

    worker = _CheckWorker(window)

    def _show(info: UpdateInfo) -> None:
        UpdateDialog(window, theme, info).exec()

    worker.found.connect(_show)
    worker.start()
    return worker
