"""Tests for the action-item lifecycle: AI generates SUGGESTIONS → user keeps /
edits / dismisses → only accepted items reach the dashboard, Todoist and count
as real. Also the shorter-summary prompt change.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_action_items.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from meeting_notes.config import Config  # noqa: E402
from meeting_notes.integrations import todoist  # noqa: E402
from meeting_notes.notes import anthropic_client, render, share  # noqa: E402
from meeting_notes.notes import service as notes_service  # noqa: E402
from meeting_notes.notes.schema import ActionItem, MeetingNotes, notes_tool_schema  # noqa: E402
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


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841

    print("== generation marks items as suggestions ==")
    fake = MeetingNotes(title="T", summary="S", attendees=[],
                        action_items=[ActionItem(task="Do X", confirmed=True),
                                      ActionItem(task="Do Y")],
                        sections=[])
    orig = anthropic_client.generate_notes
    anthropic_client.generate_notes = lambda *a, **k: fake
    try:
        cfg = Config()
        cfg.notes_provider = "anthropic"
        cfg.anthropic_api_key = "sk-x"
        out = notes_service.generate_notes("t", cfg)
        check("all generated items start unconfirmed",
              all(not a.confirmed for a in out.action_items))
    finally:
        anthropic_client.generate_notes = orig
    check("'confirmed' is NOT exposed to the model",
          "confirmed" not in json.dumps(notes_tool_schema()))
    check("summary prompt asks for 1-2 sentences", "1-2" in anthropic_client.SYSTEM_PROMPT)

    print("== gating: dashboard / todoist / exports ==")
    NOTES = {"title": "M", "summary": "s", "attendees": [],
             "action_items": [
                 {"task": "Accepted open", "owner": "Sam", "done": False, "confirmed": True},
                 {"task": "Suggested open", "owner": None, "done": False, "confirmed": False},
                 {"task": "Legacy open (no key)", "owner": None, "done": False},
                 {"task": "Suggested but done", "owner": None, "done": True, "confirmed": False},
             ], "sections": []}
    repo = MeetingRepository(dbmod.connect(Path(tempfile.mkdtemp()) / "a.db"))
    m = repo.create(date_text="d", date_iso="2026-07-02", attendees=[])
    repo.update(m.id, title="M", status="Done", notes_json=json.dumps(NOTES))

    theme = ThemeController(Config())
    theme.apply()
    shell = _Shell()
    home = HomePage(shell, repo, Config(), theme)
    pending = home._gather_pending(repo.list())
    tasks = [p["task"] for p in pending]
    check("dashboard shows accepted + legacy only",
          tasks == ["Accepted open", "Legacy open (no key)"])

    created = []
    orig_create = todoist.create_task
    todoist.create_task = lambda tok, content, description="", due_date=None, timeout=15.0: (created.append(content) or "T1")
    try:
        notes_copy = json.loads(json.dumps(NOTES))
        sent, _sk = todoist.send_open_items("tok", notes_copy, meeting_title="M", date_text="d")
        check("todoist sends accepted + legacy only",
              sent == 2 and created == ["Accepted open", "Legacy open (no key)"])
    finally:
        todoist.create_task = orig_create

    html = render.to_html(NOTES)
    txt = render.to_plaintext(NOTES)
    check("copy-HTML marks suggestions", html.count("(suggested)") == 1)
    check("plaintext marks suggestions", txt.count("(suggested)") == 1)
    mm = repo.get(m.id)
    check("share-HTML marks suggestions", share.to_share_html(mm).count("(suggested)") == 1)

    print("== render: Notion to-do markdown + summary-only + agenda ==")
    md = render.todo_markdown(NOTES)
    md_lines = md.splitlines()
    check("one markdown line per action item", len(md_lines) == len(NOTES["action_items"]))
    check("open items render as '- [ ]'", md_lines[0].startswith("- [ ] "))
    check("done items render as '- [x]'",
          any(ln.startswith("- [x] ") and "Suggested but done" in ln for ln in md_lines))
    check("owner rides along in parentheses", "(Sam)" in md_lines[0])
    check("suggestions marked in the to-do list", sum("suggested" in ln for ln in md_lines) >= 1)
    check("no em dashes in the to-do markdown", "—" not in md)

    s_html = render.to_html(NOTES, agenda="Talk pricing", include_actions=False)
    s_txt = render.to_plaintext(NOTES, agenda="Talk pricing", include_actions=False)
    check("summary-only HTML has no action items", "Action items" not in s_html and "Accepted open" not in s_html)
    check("summary-only plaintext has no action items", "Accepted open" not in s_txt)
    check("agenda included in summary HTML", "Agenda" in s_html and "Talk pricing" in s_html)
    check("agenda included in summary plaintext", "Talk pricing" in s_txt)
    check("full copy still includes actions AND agenda",
          "Accepted open" in render.to_plaintext(NOTES, agenda="Talk pricing"))

    print("== detail page: Copy menu (all / action items / summary) ==")
    page = DetailPage(shell, repo, Config(), theme)
    page.load(m.id)
    check("copy button reads 'Copy'", page.copy_btn.text() == "Copy")
    copy_texts = [a.text() for a in page.copy_menu.actions()]
    check("copy menu offers all/action items/summary",
          copy_texts == ["Copy all", "Copy action items", "Copy summary"])
    from PySide6.QtWidgets import QApplication as _QApp
    page._copy_actions_todo()
    cb = _QApp.clipboard().mimeData()
    check("copy action items puts markdown to-dos on the clipboard",
          cb.text().startswith("- [") and "- [ ] Accepted open (Sam)" in cb.text())
    check("copy action items is PLAIN TEXT only (Notion needs it to parse '- [ ]')",
          not cb.hasHtml())
    page._copy_summary_only()
    cb = _QApp.clipboard().mimeData()
    check("copy summary has rich HTML flavour", cb.hasHtml())
    check("copy summary excludes action items", "Accepted open" not in cb.text())
    page._copy_all()
    cb = _QApp.clipboard().mimeData()
    check("copy all includes action items", "Accepted open" in cb.text())
    # leave the clipboard empty: offscreen Qt segfaults at interpreter exit
    # tearing down a clipboard that still owns our QMimeData
    _QApp.clipboard().clear()
    app.processEvents()

    print("== detail page: keep / edit / dismiss ==")

    page._confirm_action(1)  # keep "Suggested open"
    items = repo.get(m.id).notes["action_items"]
    check("keep sets confirmed", items[1]["confirmed"] is True)
    check("kept item now on the dashboard",
          "Suggested open" in [p["task"] for p in home._gather_pending(repo.list())])
    check("shell notified so home refreshes", getattr(shell, "notified", False))

    page._apply_action_edit(1, "Suggested open — reworded", "Hayden")
    items = repo.get(m.id).notes["action_items"]
    check("edit updates task + owner",
          items[1]["task"] == "Suggested open — reworded" and items[1]["owner"] == "Hayden")

    page._apply_action_edit(1, "", "")  # emptied task = dismissal
    items = repo.get(m.id).notes["action_items"]
    check("emptied edit dismisses the item", len(items) == 3
          and all(i["task"] != "Suggested open — reworded" for i in items))

    page._dismiss_action(0)
    items = repo.get(m.id).notes["action_items"]
    check("dismiss deletes the item", len(items) == 2 and items[0]["task"] == "Legacy open (no key)")

    # done-toggle persistence still works (legacy path)
    page.refresh()
    page._persist_action(0, True)
    check("done toggle persists", repo.get(m.id).notes["action_items"][0]["done"] is True)

    repo.close()
    print("\nACTION-ITEM LIFECYCLE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
