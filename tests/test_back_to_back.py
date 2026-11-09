"""Back-to-back meetings: the next recording can start while the last one is
still being transcribed, and the two never touch each other's state.

Before 0.37.0 the record page stayed on "Processing…" until transcription and
notes finished, yet reopening the page re-enabled the button. Starting meeting
B then let A's completion reset the page under B: B kept recording but every
Stop did nothing, so the only way out was quitting Earshot. Attendees typed
for B were also written onto A while A processed.

Run:  QT_QPA_PLATFORM=cocoa python tests/test_back_to_back.py   (macOS)
      QT_QPA_PLATFORM=offscreen python tests/test_back_to_back.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from meeting_notes.audio import writer  # noqa: E402
from meeting_notes.audio.devices import AudioDevice  # noqa: E402
from meeting_notes.config import Config  # noqa: E402
from meeting_notes.pipeline import processing  # noqa: E402
from meeting_notes.storage import db as dbmod  # noqa: E402
from meeting_notes.storage.repository import MeetingRepository  # noqa: E402
from meeting_notes.ui import page_record  # noqa: E402
from meeting_notes.ui.theme_controller import ThemeController  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


class _Shell:
    def __init__(self):
        self.opened = []

    def set_recording(self, _active):
        return None

    def notify_data_changed(self):
        return None

    def open_meeting(self, mid):
        self.opened.append(mid)


class _Recorder:
    made: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.running = False
        self.started_at = time.monotonic()
        self.mic_level = self.them_level = 0.0
        self.write_error = None
        self.capture_warning = None
        _Recorder.made.append(self)

    @property
    def elapsed(self):
        return 1.0

    def start(self):
        self.running = True

    def stop(self):
        self.running = False
        return types.SimpleNamespace(duration_secs=42.0)


class _Boxes:
    def __init__(self):
        self.shown = []

    def warning(self, _parent, title, text):
        self.shown.append(title)

    def critical(self, _parent, title, text):
        self.shown.append(title)


def wait_for(app, cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    app.processEvents()
    return cond()


def main() -> int:
    app = QApplication.instance() or QApplication([])
    cfg = Config()
    cfg.overlay_enabled = False
    cfg.capture_screen = False
    cfg.auto_transcribe = True
    repo = MeetingRepository(dbmod.connect(Path(tempfile.mkdtemp()) / "b.db"))
    theme = ThemeController(cfg)
    theme.apply()
    shell = _Shell()
    page = page_record.RecordPage(shell, repo, cfg, theme)
    page.capture_screen.setChecked(False)

    mics = [AudioDevice(0, "Haydens iphone Microphone", 1, 48000, False),
            AudioDevice(1, "MacBook Pro Microphone", 1, 48000, False, True)]
    them = AudioDevice(-1, "System audio", 2, 48000, True, True)
    release = {}  # meeting id -> Event that lets its fake transcription finish

    def slow_processing(repo_, mid, cfg_, progress=None, summarize=True):
        repo_.update(mid, status="Transcribing")
        release.setdefault(mid, threading.Event()).wait(30)
        repo_.update(mid, status="Done")

    saved = (page_record.dev.list_input_devices, page_record.dev.list_loopback_devices,
             page_record.DualStreamRecorder, page_record.ensure_capture_permissions,
             page_record.QMessageBox, writer.finalize_recording, processing.process_recording,
             page_record.today_pair)
    boxes = _Boxes()
    page_record.dev.list_input_devices = lambda: list(mics)
    page_record.dev.list_loopback_devices = lambda: [them]
    page_record.DualStreamRecorder = _Recorder
    page_record.ensure_capture_permissions = lambda *_a: None
    page_record.QMessageBox = boxes
    writer.finalize_recording = lambda spool, audio_dir: None
    processing.process_recording = slow_processing
    try:
        print("== the mic picker falls back to the system default ==")
        cfg.mic_device_name = "AirPods Pro"  # saved, but in its case right now
        page.on_shown()
        check("default mic chosen, not whatever is listed first",
              page.mic_combo.currentData().name == "MacBook Pro Microphone")

        print("== meeting A ==")
        page.attendees.setText("Alice")
        page_record.today_pair = lambda: ("10th November 2026", "2026-11-10")
        page._start()
        a = page.meeting_id
        check("A is recording", a is not None and page.recorder.running)
        check("A is dated when it starts, not when the page opened",
              repo.get(a).date_iso == "2026-11-10")
        page._start()
        check("a second start (e.g. the call prompt) can't replace a live recording",
              len(_Recorder.made) == 1 and page.meeting_id == a)

        page._toggle()  # stop A
        check("stopping A clears the form for the next meeting",
              page.attendees.text() == "" and page.meeting_id is None)
        check("A keeps its own attendees", repo.get(a).attendees == ["Alice"])
        check("A's audio is saved quickly", wait_for(app, lambda: not page._saving))
        check("the page is ready for the next meeting",
              page.record_btn.isEnabled() and page.record_btn.text() == "Start recording")
        # the background worker flips the status on its own thread; on a slow
        # CI runner that can land a moment after the save completes
        check("A transcribes in the background",
              wait_for(app, lambda: repo.get(a).status == "Transcribing"))
        check("a new call can be offered while A transcribes", not page.is_recording())
        check("but quitting still waits for A", page.is_busy())

        print("== meeting B while A is still transcribing ==")
        page.on_shown()  # e.g. clicking New recording in the sidebar
        page.attendees.setText("Bob, Carol")
        page._persist_attendees()  # editingFinished
        check("B's attendees never land on A", repo.get(a).attendees == ["Alice"])
        page._toggle()  # start B
        b = page.meeting_id
        check("B is recording", b not in (None, a) and page.recorder.running)

        release.setdefault(a, threading.Event()).set()  # A's notes finish mid-B
        check("A finishes", wait_for(app, lambda: repo.get(a).status == "Done" and not page._processing.get(a)))
        check("finishing A doesn't navigate away from B's recording", shell.opened == [])
        check("finishing A doesn't detach the page from B",
              page.meeting_id == b and page.recorder is not None and page.recorder.running)
        check("B's form is untouched", page.attendees.text() == "Bob, Carol")

        page._toggle()  # stop B: this did nothing before the fix
        check("B can still be stopped", page.meeting_id is None and page._saving)
        check("B keeps its attendees", repo.get(b).attendees == ["Bob", "Carol"])
        check("B's audio is saved", wait_for(app, lambda: not page._saving)
              and not _Recorder.made[-1].running and repo.get(b).status != "Recording")
        release.setdefault(b, threading.Event()).set()
        check("B's notes finish", wait_for(app, lambda: repo.get(b).status == "Done"
                                            and not page._processing.get(b)))
        check("with nothing else going on, the finished meeting opens", shell.opened == [b])
        check("no error boxes along the way", boxes.shown == [])
    finally:
        for ev in release.values():
            ev.set()
        page.poll.stop()
        wait_for(app, lambda: not page.is_busy(), timeout=10)  # before un-patching
        (page_record.dev.list_input_devices, page_record.dev.list_loopback_devices,
         page_record.DualStreamRecorder, page_record.ensure_capture_permissions,
         page_record.QMessageBox, writer.finalize_recording, processing.process_recording,
         page_record.today_pair) = saved
    print("\nBACK-TO-BACK TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
