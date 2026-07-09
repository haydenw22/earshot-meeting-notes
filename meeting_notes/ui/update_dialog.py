"""'Update available' dialog + the startup check that raises it.

Mirrors cloud_link.py: a QThread does the network work (checking, then
downloading) so the GUI never blocks, and the dialog owns the worker's lifecycle.
"""
from __future__ import annotations

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
    done = Signal(str)         # installer path
    failed = Signal(str)

    def __init__(self, url: str, dest: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.dest = dest

    def run(self) -> None:
        try:
            path = updater.download_installer(self.url, self.dest,
                                              progress_cb=self.progress.emit)
        except Exception as e:  # noqa: BLE001 — surface any failure to the user
            self.failed.emit(f"Download failed: {e}")
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
        self.later_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.status.setStyleSheet("")
        self.status.setText("Downloading update…")
        self.worker = _DownloadWorker(self.info.download_url,
                                      updater.default_download_path(), self)
        self.worker.progress.connect(lambda f: self.progress.setValue(int(f * 100)))
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_done(self, path: str) -> None:
        self.status.setText("Starting the installer — Earshot will close and reopen. "
                            "Your recordings and notes are kept.")
        try:
            updater.run_installer(path)
        except Exception as e:  # noqa: BLE001
            self._on_failed(f"Couldn't start the installer: {e}")
            return
        # Quit so the installer can replace the running app; it relaunches Earshot.
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _on_failed(self, msg: str) -> None:
        self.progress.setVisible(False)
        self.status.setStyleSheet(f"color:{self.theme.color('danger')};")
        self.status.setText(msg + "  You can also download it from tryearshot.app.")
        self.install_btn.setEnabled(True)
        self.later_btn.setEnabled(True)

    def _stop_worker(self) -> None:
        if self.worker is not None:
            self.worker.wait(3000)
            self.worker = None

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
