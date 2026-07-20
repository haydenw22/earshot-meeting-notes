"""Tests for Phase C: due dates on action items — model validator tolerance,
tool-schema exposure, due_label/due_severity truth table (incl. Windows %#d
formatting), the suggestion→confirmed pipeline preserving due, inline-edit
persistence, the home dashboard's _set_due, Todoist due_date wiring, and
render/share showing the due label.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_due_dates.py
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Isolate ALL app data to a throwaway dir so the test can never read or overwrite
# the real %LOCALAPPDATA%\Earshot\config.json.
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from meeting_notes.config import Config  # noqa: E402
from meeting_notes.integrations import todoist  # noqa: E402
from meeting_notes.notes import anthropic_client, render, service as notes_service, share  # noqa: E402
from meeting_notes.notes.schema import ActionItem, MeetingNotes, notes_tool_schema  # noqa: E402
from meeting_notes.storage import db as dbmod  # noqa: E402
from meeting_notes.storage.repository import MeetingRepository  # noqa: E402
from meeting_notes.ui.page_detail import DetailPage  # noqa: E402
from meeting_notes.ui.page_home import HomePage  # noqa: E402
from meeting_notes.ui.theme_controller import ThemeController  # noqa: E402
from meeting_notes.util.dues import due_label, due_severity, parse_due  # noqa: E402


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


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841

    print("== ActionItem.due validator: tolerant, never raises ==")
    check("good ISO kept", ActionItem(task="x", due="2026-07-10").due == "2026-07-10")
    check("junk -> None", ActionItem(task="x", due="whenever").due is None)
    check("empty string -> None", ActionItem(task="x", due="").due is None)
    check("whitespace -> None", ActionItem(task="x", due="   ").due is None)
    check("None stays None", ActionItem(task="x", due=None).due is None)
    check("non-string -> None (never raises)", ActionItem(task="x", due=12345).due is None)
    check("almost-ISO junk -> None", ActionItem(task="x", due="2026-13-40").due is None)
    check("default is None", ActionItem(task="x").due is None)

    print("== tool schema exposes 'due' but never 'confirmed' ==")
    schema = notes_tool_schema()
    item_props = schema["properties"]["action_items"]["items"]["properties"]
    check("schema has 'due'", "due" in item_props)
    check("due allows null", "null" in item_props["due"]["type"])
    check("'confirmed' NOT exposed to the model", "confirmed" not in json.dumps(schema))
    check("ACTION ITEMS prompt bullet mentions due date",
          "due" in anthropic_client.SYSTEM_PROMPT and "meeting date" in anthropic_client.SYSTEM_PROMPT)

    print("== due parsing/label/severity truth table ==")
    today = _dt.date(2026, 7, 3)
    check("parse_due: bad input -> None", parse_due("nope") is None)
    check("parse_due: None -> None", parse_due(None) is None)
    check("parse_due: good ISO -> date", parse_due("2026-07-03") == today)

    check("label: overdue", due_label("2026-07-02", today) == "Overdue")
    check("label: today", due_label("2026-07-03", today) == "Today")
    check("label: tomorrow", due_label("2026-07-04", today) == "Tomorrow")
    # Windows strftime "%#d %b" — no leading zero on single-digit days
    check("label: future single-digit day formats without leading zero",
          due_label("2026-07-09", today) == "9 Jul")
    check("label: future double-digit day", due_label("2026-07-25", today) == "25 Jul")
    check("label: unparseable -> empty string", due_label("garbage", today) == "")
    check("label: None -> empty string", due_label(None, today) == "")

    check("severity: overdue", due_severity("2026-07-02", today) == "overdue")
    check("severity: today", due_severity("2026-07-03", today) == "today")
    check("severity: future", due_severity("2026-07-04", today) == "future")
    check("severity: none for unparseable", due_severity("garbage", today) == "none")
    check("severity: none for missing", due_severity(None, today) == "none")

    print("== suggested item keeps its due through service's confirmed=False step ==")
    fake = MeetingNotes(
        title="T", summary="S", attendees=[],
        action_items=[ActionItem(task="Send report", due="2026-07-10")],
        sections=[],
    )
    orig = anthropic_client.generate_notes
    anthropic_client.generate_notes = lambda *a, **k: fake
    try:
        cfg = Config()
        cfg.notes_provider = "anthropic"
        cfg.anthropic_api_key = "sk-x"
        out = notes_service.generate_notes("t", cfg)
        check("item is unconfirmed (suggestion)", out.action_items[0].confirmed is False)
        check("due survives the confirmed=False post-step", out.action_items[0].due == "2026-07-10")
    finally:
        anthropic_client.generate_notes = orig

    print("== detail page: _apply_action_edit persists due ==")
    theme = ThemeController(Config())
    theme.apply()
    shell = _Shell()
    repo = MeetingRepository(dbmod.connect(Path(tempfile.mkdtemp()) / "a.db"))
    notes = {
        "title": "M", "summary": "s", "attendees": [],
        "action_items": [
            {"task": "Ship it", "owner": "Sam", "done": False, "confirmed": True, "due": None},
        ],
        "sections": [],
    }
    m = repo.create(date_text="d", date_iso="2026-07-03", attendees=[])
    repo.update(m.id, title="M", status="Done", notes_json=json.dumps(notes))

    page = DetailPage(shell, repo, Config(), theme)
    page.load(m.id)
    page._apply_action_edit(0, "Ship it", "Sam", "2026-07-15")
    items = repo.get(m.id).notes["action_items"]
    check("edit persists a due date", items[0]["due"] == "2026-07-15")

    page._apply_action_edit(0, "Ship it", "Sam", None)
    items = repo.get(m.id).notes["action_items"]
    check("edit can clear the due date", items[0]["due"] is None)

    print("== detail page: _set_due / _pick_row_due plumbing persists ==")
    page._apply_action_edit(0, "Ship it", "Sam", "2026-07-15")  # reset
    page._set_due(0, "2026-08-01")
    items = repo.get(m.id).notes["action_items"]
    check("_set_due persists a new date", items[0]["due"] == "2026-08-01")
    page._set_due(0, None)
    items = repo.get(m.id).notes["action_items"]
    check("_set_due(None) clears it", items[0]["due"] is None)

    print("== detail page: due chip / mini calendar button render per row ==")
    # A fixed date here is a time bomb: the label becomes "Today"/"Overdue"
    # once the calendar catches up (it did). Use a always-future date instead.
    future = _dt.date.today() + _dt.timedelta(days=45)
    month_abbr = future.strftime("%b")
    with_due = {"task": "Has due", "owner": None, "done": False, "confirmed": True,
                "due": future.isoformat()}
    without_due = {"task": "No due", "owner": None, "done": False, "confirmed": True, "due": None}
    row_with = page._action_row(0, dict(with_due))
    row_without = page._action_row(0, dict(without_due))
    from PySide6.QtWidgets import QPushButton
    with_texts = [b.text() for b in row_with.findChildren(QPushButton)]
    without_texts = [b.text() for b in row_without.findChildren(QPushButton)]
    check("row with a due date shows its label as a button",
          any(month_abbr in t for t in with_texts))
    check("row without a due date has no date-label button",
          not any(month_abbr in t for t in without_texts))

    print("== home dashboard: _gather_pending carries due, _set_due persists ==")
    home = HomePage(shell, repo, Config(), theme)
    notes2 = {
        "title": "M2", "summary": "s", "attendees": [],
        "action_items": [
            {"task": "Pending w/ due", "owner": None, "done": False, "confirmed": True, "due": "2026-07-11"},
            {"task": "Pending no due", "owner": None, "done": False, "confirmed": True, "due": None},
        ],
        "sections": [],
    }
    m2 = repo.create(date_text="d", date_iso="2026-07-03", attendees=[])
    repo.update(m2.id, title="M2", status="Done", notes_json=json.dumps(notes2))
    pending = home._gather_pending(repo.list())
    by_task = {p["task"]: p for p in pending}
    check("pending dict carries due when present", by_task["Pending w/ due"]["due"] == "2026-07-11")
    check("pending dict carries due=None when absent", by_task["Pending no due"]["due"] is None)

    home._set_due(m2.id, 1, "2026-09-01")
    app.processEvents()
    items2 = repo.get(m2.id).notes["action_items"]
    check("home._set_due persists a date", items2[1]["due"] == "2026-09-01")
    home._set_due(m2.id, 1, None)
    app.processEvents()
    items2 = repo.get(m2.id).notes["action_items"]
    check("home._set_due(None) clears it", items2[1]["due"] is None)

    print("== todoist: create_task receives due_date, send_open_items wires it ==")
    calls = []
    orig_create = todoist.create_task

    def fake_create(tok, content, description="", due_date=None, timeout=15.0):
        calls.append({"content": content, "due_date": due_date})
        return "T1"

    todoist.create_task = fake_create
    try:
        notes3 = {
            "title": "M3", "summary": "", "attendees": [],
            "action_items": [
                {"task": "With deadline", "owner": None, "done": False, "confirmed": True, "due": "2026-07-30"},
                {"task": "Without deadline", "owner": None, "done": False, "confirmed": True, "due": None},
            ],
            "sections": [],
        }
        sent, _skipped = todoist.send_open_items("tok", notes3, meeting_title="M3", date_text="d")
        check("both items sent", sent == 2)
        by_content = {c["content"]: c["due_date"] for c in calls}
        check("todoist receives due_date when present", by_content["With deadline"] == "2026-07-30")
        check("todoist receives due_date=None when absent", by_content["Without deadline"] is None)
    finally:
        todoist.create_task = orig_create

    print("== render / share show the due label ==")
    notes4 = {
        "title": "M4", "summary": "s", "attendees": [],
        "action_items": [
            {"task": "Finish deck", "owner": "Sam", "done": False, "confirmed": True, "due": "2026-07-25"},
            {"task": "No deadline here", "owner": None, "done": False, "confirmed": True, "due": None},
        ],
        "sections": [],
    }
    html_out = render.to_html(notes4)
    txt_out = render.to_plaintext(notes4)
    check("HTML export shows '· due <label>'", "due 25 Jul" in html_out)
    check("plaintext export shows '— due <label>'", "— due 25 Jul" in txt_out)
    check("no due text for the item without one", txt_out.count("due 25 Jul") == 1)

    m4 = repo.create(date_text="d", date_iso="2026-07-03", attendees=[])
    repo.update(m4.id, title="M4", status="Done", notes_json=json.dumps(notes4))
    share_out = share.to_share_html(repo.get(m4.id))
    check("share HTML shows the due label", "due 25 Jul" in share_out)

    repo.close()
    print("\nDUE-DATE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
