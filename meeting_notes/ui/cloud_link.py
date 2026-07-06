"""Device-link dialog for signing in to Earshot Plus.

Flow (per the API contract):
  1. POST /v1/device/code → a short code (e.g. "ABC-123"), a poll_token and a
     verify_url. We show the code big and offer to open the verify page.
  2. The user opens tryearshot.app, signs in and enters the code.
  3. We poll POST /v1/device/poll at the server's `interval` until it returns
     status "ok" (with the device_token + email), "expired", or we time out.

All network work happens on a QThread worker so the GUI never blocks. The
servers aren't deployed yet, so connection errors show the friendly
"not live yet" copy and let the user retry rather than crashing or hanging.
"""
from __future__ import annotations

import time
import webbrowser

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..transcription import earshot_client
from ..transcription.earshot_client import CloudError


def _device_name() -> str:
    import os
    import platform

    return (os.environ.get("COMPUTERNAME") or platform.node() or "This PC").strip() or "This PC"


class _LinkWorker(QThread):
    """Requests a device code, emits it for display, then polls to completion."""

    code_ready = Signal(str, str)   # code, verify_url
    linked = Signal(dict)           # {device_token, email, plan, sub_status}
    failed = Signal(str)            # friendly message

    def __init__(self, base_url: str, device_name: str = "", parent=None):
        super().__init__(parent)
        self.base_url = base_url
        self.device_name = (device_name or "").strip() or _device_name()
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            info = earshot_client.request_device_code(
                self.base_url, app_version=__version__, device_name=self.device_name)
        except CloudError as e:
            self.failed.emit(str(e))
            return
        except Exception as e:  # never let the worker die silently
            self.failed.emit(f"Could not start sign-in: {e}")
            return

        poll_token = info.get("poll_token") or ""
        code = info.get("code") or "??? ???"
        verify_url = info.get("verify_url") or "https://tryearshot.app"
        interval = max(1, int(info.get("interval") or 3))
        expires_in = max(30, int(info.get("expires_in") or 900))
        self.code_ready.emit(code, verify_url)

        deadline = time.monotonic() + expires_in
        while not self._stop and time.monotonic() < deadline:
            # sleep in short slices so a cancel (dialog close) is responsive
            waited = 0.0
            while waited < interval and not self._stop:
                time.sleep(0.2)
                waited += 0.2
            if self._stop:
                return
            try:
                data = earshot_client.poll_device(self.base_url, poll_token=poll_token)
            except CloudError as e:
                self.failed.emit(str(e))
                return
            except Exception as e:
                self.failed.emit(f"Sign-in check failed: {e}")
                return
            status = data.get("status")
            if status == "ok":
                self.linked.emit(data)
                return
            if status == "expired":
                self.failed.emit("That sign-in code expired or was declined. Try again.")
                return
            # "pending" — keep polling
        if not self._stop:
            self.failed.emit("Sign-in timed out. Please try again.")


