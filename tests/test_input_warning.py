"""The 'no input detected' warning on the record page: it fires only after the
grace period when a channel has produced no audio, names the right channel(s),
and clears the moment sound is seen (so normal pauses don't trip it).

Run:  QT_QPA_PLATFORM=offscreen python tests/test_input_warning.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from meeting_notes.config import Config  # noqa: E402
from meeting_notes.storage import db as dbmod  # noqa: E402
from meeting_notes.storage.repository import MeetingRepository  # noqa: E402
from meeting_notes.ui.page_record import RecordPage  # noqa: E402
from meeting_notes.ui.theme_controller import ThemeController  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


class FakeRecorder:
    def __init__(self, mic=0.0, them=0.0, elapsed=0.0, write_error=None):
        self.mic_level = mic
        self.them_level = them
        self.elapsed = elapsed
        self.running = True
        self.write_error = write_error


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841
    cfg = Config()
    repo = MeetingRepository(dbmod.connect(Path(tempfile.mkdtemp()) / "w.db"))
    theme = ThemeController(cfg)
    theme.apply()
    page = RecordPage(object(), repo, cfg, theme)  # shell unused by the bits we exercise

    def shown():
        return not page.input_warning.isHidden()

    def text():
        return page.input_warning_text.text().lower()

    print("== grace period ==")
    page._mic_seen = page._them_seen = False
    page._update_input_warning(5.0)
    check("hidden before grace period elapses", not shown())

    print("== both channels healthy ==")
    page._mic_seen = page._them_seen = True
    page._update_input_warning(30.0)
    check("hidden when both seen", not shown())

    print("== one channel silent ==")
    page._mic_seen, page._them_seen = False, True
    page._update_input_warning(30.0)
    check("shown when mic silent", shown())
    check("names the microphone", "microphone" in text() and "other side" not in text())

    page._mic_seen, page._them_seen = True, False
    page._update_input_warning(30.0)
    check("shown when their audio silent", shown())
    check("names the other side", "other side" in text() and "microphone" not in text())

    print("== both silent ==")
    page._mic_seen = page._them_seen = False
    page._update_input_warning(30.0)
    check("shown when both silent", shown())
    check("names both", "microphone" in text() and "other side" in text())

    print("== poll drives detection + auto-clear ==")
    page._mic_seen = page._them_seen = False
    page.recorder = FakeRecorder(mic=0.0, them=0.5, elapsed=15.0)
    page._on_poll()
    check("poll marks them active, mic still silent", page._them_seen and not page._mic_seen)
    check("poll surfaces mic warning", shown() and "microphone" in text())
    page.recorder = FakeRecorder(mic=0.5, them=0.5, elapsed=16.0)
    page._on_poll()
    check("warning auto-clears once mic produces sound", page._mic_seen and not shown())

    print("== sub-threshold noise floor stays 'silent' ==")
    page._mic_seen = page._them_seen = False
    page.recorder = FakeRecorder(mic=0.02, them=0.02, elapsed=15.0)
    page._on_poll()
    check("levels below the active threshold don't count", not page._mic_seen and not page._them_seen)
    check("warning shown for both", shown() and "microphone" in text() and "other side" in text())

    print("== spool write failure overrides the input warning (danger banner) ==")
    page._mic_seen = page._them_seen = True  # channels look healthy...
    page._write_error_reported = True        # (repo stamping tested in test_audit_fixes)
    page.recorder = FakeRecorder(mic=0.5, them=0.5, elapsed=30.0,
                                 write_error="your microphone (OSError: disk full)")
    page._on_poll()
    check("write failure shows the banner even with healthy levels",
          shown() and "recording problem" in text())
    check("banner names the failing side", "your microphone" in text())

    print("== _stop is a no-op when idle (stale call-ended toast can't crash it) ==")
    # Regression: a stale "call ended — Stop & process?" toast could fire _stop
    # after the user already stopped; with no recorder that raised in
    # meeting_dir(None) and left the button stuck disabled on "Processing…".
    page.recorder = None
    page.meeting_id = None
    page.record_btn.setEnabled(True)
    page.record_btn.setText("Start recording")
    page._stop()  # must not raise
    check("idle _stop leaves the record button enabled", page.record_btn.isEnabled())
    check("idle _stop keeps the Start label", "start" in page.record_btn.text().lower())

    repo.close()
    print("\nINPUT-WARNING TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
