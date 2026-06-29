"""Smoke test for the 9 new features' data/back-end layer.

Isolates LOCALAPPDATA to a temp dir BEFORE importing meeting_notes so it can
never touch the user's real Earshot config/DB.
"""
import json
import os
import tempfile

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from meeting_notes.config import Config, DEFAULT_AI_ACTIONS
from meeting_notes.storage.repository import MeetingRepository
from meeting_notes.util import stats
from meeting_notes.integrations import webhook


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


print("== config round-trip ==")
cfg = Config.load()
cfg.show_dashboard = False
cfg.custom_instructions_enabled = True
cfg.custom_instructions = "Use British English."
cfg.webhook_url = "https://example.com/hook"
cfg.webhook_when = "transcript"
cfg.templates = [{"name": "Sales call", "instructions": "Focus on objections and budget."}]
cfg.ai_actions = [{"name": "Email", "prompt": "Draft an email."}]
cfg.save()
cfg2 = Config.load()
check("show_dashboard persisted", cfg2.show_dashboard is False)
check("custom instr persisted", cfg2.custom_instructions == "Use British English.")
check("webhook persisted", cfg2.webhook_url == "https://example.com/hook" and cfg2.webhook_when == "transcript")
check("templates persisted", cfg2.templates[0]["name"] == "Sales call")
check("ai_actions persisted", cfg2.ai_actions[0]["name"] == "Email")
check("notes_instructions merges custom + template",
      "British English" in cfg2.notes_instructions("Sales call") and "objections" in cfg2.notes_instructions("Sales call"))
check("template_instructions lookup", cfg2.template_instructions("Sales call") == "Focus on objections and budget.")

cfg3 = Config.load()
cfg3.ai_actions = []
check("effective_ai_actions falls back to defaults", cfg3.effective_ai_actions() == DEFAULT_AI_ACTIONS)

print("== repository: template, bookmarks, FTS ==")
repo = MeetingRepository()
m = repo.create(date_text="26 Jun 2026", date_iso="2026-06-26", attendees=["Alice"], agenda="Pricing", template="Sales call")
check("template stored on create", repo.get(m.id).template == "Sales call")

repo.update(m.id, transcript="[00:01] Me: Hello there everyone\n[00:05] Them: Hi, let's discuss the pricing widget")
repo.update(m.id, bookmarks=[{"ms": 5000, "label": ""}, {"ms": 12000, "label": ""}])
notes = {"summary": "Talked about pricing.", "action_items": [{"task": "Send proposal", "owner": "Alice", "done": False}],
         "sections": [{"heading": "Decisions", "bullets": ["Use annual billing"]}]}
repo.update(m.id, title="Pricing sync", notes_json=json.dumps(notes))

got = repo.get(m.id)
check("bookmarks round-trip", isinstance(got.bookmarks, list) and got.bookmarks[0]["ms"] == 5000)
check("notes round-trip", got.notes["action_items"][0]["task"] == "Send proposal")

# create a second meeting so search has to discriminate
m2 = repo.create(date_text="26 Jun 2026", date_iso="2026-06-26", attendees=["Bob"], agenda="")
repo.update(m2.id, title="Standup", transcript="[00:01] Me: quick standup about the build")

check("FTS finds word in transcript (widget)", m.id in repo.search("widget") and m2.id not in repo.search("widget"))
check("FTS finds word in notes (annual)", m.id in repo.search("annual"))
check("FTS finds attendee (alice)", m.id in repo.search("alice"))
check("FTS prefix match (pric)", m.id in repo.search("pric"))
check("FTS finds standup in other meeting", m2.id in repo.search("standup") and m.id not in repo.search("standup"))
check("empty query → no results", repo.search("") == [])

# delete removes from FTS
repo.delete(m2.id)
check("deleted meeting drops out of FTS", m2.id not in repo.search("standup"))

print("== talk-time ==")
tt = stats.talk_time("[00:01] Me: one two three\n[00:05] Them: four five")
check("talk-time counts words", tt["me_words"] == 3 and tt["them_words"] == 2)
check("talk-time pct", tt["me_pct"] == 60 and tt["them_pct"] == 40)
check("talk-time has_speakers", tt["has_speakers"] is True)
tt2 = stats.talk_time("[00:01] hello world no speaker label")
check("single-speaker import → has_speakers False", tt2["has_speakers"] is False)

print("== webhook payload ==")
payload = webhook.build_payload(repo.get(m.id))
check("payload has notes+transcript+bookmarks",
      payload["notes"]["summary"] == "Talked about pricing." and "widget" in payload["transcript"] and len(payload["bookmarks"]) == 2)

print("\nALL FEATURE SMOKE TESTS PASSED")
