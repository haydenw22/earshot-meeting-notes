"""Keeps the cached Earshot Plus plan fresh and raises the trial prompt.

Fetches GET /v1/me shortly after launch and every few hours (cloud mode only),
caches it (cloud_plan.cache_me) for the sidebar and Settings, and shows
TrialDialog when the trial is ending, out of hours or over, at most once a day
per kind. A recording is never interrupted: the prompt waits until it stops.
blocked() is the fast path when a transcription was just refused for the
plan: re-check now and show the prompt straight away.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QMessageBox

from .. import cloud_plan


class TrialWatcher(QObject):
    FIRST_CHECK_MS = 4_000
    EVERY_MS = 3 * 60 * 60 * 1000
    RETRY_WHILE_RECORDING_MS = 5 * 60 * 1000

    def __init__(self, shell):
        super().__init__(shell)
        self.shell = shell
        self.cfg = shell.cfg
        self._worker = None
        self._dialog = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check)

    def start(self) -> None:
        if os.environ.get("QT_QPA_PLATFORM", "") == "offscreen":
            return  # tests / CI: no network, no modal
        QTimer.singleShot(self.FIRST_CHECK_MS, self.check)
        self._timer.start(self.EVERY_MS)

    # ---------- fetch ----------
    def check(self, *, force: bool = False, blocked_msg: str = "") -> None:
        """Refresh the plan snapshot, then maybe prompt. `force` skips the
        once-a-day limit (the person just hit the wall)."""
        if self.cfg.account_mode != "cloud" or not self.cfg.cloud_token:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        from .workers import FuncWorker
        base, token = self.cfg.cloud_api_base, self.cfg.cloud_token

        def job(_progress):
            from ..transcription import earshot_client
            return earshot_client.get_me(base, token)

        self._worker = FuncWorker(job)
        self._worker.done.connect(lambda data: self._on_me(data, force, blocked_msg))
        self._worker.failed.connect(lambda _msg: self._fallback(blocked_msg))
        self._worker.start()

    def blocked(self, message: str) -> None:
        """A cloud request was refused for the plan (trial over / allowance used
        / no subscription): show the upgrade prompt now, or the plain error if
        this account isn't on the free trial."""
        self.check(force=True, blocked_msg=message)

    def _on_me(self, data, force: bool, blocked_msg: str) -> None:
        if not isinstance(data, dict):
            self._fallback(blocked_msg)
            return
        cloud_plan.cache_me(self.cfg, data)
        self.cfg.save()
        if hasattr(self.shell, "refresh_plan_state"):
            self.shell.refresh_plan_state()
        kind = cloud_plan.prompt_kind(self.cfg)
        if kind is None:
            self._fallback(blocked_msg)
            return
        if force or cloud_plan.should_prompt(self.cfg, kind):
            self.show_prompt(kind)

    def _fallback(self, blocked_msg: str) -> None:
        if blocked_msg:  # not a trial account (or offline): the plain error box
            QMessageBox.critical(self.shell, "Processing failed", blocked_msg)

    # ---------- prompt ----------
    def show_prompt(self, kind: str | None = None, *, from_user: bool = False) -> None:
        """Open the upgrade dialog. `from_user` (the sidebar card, Settings)
        opens it right away; an automatic prompt waits out a recording."""
        kind = kind or cloud_plan.prompt_kind(self.cfg) or "ending"
        if self._dialog is not None and self._dialog.isVisible():
            return
        record = getattr(self.shell, "record", None)
        if (not from_user and record is not None and hasattr(record, "is_recording")
                and record.is_recording()):
            QTimer.singleShot(self.RETRY_WHILE_RECORDING_MS, lambda: self.show_prompt(kind))
            return
        from .trial_dialog import TrialDialog
        if not from_user:
            cloud_plan.mark_prompted(self.cfg, kind)
            self.cfg.save()
        self._dialog = TrialDialog(self.shell, self.cfg, self.shell.theme, self.shell.repo, kind,
                                   on_subscribed=lambda: QTimer.singleShot(1500, self.check))
        self._dialog.open()  # window-modal, non-blocking
