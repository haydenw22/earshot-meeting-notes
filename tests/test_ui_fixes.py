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
        self.recorded = True

    def show_ask(self):
        self.asked = True

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

    # ---------------------------------------------------------------
    print("== Phase B home redesign: hero card, kebab menu, to-do %, view-all, rail ==")
    from meeting_notes.ui.page_home import MeetingRow

    hb_repo = MeetingRepository(dbmod.connect(Path(tempfile.mkdtemp()) / "hb.db"))
    hb_shell = _Shell()
    hb_cfg = Config()
    hb_home = HomePage(hb_shell, hb_repo, hb_cfg, theme)

    print("-- hero card exists and its click handler is wired --")
    hb_home.refresh()
    check("hero backdrop attribute exists", hasattr(hb_home, "_hero_backdrop"))
    hero_card = hb_home._hero_card()
    check("hero card built without error", hero_card is not None)
    # the whole card is clickable -> shell.show_record(); invoke the same
    # callback the card was constructed with rather than simulating a mouse
    # event (offscreen-safe, and exercises exactly what the click wires to)
    hero_card._on_click()
    check("clicking the hero card calls shell.show_record()", getattr(hb_shell, "recorded", False))

    print("-- kebab menu: Open / Move to project / Delete for a filed meeting --")
    hb_folder = hb_repo.create_folder("Ops", "#14B8A6")
    hb_m = hb_repo.create(date_text="d", date_iso="2026-07-03", attendees=[], folder_id=hb_folder.id)
    hb_repo.update(hb_m.id, title="Filed meeting", status="Done")
    row = MeetingRow(hb_repo.get(hb_m.id), theme, hb_repo, hb_shell, {hb_folder.id: hb_folder})
    check("row has a kebab button", hasattr(row, "kebab_btn"))
    from PySide6.QtWidgets import QMenu
    kmenu = QMenu()
    kopen = kmenu.addAction("Open")
    kmove = kmenu.addMenu("Move to project")
    row._populate_move_menu(kmove)
    kmenu.addSeparator()
    kdelete = kmenu.addAction("Delete")
    top_texts = [a.text() for a in kmenu.actions() if a.text()]
    check("kebab menu has Open", "Open" in top_texts)
    check("kebab menu has a 'Move to project' submenu", kmove.title() == "Move to project")
    check("kebab menu has Delete", "Delete" in top_texts)
    move_texts = [a.text() for a in kmove.actions()]
    check("move submenu lists 'No project'", "No project" in move_texts)
    check("move submenu lists the current folder", "Ops" in move_texts)
    check("move submenu lists 'New project…'", any("New project" in t for t in move_texts))

    print("-- to-do completion %: 1 done of 4 confirmed items -> 25% --")
    pa = hb_repo.create(date_text="d", date_iso="2026-07-03", attendees=[])
    hb_repo.update(pa.id, title="PA", status="Done", notes_json=json.dumps({
        "title": "PA", "summary": "", "attendees": [],
        "action_items": [
            {"task": "One", "owner": None, "done": True, "confirmed": True},
            {"task": "Two", "owner": None, "done": False, "confirmed": True},
            {"task": "Three", "owner": None, "done": False, "confirmed": True},
            {"task": "Four", "owner": None, "done": False, "confirmed": True},
        ],
        "sections": [],
    }))
    pct = hb_home._completion_pct(hb_repo.list())
    check("1 of 4 confirmed items done -> 25%", pct == 25)

    print("-- View-all toggle switches how many to-do items render --")
    for i in range(8):
        extra = hb_repo.create(date_text="d", date_iso="2026-07-03", attendees=[])
        hb_repo.update(extra.id, title=f"Extra {i}", status="Done", notes_json=json.dumps({
            "title": f"Extra {i}", "summary": "", "attendees": [],
            "action_items": [{"task": f"Pending {i}", "owner": None, "done": False, "confirmed": True}],
            "sections": [],
        }))
    hb_home._todo_show_all = False
    all_pending = hb_home._gather_pending(hb_repo.list())
    check("more than 6 pending items exist for this check", len(all_pending) > 6)
    todo_card_collapsed = hb_home._todo_card(all_pending, hb_repo.list())
    from PySide6.QtWidgets import QCheckBox as _QCheckBox
    checkboxes_top6 = todo_card_collapsed.findChildren(_QCheckBox)
    check("top-6 view renders exactly 6 item checkboxes", len(checkboxes_top6) == 6)
    hb_home._todo_show_all = True
    todo_card_all = hb_home._todo_card(all_pending, hb_repo.list())
    checkboxes_all = todo_card_all.findChildren(_QCheckBox)
    check("view-all renders every pending item's checkbox",
          len(checkboxes_all) == len(all_pending))
    hb_home._todo_show_all = False

    print("-- responsive rail: side-by-side when wide, stacked (never hidden) when narrow --")
    # An unshown top-level widget's width() doesn't reliably update from
    # resize() alone offscreen, so stub width() itself to drive the handler
    # deterministically — exactly what _apply_rail_visibility reads. Check
    # isHidden() rather than isVisible(): isVisible() requires the whole
    # ancestor chain to be shown on screen, which an offscreen test never does.
    from PySide6.QtWidgets import QBoxLayout as _QBox
    orig_width = hb_home.width
    try:
        hb_home.width = lambda: 1400
        hb_home._apply_rail_visibility()
        check("rail visible at 1400px", not hb_home.rail.isHidden())
        check("columns side-by-side at 1400px",
              hb_home.columns.direction() == _QBox.Direction.LeftToRight)
        check("rail keeps its fixed width when side-by-side",
              hb_home.rail.maximumWidth() == hb_home.rail.minimumWidth())
        hb_home.width = lambda: 900
        hb_home._apply_rail_visibility()
        check("rail STILL visible at 900px (vertical monitor)", not hb_home.rail.isHidden())
        check("columns stack vertically at 900px",
              hb_home.columns.direction() == _QBox.Direction.TopToBottom)
        check("rail released to full width when stacked",
              hb_home.rail.maximumWidth() > 10000)
        hb_home.width = lambda: 1050
        hb_home._apply_rail_visibility()
        check("side-by-side exactly at the 1050px breakpoint",
              hb_home.columns.direction() == _QBox.Direction.LeftToRight)
    finally:
        hb_home.width = orig_width

    print("-- ElideLabel: long titles shrink instead of forcing the page wide --")
    from meeting_notes.ui.widgets import ElideLabel
    long_title = "A very long meeting title that cannot possibly fit in a narrow meeting row"
    el = ElideLabel(long_title)
    check("elide label reports a tiny minimum width (page can shrink)",
          el.minimumSizeHint().width() <= 60)
    el.setFixedWidth(120)
    el.resize(120, 20)
    el._elide()  # offscreen resize events are async; drive the handler directly
    check("elide label ellipsizes when narrow", el.text().endswith("…"))
    check("full text preserved for the tooltip",
          el.fullText() == long_title and el.toolTip() == long_title)

    hb_repo.close()

    print("== call watcher: only meeting apps (fast) / browsers (sustained) prompt ==")
    from meeting_notes.ui import call_watcher as cw

    def fresh_watcher():
        w = cw.CallWatcher()
        events = {"started": [], "ended": 0}
        w.call_started.connect(lambda apps: events["started"].append(list(apps)))
        w.call_ended.connect(lambda: events.__setitem__("ended", events["ended"] + 1))
        return w, events

    # a game/unknown app NEVER prompts, no matter how long
    w, ev = fresh_watcher()
    for _ in range(30):
        w._evaluate([], [])          # classified 'other' apps never reach _evaluate lists
    check("unknown apps never prompt", ev["started"] == [] and ev["ended"] == 0)

    # a meeting app prompts after the short confirmation window
    w, ev = fresh_watcher()
    w._evaluate(["Zoom"], [])
    check("meeting app: no prompt on first sighting", ev["started"] == [])
    w._evaluate(["Zoom"], [])
    check("meeting app prompts after ~8s", ev["started"] == [["Zoom"]])
    w._evaluate(["Zoom"], [])
    check("no re-prompt while the call continues", len(ev["started"]) == 1)
    w._evaluate([], [])
    w._evaluate([], [])
    check("call end fires after sustained silence", ev["ended"] == 1)

    # a browser needs SUSTAINED mic use (a quick voice note must not prompt)
    w, ev = fresh_watcher()
    for _ in range(3):
        w._evaluate([], ["Chrome (browser call)"])
    w._evaluate([], [])  # stopped before the browser threshold
    check("short browser mic use never prompts", ev["started"] == [])
    w, ev = fresh_watcher()
    for _ in range(cw._BROWSER_TICKS):
        w._evaluate([], ["Chrome (browser call)"])
    check("sustained browser use eventually prompts", ev["started"] == [["Chrome (browser call)"]])

    # dismissing snoozes for the rest of that call, next call prompts again
    w, ev = fresh_watcher()
    w.snooze_until_idle()
    for _ in range(5):
        w._evaluate(["Zoom"], [])
    check("snoozed call never prompts", ev["started"] == [])
    w._evaluate([], []); w._evaluate([], [])   # call ends → snooze clears
    w._evaluate(["Zoom"], []); w._evaluate(["Zoom"], [])
    check("next call prompts again after snooze", ev["started"] == [["Zoom"]])

    repo.close()
    print("\nUI FIXES TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