class CloudLinkDialog(QDialog):
    """Modal device-link dialog. On success the caller's cfg is updated
    (account_mode="cloud", token + email saved) and on_linked() is invoked."""

    def __init__(self, parent, cfg, theme, *, on_linked=None):
        super().__init__(parent)
        self.cfg = cfg
        self.theme = theme
        # NB: must NOT be named _on_linked — that would shadow the method below,
        # so the worker's `linked` signal would connect to the callback (or None)
        # instead of the handler that saves the token. That exact bug shipped:
        # approval succeeded on the site and the app just sat there.
        self._linked_cb = on_linked
        self.worker: _LinkWorker | None = None
        self._verify_url = "https://tryearshot.app"
        self.linked_ok = False

        self.setWindowTitle("Sign in to Earshot Plus")
        self.setMinimumWidth(440)
        self._build()
        self._start_link()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(14)

        title = QLabel("Sign in to Earshot Plus")
        title.setObjectName("H2")
        v.addWidget(title)

        self.intro = QLabel(
            "We'll show you a short code. Open tryearshot.app, sign in, and enter the "
            "code to link this PC."
        )
        self.intro.setObjectName("Muted")
        self.intro.setWordWrap(True)
        v.addWidget(self.intro)

        name_row = QHBoxLayout()
        name_lbl = QLabel("Device name")
        name_lbl.setObjectName("Muted")
        name_row.addWidget(name_lbl)
        self.name_edit = QLineEdit(_device_name())
        self.name_edit.setToolTip("How this PC appears in your account's device list")
        name_row.addWidget(self.name_edit, 1)
        v.addLayout(name_row)

        self.code_lbl = QLabel("Contacting Earshot Plus…")
        self.code_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.code_lbl.setStyleSheet(
            f"font-size:34px; font-weight:800; letter-spacing:6px; color:{self.theme.color('primary')};"
            "padding:8px 0;"
        )
        v.addWidget(self.code_lbl)

        copy_row = QHBoxLayout()
        copy_row.addStretch(1)
        self.copy_btn = QPushButton("Copy code")
        self.copy_btn.setProperty("variant", "ghost")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_code)
        copy_row.addWidget(self.copy_btn)
        copy_row.addStretch(1)
        v.addLayout(copy_row)

        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("Faint")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setWordWrap(True)
        v.addWidget(self.status_lbl)

        btn_row = QHBoxLayout()
        self.open_btn = QPushButton("Open tryearshot.app")
        self.open_btn.setProperty("variant", "primary")
        self.open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_verify)
        btn_row.addWidget(self.open_btn)

        self.retry_btn = QPushButton("Retry")
        self.retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.retry_btn.setVisible(False)
        self.retry_btn.clicked.connect(self._start_link)
        btn_row.addWidget(self.retry_btn)

        btn_row.addStretch(1)
        self.close_btn = QPushButton("Cancel")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.close_btn)
        v.addLayout(btn_row)

    # ---------- linking ----------
    def _start_link(self) -> None:
        self._stop_worker()
        self.retry_btn.setVisible(False)
        self.open_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.code_lbl.setText("Contacting Earshot Plus…")
        self.status_lbl.setText("")
        self.worker = _LinkWorker(self.cfg.cloud_api_base, self.name_edit.text(), self)
        self.worker.code_ready.connect(self._on_code_ready)
        self.worker.linked.connect(self._on_linked)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_code_ready(self, code: str, verify_url: str) -> None:
        self._verify_url = verify_url or self._verify_url
        self.code_lbl.setText(code)
        self.open_btn.setEnabled(True)
        self.copy_btn.setEnabled(True)
        self.status_lbl.setText("Waiting for you to enter the code at tryearshot.app…")

    def _copy_code(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.code_lbl.text().strip())
        self.copy_btn.setText("Copied ✓")
        QTimer.singleShot(1600, lambda: self.copy_btn.setText("Copy code"))

    def _open_verify(self) -> None:
        try:
            webbrowser.open(self._verify_url)
        except Exception:
            pass

    def _on_linked(self, data: dict) -> None:
        token = data.get("device_token") or ""
        if not token:
            self._on_failed("Sign-in didn't return a valid token. Please try again.")
            return
        self.cfg.cloud_token = token
        self.cfg.cloud_email = data.get("email") or ""
        self.cfg.account_mode = "cloud"
        self.cfg.save()
        self.linked_ok = True
        self.status_lbl.setText("Signed in.")
        if self._linked_cb is not None:
            self._linked_cb(data)
        self.accept()

    def _on_failed(self, msg: str) -> None:
        self.code_lbl.setText("—")
        self.status_lbl.setStyleSheet(f"color:{self.theme.color('danger')};")
        self.status_lbl.setText(msg)
        self.open_btn.setEnabled(False)
        self.retry_btn.setVisible(True)

    def _stop_worker(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(2000)
            self.worker = None

    def reject(self) -> None:  # noqa: N802 (Qt override)
        self._stop_worker()
        super().reject()

    def closeEvent(self, event):  # noqa: N802 (Qt override)
        self._stop_worker()
        super().closeEvent(event)
