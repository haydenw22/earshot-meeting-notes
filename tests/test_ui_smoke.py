"""Construct the shell + every page under the offscreen Qt platform, navigate
between them, and toggle the theme — catching API/wiring errors without showing
a window.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_ui_smoke.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Isolate ALL app data to a throwaway dir so the test can never read or overwrite
# the real %LOCALAPPDATA%\Earshot\config.json (which would wipe the user's key/URL).
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from meeting_notes.config import Config  # noqa: E402
from meeting_notes.storage import db as dbmod  # noqa: E402
from meeting_notes.storage.repository import MeetingRepository  # noqa: E402
from meeting_notes.ui.shell import Shell  # noqa: E402
from meeting_notes.ui.theme_controller import ThemeController  # noqa: E402

NOTES = (
    '{"title":"Demo","summary":"A demo meeting.","attendees":["Hayden"],'
    '"action_items":[{"task":"Write docs","owner":"Hayden","done":false},'
    '{"task":"Ship it","owner":null,"done":true}],'
    '"sections":[{"heading":"Topic","bullets":["A **key** point","Another point"]}]}'
)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    cfg = Config()
    tmp = tempfile.mkdtemp()
    repo = MeetingRepository(dbmod.connect(Path(tmp) / "ui.db"))
    m = repo.create(date_text="25th June 2026", date_iso="2026-06-25", attendees=["Hayden"])
    repo.update(m.id, title="Demo meeting", status="Done",
                transcript="[00:00] Me: hello\n[00:02] Them: hi", notes_json=NOTES)

    theme = ThemeController(cfg)
    theme.apply()
    shell = Shell(repo, cfg, theme)
    shell.resize(1100, 720)
    app.processEvents()

    shell.show_record(); app.processEvents()
    shell.open_meeting(m.id); app.processEvents()
    shell.show_settings(); app.processEvents()
    theme.toggle(); app.processEvents()      # light -> dark, refresh all pages
    shell.show_home(); app.processEvents()
    assert theme.mode == "dark"

    repo.close()
    print("UI SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
