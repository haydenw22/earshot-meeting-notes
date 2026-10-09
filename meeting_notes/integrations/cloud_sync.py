"""Cloud sync: keep a copy of this library's meetings (text only, never audio)
on the user's Earshot Plus account, so teammates' Claude can search them.

The whole sync model is one idempotent `reconcile()`:

  1. GET  /v1/sync/state?library_id=...  -> {"meetings": {"<id>": "<hash>"}, "team": ...}
  2. PUT  /v1/sync/meetings              -> every local syncable meeting whose hash
                                            differs or is missing (batched by size)
  3. POST /v1/sync/delete                -> server ids no longer in the local
                                            syncable set (deleted locally, moved
                                            into an excluded folder, ...)

There are no per-edit diffs, so re-running it is always safe. `library_id` is a
uuid made once per local library (config), so re-linking the device updates the
same server copies instead of duplicating them.

Never sent: audio, audio_dir, bookmarks, share_url, Ask chats. This module is
Qt-free; ui/sync_scheduler.py decides WHEN to run it (always off the UI thread).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import platform
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..transcription.earshot_client import CloudError, _friendly

log = logging.getLogger(__name__)

# Server contract limits.
MAX_BATCH_MEETINGS = 25
MAX_BODY_BYTES = 900_000           # keep each PUT body under ~900 KB
MAX_TRANSCRIPT_CHARS = 2_000_000   # the server rejects bigger with 413
DELETE_CHUNK = 500

# Not syncable yet: nothing worth searching exists before transcription.
_SKIP_STATUSES = frozenset({"New", "Recording", "Recorded"})
# Mid-processing (a re-transcribe / re-summarise of a meeting that may already be
# on the server): neither pushed nor deleted until it settles, so a retry never
# makes the server copy flicker away and back.
_HOLD_STATUSES = frozenset({"Transcribing", "Summarizing"})

# Friendly copy (no em dashes: house style for app strings).
MSG_SIGNED_OUT = "Signed out. Sign in to your Earshot account again to keep syncing."
MSG_NEEDS_PLUS = "Cloud sync needs Earshot Plus."
MSG_RATE_LIMITED = "Earshot is busy right now. Sync will try again shortly."
MSG_OFFLINE = "Can't reach Earshot right now. Sync will try again shortly."


class SyncError(CloudError):
    """A failed sync request. `status` is the HTTP status (0 = network) and
    `code` the server's error slug, so the scheduler can pick its reaction."""

    def __init__(self, message: str, *, status: int = 0, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code

    @property
    def auth(self) -> bool:
        return self.status == 401 or self.code == "auth_invalid"

    @property
    def needs_plus(self) -> bool:
        return self.status == 402 or self.code == "sub_inactive"

    @property
    def too_large(self) -> bool:
        return self.status == 413 or self.code == "too_large"


@dataclass
class SyncResult:
    ok: bool = False
    pushed: int = 0                  # meetings uploaded this run
    deleted: int = 0                 # server copies removed this run
    total: int = 0                   # meetings the account now holds for this library
    skipped: dict = field(default_factory=dict)   # {"<id>": "<hash>"} too large to send
    team: Optional[dict] = None
    error: str = ""
    status: int = 0                  # HTTP status of the failure (0 = network / none)
    code: str = ""
    finished_at: str = ""            # ISO-8601 UTC

    @property
    def auth_failed(self) -> bool:
        return self.status == 401 or self.code == "auth_invalid"

    @property
    def needs_plus(self) -> bool:
        return self.status == 402 or self.code == "sub_inactive"


# ---------------------------------------------------------------- payloads ----
def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def payload_hash(payload: dict) -> str:
    """sha256 hex of the canonical JSON of the payload WITHOUT its "hash" key."""
    body = {k: v for k, v in payload.items() if k != "hash"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _duration(v) -> float:
    try:
        f = float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def build_meeting_payload(m, folder_name: str = "") -> dict:
    """The upload shape for one meeting (a repository Meeting), hash included.
    Audio, audio_dir, bookmarks, share_url and chats are deliberately absent."""
    p = {
        "id": int(m.id),
        "title": m.title or "",
        "date_text": m.date_text or "",
        "date_iso": m.date_iso or "",
        "attendees": [str(a) for a in (m.attendees or []) if str(a).strip()],
        "agenda": m.agenda or "",
        "transcript": m.transcript or "",
        "notes": m.notes,              # parsed notes_json dict, or None
        "folder": folder_name or "",
        "duration_secs": _duration(m.duration_secs),
        "status": m.status or "",
        "template": m.template or "",
        "created_at": m.created_at or "",
        "updated_at": m.updated_at or "",
    }
    p["hash"] = payload_hash(p)
    return p


def is_syncable(m) -> bool:
    """Has something to search (a transcript or notes) and isn't mid-flight."""
    if (m.status or "") in _SKIP_STATUSES or (m.status or "") in _HOLD_STATUSES:
        return False
    return bool((m.transcript or "").strip() or m.notes)


def is_held(m) -> bool:
    return (m.status or "") in _HOLD_STATUSES


def encode_body(obj: dict) -> bytes:
    """The exact bytes sent as a JSON request body (and measured for batching)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def plan_batches(payloads: list[dict], *, library_id: str, device_name: str,
                 max_meetings: int = MAX_BATCH_MEETINGS,
                 max_bytes: int = MAX_BODY_BYTES) -> list[list[dict]]:
    """Group meeting payloads into PUT batches of at most `max_meetings` and
    (as far as possible) `max_bytes` of body. A meeting too big to share a
    request goes alone."""
    envelope = len(encode_body({"library_id": library_id, "device_name": device_name,
                                "meetings": []}))
    batches: list[list[dict]] = []
    cur: list[dict] = []
    cur_bytes = envelope
    for p in payloads:
        size = len(encode_body(p)) + 1  # +1 for the separating comma
        if cur and (len(cur) >= max_meetings or cur_bytes + size > max_bytes):
            batches.append(cur)
            cur, cur_bytes = [], envelope
        cur.append(p)
        cur_bytes += size
    if cur:
        batches.append(cur)
    return batches


def new_library_id() -> str:
    return str(uuid.uuid4())


def ensure_library_id(cfg) -> str:
    """The library id, generated (and set on cfg; caller saves) on first use."""
    if not (cfg.cloud_sync_library_id or "").strip():
        cfg.cloud_sync_library_id = new_library_id()
    return cfg.cloud_sync_library_id


def device_name() -> str:
    import os
    return (os.environ.get("COMPUTERNAME") or platform.node() or "This computer").strip() \
        or "This computer"


def signed_in(cfg) -> bool:
    return cfg.account_mode == "cloud" and bool((cfg.cloud_token or "").strip())


def sync_ready(cfg) -> bool:
    """Signed in to an Earshot account with Cloud sync switched on."""
    return bool(cfg.cloud_sync_enabled) and signed_in(cfg)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------ client ----
class SyncClient:
    """Thin httpx client for the three /v1/sync endpoints. `transport` lets
    tests drive the REAL request encoder against an httpx.MockTransport."""

    def __init__(self, base_url: str, token: str, *,
                 transport: Optional[httpx.BaseTransport] = None, timeout: float = 60.0):
        if not token:
            raise SyncError(MSG_SIGNED_OUT, status=401, code="auth_invalid")
        base = (base_url or "").rstrip("/")
        if not base:
            raise SyncError("No Earshot server URL configured.")
        self._client = httpx.Client(
            base_url=base,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(timeout, connect=10.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def _send(self, method: str, path: str, *, params=None, body: Optional[bytes] = None) -> dict:
        headers = {"Content-Type": "application/json"} if body is not None else None
        try:
            resp = self._client.request(method, path, params=params, content=body, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise SyncError(MSG_OFFLINE) from e
        except httpx.HTTPError as e:
            log.info("cloud sync network error: %s", e)
            raise SyncError(MSG_OFFLINE) from e
        if resp.status_code != 200:
            code = ""
            try:
                err = resp.json().get("error")
                if isinstance(err, dict):
                    code = str(err.get("code") or "")
            except (ValueError, AttributeError):
                pass
            if resp.status_code == 401 or code == "auth_invalid":
                msg = MSG_SIGNED_OUT
            elif resp.status_code == 402 or code == "sub_inactive":
                msg = MSG_NEEDS_PLUS
            elif resp.status_code == 429 or code == "rate_limited":
                msg = MSG_RATE_LIMITED
            elif resp.status_code == 413 or code == "too_large":
                msg = "That meeting is too large to sync."
            else:
                msg = str(_friendly(resp))
            raise SyncError(msg, status=resp.status_code, code=code)
        try:
            data = resp.json()
        except ValueError as e:
            raise SyncError("Earshot returned an unexpected (non-JSON) response.",
                            status=resp.status_code) from e
        if not isinstance(data, dict):
            raise SyncError("Earshot returned an unexpected response shape.",
                            status=resp.status_code)
        return data

    def get_state(self, library_id: str) -> dict:
        return self._send("GET", "/v1/sync/state", params={"library_id": library_id})

    def put_meetings(self, library_id: str, device: str, meetings: list[dict]) -> dict:
        body = encode_body({"library_id": library_id, "device_name": device,
                            "meetings": meetings})
        return self._send("PUT", "/v1/sync/meetings", body=body)

    def delete(self, library_id: str, *, ids: Optional[list[int]] = None,
               all_meetings: bool = False) -> dict:
        payload: dict = {"library_id": library_id}
        if all_meetings:
            payload["all"] = True
        else:
            payload["ids"] = [int(i) for i in (ids or [])]
        return self._send("POST", "/v1/sync/delete", body=encode_body(payload))


# --------------------------------------------------------------- reconcile ----
def _server_ids(state: dict) -> dict[int, str]:
    raw = state.get("meetings")
    out: dict[int, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                out[int(k)] = str(v or "")
            except (TypeError, ValueError):
                continue  # not one of ours: never touch it
    return out


def _team(state: dict) -> Optional[dict]:
    t = state.get("team")
    if not isinstance(t, dict):
        return None
    members = t.get("members")
    return {"name": str(t.get("name") or ""),
            "members": [str(x) for x in members] if isinstance(members, list) else []}


def local_plan(repo) -> tuple[dict[int, dict], set[int]]:
    """({id: payload} for every syncable meeting, {ids held mid-processing})."""
    syncable: dict[int, dict] = {}
    held: set[int] = set()
    for m, folder_name, folder_excluded in repo.sync_snapshot():
        if folder_excluded:
            continue
        if is_held(m):
            held.add(int(m.id))
        elif is_syncable(m):
            syncable[int(m.id)] = build_meeting_payload(m, folder_name)
    return syncable, held


def reconcile(repo, cfg, *, transport: Optional[httpx.BaseTransport] = None,
              device: Optional[str] = None) -> SyncResult:
    """Bring the account's copy of this library in line with the local DB.

    Reads cfg (never mutates it, apart from ensure_library_id) and returns a
    SyncResult for the caller to persist; never raises for network/server
    failures (they come back as result.error + status)."""
    res = SyncResult(skipped=dict(cfg.cloud_sync_skipped or {}))
    if not sync_ready(cfg):
        res.error = "" if not cfg.cloud_sync_enabled else MSG_SIGNED_OUT
        return res
    library_id = ensure_library_id(cfg)
    device = device or device_name()
    try:
        with SyncClient(cfg.cloud_api_base, cfg.cloud_token, transport=transport) as client:
            state = client.get_state(library_id)
            remote = _server_ids(state)
            res.team = _team(state)
            syncable, held = local_plan(repo)

            # forget skips for meetings that are gone or changed (a new version
            # gets one fresh try)
            res.skipped = {k: h for k, h in res.skipped.items()
                           if k.isdigit() and int(k) in syncable
                           and syncable[int(k)]["hash"] == h}

            to_push = []
            for mid, p in syncable.items():
                if remote.get(mid) == p["hash"]:
                    continue
                if res.skipped.get(str(mid)) == p["hash"]:
                    continue  # this exact version was already refused as too large
                if len(p["transcript"]) > MAX_TRANSCRIPT_CHARS:
                    res.skipped[str(mid)] = p["hash"]
                    log.warning("cloud sync: meeting %s transcript too long, skipped", mid)
                    continue
                to_push.append(p)

            pushed_ids: set[int] = set()
            for batch in plan_batches(to_push, library_id=library_id, device_name=device):
                try:
                    client.put_meetings(library_id, device, batch)
                    pushed_ids.update(p["id"] for p in batch)
                except SyncError as e:
                    if not e.too_large:
                        raise
                    # 413: send each meeting alone once, skip the ones still
                    # refused (never loop on them)
                    for p in batch:
                        if len(batch) > 1:
                            try:
                                client.put_meetings(library_id, device, [p])
                                pushed_ids.add(p["id"])
                                continue
                            except SyncError as e1:
                                if not e1.too_large:
                                    raise
                        res.skipped[str(p["id"])] = p["hash"]
                        log.warning("cloud sync: meeting %s too large, skipped", p["id"])
            res.pushed = len(pushed_ids)

            stale = sorted(set(remote) - set(syncable) - held)
            for i in range(0, len(stale), DELETE_CHUNK):
                client.delete(library_id, ids=stale[i:i + DELETE_CHUNK])
            res.deleted = len(stale)

            on_server = (set(remote) - set(stale)) | pushed_ids
            res.total = len(on_server)
    except SyncError as e:
        res.error, res.status, res.code = str(e), e.status, e.code
        log.info("cloud sync failed (%s %s): %s", e.status, e.code, e)
        return res
    except Exception as e:  # never let a sync bug take the app down
        log.exception("cloud sync crashed")
        res.error = f"Sync failed: {e}"
        return res
    res.ok = True
    res.finished_at = _now_iso()
    log.info("cloud sync ok: pushed=%d deleted=%d total=%d skipped=%d",
             res.pushed, res.deleted, res.total, len(res.skipped))
    return res


def delete_all(cfg, *, transport: Optional[httpx.BaseTransport] = None) -> SyncResult:
    """Remove every server copy of this library (the 'turn sync off and remove
    my copies' path). Works while sync is disabled; needs a signed-in device."""
    res = SyncResult()
    library_id = (cfg.cloud_sync_library_id or "").strip()
    if not library_id:
        res.ok = True  # never synced: nothing on the server
        return res
    if not signed_in(cfg):
        res.error, res.status = MSG_SIGNED_OUT, 401
        return res
    try:
        with SyncClient(cfg.cloud_api_base, cfg.cloud_token, transport=transport) as client:
            data = client.delete(library_id, all_meetings=True)
        res.deleted = int(data.get("deleted") or 0)
    except SyncError as e:
        res.error, res.status, res.code = str(e), e.status, e.code
        return res
    except Exception as e:
        log.exception("cloud sync delete-all crashed")
        res.error = f"Could not remove the copies: {e}"
        return res
    res.ok = True
    res.finished_at = _now_iso()
    return res


def apply_result(cfg, res: SyncResult) -> None:
    """Persist a reconcile outcome into cfg (the caller saves)."""
    cfg.cloud_sync_skipped = dict(res.skipped)
    if res.ok:
        cfg.cloud_sync_last_ok = res.finished_at
        cfg.cloud_sync_last_error = ""
        cfg.cloud_sync_count = res.total
        cfg.cloud_sync_team = dict(res.team) if res.team else {}
    else:
        cfg.cloud_sync_last_error = res.error


def _ago(iso: str, now: Optional[datetime] = None) -> str:
    try:
        t = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    secs = max(0, int((now - t).total_seconds()))
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        h = secs // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    local = t.astimezone()
    return f"on {local.day} {local.strftime('%b')}"


def status_line(cfg, *, running: bool = False, now: Optional[datetime] = None) -> str:
    """One plain-words line for the Account page, e.g.
    'Last synced 2 min ago · 56 meetings'."""
    if not signed_in(cfg):
        return "Sign in to your Earshot account to sync."
    if not cfg.cloud_sync_enabled:
        return "Sync is off."
    if running:
        return "Syncing now..."
    if cfg.cloud_sync_last_error:
        return cfg.cloud_sync_last_error
    if not cfg.cloud_sync_last_ok:
        return "Not synced yet."
    n = int(cfg.cloud_sync_count or 0)
    parts = [f"Last synced {_ago(cfg.cloud_sync_last_ok, now)}",
             f"{n} meeting{'s' if n != 1 else ''}"]
    skipped = len(cfg.cloud_sync_skipped or {})
    if skipped:
        parts.append(f"{skipped} too large to sync")
    team = (cfg.cloud_sync_team or {}).get("name") if isinstance(cfg.cloud_sync_team, dict) else ""
    if team:
        parts.append(f"shared with {team}")
    return " · ".join(parts)
