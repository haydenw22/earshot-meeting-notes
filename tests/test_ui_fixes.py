"""Regression tests for the Phase S1 fixes: action-item inline edit (missing
QLineEdit import), the detail-page "More" menu, the sidebar double-highlight
bug, **bold** markdown rendering in copy/share, and the Home to-do dashboard
(collapsible + mark-all-done + clear-all).

Run:  QT_QPA_PLATFORM=offscreen python tests/test_ui_fixes.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Isolate ALL app data to a throwaway dir so the test can never read or overwrite
# the real %LOCALAPPDATA%\Earshot\config.json (which would wipe the user's key/URL).
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from meeting_notes.config import Config  # noqa: E402
from meeting_notes.notes import render, share  # noqa: E402
from meeting_notes.storage import db as dbmod  # noqa: E402
from meeting_notes.storage.repository import MeetingRepository  # noqa: E402
from meeting_notes.ui.page_detail import DetailPage  # noqa: E402
from meeting_notes.ui.page_home import HomePage  # noqa: E402
from meeting_notes.ui.theme_controller import ThemeController  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


class _Shell:
    def show_home(self):
        pass

    def show_record(self):
        pass

    def open_meeting(self, _mid):
        pass

    def notify_data_changed(self):
        self.notified = True


NOTES_ONE_CONFIRMED = {
    "title": "M", "summary": "s", "attendees": [],
    "action_items": [
        {"task": "Close the deal", "owner": "Sam", "done": False, "confirmed": True},
    ],
    "sections": [],
}


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841
    theme = ThemeController(Config())
    theme.apply()

    # ---------------------------------------------------------------
    print("== action-item inline edit (regression: missing QLineEdit import) ==")
    repo = MeetingRepository(dbmod.connect(Path(tempfile.mkdtemp()) / "a.db"))
    shell = _Shell()
    m = repo.create(date_text="d", date_iso="2026-07-02", attendees=[])
    repo.update(m.id, title="M", status="Done", notes_json=json.dumps(NOTES_ONE_CONFIRMED))

    page = DetailPage(shell, repo, Config(), theme)
    page.load(m.id)
    row = page._action_row(0, dict(NOTES_ONE_CONFIRMED["action_items"][0]))
    # Before the fix, _begin_action_edit raised NameError (QLineEdit undefined)
    # after already clearing the row — leaving it empty ("the item disappears").
    page._begin_action_edit(row, 0, dict(NOTES_ONE_CONFIRMED["action_items"][0]))
    line_edits = row.findChildren(QLineEdit)
    check("edit row now contains 2 QLineEdits (task + owner)", len(line_edits) == 2)

    print("== _apply_action_edit still round-trips task+owner ==")
    page._apply_action_edit(0, "Close the deal — reworded", "Hayden")
    items = repo.get(m.id).notes["action_items"]
    check("edit updates task + owner",
          items[0]["task"] == "Close the deal — reworded" and items[0]["owner"] == "Hayden")

    # ---------------------------------------------------------------
    print("== More menu actions ==")
    check("act_resummarise exists", hasattr(page, "act_resummarise"))
    check("act_reprocess exists", hasattr(page, "act_reprocess"))
    check("act_folder exists", hasattr(page, "act_folder"))

    m2 = repo.create(date_text="d", date_iso="2026-07-02", attendees=[])
    audio_dir = tempfile.mkdtemp()
    repo.update(m2.id, title="With transcript+audio", transcript="hello there",
                audio_dir=audio_dir, status="Done")
    page.load(m2.id)
    check("resummarise enabled with transcript", page.act_resummarise.isEnabled())
    check("reprocess enabled with audio_dir", page.act_reprocess.isEnabled())
    check("folder enabled with audio_dir", page.act_folder.isEnabled())

    m3 = repo.create(date_text="d", date_iso="2026-07-02", attendees=[])
    repo.update(m3.id, title="Bare meeting", status="New")
    page.load(m3.id)
    page.refresh()
    check("resummarise disabled with no transcript", not page.act_resummarise.isEnabled())
    check("reprocess disabled with no audio_dir", not page.act_reprocess.isEnabled())
    check("folder disabled with no audio_dir", not page.act_folder.isEnabled())

    # ---------------------------------------------------------------
    print("== shell nav: Home clears the sidebar list selection ==")
    from meeting_notes.ui.shell import Shell

    shell_repo = MeetingRepository(dbmod.connect(Path(tempfile.mkdtemp()) / "s.db"))
    sm = shell_repo.create(date_text="d", date_iso="2026-07-02", attendees=[])
    shell_repo.update(sm.id, title="Nav test", status="Done")
    real_shell = Shell(shell_repo, Config(), theme)
    # A real click both selects the row (Qt's built-in click behaviour) and fires
    # itemClicked -> _on_list_click -> open_meeting; simulate the selection side
    # effect explicitly since open_meeting() alone doesn't touch selection.
    real_shell.meeting_list.setCurrentRow(0)
    real_shell.open_meeting(sm.id)
    app.processEvents()
    check("a row is selected after open_meeting", len(real_shell.meeting_list.selectedItems()) >= 1)
    real_shell.show_home()
    app.processEvents()
    check("no row selected after show_home", real_shell.meeting_list.selectedItems() == [])
    shell_repo.close()

    # ---------------------------------------------------------------
    print("== markdown: **bold** renders as <b>, no literal ** ==")
    md_notes = {
        "title": "M", "summary": "s", "attendees": [],
        "action_items": [{"task": "Close **$25k**", "owner": None, "done": False, "confirmed": True}],
        "sections": [],
    }
    html_out = render.to_html(md_notes)
    check("render.to_html bolds the task", "<b>$25k</b>" in html_out)
    check("render.to_html has no literal **", "**" not in html_out)

    mmd = repo.create(date_text="d", date_iso="2026-07-02", attendees=[])
    repo.update(mmd.id, title="M", status="Done", notes_json=json.dumps(md_notes))
    share_out = share.to_share_html(repo.get(mmd.id))
    check("share.to_share_html bolds the task", "<b>$25k</b>" in share_out)
    check("share.to_share_html has no literal **", "**" not in share_out)

    # ---------------------------------------------------------------
    print("== dashboard: collapsed persists, mark-all-done, clear-all ==")
    cfg = Config()
    check("dashboard_collapsed defaults False", cfg.dashboard_collapsed is False)
    cfg.dashboard_collapsed = True
    cfg.save()
    reloaded = Config.load()
    check("dashboard_collapsed round-trips via Config.load", reloaded.dashboard_collapsed is True)

    dash_repo = MeetingRepository(dbmod.connect(Path(tempfile.mkdtemp()) / "d.db"))
    dcfg = Config()
    da = dash_repo.create(date_text="d", date_iso="2026-07-02", attendees=[])
    dash_repo.update(da.id, title="A", status="Done", notes_json=json.dumps({
        "title": "A", "summary": "", "attendees": [],
        "action_items": [
            {"task": "Task A1", "owner": None, "done": False, "confirmed": True},
            {"task": "Task A2", "owner": None, "done": False, "confirmed": True},
        ],
        "sections": [],
    }))
    db_ = dash_repo.create(date_text="d", date_iso="2026-07-02", attendees=[])
    dash_repo.update(db_.id, title="B", status="Done", notes_json=json.dumps({
        "title": "B", "summary": "", "attendees": [],
        "action_items": [
            {"task": "Task B1", "owner": None, "done": False, "confirmed": True},
        ],
        "sections": [],
    }))

    home = HomePage(shell, dash_repo, dcfg, theme)
    home.refresh()
    pending = home._gather_pending(dash_repo.list())
    check("3 pending items gathered", len(pending) == 3)

    home._dash_pending = pending
    home._mark_all_done()
    app.processEvents()
    notes_a = dash_repo.get(da.id).notes["action_items"]
    notes_b = dash_repo.get(db_.id).notes["action_items"]
    check("mark-all-done: meeting A items all done", all(i["done"] for i in notes_a))
    check("mark-all-done: meeting B item done", all(i["done"] for i in notes_b))

    # reset to open items, then exercise clear-all (bypassing the confirm dialog
    # by calling the private worker method directly, per the spec)
    dash_repo.update(da.id, notes_json=json.dumps({
        "title": "A", "summary": "", "attendees": [],
        "action_items": [
            {"task": "Task A1", "owner": None, "done": False, "confirmed": True},
            {"task": "Task A2", "owner": None, "done": False, "confirmed": True},
        ],
        "sections": [],
    }))
    dash_repo.update(db_.id, notes_json=json.dumps({
        "title": "B", "summary": "", "attendees": [],
        "action_items": [
            {"task": "Task B1", "owner": None, "done": False, "confirmed": True},
        ],
        "sections": [],
    }))
    home.refresh()
    pending2 = home._gather_pending(dash_repo.list())
    home._dash_pending = pending2
    home._clear_all_pending()
    app.processEvents()
    check("clear-all: meeting A has no action items left",
          (dash_repo.get(da.id).notes.get("action_items") or []) == [])
    check("clear-all: meeting B has no action items left",
          (dash_repo.get(db_.id).notes.get("action_items") or []) == [])

    dash_repo.close()
    repo.close()
    print("\nUI FIXES TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
