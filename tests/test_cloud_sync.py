"""Tests for Cloud sync (integrations/cloud_sync.py + ui/sync_scheduler.py).

Covers: the meeting payload + canonical hash (stable, excludes audio/bookmarks/
share links), the folders.sync_excluded migration on an old DB, the repository
change listener, and reconcile() against a fake server built on
httpx.MockTransport. The fake server receives the REAL encoded httpx requests
(per the v0.22.1 lesson: never only stub httpx), so the JSON bodies, headers
and query strings asserted here are exactly what goes over the wire.
reconcile cases: push-changed-only, delete-stale, excluded folder -> delete,
held mid-processing meetings, 401 / 402 / network errors, batching by count
and size, 413 skip without looping, the 2M-char transcript cap, delete-all.
Then the scheduler (one run at a time, debounce, 401 halt) and the Account
page toggle / sign-out behaviour.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_cloud_sync.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from meeting_notes.config import Config  # noqa: E402
from meeting_notes.integrations import cloud_sync as cs  # noqa: E402
from meeting_notes.storage import db as dbmod  # noqa: E402
from meeting_notes.storage.repository import MeetingRepository  # noqa: E402

PASSED = 0


def check(label, cond):
    global PASSED
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label
    PASSED += 1


NOTES = {"title": "Budget", "summary": "We agreed the cap.", "attendees": ["Hayden"],
         "action_items": [{"task": "Send deck", "owner": "Hayden"}],
         "sections": [{"heading": "Money", "bullets": ["Cap $40k"]}]}


# ----------------------------------------------------------------- fake server
class FakeServer:
    """In-memory /v1/sync/* server. Every request arrives as a real, fully
    encoded httpx.Request (MockTransport), so .read() is the actual body."""

    def __init__(self, token="tok"):
        self.token = token
        self.libs: dict[str, dict[str, str]] = {}
        self.requests: list[dict] = []
        self.force_status = 0          # e.g. 401 / 402 / 429 on every request
        self.reject_ids: set[int] = set()   # PUTs containing these ids get 413
        self.max_body = 0
        self.team = {"name": "Whittle Digital", "members": ["Hayden", "Talissa"]}
        self.gate: threading.Event | None = None   # blocks GET /state until set
        self.inflight = 0
        self.max_inflight = 0
        self._lock = threading.Lock()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _err(self, status, code, msg="nope"):
        return httpx.Response(status, json={"error": {"code": code, "message": msg}})

    def handle(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            return self._handle(request)
        finally:
            with self._lock:
                self.inflight -= 1

    def _handle(self, request: httpx.Request) -> httpx.Response:
        raw = request.read()
        body = json.loads(raw) if raw else None
        self.requests.append({"method": request.method, "path": request.url.path,
                              "params": dict(request.url.params), "body": body,
                              "raw": raw, "headers": dict(request.headers)})
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return self._err(401, "auth_invalid")
        if self.force_status == 401:
            return self._err(401, "auth_invalid")
        if self.force_status == 402:
            return self._err(402, "sub_inactive")
        if self.force_status == 429:
            return self._err(429, "rate_limited")
        path = request.url.path
        if request.method == "GET" and path == "/v1/sync/state":
            if self.gate is not None:
                self.gate.wait(10)
            lib = request.url.params.get("library_id", "")
            return httpx.Response(200, json={"library_id": lib,
                                             "meetings": dict(self.libs.get(lib, {})),
                                             "team": self.team})
        if request.method == "PUT" and path == "/v1/sync/meetings":
            self.max_body = max(self.max_body, len(raw))
            ms = body["meetings"]
            if len(ms) > 25:
                return self._err(400, "bad_request")
            if any(m["id"] in self.reject_ids for m in ms):
                return self._err(413, "too_large")
            lib = self.libs.setdefault(body["library_id"], {})
            for m in ms:
                lib[str(m["id"])] = m["hash"]
            return httpx.Response(200, json={"ok": True, "upserted": len(ms)})
        if request.method == "POST" and path == "/v1/sync/delete":
            lib = self.libs.setdefault(body["library_id"], {})
            if body.get("all"):
                n = len(lib)
                lib.clear()
            else:
                n = sum(1 for i in body["ids"] if lib.pop(str(i), None) is not None)
            return httpx.Response(200, json={"ok": True, "deleted": n})
        return self._err(404, "not_found")

    def of(self, method, path=None):
        return [r for r in self.requests if r["method"] == method
                and (path is None or r["path"] == path)]

    def reset_log(self):
        self.requests.clear()


def make_cfg(**kw) -> Config:
    cfg = Config()
    cfg.account_mode = "cloud"
    cfg.cloud_token = "tok"
    cfg.cloud_api_base = "https://api.example.test"
    cfg.cloud_sync_enabled = True
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def new_repo() -> MeetingRepository:
    conn = dbmod.connect(Path(tempfile.mkdtemp()) / "m.db")
    return MeetingRepository(conn)


def add(repo, *, status="Done", transcript="Me: hi\nThem: hello", notes=None, folder_id=None,
        title="A meeting"):
    m = repo.create(date_text="9th October 2026", date_iso="2026-10-09", attendees=["Hayden"],
                    folder_id=folder_id)
    repo.update(m.id, title=title, status=status, transcript=transcript,
                notes_json=json.dumps(notes) if notes is not None else None,
                duration_secs=60.0)
    return m.id


def main() -> int:
    app = QApplication.instance() or QApplication([])

    # ------------------------------------------------------------------
    print("== payload + canonical hash ==")
    repo = new_repo()
    fid = repo.create_folder("Infinite Wealth", "#6366F1").id
    mid = add(repo, notes=NOTES, folder_id=fid, title="Q3 Budget")
    repo.update(mid, bookmarks=[{"ms": 1000, "label": "x"}], share_url="https://x/y",
                audio_dir="/tmp/secret")
    m = repo.get(mid)
    p = cs.build_meeting_payload(m, "Infinite Wealth")
    expected_keys = {"id", "hash", "title", "date_text", "date_iso", "attendees", "agenda",
                     "transcript", "notes", "folder", "duration_secs", "status", "template",
                     "created_at", "updated_at"}
    check("payload has exactly the contract keys", set(p) == expected_keys)
    check("no audio / bookmarks / share link in the payload",
          "secret" not in json.dumps(p) and "bookmarks" not in p and "https://x/y" not in json.dumps(p))
    check("notes are the parsed object", p["notes"] == NOTES)
    check("folder carried by name", p["folder"] == "Infinite Wealth")
    check("duration is a float", isinstance(p["duration_secs"], float))
    manual = hashlib.sha256(json.dumps({k: v for k, v in p.items() if k != "hash"},
                                       sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    check("hash = sha256 of canonical JSON without hash", p["hash"] == manual)
    check("hash is stable across builds", cs.build_meeting_payload(m, "Infinite Wealth")["hash"] == p["hash"])
    reordered = dict(reversed(list({k: v for k, v in p.items() if k != "hash"}.items())))
    check("hash ignores key order", cs.payload_hash(reordered) == p["hash"])
    check("hash changes when the folder name changes",
          cs.build_meeting_payload(m, "Renamed")["hash"] != p["hash"])
    m.title = "Q3 Budget (edited)"
    check("hash changes when the title changes",
          cs.build_meeting_payload(m, "Infinite Wealth")["hash"] != p["hash"])
    m.transcript = "Me: café ☃"
    up = cs.build_meeting_payload(m, "")
    check("unicode hashes as UTF-8 (ensure_ascii=False)",
          up["hash"] == hashlib.sha256(json.dumps({k: v for k, v in up.items() if k != "hash"},
                                                  sort_keys=True, ensure_ascii=False,
                                                  separators=(",", ":")).encode()).hexdigest())
    bad = repo.get(mid)
    bad.notes_json = "[1,2]"
    check("non-dict notes_json sends null notes", cs.build_meeting_payload(bad, "")["notes"] is None)

    # ------------------------------------------------------------------
    print("== migration: folders.sync_excluded on an old DB ==")
    old = Path(tempfile.mkdtemp()) / "old.db"
    c = sqlite3.connect(str(old))
    c.executescript("""
        CREATE TABLE meetings (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, date_text TEXT,
            date_iso TEXT, attendees TEXT, transcript TEXT, notes_json TEXT, audio_dir TEXT,
            headphones_mode INTEGER DEFAULT 1, duration_secs REAL, status TEXT DEFAULT 'New',
            error TEXT, notion_page_id TEXT, notion_synced_at TEXT,
            created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE folders (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '#6366F1', created_at TEXT DEFAULT (datetime('now')));
        INSERT INTO folders (name) VALUES ('Old client');
    """)
    c.commit()
    c.close()
    cols = lambda conn: {r[1] for r in conn.execute("PRAGMA table_info(folders)")}  # noqa: E731
    c = sqlite3.connect(str(old))
    check("old DB starts without the column", "sync_excluded" not in cols(c))
    c.close()
    orepo = MeetingRepository(dbmod.connect(old))
    check("init_db adds folders.sync_excluded", "sync_excluded" in cols(orepo.conn))
    check("existing folders default to synced",
          [f.sync_excluded for f in orepo.list_folders()] == [False])
    dbmod.init_db(orepo.conn)
    check("migration is idempotent", "sync_excluded" in cols(orepo.conn))
    f0 = orepo.list_folders()[0]
    orepo.set_folder_sync_excluded(f0.id, True)
    check("opt-out persists", orepo.list_folders()[0].sync_excluded is True)

    # ------------------------------------------------------------------
    print("== repository change listener ==")
    hits = []
    repo.add_change_listener(lambda: hits.append(1))
    repo.update(mid, title="x")
    check("content update fires", len(hits) == 1)
    repo.update(mid, bookmarks=[])
    check("bookmarks-only update does NOT fire", len(hits) == 1)
    repo.update(mid, folder_id=None)
    check("folder move fires", len(hits) == 2)
    f2 = repo.create_folder("Tmp", "#000000")
    repo.update_folder(f2.id, name="Tmp2")
    repo.set_folder_sync_excluded(f2.id, True)
    repo.delete_folder(f2.id)
    check("folder create/rename/opt-out/delete each fire", len(hits) == 6)
    nid = add(repo)
    before = len(hits)
    repo.delete(nid)
    check("meeting delete fires", len(hits) == before + 1)

    def boom():
        raise RuntimeError("listener bug")
    repo.add_change_listener(boom)
    repo.update(mid, title="still saves")
    check("a failing listener never breaks the write", repo.get(mid).title == "still saves")
    repo.remove_change_listener(boom)

    # ------------------------------------------------------------------
    print("== reconcile: first sync pushes the syncable set only ==")
    repo = new_repo()
    srv = FakeServer()
    tr = srv.transport()
    cfg = make_cfg()
    excl = repo.create_folder("Private", "#111111")
    ids = {
        "done": add(repo, notes=NOTES),
        "transcribed": add(repo, status="Transcribed"),
        "error_with_text": add(repo, status="Error"),
        "new": add(repo, status="New", transcript=""),
        "recording": add(repo, status="Recording", transcript=""),
        "recorded": add(repo, status="Recorded", transcript=""),
        "transcribing": add(repo, status="Transcribing", transcript=""),
        "error_empty": add(repo, status="Error", transcript=""),
        "excluded": add(repo, folder_id=excl.id),
    }
    repo.set_folder_sync_excluded(excl.id, True)
    res = cs.reconcile(repo, cfg, transport=tr, device="Test Mac")
    check("reconcile ok", res.ok and not res.error)
    check("library id generated on first sync", len(cfg.cloud_sync_library_id) == 36)
    lib = srv.libs[cfg.cloud_sync_library_id]
    check("pushed exactly the syncable meetings",
          set(lib) == {str(ids["done"]), str(ids["transcribed"]), str(ids["error_with_text"])})
    check("result counts", res.pushed == 3 and res.deleted == 0 and res.total == 3)
    check("team info returned", res.team == srv.team)

    print("== the REAL encoded requests ==")
    get = srv.of("GET")[0]
    check("state GET carries library_id in the query", get["params"]["library_id"] == cfg.cloud_sync_library_id)
    check("bearer token header sent", get["headers"]["authorization"] == "Bearer tok")
    put = srv.of("PUT")[0]
    check("PUT is JSON", put["headers"]["content-type"] == "application/json")
    check("PUT body is valid UTF-8 JSON bytes", isinstance(put["raw"], bytes)
          and json.loads(put["raw"].decode("utf-8"))["library_id"] == cfg.cloud_sync_library_id)
    check("PUT carries the device name", put["body"]["device_name"] == "Test Mac")
    sent = {m_["id"]: m_ for m_ in put["body"]["meetings"]}
    check("ids sent as integers", all(isinstance(k, int) for k in sent))
    check("sent hash verifies against the sent content",
          all(cs.payload_hash(m_) == m_["hash"] for m_ in sent.values()))
    check("sent notes are an object, not a string", isinstance(sent[ids["done"]]["notes"], dict))
    check("no forbidden keys on the wire", not any(k in put["raw"].decode() for k in
                                                   ('"audio_dir"', '"bookmarks"', '"share_url"')))

    print("== push-changed-only ==")
    srv.reset_log()
    res = cs.reconcile(repo, cfg, transport=tr)
    check("unchanged library: no PUT, no delete", res.ok and not srv.of("PUT") and not srv.of("POST"))
    repo.update(ids["done"], title="Renamed meeting")
    srv.reset_log()
    res = cs.reconcile(repo, cfg, transport=tr)
    put_ids = [m_["id"] for r in srv.of("PUT") for m_ in r["body"]["meetings"]]
    check("only the edited meeting is re-sent", put_ids == [ids["done"]] and res.pushed == 1)

    print("== delete-stale ==")
    repo.delete(ids["transcribed"])
    srv.reset_log()
    res = cs.reconcile(repo, cfg, transport=tr)
    dels = srv.of("POST", "/v1/sync/delete")
    check("locally deleted meeting removed from server",
          len(dels) == 1 and dels[0]["body"]["ids"] == [ids["transcribed"]]
          and str(ids["transcribed"]) not in lib)
    check("delete body is exactly {library_id, ids}",
          set(dels[0]["body"]) == {"library_id", "ids"})
    check("total reflects the delete", res.total == 2 and res.deleted == 1)

    print("== excluded folder -> delete, re-include -> push ==")
    work = repo.create_folder("Work", "#222222")
    repo.update(ids["done"], folder_id=work.id)
    cs.reconcile(repo, cfg, transport=tr)
    check("meeting in a synced folder is on the server", str(ids["done"]) in lib)
    repo.set_folder_sync_excluded(work.id, True)
    srv.reset_log()
    cs.reconcile(repo, cfg, transport=tr)
    check("excluding its folder deletes the server copy", str(ids["done"]) not in lib
          and srv.of("POST")[0]["body"]["ids"] == [ids["done"]])
    repo.set_folder_sync_excluded(work.id, False)
    cs.reconcile(repo, cfg, transport=tr)
    check("re-including it pushes it again", str(ids["done"]) in lib)
    check("meetings with no folder sync too", str(ids["error_with_text"]) in lib)

    print("== mid-processing meetings are held (not pushed, not deleted) ==")
    repo.update(ids["done"], status="Summarizing", title="half-done")
    srv.reset_log()
    res = cs.reconcile(repo, cfg, transport=tr)
    check("held meeting stays on the server", str(ids["done"]) in lib and not srv.of("POST"))
    check("held meeting not re-sent", not srv.of("PUT"))
    repo.update(ids["done"], status="Done")
    cs.reconcile(repo, cfg, transport=tr)
    check("pushed once it settles", lib[str(ids["done"])] ==
          cs.build_meeting_payload(repo.get(ids["done"]), "Work")["hash"])

    print("== 401 / 402 / 429 / network ==")
    srv.force_status = 401
    res = cs.reconcile(repo, cfg, transport=tr)
    check("401 -> not ok, auth_failed, signed-out copy",
          not res.ok and res.auth_failed and res.error == cs.MSG_SIGNED_OUT)
    srv.force_status = 402
    res = cs.reconcile(repo, cfg, transport=tr)
    check("402 -> needs_plus with Plus copy", not res.ok and res.needs_plus
          and res.error == cs.MSG_NEEDS_PLUS)
    srv.force_status = 429
    res = cs.reconcile(repo, cfg, transport=tr)
    check("429 -> rate-limited copy", not res.ok and res.status == 429 and res.error == cs.MSG_RATE_LIMITED)
    srv.force_status = 0

    def offline(_req):
        raise httpx.ConnectError("refused")
    res = cs.reconcile(repo, cfg, transport=httpx.MockTransport(offline))
    check("network error -> friendly offline copy, no crash",
          not res.ok and res.status == 0 and res.error == cs.MSG_OFFLINE)
    wrong = make_cfg(cloud_token="other", cloud_sync_library_id=cfg.cloud_sync_library_id)
    res = cs.reconcile(repo, wrong, transport=tr)
    check("bad token -> 401 from the server", res.auth_failed)
    off = make_cfg(cloud_sync_enabled=False)
    srv.reset_log()
    res = cs.reconcile(repo, off, transport=tr)
    check("disabled -> no requests at all", not res.ok and not srv.requests)
    signed_out = make_cfg(account_mode="selfhost")
    res = cs.reconcile(repo, signed_out, transport=tr)
    check("signed out -> no requests", not srv.requests)

    print("== batching by count and size ==")
    brepo = new_repo()
    for i in range(30):
        add(brepo, title=f"small {i}")
    bsrv = FakeServer()
    bcfg = make_cfg()
    res = cs.reconcile(brepo, bcfg, transport=bsrv.transport())
    puts = bsrv.of("PUT")
    check("30 small meetings -> 2 PUTs (25 + 5)",
          [len(r["body"]["meetings"]) for r in puts] == [25, 5] and res.pushed == 30)
    big = new_repo()
    for i in range(5):
        add(big, title=f"big {i}", transcript="word " * 70_000)   # ~350 KB each
    add(big, title="huge", transcript="x" * 1_200_000)             # > 900 KB on its own
    gsrv = FakeServer()
    res = cs.reconcile(big, make_cfg(), transport=gsrv.transport())
    gputs = gsrv.of("PUT")
    multi = [r for r in gputs if len(r["body"]["meetings"]) > 1]
    check("multi-meeting PUT bodies stay under 900 KB",
          multi and all(len(r["raw"]) <= cs.MAX_BODY_BYTES for r in multi))
    alone = [r for r in gputs if any(m_["title"] == "huge" for m_ in r["body"]["meetings"])]
    check("an oversized meeting goes alone", len(alone) == 1 and len(alone[0]["body"]["meetings"]) == 1)
    check("all 6 delivered", res.pushed == 6 and len(next(iter(gsrv.libs.values()))) == 6)
    plan = cs.plan_batches([{"id": i, "x": "y" * 100} for i in range(3)], library_id="L",
                           device_name="D", max_bytes=250)
    check("plan_batches splits by bytes", len(plan) >= 2 and sum(len(b) for b in plan) == 3)

    print("== 413: skip that meeting, record it, don't loop ==")
    trepo = new_repo()
    a = add(trepo, title="ok one")
    b = add(trepo, title="rejected one")
    c_ = add(trepo, title="ok two")
    tsrv = FakeServer()
    tsrv.reject_ids = {b}
    tcfg = make_cfg()
    res = cs.reconcile(trepo, tcfg, transport=tsrv.transport())
    tlib = tsrv.libs[tcfg.cloud_sync_library_id]
    check("other meetings still delivered", set(tlib) == {str(a), str(c_)})
    check("413 meeting recorded as skipped", str(b) in res.skipped and res.ok)
    check("bounded attempts (1 batch + 3 singles)", len(tsrv.of("PUT")) == 4)
    cs.apply_result(tcfg, res)
    tsrv.reset_log()
    res = cs.reconcile(trepo, tcfg, transport=tsrv.transport())
    check("next run does not resend the same refused version", not tsrv.of("PUT"))
    trepo.update(b, title="rejected one, edited")
    tsrv.reset_log()
    res = cs.reconcile(trepo, tcfg, transport=tsrv.transport())
    check("a changed version gets one fresh try", len(tsrv.of("PUT")) == 1 and str(b) in res.skipped)
    cs.apply_result(tcfg, res)
    check("status line mentions the skipped meeting", "1 too large to sync" in cs.status_line(tcfg))

    print("== transcript cap: never sent ==")
    crepo = new_repo()
    huge = add(crepo, transcript="y" * (cs.MAX_TRANSCRIPT_CHARS + 1))
    csrv = FakeServer()
    res = cs.reconcile(crepo, make_cfg(), transport=csrv.transport())
    check("over-cap transcript skipped client-side", not csrv.of("PUT") and str(huge) in res.skipped)

    print("== delete all ==")
    srv.reset_log()
    res = cs.delete_all(cfg, transport=tr)
    dreq = srv.of("POST")
    check("delete-all sends {library_id, all: true}",
          len(dreq) == 1 and dreq[0]["body"] == {"library_id": cfg.cloud_sync_library_id, "all": True})
    check("server copies gone", res.ok and lib == {})
    fresh = make_cfg()
    srv.reset_log()
    check("never-synced library: delete-all is a no-op", cs.delete_all(fresh, transport=tr).ok
          and not srv.requests)

    print("== status line ==")
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    s = make_cfg(cloud_sync_last_ok=(now - timedelta(minutes=2)).isoformat(), cloud_sync_count=56)
    check("last synced line", cs.status_line(s, now=now) == "Last synced 2 min ago · 56 meetings")
    s.cloud_sync_team = {"name": "Whittle Digital", "members": []}
    check("team appended", cs.status_line(s, now=now).endswith("shared with Whittle Digital"))
    s.cloud_sync_last_error = cs.MSG_NEEDS_PLUS
    check("error wins", cs.status_line(s, now=now) == cs.MSG_NEEDS_PLUS)
    check("running wins", cs.status_line(s, running=True, now=now) == "Syncing now...")

    print("== no em/en dashes in user-facing sync copy ==")
    from meeting_notes.ui import page_account
    copy = [cs.MSG_SIGNED_OUT, cs.MSG_NEEDS_PLUS, cs.MSG_RATE_LIMITED, cs.MSG_OFFLINE,
            page_account.SYNC_TOGGLE_TEXT, page_account.SYNC_EXPLAINER, cs.status_line(s, now=now)]
    root = Path(__file__).resolve().parent.parent / "meeting_notes"
    files = [root / "integrations" / "cloud_sync.py", root / "ui" / "sync_scheduler.py"]
    check("no dashes in sync strings", not any(("—" in t or "–" in t) for t in copy))
    check("no dashes anywhere in the sync modules",
          not any(("—" in f.read_text(encoding="utf-8") or "–" in f.read_text(encoding="utf-8"))
                  for f in files))

    # ------------------------------------------------------------------
    print("== scheduler ==")
    from meeting_notes.ui.sync_scheduler import SyncScheduler

    def pump(cond, secs=8.0):
        t0 = time.time()
        while time.time() - t0 < secs:
            app.processEvents()
            if cond():
                return True
            time.sleep(0.01)
        return False

    srepo = new_repo()
    add(srepo, title="sched one")
    ssrv = FakeServer()
    scfg = make_cfg()
    sched = SyncScheduler(srepo, scfg, transport=ssrv.transport())
    sched._debounce.setInterval(50)
    sched.sync_now()
    check("sync runs off the UI thread and finishes", pump(lambda: not sched.running
                                                         and scfg.cloud_sync_last_ok))
    check("result persisted to cfg", scfg.cloud_sync_count == 1 and not scfg.cloud_sync_last_error)
    check("library id persisted to the saved config",
          Config.load().cloud_sync_library_id == scfg.cloud_sync_library_id)

    ssrv.reset_log()
    add(srepo, title="sched two")
    check("a repo change triggers a debounced sync",
          pump(lambda: len(ssrv.of("PUT")) >= 1 and not sched.running))
    check("debounce coalesced the burst into one run", len(ssrv.of("GET")) == 1)

    print("== scheduler: never two at once ==")
    ssrv.reset_log()
    ssrv.gate = threading.Event()
    sched.sync_now()
    pump(lambda: len(ssrv.of("GET")) == 1, 3)
    sched.request_sync()
    sched.request_sync()
    sched.sync_now()
    check("still exactly one request in flight", ssrv.max_inflight == 1 and len(ssrv.of("GET")) == 1)
    ssrv.gate.set()
    check("queued triggers collapse into ONE follow-up run",
          pump(lambda: len(ssrv.of("GET")) == 2 and not sched.running))
    pump(lambda: False, 0.3)
    check("no third run", len(ssrv.of("GET")) == 2 and ssrv.max_inflight == 1)
    ssrv.gate = None

    print("== scheduler: 401 halts, Sync now resumes ==")
    ssrv.force_status = 401
    sched.sync_now()
    check("401 halts the scheduler", pump(lambda: sched.halted and not sched.running))
    check("signed-out status shown", scfg.cloud_sync_last_error == cs.MSG_SIGNED_OUT)
    ssrv.reset_log()
    srepo.update(srepo.list()[0].id, title="edited while halted")
    pump(lambda: False, 0.4)
    check("edits don't trigger syncs while halted", not ssrv.requests)
    ssrv.force_status = 0
    sched.sync_now()
    check("Sync now clears the halt", pump(lambda: not sched.halted and not sched.running
                                           and not scfg.cloud_sync_last_error))

    print("== scheduler: network error backs off ==")
    sched._transport = httpx.MockTransport(offline)
    sched.sync_now()
    check("offline -> error + retry scheduled",
          pump(lambda: not sched.running and scfg.cloud_sync_last_error == cs.MSG_OFFLINE)
          and sched._retry.isActive() and sched._backoff_s == SyncScheduler.BACKOFF_START_S)
    sched.request_sync()
    check("auto trigger during backoff doesn't run", not sched.running)
    sched._transport = ssrv.transport()
    sched.sync_now()
    check("manual sync ignores backoff and recovers",
          pump(lambda: not sched.running and not scfg.cloud_sync_last_error)
          and sched._backoff_s == 0)

    print("== scheduler: 402 waits an hour ==")
    ssrv.force_status = 402
    sched.sync_now()
    pump(lambda: not sched.running and scfg.cloud_sync_last_error == cs.MSG_NEEDS_PLUS)
    check("402 -> long retry", sched._retry.isActive()
          and sched._retry.interval() >= SyncScheduler.NEEDS_PLUS_WAIT_S * 1000 - 5)
    ssrv.force_status = 0
    sched.shutdown()

    # ------------------------------------------------------------------
    print("== Account page: toggle, delete-all prompt, sign out ==")
    from meeting_notes.ui.page_account import AccountPage
    from meeting_notes.ui.theme_controller import ThemeController

    prepo = new_repo()
    add(prepo, title="page one")
    psrv = FakeServer()
    pcfg = make_cfg(cloud_email="hayden@example.com", cloud_sync_enabled=False)
    psched = SyncScheduler(prepo, pcfg, transport=psrv.transport())

    class _Shell:
        sync = psched

        def refresh_account_card(self):
            pass

        def refresh_plan_state(self):
            pass

        def on_account_changed(self):
            psched.account_changed()

    import meeting_notes.ui.page_account as pa
    from meeting_notes.transcription import earshot_client as ec
    orig_get_me = ec.get_me
    ec.get_me = lambda *_a, **_k: {"sub_status": "active", "usage": {}}
    try:
        page = AccountPage(_Shell(), prepo, pcfg, ThemeController(pcfg))
        check("sync card shown when signed in", hasattr(page, "sync_toggle"))
        check("toggle copy", page.sync_toggle.text() == pa.SYNC_TOGGLE_TEXT)
        check("toggle starts off", not page.sync_toggle.isChecked())
        check("Sync now disabled while off", not page.sync_now_btn.isEnabled())
        page.sync_toggle.setChecked(True)
        check("turning on syncs", pump(lambda: not psched.running and pcfg.cloud_sync_count == 1))
        check("status label shows the count", "1 meeting" in page.sync_status_lbl.text())
        check("Sync now enabled while on", page.sync_now_btn.isEnabled())

        page._ask_remove_copies = lambda: False
        psrv.reset_log()
        page.sync_toggle.setChecked(False)
        pump(lambda: False, 0.3)
        check("off + keep copies -> nothing deleted", not psrv.of("POST") and pcfg.cloud_sync_enabled is False)
        check("status says off", page.sync_status_lbl.text() == "Sync is off.")

        page.sync_toggle.setChecked(True)
        pump(lambda: not psched.running and psrv.of("GET"))
        page._ask_remove_copies = lambda: True
        psrv.reset_log()
        page.sync_toggle.setChecked(False)
        check("off + remove copies -> delete all",
              pump(lambda: len(psrv.of("POST")) == 1 and not psched.running)
              and psrv.of("POST")[0]["body"].get("all") is True
              and psrv.libs[pcfg.cloud_sync_library_id] == {})

        page.sync_toggle.setChecked(True)
        pump(lambda: not psched.running and psrv.libs[pcfg.cloud_sync_library_id])
        ec_revoke = ec.revoke
        ec.revoke = lambda *_a, **_k: None
        psrv.reset_log()
        page._sign_out()
        pump(lambda: False, 0.3)
        ec.revoke = ec_revoke
        check("sign out stops syncing without deleting server data",
              not psrv.of("POST") and psrv.libs[pcfg.cloud_sync_library_id]
              and pcfg.cloud_sync_enabled is False)
        check("self-host page has no sync card", not hasattr(page, "sync_toggle"))
        prepo.update(prepo.list()[0].id, title="after sign out")
        pump(lambda: False, 0.3)
        check("no sync after sign out", not psrv.requests)
    finally:
        ec.get_me = orig_get_me
        psched.shutdown()

    # ------------------------------------------------------------------
    print("== project menu toggle + sidebar badge ==")
    from PySide6.QtWidgets import QLabel, QMenu

    from meeting_notes.ui.shell import Shell
    shrepo = new_repo()
    f = shrepo.create_folder("Client A", "#6366F1")
    shcfg = Config()
    shell = Shell(shrepo, shcfg, ThemeController(shcfg))
    menu = QMenu()
    act = Shell.add_sync_toggle_action(menu, shrepo.list_folders()[0])
    check("menu offers Don't sync", act.text() == "Don't sync this project")
    shell.toggle_folder_sync(shrepo.list_folders()[0])
    check("toggle excludes the folder", shrepo.list_folders()[0].sync_excluded)
    menu2 = QMenu()
    act2 = Shell.add_sync_toggle_action(menu2, shrepo.list_folders()[0])
    check("menu then offers to sync again", act2.text() == "Sync this project again")
    row = shell._project_rows[f.id]
    check("excluded folder row shows the not-synced badge",
          any(lbl.objectName() == "SyncOffBadge" for lbl in row.findChildren(QLabel)))
    shell.toggle_folder_sync(shrepo.list_folders()[0])
    row = shell._project_rows[f.id]
    check("badge gone when synced again",
          not any(lbl.objectName() == "SyncOffBadge" for lbl in row.findChildren(QLabel)))
    shell.sync.shutdown()
    shell.deleteLater()

    print(f"\nCLOUD SYNC TESTS PASSED ({PASSED} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
