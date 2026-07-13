"""Regression tests for the 2026-07 audit fixes:

  P1-02  spool write failures are latched and surfaced (audio/capture.py)
  P1-04  meeting deletion only removes the DB row once files are gone
  P2-01  Todoist ids created before a mid-batch failure are persisted
  P2-03  citations: empty quotes rejected, timestamps derived from the transcript
  P2-08  the "after summary" webhook does not fire when note generation failed
  P2-12  cloud_api_base must be HTTPS (or loopback HTTP) or falls back to prod

Run:  QT_QPA_PLATFORM=offscreen python tests/test_audit_fixes.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Isolate ALL app data to a throwaway dir so the test can never read or overwrite
# the real %LOCALAPPDATA%\Earshot\config.json (which would wipe the user's key/URL).
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


def new_repo():
    from meeting_notes.storage import db as dbmod
    from meeting_notes.storage.repository import MeetingRepository
    return MeetingRepository(dbmod.connect(Path(tempfile.mkdtemp()) / "t.db"))


# ---------------------------------------------------------------- P1-02 --
def test_spool_write_error_latched():
    print("== P1-02: spool write failures latch and surface ==")
    from meeting_notes.audio import capture

    class _BadFile:
        def write(self, _b):
            raise OSError(28, "No space left on device")

    s = object.__new__(capture._Stream)
    s.channels, s.rate, s.level, s.write_error = 1, 48000, 0.0, None
    s._file = _BadFile()
    ret = s._cb(b"\x00\x20" * 480, 480, None, None)
    check("callback survives the write failure and keeps capturing",
          ret == (None, capture.pyaudio.paContinue))
    check("write error latched with the OS reason",
          bool(s.write_error) and "No space left" in s.write_error)
    check("level meter still updates (the deceptive healthy look the UI must override)",
          s.level > 0.0)
    first = s.write_error
    s._cb(b"\x00\x20" * 480, 480, None, None)
    check("first failure stays latched (not overwritten)", s.write_error == first)

    rec = object.__new__(capture.DualStreamRecorder)
    rec._mic, rec._loop = s, SimpleNamespace(write_error=None)
    check("recorder reports the failing channel",
          rec.write_error is not None and rec.write_error.startswith("your microphone"))
    rec2 = object.__new__(capture.DualStreamRecorder)
    rec2._mic, rec2._loop = SimpleNamespace(write_error=None), SimpleNamespace(write_error=None)
    check("healthy recorder reports None", rec2.write_error is None)


# ---------------------------------------------------------------- P1-04 --
def test_delete_meeting_honest():
    print("== P1-04: deletion keeps the meeting when files can't be removed ==")
    from meeting_notes.paths import recordings_dir
    from meeting_notes.storage.deletion import delete_meeting

    repo = new_repo()
    folder = recordings_dir() / "meeting_777"
    folder.mkdir(parents=True, exist_ok=True)
    wav = folder / "audio.wav"
    wav.write_bytes(b"RIFF fake audio")
    m = repo.create(date_text="d", date_iso="2026-07-13", attendees=[])
    repo.update(m.id, audio_dir=str(folder), status="Done")

    with open(wav, "rb"):  # Windows: an open handle blocks deletion
        result = delete_meeting(repo, m.id)
        check("locked file -> deletion reports failure", not result.ok)
        check("failure names the folder still holding files", result.folder == str(folder))
        check("meeting row is KEPT so the files stay discoverable",
              any(mm.id == m.id for mm in repo.list()))
        check("folder still exists on disk", folder.exists())

    result = delete_meeting(repo, m.id)  # handle released -> retry succeeds
    check("retry after unlock succeeds", result.ok)
    check("meeting row removed", not any(mm.id == m.id for mm in repo.list()))
    check("folder removed", not folder.exists())

    # a folder OUTSIDE the recordings root is never rmtree'd (path guard), but
    # the row still deletes -> the file stays where the user put it
    outside = Path(tempfile.mkdtemp(prefix="earshot_outside_"))
    (outside / "keep.txt").write_text("x")
    m2 = repo.create(date_text="d", date_iso="2026-07-13", attendees=[])
    repo.update(m2.id, audio_dir=str(outside))
    result = delete_meeting(repo, m2.id)
    check("out-of-root folder: row deleted, files untouched",
          result.ok and (outside / "keep.txt").exists())


# ---------------------------------------------------------------- P2-01 --
def _three_open_items():
    return {"action_items": [
        {"task": "Task one", "done": False, "confirmed": True},
        {"task": "Task two", "done": False, "confirmed": True},
        {"task": "Task three", "done": False, "confirmed": True},
    ]}


def test_todoist_partial_failure():
    print("== P2-01: mid-batch Todoist failure keeps the ids already created ==")
    from meeting_notes.integrations import todoist

    calls = {"n": 0}
    real_create = todoist.create_task

    def fake_create(token, content, description="", due_date=None, timeout=15.0):
        calls["n"] += 1
        if calls["n"] == 2:
            raise todoist.TodoistError("simulated 500")
        return f"task-{calls['n']}"

    todoist.create_task = fake_create
    try:
        notes = _three_open_items()
        raised = False
        try:
            todoist.send_open_items("tok", notes, meeting_title="M", date_text="d")
        except todoist.TodoistError:
            raised = True
        items = notes["action_items"]
        check("failure propagates to the caller", raised)
        check("item created before the failure keeps its id in the notes dict",
              items[0].get("todoist_id") == "task-1")
        check("failed + unsent items carry no id",
              not items[1].get("todoist_id") and not items[2].get("todoist_id"))

        # the DetailPage worker must PERSIST that partially-updated dict, so a
        # retry can't recreate task one (this is the actual v0.33.0 fix)
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from meeting_notes.config import Config
        from meeting_notes.ui.page_detail import DetailPage
        from meeting_notes.ui.theme_controller import ThemeController

        repo = new_repo()
        m = repo.create(date_text="d", date_iso="2026-07-13", attendees=[])
        repo.update(m.id, title="M", status="Done",
                    notes_json=json.dumps(_three_open_items()))
        cfg = Config()
        cfg.todoist_token = "tok"
        shell = SimpleNamespace(show_home=lambda: None, notify_data_changed=lambda: None,
                                open_meeting=lambda _id: None)
        theme = ThemeController(cfg)
        page = DetailPage(shell, repo, cfg, theme)
        page.load(m.id)
        calls["n"] = 0
        page._send_todoist()
        page._todoist_worker.wait(10000)
        for _ in range(20):
            app.processEvents()
        saved = repo.get(m.id).notes["action_items"]
        check("id created before the failure is PERSISTED to the database",
              saved[0].get("todoist_id") == "task-1")

        calls["n"] = 10  # every retry call now succeeds (returns task-11, ...)
        page._send_todoist()
        page._todoist_worker.wait(10000)
        for _ in range(20):
            app.processEvents()
        saved = repo.get(m.id).notes["action_items"]
        check("retry does NOT recreate the already-sent task",
              saved[0].get("todoist_id") == "task-1")
        check("retry fills in the remaining tasks",
              saved[1].get("todoist_id") and saved[2].get("todoist_id"))
    finally:
        todoist.create_task = real_create


# ---------------------------------------------------------------- P2-03 --
def test_citation_verification():
    print("== P2-03: citations reject empty quotes and derive timestamps ==")
    from meeting_notes.qa import ask

    transcript = ("Intro line without a tag.\n"
                  "[00:12] Me: We agreed to ship on Friday.\n"
                  "[01:05] Them: The budget is ten thousand.\n")
    m = SimpleNamespace(id=1, title="Sync", date_text="d", transcript=transcript)
    by_id = {1: m}

    out = ask._verified_citations([
        {"meeting_id": 1, "timestamp": "[59:59]", "quote": "ship on Friday"},
        {"meeting_id": 1, "timestamp": "[00:01]", "quote": ""},
        {"meeting_id": 1, "timestamp": "[00:12]", "quote": "we never said this"},
        {"meeting_id": 1, "timestamp": "[42:00]", "quote": "budget is ten thousand"},
        {"meeting_id": 1, "timestamp": "[00:30]", "quote": "Intro line without a tag"},
    ], by_id)
    check("empty quote dropped", all(c["quote"] for c in out))
    check("fabricated quote dropped", all("never said" not in c["quote"] for c in out))
    check("real quote kept", any("ship on Friday" in c["quote"] for c in out))
    ship = next(c for c in out if "ship on Friday" in c["quote"])
    check("timestamp derived from the transcript, model's fake [59:59] discarded",
          ship["timestamp"] == "[00:12]")
    budget = next(c for c in out if "budget" in c["quote"])
    check("second quote gets its own line's tag", budget["timestamp"] == "[01:05]")
    intro = next(c for c in out if "Intro line" in c["quote"])
    check("quote before any tag -> no timestamp rather than a made-up one",
          intro["timestamp"] == "")

    cloud = ask._verified_cloud_citations([
        {"meeting_title": "Sync", "quote": ""},
        {"meeting_title": "Sync", "quote": "ship on Friday"},
    ], [m])
    check("cloud path drops empty quotes too",
          len(cloud) == 1 and "ship on Friday" in cloud[0]["quote"])


# ---------------------------------------------------------------- P2-08 --
def test_summary_webhook_semantics():
    print("== P2-08: 'after summary' webhook only fires when a summary exists ==")
    from meeting_notes.config import Config
    from meeting_notes.notes import service as notes_service
    from meeting_notes.pipeline import processing

    fired = []
    real_fire = processing.fire_webhook
    real_ready, real_gen = notes_service.ready, notes_service.generate_notes
    processing.fire_webhook = lambda repo, mid, cfg, progress: fired.append(mid)
    try:
        repo = new_repo()
        cfg = Config()
        cfg.webhook_url, cfg.webhook_when = "https://example.com/hook", "summary"

        notes_service.ready = lambda _cfg: True
        notes_service.generate_notes = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model down"))
        m = repo.create(date_text="d", date_iso="2026-07-13", attendees=[])
        processing._summarise(repo, m.id, cfg, m, "some transcript", True, lambda _s: None)
        check("notes failure -> NO summary webhook", fired == [])
        check("meeting lands in Transcribed with the notes error",
              repo.get(m.id).status == "Transcribed" and "Notes failed" in (repo.get(m.id).error or ""))

        fake_notes = SimpleNamespace(title="T", attendees=[],
                                     model_dump_json=lambda: json.dumps({"summary": "s"}))
        notes_service.generate_notes = lambda *a, **k: fake_notes
        m2 = repo.create(date_text="d", date_iso="2026-07-13", attendees=[])
        processing._summarise(repo, m2.id, cfg, m2, "some transcript", True, lambda _s: None)
        check("successful notes -> webhook fires once", fired == [m2.id])

        notes_service.ready = lambda _cfg: False  # AI deliberately not configured
        m3 = repo.create(date_text="d", date_iso="2026-07-13", attendees=[])
        processing._summarise(repo, m3.id, cfg, m3, "some transcript", True, lambda _s: None)
        check("notes deliberately skipped (no AI) -> webhook still fires (terminal state)",
              fired == [m2.id, m3.id])
    finally:
        processing.fire_webhook = real_fire
        notes_service.ready, notes_service.generate_notes = real_ready, real_gen


# ---------------------------------------------------------------- P2-12 --
def test_cloud_base_https_guard():
    print("== P2-12: cloud_api_base must be HTTPS or loopback ==")
    from meeting_notes.config import Config
    from meeting_notes.paths import config_path

    default = Config.__dataclass_fields__["cloud_api_base"].default

    def load_with(base):
        p = config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"cloud_api_base": base}), encoding="utf-8")
        return Config.load().cloud_api_base

    check("plain-http remote host falls back to production",
          load_with("http://evil.example") == default)
    check("scheme-less value falls back to production",
          load_with("api.evil.example") == default)
    check("empty value falls back to production", load_with("") == default)
    check("loopback http kept (local dev server)",
          load_with("http://localhost:8787") == "http://localhost:8787")
    check("127.0.0.1 http kept", load_with("http://127.0.0.1:8787") == "http://127.0.0.1:8787")
    check("https endpoint kept",
          load_with("https://api.tryearshot.app") == "https://api.tryearshot.app")
    # restore a clean default config so later tests aren't affected
    config_path().unlink(missing_ok=True)


def main() -> int:
    test_spool_write_error_latched()
    test_delete_meeting_honest()
    test_todoist_partial_failure()
    test_citation_verification()
    test_summary_webhook_semantics()
    test_cloud_base_https_guard()
    print("\nAUDIT-FIX TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
