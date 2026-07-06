"""Tests for the Earshot Plus (cloud) client + service dispatch.

Covers:
  - the /v1/transcribe request SHAPE via httpx's REAL encoder (httpx.Request().read()),
    never stub-only (the v0.22.1 lesson: list-of-tuples data crashed the encoder);
  - error-slug → friendly-message mapping + connect-error "servers aren't live" copy;
  - transcription + notes service dispatch by account_mode;
  - notes / action / ask cloud routing with stubbed httpx;
  - config round-trips of the new cloud fields.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_cloud.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Isolate ALL app data to a throwaway dir so the test can never read or overwrite
# the real %LOCALAPPDATA%\Earshot\config.json (which would wipe the user's key/URL).
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from meeting_notes.config import Config  # noqa: E402
from meeting_notes.notes import earshot_llm  # noqa: E402
from meeting_notes.transcription import earshot_client as ec  # noqa: E402
from meeting_notes.transcription import service as tservice  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


class _Resp:
    def __init__(self, status, payload=None, *, text="err"):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        if self._payload is _NO_JSON:
            raise ValueError("no json")
        return self._payload


_NO_JSON = object()


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    tone = 0.3 * np.sin(2 * np.pi * 440 * np.arange(16000, dtype=np.float32) / 16000)
    small = tmp / "a.flac"
    sf.write(str(small), tone, 16000, subtype="PCM_16")

    # ---------------------------------------------------------------
    print("== config: new cloud fields default + round-trip ==")
    cfg = Config()
    check("account_mode defaults to selfhost", cfg.account_mode == "selfhost")
    check("cloud_api_base defaults to api.tryearshot.app",
          cfg.cloud_api_base == "https://api.tryearshot.app")
    check("cloud_token empty by default", cfg.cloud_token == "")
    check("onboarding_done False by default", cfg.onboarding_done is False)
    cfg.account_mode = "cloud"
    cfg.cloud_token = "tok_abc"
    cfg.cloud_email = "hayden@example.com"
    cfg.onboarding_done = True
    cfg.save()
    reloaded = Config.load()
    check("account_mode round-trips", reloaded.account_mode == "cloud")
    check("cloud_token round-trips", reloaded.cloud_token == "tok_abc")
    check("cloud_email round-trips", reloaded.cloud_email == "hayden@example.com")
    check("onboarding_done round-trips", reloaded.onboarding_done is True)

    # ---------------------------------------------------------------
    print("== /v1/transcribe request SHAPE (httpx REAL encoder) ==")
    seen = {}

    def encoding_post(url, headers=None, data=None, files=None, timeout=None):
        # Run httpx's REAL multipart encoder — a list-of-tuples `data=` alongside
        # `files=` crashes it (the shipped v0.22.1 bug). .read() forces encoding.
        req = httpx.Request("POST", url, headers=headers or {}, data=data, files=files)
        req.read()
        seen["url"] = url
        seen["auth"] = (headers or {}).get("Authorization")
        seen["data"] = dict(data) if data else {}
        seen["file_field"] = "file" in (files or {})
        seen["mime"] = files["file"][2] if files and "file" in files else None
        return _Resp(200, {"text": "hello", "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}]})

    orig_post = ec.httpx.post
    ec.httpx.post = encoding_post
    try:
        result = ec.transcribe(small, base_url="https://api.tryearshot.app", token="tok",
                               language="en")
        check("real multipart encodes without error", result["text"] == "hello")
        check("posts to /v1/transcribe", seen["url"].endswith("/v1/transcribe"))
        check("sends Bearer token", seen["auth"] == "Bearer tok")
        check("file field is named 'file'", seen["file_field"] is True)
        check("flac mime set", seen["mime"] == "audio/flac")
        check("language sent as form field", seen["data"].get("language") == "en")
        check("data is a dict (not list-of-tuples)", isinstance(seen["data"], dict))
        check("segments parsed", len(result["segments"]) == 1)

        # blank language must NOT add the field
        seen.clear()
        ec.transcribe(small, base_url="https://api.tryearshot.app", token="tok", language="")
        check("blank language omits the form field", "language" not in seen["data"])
    finally:
        ec.httpx.post = orig_post

    # ---------------------------------------------------------------
    print("== error-slug → friendly message mapping ==")

    def make_err_post(status, slug, extra=None):
        payload = {"error": {"code": slug, "message": "server sentence"}}
        if extra:
            payload["error"].update(extra)

        def _post(url, headers=None, data=None, files=None, timeout=None):
            return _Resp(status, payload)
        return _post

    cases = [
        (401, "auth_invalid", "session has expired"),
        (402, "sub_inactive", "subscription isn't active"),
        (413, "too_large", "too large"),
        (502, "upstream_error", "temporary problem"),
    ]
    for status, slug, needle in cases:
        ec.httpx.post = make_err_post(status, slug)
        try:
            ec.transcribe(small, base_url="https://x", token="t")
            check(f"{slug} raised", False)
        except ec.CloudError as e:
            check(f"{slug} → friendly ({needle!r})", needle in str(e))
        finally:
            ec.httpx.post = orig_post

    print("== cap_reached includes retry_after_days ==")
    ec.httpx.post = make_err_post(429, "cap_reached", {"retry_after_days": 12})
    try:
        ec.transcribe(small, base_url="https://x", token="t")
        check("cap raised", False)
    except ec.CloudError as e:
        check("cap message mentions the reset window", "12 day" in str(e))
    finally:
        ec.httpx.post = orig_post

    print("== connect error → 'servers aren't live yet' ==")

    def connect_error_post(url, headers=None, data=None, files=None, timeout=None):
        raise httpx.ConnectError("connection refused")

    ec.httpx.post = connect_error_post
    try:
        ec.transcribe(small, base_url="https://api.tryearshot.app", token="t")
        check("connect error raised", False)
    except ec.CloudError as e:
        check("connect error shows the friendly not-live copy",
              str(e) == ec.SERVERS_NOT_LIVE)
    finally:
        ec.httpx.post = orig_post

    print("== unknown slug falls back to the server sentence ==")

    def unknown_slug_post(url, headers=None, data=None, files=None, timeout=None):
        return _Resp(500, {"error": {"code": "mystery", "message": "something odd"}})

    ec.httpx.post = unknown_slug_post
    try:
        ec.transcribe(small, base_url="https://x", token="t")
        check("unknown slug raised", False)
    except ec.CloudError as e:
        check("unknown slug uses the server message", "something odd" in str(e))
    finally:
        ec.httpx.post = orig_post

    # ---------------------------------------------------------------
    print("== ping / get_me via GET /v1/me ==")

    def me_get_ok(url, headers=None, timeout=None):
        check("me hits /v1/me", url.endswith("/v1/me"))
        return _Resp(200, {"email": "h@x.io", "plan": "plus", "sub_status": "active",
                           "usage": {"transcribe_seconds": 3600, "cap_seconds": 144000}})

    orig_get = ec.httpx.get
    ec.httpx.get = me_get_ok
    try:
        check("ping True on 200", ec.ping("https://x", "tok") is True)
        data = ec.get_me("https://x", "tok")
        check("get_me returns the account dict", data["sub_status"] == "active")
    finally:
        ec.httpx.get = orig_get

    def me_get_401(url, headers=None, timeout=None):
        return _Resp(401, {"error": {"code": "auth_invalid", "message": "no"}})

    ec.httpx.get = me_get_401
    try:
        check("ping False on 401", ec.ping("https://x", "tok") is False)
    finally:
        ec.httpx.get = orig_get

    # ---------------------------------------------------------------
    print("== transcription service dispatch by account_mode ==")
    ccfg = Config()
    ccfg.account_mode = "cloud"
    ccfg.cloud_token = "tok"
    ccfg.whisper_language = "en"

    routed = {}

    def route_post(url, headers=None, data=None, files=None, timeout=None):
        routed["url"] = url
        return _Resp(200, {"text": "routed", "segments": []})

    ec.httpx.post = route_post
    try:
        out = tservice.transcribe(small, ccfg)
        check("cloud mode routes through the earshot client", out["text"] == "routed")
        check("service posts to /v1/transcribe", routed["url"].endswith("/v1/transcribe"))
    finally:
        ec.httpx.post = orig_post

    ec.httpx.get = me_get_ok
    try:
        check("test_connection uses /v1/me in cloud mode", tservice.test_connection(ccfg) is True)
    finally:
        ec.httpx.get = orig_get

    # ---------------------------------------------------------------
    print("== notes service: ready/missing_hint + generate_notes cloud routing ==")
    from meeting_notes.notes import service as nservice  # noqa: E402

    check("ready() True when cloud_token set", nservice.ready(ccfg) is True)
    empty_cloud = Config()
    empty_cloud.account_mode = "cloud"
    check("ready() False when no cloud_token", nservice.ready(empty_cloud) is False)
    check("missing_hint points at the Account page",
          "Account page" in nservice.missing_hint(empty_cloud))

    def notes_post(url, headers=None, json=None, timeout=None):
        check("notes posts to /v1/notes", url.endswith("/v1/notes"))
        check("notes sends transcript in body", json.get("transcript") == "hello team")
        return _Resp(200, {
            "title": "Sync", "summary": "We synced.", "attendees": ["A"],
            "action_items": [{"task": "Do it", "owner": "A", "done": False}],
            "sections": [{"heading": "Topic", "bullets": ["a point"]}],
        })

    orig_llm_post = earshot_llm.httpx.post
    earshot_llm.httpx.post = notes_post
    try:
        notes = nservice.generate_notes("hello team", ccfg, attendees=["A"], human_date="6 Jul 2026")
        check("cloud notes validated into MeetingNotes", notes.title == "Sync")
        check("action items marked unconfirmed (suggestions)",
              notes.action_items[0].confirmed is False)
    finally:
        earshot_llm.httpx.post = orig_llm_post

    print("== notes service: run_action cloud routing → /v1/action ==")

    def action_post(url, headers=None, json=None, timeout=None):
        check("action posts to /v1/action", url.endswith("/v1/action"))
        check("action sends instruction + context",
              json.get("instruction") == "Summarise" and "context" in json)
        return _Resp(200, {"text": "a summary"})

    earshot_llm.httpx.post = action_post
    try:
        out = nservice.run_action("Summarise", ccfg, transcript="t", title="M")
        check("run_action returns proxy text", out == "a summary")
    finally:
        earshot_llm.httpx.post = orig_llm_post

    # ---------------------------------------------------------------
    print("== ask: cloud routing → /v1/ask + citation verification ==")
    from meeting_notes.qa import ask as qa_ask  # noqa: E402

    class _M:
        def __init__(self, mid, title, transcript):
            self.id = mid
            self.title = title
            self.transcript = transcript
            self.notes = None
            self.attendees = []
            self.date_text = "6 Jul 2026"
            self.status = "Done"

    meetings = [_M(1, "Budget review", "We agreed the budget is forty thousand.")]

    def ask_post(url, headers=None, json=None, timeout=None):
        check("ask posts to /v1/ask", url.endswith("/v1/ask"))
        check("ask sends context_blocks", isinstance(json.get("context_blocks"), list))
        return _Resp(200, {
            "answer_html": "The budget is <b>forty thousand</b>.",
            "citations": [
                {"meeting_title": "Budget review", "quote": "the budget is forty thousand"},
                {"meeting_title": "Budget review", "quote": "a line that never appears"},
            ],
        })

    earshot_llm.httpx.post = ask_post
    try:
        ans = qa_ask.answer("What is the budget?", meetings=meetings, cfg=ccfg, today="2026-07-06")
        check("answer_html stripped to plain text", "<b>" not in ans.text and "forty thousand" in ans.text)
        check("only the verifiable citation survives", len(ans.citations) == 1)
        check("verified citation carries meeting_id + title",
              ans.citations[0]["meeting_id"] == 1 and ans.citations[0]["title"] == "Budget review")
    finally:
        earshot_llm.httpx.post = orig_llm_post

    # ---------------------------------------------------------------
    print("== device code + poll shape ==")

    def code_post(url, headers=None, json=None, timeout=None):
        check("code posts to /v1/device/code", url.endswith("/v1/device/code"))
        check("code body has app_version + device_name",
              "app_version" in json and "device_name" in json)
        return _Resp(200, {"code": "ABC-123", "poll_token": "pt", "verify_url": "https://x/link",
                           "expires_in": 900, "interval": 3})

    ec.httpx.post = code_post
    try:
        info = ec.request_device_code("https://x", app_version="0.24.0", device_name="PC")
        check("device code returned", info["code"] == "ABC-123")
    finally:
        ec.httpx.post = orig_post

    def poll_410(url, headers=None, json=None, timeout=None):
        return _Resp(410, {"error": {"code": "code_expired", "message": "gone"}})

    ec.httpx.post = poll_410
    try:
        out = ec.poll_device("https://x", poll_token="pt")
        check("410 poll → status expired", out["status"] == "expired")
    finally:
        ec.httpx.post = orig_post

    def poll_ok(url, headers=None, json=None, timeout=None):
        return _Resp(200, {"status": "ok", "device_token": "dt", "email": "h@x.io",
                           "plan": "plus", "sub_status": "active"})

    ec.httpx.post = poll_ok
    try:
        out = ec.poll_device("https://x", poll_token="pt")
        check("approved poll → device_token", out["device_token"] == "dt")
    finally:
        ec.httpx.post = orig_post

    print("\nCLOUD TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
