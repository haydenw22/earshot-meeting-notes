"""When Cloud sync runs. One scheduler per app, owned by the Shell.

Triggers: shortly after startup (once signed in and enabled), ~5 s after any
meeting or folder change (the repository's change listener; debounced), every
10 minutes as a safety net, and the Account page's "Sync now".

Each run is integrations.cloud_sync.reconcile() on a plain daemon thread: never
on the UI thread, never two at once (a trigger during a run queues exactly one
follow-up), and never touching recording or transcription. Results come back to
the UI thread via a queued signal, which is the only place cfg is written.

Failure handling: 401 halts automatic syncing until the account changes or the
user presses Sync now; 402 waits an hour between tries; network/server errors
back off exponentially (1 min doubling to 30 min).
"""
from __future__ import annotations

import dataclasses
import logging
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

from ..integrations import cloud_sync

log = logging.getLogger(__name__)


class SyncScheduler(QObject):
    status_changed = Signal()            # cfg sync status changed / run started or ended
    _dirty_sig = Signal()                # emitted from any thread by the repo listener
    _finished_sig = Signal(str, object)  # (kind, SyncResult), from the worker thread

    STARTUP_MS = 3000
    DEBOUNCE_MS = 5000
    PERIOD_MS = 10 * 60 * 1000
    BACKOFF_START_S = 60
    BACKOFF_MAX_S = 30 * 60
    NEEDS_PLUS_WAIT_S = 60 * 60

    def __init__(self, repo, cfg, parent=None, *, transport=None):
        super().__init__(parent)
        self.repo = repo
        self.cfg = cfg
        self._transport = transport      # tests: an httpx.MockTransport
        self._thread: threading.Thread | None = None
        self._running = False
        self._pending: str | None = None  # "reconcile" | "delete_all"
        self._halted = False              # after a 401
        self._backoff_s = 0
        self._next_allowed = 0.0          # time.monotonic() before which auto runs wait
        self._closing = False

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self.DEBOUNCE_MS)
        self._debounce.timeout.connect(self.request_sync)

        self._periodic = QTimer(self)
        self._periodic.setInterval(self.PERIOD_MS)
        self._periodic.timeout.connect(self.request_sync)

        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self.request_sync)

        self._dirty_sig.connect(self._on_dirty)
        self._finished_sig.connect(self._on_finished)
        repo.add_change_listener(self._on_repo_changed)

    # ---------------------------------------------------------- lifecycle ----
    def start(self) -> None:
        self._periodic.start()
        if cloud_sync.sync_ready(self.cfg):
            QTimer.singleShot(self.STARTUP_MS, self.request_sync)

    def shutdown(self, timeout: float = 2.0) -> None:
        """Stop timers and give an in-flight run a moment to finish. The worker
        is a daemon thread, so a slow request can never hold up quitting."""
        self._closing = True
        for t in (self._debounce, self._periodic, self._retry):
            t.stop()
        self.repo.remove_change_listener(self._on_repo_changed)
        th = self._thread
        if th is not None and th.is_alive():
            th.join(timeout)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def halted(self) -> bool:
        return self._halted

    # ------------------------------------------------------------ triggers ----
    def _on_repo_changed(self) -> None:
        # any thread (recording / processing workers write the repo too): hop to
        # the UI thread via a queued signal before touching timers
        if self._closing:
            return
        try:
            self._dirty_sig.emit()
        except RuntimeError:
            pass  # scheduler already destroyed (app quitting)

    def _on_dirty(self) -> None:
        if self._closing or self._halted or not cloud_sync.sync_ready(self.cfg):
            return
        self._debounce.start()  # restart: a burst of edits = one sync

    def account_changed(self) -> None:
        """Signed in / out (or the sync toggle flipped): clear any halt and run
        soon if syncing is now possible."""
        self._halted = False
        self._backoff_s = 0
        self._next_allowed = 0.0
        self._retry.stop()
        if cloud_sync.sync_ready(self.cfg):
            self._debounce.start()
        else:
            self._debounce.stop()
        self.status_changed.emit()

    def sync_now(self) -> None:
        """The user's explicit "Sync now": ignores backoff and a 401 halt."""
        self._halted = False
        self._backoff_s = 0
        self._next_allowed = 0.0
        self._debounce.stop()
        self.request_sync()

    def request_sync(self) -> None:
        self._request("reconcile")

    def request_delete_all(self) -> None:
        """Remove every server copy of this library (sync was just turned off and
        the user said yes). Runs after any in-flight sync, and wins over a queued
        one."""
        self._debounce.stop()
        self._retry.stop()
        self._request("delete_all")

    def _request(self, kind: str) -> None:
        if self._closing:
            return
        if kind == "reconcile":
            if not cloud_sync.sync_ready(self.cfg) or self._halted:
                return
        if self._running:
            if self._pending != "delete_all":
                self._pending = kind
            return
        if kind == "reconcile":
            wait = self._next_allowed - time.monotonic()
            if wait > 0:
                if not self._retry.isActive():
                    self._retry.start(int(wait * 1000) + 50)
                return
        self._start(kind)

    # -------------------------------------------------------------- worker ----
    def _start(self, kind: str) -> None:
        if kind == "reconcile":
            # made on the UI thread so it's persisted exactly once
            if not (self.cfg.cloud_sync_library_id or "").strip():
                cloud_sync.ensure_library_id(self.cfg)
                self.cfg.save()
        snapshot = dataclasses.replace(self.cfg)  # the worker never reads live cfg
        snapshot.cloud_sync_skipped = dict(self.cfg.cloud_sync_skipped or {})
        repo, transport = self.repo, self._transport
        self._running = True
        self.status_changed.emit()

        def run() -> None:
            try:
                if kind == "delete_all":
                    res = cloud_sync.delete_all(snapshot, transport=transport)
                else:
                    res = cloud_sync.reconcile(repo, snapshot, transport=transport)
            except Exception as e:  # reconcile already catches; belt and braces
                log.exception("cloud sync worker crashed")
                res = cloud_sync.SyncResult(error=f"Sync failed: {e}")
            try:
                self._finished_sig.emit(kind, res)
            except RuntimeError:
                pass  # app quit mid-run: nothing left to update

        self._thread = threading.Thread(target=run, name=f"earshot-sync-{kind}", daemon=True)
        self._thread.start()

    def _on_finished(self, kind: str, res) -> None:
        self._running = False
        if self._closing:
            return
        if kind == "delete_all":
            if res.ok:
                self.cfg.cloud_sync_count = 0
                self.cfg.cloud_sync_last_ok = ""
                self.cfg.cloud_sync_last_error = ""
                self.cfg.cloud_sync_skipped = {}
            else:
                self.cfg.cloud_sync_last_error = (
                    f"Couldn't remove the copies from your account: {res.error}")
            self.cfg.save()
        else:
            cloud_sync.apply_result(self.cfg, res)
            self.cfg.save()
            self._after_reconcile(res)
        self.status_changed.emit()
        pending, self._pending = self._pending, None
        if pending:
            self._request(pending)

    def _after_reconcile(self, res) -> None:
        if res.ok:
            self._backoff_s = 0
            self._next_allowed = 0.0
            return
        if res.auth_failed:
            self._halted = True
            self._pending = None
            log.info("cloud sync halted: device signed out")
            return
        if res.needs_plus:
            wait = self.NEEDS_PLUS_WAIT_S
        elif not cloud_sync.sync_ready(self.cfg):
            return
        else:
            self._backoff_s = min(self.BACKOFF_MAX_S,
                                  (self._backoff_s * 2) or self.BACKOFF_START_S)
            wait = self._backoff_s
        self._next_allowed = time.monotonic() + wait
        self._retry.start(int(wait * 1000))

    def status_text(self) -> str:
        return cloud_sync.status_line(self.cfg, running=self._running)
