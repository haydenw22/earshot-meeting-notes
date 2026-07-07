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
    # exercise the new customisation surfaces
    cfg.templates = [{"name": "Sales call", "instructions": "Focus on objections."}]
    cfg.ai_actions = [{"name": "Follow-up email", "prompt": "Draft an email."}]
    cfg.show_dashboard = True
    tmp = tempfile.mkdtemp()
    repo = MeetingRepository(dbmod.connect(Path(tmp) / "ui.db"))
    m = repo.create(date_text="25th June 2026", date_iso="2026-06-25", attendees=["Hayden"],
                    template="Sales call")
    repo.update(m.id, title="Demo meeting", status="Done", duration_secs=120,
                transcript="[00:00] Me: hello there\n[00:02] Them: hi", notes_json=NOTES,
                bookmarks=[{"ms": 4000, "label": ""}, {"ms": 60000, "label": ""}])

    theme = ThemeController(cfg)
    theme.apply()
    shell = Shell(repo, cfg, theme)
    shell.resize(1100, 720)
    app.processEvents()

    shell.show_record(); app.processEvents()
    # offscreen never "shows" the window, so check the explicit hidden flag, not isVisible()
    assert not shell.record.template_box.isHidden(), "template selector should show when templates exist"
    assert shell.record.template_combo.count() >= 2, "template combo should list General + templates"

    shell.open_meeting(m.id); app.processEvents()
    assert not shell.detail.bookmarks_host.isHidden(), "bookmark chips should show"
    assert shell.detail.action_combo.count() >= 1, "AI action combo should be populated"
    shell.detail._jump_fraction(0.5); app.processEvents()      # bookmark jump
    shell.show_home(); app.processEvents()
    assert shell.home.cfg.show_dashboard       # dashboard enabled; pending "Write docs" renders

    # full-text search opens results in the main window (project page)
    shell.search.setText("objection")          # no body match — must not crash
    shell._run_search(); app.processEvents()
    shell.search.setText("hello")
    shell._run_search(); app.processEvents()
    assert shell.stack.currentWidget() is shell.project
    assert shell.project.mode == ("search", "hello")
    shell.search.setText("")
    shell._run_search(); app.processEvents()

    # settings save round-trips the new fields
    shell.show_settings(); app.processEvents()
    s = shell.settings
    s.dashboard_toggle.setChecked(False)
    s.ci_toggle.setChecked(True)
    s.ci_text.setPlainText("Use British English.")
    s._save(); app.processEvents()
    reloaded = Config.load()
    assert reloaded.show_dashboard is False
    assert reloaded.custom_instructions == "Use British English."

    # webhook lives on the Integrations pane inside Settings — same round-trip
    # check, new home
    shell.show_integrations(); app.processEvents()
    assert shell.stack.currentWidget() is shell.settings
    assert shell.settings._current_key == "integrations"
    shell.integrations.webhook_url.setText("https://example.com/hook")
    shell.integrations._save(); app.processEvents()
    reloaded2 = Config.load()
    assert reloaded2.webhook_url == "https://example.com/hook"

    # Account routes into Settings too, and the Help page shows
    shell.show_account(); app.processEvents()
    assert shell.stack.currentWidget() is shell.settings
    assert shell.settings._current_key == "account"
    shell.show_help(); app.processEvents()
    assert shell.stack.currentWidget() is shell.help

    theme.toggle(); app.processEvents()      # light -> dark, refresh all pages
    shell.show_home(); app.processEvents()
    assert theme.mode == "dark"

    repo.close()
    print("UI SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
