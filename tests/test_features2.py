"""Tests for the v0.10–v0.14 feature batch: local-LLM notes parsing, share-HTML
export, Todoist send/dedupe, call-detection helpers, and the notes-provider
dispatcher. All network calls are stubbed.

Run:  python tests/test_features2.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meeting_notes.config import Config  # noqa: E402
from meeting_notes.integrations import todoist  # noqa: E402
from meeting_notes.notes import openai_llm, share  # noqa: E402
from meeting_notes.notes import service as notes_service  # noqa: E402
from meeting_notes.storage.repository import Meeting  # noqa: E402
from meeting_notes.util import mic_usage  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


NOTES_JSON = {
    "title": "Budget sync",
    "summary": "We agreed the Q3 budget.",
    "attendees": ["Hayden", "Sam"],
    "action_items": [
        {"task": "Send the budget sheet", "owner": "Sam", "done": False},
        {"task": "Book venue", "owner": None, "done": True},
        {"task": "Chase invoices", "owner": "Hayden", "done": False, "todoist_id": "T-1"},
    ],
    "sections": [{"heading": "Decisions", "bullets": ["Budget is **$50k**"]}],
}


def main() -> int:
    print("== openai_llm: JSON extraction ==")
    ex = openai_llm._extract_json
    check("plain object", ex('{"a": 1}') == {"a": 1})
    check("fenced json", ex('```json\n{"a": 1}\n```') == {"a": 1})
    check("prose around object", ex('Sure! Here it is: {"a": 1} hope that helps') == {"a": 1})
    try:
        ex("no json here")
        check("garbage raises", False)
    except ValueError:
        check("garbage raises", True)

    print("== openai_llm: notes via stubbed chat ==")
    calls = {}

    def fake_chat(base_url, api_key, model, system, user, *, want_json, max_tokens):
        calls["system"] = system
        calls["user"] = user
        return "```json\n" + json.dumps(NOTES_JSON) + "\n```"

    orig = openai_llm._chat
    openai_llm._chat = fake_chat
    try:
        notes = openai_llm.generate_notes(
            "[00:01] Me: hello", base_url="http://localhost:11434/v1", model="llama3.1",
            attendees=["Hayden"], human_date="2 Jul 2026", extra_instructions="Use British English.",
        )
        check("valid MeetingNotes returned", notes.title == "Budget sync")
        check("schema embedded in system prompt", "record_meeting_notes" in calls["system"]
              or "action_items" in calls["system"])
        check("custom instructions included", "British English" in calls["system"])
        check("transcript fenced as data", "<transcript>" in calls["user"])
    finally:
        openai_llm._chat = orig

    print("== notes provider dispatcher ==")
    cfg = Config()
    cfg.notes_provider = "anthropic"
    cfg.anthropic_api_key = ""
    os.environ.pop("ANTHROPIC_API_KEY", None)
    check("anthropic not ready without key", not notes_service.ready(cfg))
    # "openai" (S3: hosted OpenAI-compatible cloud) needs base url + model + key.
    cfg.notes_provider = "openai"
    cfg.llm_base_url = "https://api.openai.com/v1"
    cfg.llm_model = ""
    cfg.llm_api_key = ""
    check("openai not ready without model", not notes_service.ready(cfg))
    cfg.llm_model = "gpt-4o-mini"
    check("openai not ready without key", not notes_service.ready(cfg))
    cfg.llm_api_key = "sk-x"
    check("openai ready with url+model+key", notes_service.ready(cfg))
    check("hint mentions settings", "Settings" in notes_service.missing_hint(cfg))
    # "local" (S3: fully local server) needs base url + model but NOT a key.
    cfg.notes_provider = "local"
    cfg.local_llm_base_url = "http://localhost:11434/v1"
    cfg.local_llm_model = ""
    check("local not ready without model", not notes_service.ready(cfg))
    cfg.local_llm_model = "llama3.1"
    check("local ready with url+model (no key needed)", notes_service.ready(cfg))
    check("local hint mentions settings", "Settings" in notes_service.missing_hint(cfg))

    print("== share HTML export ==")
    m = Meeting(id=7, title="Budget sync", date_text="2 Jul 2026", attendees=["Hayden", "Sam"],
                duration_secs=1860, transcript="[00:01] Me: hello <script>alert(1)</script>",
                notes_json=json.dumps(NOTES_JSON))
    doc = share.to_share_html(m, include_transcript=True)
    check("standalone document", doc.startswith("<!doctype html") and "</html>" in doc)
    check("title + summary present", "Budget sync" in doc and "Q3 budget" in doc)
    check("bold markdown rendered", "<b>$50k</b>" in doc)
    check("transcript included when asked", "Full transcript" in doc)
    check("script tags escaped", "<script>" not in doc and "&lt;script&gt;" in doc)
    doc2 = share.to_share_html(m, include_transcript=False)
    check("transcript omitted when not asked", "Full transcript" not in doc2)

    print("== todoist: send + dedupe ==")
    created = []

    def fake_create(token, content, description="", timeout=15.0):
        created.append(content)
        return f"T-{len(created)}"

    orig_create = todoist.create_task
    todoist.create_task = fake_create
    try:
        notes = json.loads(json.dumps(NOTES_JSON))
        sent, skipped = todoist.send_open_items("tok", notes, meeting_title="Budget sync",
                                                date_text="2 Jul 2026")
        check("sends only open, unsent items", sent == 1 and created == ["Send the budget sheet"])
        check("skips already-sent items", skipped == 1)
        check("done items never sent", "Book venue" not in created)
        check("todoist_id stored on the item", notes["action_items"][0]["todoist_id"] == "T-1")
        sent2, skipped2 = todoist.send_open_items("tok", notes, meeting_title="x", date_text="y")
        check("second send is a no-op", sent2 == 0 and skipped2 == 2)
    finally:
        todoist.create_task = orig_create
    try:
        todoist.send_open_items("", {}, meeting_title="x", date_text="y")
        check("blank token raises", False)
    except todoist.TodoistError:
        check("blank token raises", True)

    print("== call detection helpers ==")
    fn = mic_usage.friendly_name
    check("zoom exe → Zoom", fn(r"C:#Users#pc#AppData#Zoom#bin#Zoom.exe") == "Zoom")
    check("teams exe → Teams", fn(r"C:#Program Files#ms-teams.exe") == "Microsoft Teams")
    check("chrome exe → browser call", "browser" in fn(r"C:#x#chrome.exe"))
    check("unknown exe → capitalised name", fn(r"C:#y#superapp.exe") == "Superapp")
    check("live query returns a list", isinstance(mic_usage.apps_using_microphone(), list))

    print("\nFEATURE BATCH 2 TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
