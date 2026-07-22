"""Regression tests for the audit hardening: config crash-safety, startup
recovery of interrupted meetings, FTS robustness, and webhook guards.

Run:  python tests/test_resilience.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meeting_notes import paths  # noqa: E402
from meeting_notes.config import Config  # noqa: E402
from meeting_notes.integrations import webhook  # noqa: E402
from meeting_notes.storage import db as dbmod  # noqa: E402
from meeting_notes.storage.repository import MeetingRepository  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


def main() -> int:
    print("== macOS legacy-folder migration guard ==")
    candidate = Path(tempfile.mkdtemp()) / "Earshot"
    candidate.mkdir()
    (candidate / "holiday-photos.txt").write_text("not app data", encoding="utf-8")
    check("generic ~/Earshot-style folder is not adopted",
          not paths._looks_like_earshot_data(candidate))
    (candidate / "config.json").write_text("{}", encoding="utf-8")
    check("recognizable Earshot data remains migratable",
          paths._looks_like_earshot_data(candidate))

    print("== config crash-safety ==")
    cfg = Config.load()
    cfg.anthropic_api_key = "sk-secret"
    cfg.templates = [{"name": "Sales", "instructions": "x"}]
    cfg.save()
    check("atomic save round-trips", Config.load().anthropic_api_key == "sk-secret")
    # no leftover temp file
    check("no .tmp left behind", not paths.config_path().with_suffix(".json.tmp").exists())

    # corrupt file → defaults, and the bad file is preserved (not silently wiped)
    paths.config_path().write_text("{ this is not json", encoding="utf-8")
    recovered = Config.load()
    check("corrupt config → safe defaults", recovered.anthropic_api_key == "")
    check("corrupt config preserved as .bad", paths.config_path().with_suffix(".json.bad").exists())

    # valid JSON but wrong top-level type → defaults, not a crash
    paths.config_path().write_text("[1, 2, 3]", encoding="utf-8")
    check("non-object JSON → defaults", Config.load().anthropic_api_key == "")

    # wrong-typed container field is ignored (default wins), doesn't crash
    paths.config_path().write_text(json.dumps({"templates": {"bad": "shape"}, "theme_mode": "dark"}),
                                   encoding="utf-8")
    c = Config.load()
    check("wrong-typed templates ignored", c.templates == [] and c.theme_mode == "dark")

    # a field a newer version wrote (demoted to extra by an older one) is re-promoted
    paths.config_path().write_text(json.dumps({"extra": {"sidebar_side": "right"}}), encoding="utf-8")
    c = Config.load()
    check("known field re-promoted out of extra", c.sidebar_side == "right" and "sidebar_side" not in c.extra)

    print("== startup recovery of interrupted meetings ==")
    repo = MeetingRepository(dbmod.connect(Path(tempfile.mkdtemp()) / "r.db"))
    a = repo.create(date_text="d", date_iso="2026-01-01", attendees=[])
    b = repo.create(date_text="d", date_iso="2026-01-01", attendees=[])
    done = repo.create(date_text="d", date_iso="2026-01-01", attendees=[])
    repo.update(a.id, status="Recording")
    repo.update(b.id, status="Transcribing")
    repo.update(done.id, status="Done")
    n = repo.recover_interrupted()
    check("two interrupted meetings reset", n == 2)
    check("Recording → Error", repo.get(a.id).status == "Error")
    check("Transcribing → Error with hint", repo.get(b.id).status == "Error" and "Re-transcribe" in (repo.get(b.id).error or ""))
    check("Done left untouched", repo.get(done.id).status == "Done")

    print("== FTS robust to wrong-shape notes ==")
    m = repo.create(date_text="d", date_iso="2026-01-01", attendees=["Alice"])
    repo.update(m.id, notes_json=json.dumps(["not", "a", "dict"]))  # wrong shape
    repo.update(m.id, transcript="pricing discussion widget")
    check("search still works despite bad notes_json", m.id in repo.search("widget"))
    check("bad notes_json reads as None", repo.get(m.id).notes is None)

    print("== webhook payload: folder routing ==")
    acme = repo.create_folder("Acme Corp", "#EF4444")
    filed = repo.create(date_text="d", date_iso="2026-01-02", attendees=[], folder_id=acme.id)
    repo.update(filed.id, title="Filed meeting", transcript="t")
    folder = next((f for f in repo.list_folders() if f.id == repo.get(filed.id).folder_id), None)
    p = webhook.build_payload(repo.get(filed.id), folder=folder)
    check("payload carries the folder for routing",
          p["folder"] == {"id": acme.id, "name": "Acme Corp", "color": "#EF4444"})
    check("unfiled meeting → folder is null",
          webhook.build_payload(repo.get(m.id), folder=None)["folder"] is None)
    legacy = webhook.build_payload(repo.get(m.id))  # old single-arg call still works
    check("backward-compatible: folder key present, other keys unchanged",
          legacy["folder"] is None and legacy["id"] == m.id and "transcript" in legacy
          and "notes" in legacy and "bookmarks" in legacy)
    repo.close()

    print("== webhook guards ==")
    try:
        webhook.send("file:///etc/passwd", {"x": 1})
        check("non-http scheme rejected", False)
    except ValueError:
        check("non-http scheme rejected", True)
    webhook.send("", {"x": 1})  # blank = no-op, must not raise
    check("blank URL is a no-op", True)

    print("\nRESILIENCE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
