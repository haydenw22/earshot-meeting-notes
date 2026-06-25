"""Pure-logic + storage + schema checks (no audio hardware, no network).

Run:  python tests/test_core.py
"""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meeting_notes.notes.schema import MeetingNotes, notes_tool_schema  # noqa: E402
from meeting_notes.storage import db as dbmod  # noqa: E402
from meeting_notes.storage.repository import MeetingRepository  # noqa: E402
from meeting_notes.transcription.merge import merge_transcripts  # noqa: E402
from meeting_notes.util.dates import human_date, iso_date  # noqa: E402


def test_dates():
    assert human_date(dt.date(2026, 6, 25)) == "25th June 2026"
    assert human_date(dt.date(2026, 6, 1)) == "1st June 2026"
    assert human_date(dt.date(2026, 6, 2)) == "2nd June 2026"
    assert human_date(dt.date(2026, 6, 3)) == "3rd June 2026"
    assert human_date(dt.date(2026, 6, 11)) == "11th June 2026"  # teens are 'th'
    assert human_date(dt.date(2026, 6, 21)) == "21st June 2026"
    assert iso_date(dt.date(2026, 6, 25)) == "2026-06-25"
    print("  dates OK")


def test_merge():
    me = {"segments": [
        {"start": 5.0, "end": 7.0, "text": "Hi everyone"},
        {"start": 12.0, "end": 13.0, "text": "I disagree actually"},
    ]}
    them = {"segments": [
        {"start": 0.0, "end": 4.0, "text": "Welcome to the call"},
        {"start": 8.0, "end": 11.0, "text": "Let's start with the budget"},
    ]}
    merged = merge_transcripts(me, them)
    lines = merged["text"].splitlines()
    assert lines[0].endswith("Them: Welcome to the call"), lines
    assert "Me: Hi everyone" in lines[1]
    assert lines[-1].endswith("Me: I disagree actually"), lines
    # ordering by start time across speakers
    starts = [s["start"] for s in merged["segments"]]
    assert starts == sorted(starts)
    print("  merge ordering OK")


def test_merge_crosstalk_dedupe():
    them = {"segments": [{"start": 10.0, "end": 13.0, "text": "The deadline is Friday"}]}
    # near-identical bleed on the mic channel, overlapping in time -> should be dropped
    me = {"segments": [{"start": 10.1, "end": 13.1, "text": "the deadline is friday"}]}
    merged = merge_transcripts(me, them, dedupe=True)
    assert len(merged["segments"]) == 1
    assert merged["segments"][0]["speaker"] == "Them"
    # without dedupe both survive
    both = merge_transcripts(me, them, dedupe=False)
    assert len(both["segments"]) == 2
    print("  crosstalk dedupe OK")


def test_schema():
    schema = notes_tool_schema()
    assert schema["type"] == "object"
    assert "title" in schema["properties"]
    assert schema["properties"]["action_items"]["items"]["properties"]["owner"]["type"] == ["string", "null"]
    notes = MeetingNotes.model_validate({
        "title": "Budget review for Q3",
        "summary": "We reviewed the budget.",
        "attendees": ["Hayden", "Sam"],
        "decisions": ["Approved the Q3 budget"],
        "action_items": [{"task": "Send the figures", "owner": "Sam", "due": None}],
        "topics": ["budget"],
    })
    assert notes.action_items[0].owner == "Sam"
    assert notes.action_items[0].due is None
    print("  schema OK")


def test_db():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        conn = dbmod.connect(Path(tmp) / "t.db")
        repo = MeetingRepository(conn)
        try:
            m = repo.create(date_text="25th June 2026", date_iso="2026-06-25", attendees=["Hayden"])
            assert m.id is not None
            repo.update(m.id, attendees=["Hayden", "Late Joiner"], status="Recording")
            repo.update(m.id, title="Test meeting", transcript="[00:00] Me: hi", status="Done")
            got = repo.get(m.id)
            assert got.title == "Test meeting"
            assert got.attendees == ["Hayden", "Late Joiner"]
            assert got.status == "Done"
            assert len(repo.list()) == 1
            repo.delete(m.id)
            assert len(repo.list()) == 0
        finally:
            repo.close()
    print("  db OK")


def test_update_whitelist():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        repo = MeetingRepository(dbmod.connect(Path(tmp) / "t.db"))
        try:
            m = repo.create(date_text="x", date_iso="2026-06-25", attendees=[])
            raised = False
            try:
                repo.update(m.id, status="Done; DROP TABLE meetings; --")  # value is safe (parametrised)
            except ValueError:
                raised = True
            assert not raised, "valid column should be allowed"
            # a non-whitelisted column name must be rejected, not interpolated into SQL
            raised = False
            try:
                repo.update(m.id, **{"id = 0 WHERE 1=1; --": "x"})
            except ValueError:
                raised = True
            assert raised, "unknown/injected column must raise"
        finally:
            repo.close()
    print("  update whitelist OK")


def test_config_preserves_unknown():
    import json as _json

    from meeting_notes import config as config_mod

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        cfg_file = Path(tmp) / "config.json"
        cfg_file.write_text(
            _json.dumps({"whisper_url": "http://x:9000", "some_future_key": 123}),
            encoding="utf-8",
        )
        orig = config_mod.config_path
        config_mod.config_path = lambda: cfg_file  # redirect away from real %LOCALAPPDATA%
        try:
            loaded = config_mod.Config.load()
        finally:
            config_mod.config_path = orig
        assert loaded.whisper_url == "http://x:9000"
        assert loaded.extra.get("some_future_key") == 123, "unknown keys must survive in extra"
    print("  config preserves unknown keys OK")


if __name__ == "__main__":
    test_dates()
    test_merge()
    test_merge_crosstalk_dedupe()
    test_schema()
    test_db()
    test_update_whitelist()
    test_config_preserves_unknown()
    print("ALL CORE TESTS PASSED")
