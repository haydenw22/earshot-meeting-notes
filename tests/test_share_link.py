"""Tests for v0.34.0: public share links + Keep all / Delete all triage.

Covers: the /v1/share payload builder (suggestion filtering, transcript
opt-in), the REAL httpx request encoding (per the v0.22.1 lesson: build
httpx.Request and read() it, never just stub), the share_url DB column
round-trip, the ShareDialog in cloud/self-host modes, and the bulk
keep-all/delete-all mutations on the detail page.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_share_link.py
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

import httpx  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from meeting_notes.config import Config  # noqa: E402
from meeting_notes.notes import share  # noqa: E402
from meeting_notes.storage.repository import MeetingRepository  # noqa: E402
from meeting_notes.ui.page_detail import DetailPage  # noqa: E402
from meeting_notes.ui.share_dialog import ShareDialog  # noqa: E402
from meeting_notes.ui.theme_controller import ThemeController  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


class _Shell:
    def show_home(self):
        pass

    def open_meeting(self, _mid):
        pass

    def notify_data_changed(self):
        self.notified = True


NOTES = {
    "title": "Q3 Budget Review",
    "summary": "We agreed the **$40k** cap.",
    "attendees": ["Hayden", "Jesiah"],
    "action_items": [
        {"task": "Kept open", "owner": "Sam", "confirmed": True, "done": False},
        {"task": "Done legacy (no flag)", "done": True},
        {"task": "Suggested only", "confirmed": False, "done": False},
    ],
    "sections": [{"heading": "Budget", "bullets": ["Cap **$40k**", "Review in Q4"]}],
}


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841
    cfg = Config()
    theme = ThemeController(cfg)
    shell = _Shell()
    repo = MeetingRepository()

    m = repo.create(date_text="13th July 2026", date_iso="2026-07-13", attendees=["Hayden", "Jesiah"])
    repo.update(m.id, title="Q3 Budget Review", status="Done", duration_secs=2700.0,
                transcript="Me: hello\nThem: hi", notes_json=json.dumps(NOTES))
    m = repo.get(m.id)

    print("== share payload builder ==")
    p = share.to_share_payload(m, include_transcript=False)
    check("payload carries meeting id", p["meeting_id"] == m.id)
    check("payload title from notes", p["title"] == "Q3 Budget Review")
    check("duration in minutes", p["duration_mins"] == 45)
    tasks = [a["task"] for a in p["action_items"]]
    check("kept + done items included", "Kept open" in tasks and "Done legacy (no flag)" in tasks)
    check("unvetted suggestion EXCLUDED from public payload", "Suggested only" not in tasks)
    check("sections carried", p["sections"][0]["heading"] == "Budget"
          and p["sections"][0]["bullets"][0] == "Cap **$40k**")
    check("no transcript unless asked", p["transcript"] == "")
    p2 = share.to_share_payload(m, include_transcript=True)
    check("transcript included when asked", "Them: hi" in p2["transcript"])

    print("== the REAL request encodes (v0.22.1 lesson) ==")
    req = httpx.Request("POST", "https://api.tryearshot.app/v1/share",
                        headers={"Authorization": "Bearer tok"}, json=p)
    raw = req.read()
    check("JSON body is bytes and non-empty", isinstance(raw, bytes) and len(raw) > 0)
    body = json.loads(raw)
    check("body round-trips the payload", body["title"] == "Q3 Budget Review"
          and body["meeting_id"] == m.id)
    check("content-type is application/json", req.headers["content-type"] == "application/json")
    dreq = httpx.Request("DELETE", f"https://api.tryearshot.app/v1/share/{m.id}",
                         headers={"Authorization": "Bearer tok"})
    check("unshare path carries the meeting id", dreq.url.path == f"/v1/share/{m.id}")

    print("== share_url column round-trip ==")
    check("fresh meeting has no share_url", (repo.get(m.id).share_url or "") == "")
    repo.update(m.id, share_url="https://tryearshot.app/u/hayden/q3-abcdef0123456789")
    check("share_url persists", repo.get(m.id).share_url.endswith("abcdef0123456789"))
    repo.update(m.id, share_url=None)
    check("share_url clears on unshare", repo.get(m.id).share_url is None)

    print("== ShareDialog ==")
    dlg = ShareDialog(None, theme, title="Q3", has_transcript=True, cloud=True, share_url="")
    check("cloud mode enables the link button", dlg.link_btn.isEnabled())
    check("link button says Create", dlg.link_btn.text() == "Create public link")
    check("transcript unchecked by default", dlg.include_transcript is False)
    dlg.tr_check.setChecked(True)
    check("transcript opt-in works", dlg.include_transcript is True)
    dlg2 = ShareDialog(None, theme, title="Q3", has_transcript=False, cloud=False, share_url="")
    check("self-host mode disables the link button", not dlg2.link_btn.isEnabled())
    check("no-transcript disables the checkbox", not dlg2.tr_check.isEnabled())
    url = "https://tryearshot.app/u/hayden/q3-abcdef0123456789"
    dlg3 = ShareDialog(None, theme, title="Q3", has_transcript=True, cloud=True, share_url=url)
    check("existing link shows Update label", dlg3.link_btn.text() == "Update public link")
    check("existing link shown read-only", dlg3.url_box.text() == url and dlg3.url_box.isReadOnly())
    dlg3._copy(url)
    check("Copy link puts the URL on the clipboard", QApplication.clipboard().text() == url)

    print("== Keep all / Delete all ==")
    page = DetailPage(shell, repo, cfg, theme)
    page.load(m.id)
    page._keep_all_actions()
    kept = repo.get(m.id).notes["action_items"]
    check("keep all confirms every item", all(a.get("confirmed") for a in kept))
    check("keep all deletes nothing", len(kept) == 3)

    repo.update(m.id, notes_json=json.dumps(NOTES))  # reset to mixed state
    page.load(m.id)
    page._delete_all_suggestions()
    left = repo.get(m.id).notes["action_items"]
    check("delete all removes only unvetted suggestions", [a["task"] for a in left]
          == ["Kept open", "Done legacy (no flag)"])
    check("shell notified of the change", getattr(shell, "notified", False))

    print("\nall good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
