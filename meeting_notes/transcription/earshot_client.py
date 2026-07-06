"""Transcription via the Earshot Plus cloud proxy (`POST /v1/transcribe`).

The server holds the actual provider credentials and prompts — the client only
sends audio (per the API contract in PLAN-plus.md). The response is the SAME
{"text", "segments"} shape every other provider returns, so the two-channel
merge and the rest of the pipeline can't tell which backend was used.

Errors from the proxy are JSON `{"error": {"code": "<slug>", "message": "..."}}`
with a proper HTTP status; the slugs are mapped to friendly `CloudError`
messages here. Connection errors (the servers aren't deployed yet) get the
"not live yet" message so nothing crashes or hangs when the base URL is
unreachable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx

# Shared "servers aren't live yet" copy — connection-level failures (DNS, refused,
# timeouts connecting) mean the cloud isn't reachable, which today means it isn't
# deployed. Keep this string identical wherever a connect error is surfaced.
SERVERS_NOT_LIVE = "Earshot Plus servers aren't live yet — check back soon."

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # /v1/transcribe hard cap (contract: ≤25 MB)

# error slug -> friendly sentence (the server also sends a human `message`, but we
# prefer our own consistent copy for the slugs the contract enumerates)
_SLUG_MESSAGES = {
    "auth_invalid": "Your Earshot Plus session has expired — sign in again from the Account page.",
    "sub_inactive": "Your Earshot Plus subscription isn't active — manage billing from the Account page.",
    "cap_reached": "You've reached this month's Earshot Plus transcription limit.",
    "too_large": "That audio is too large for Earshot Plus (max 25 MB per request).",
    "upstream_error": "Earshot Plus had a temporary problem transcribing that — please retry shortly.",
}

_MIME = {".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg",
         ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".webm": "audio/webm"}


class CloudError(RuntimeError):
    """A user-facing error from the Earshot Plus proxy (already friendly)."""


def _timeout(timeout: Optional[float]) -> httpx.Timeout:
    if timeout is None:
        return httpx.Timeout(600.0, connect=15.0)
    return httpx.Timeout(timeout, connect=15.0)


def _friendly(resp: httpx.Response) -> CloudError:
    """Map a non-200 proxy response to a friendly CloudError, appending
    `retry_after_days` for the cap case when the server supplies it."""
    slug = ""
    server_msg = ""
    retry_after_days = None
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            slug = err.get("code") or ""
            server_msg = err.get("message") or ""
            retry_after_days = err.get("retry_after_days")
    except ValueError:
        pass
    msg = _SLUG_MESSAGES.get(slug)
    if msg is None:
        # unknown slug — fall back to the server's sentence, then a generic line
        msg = server_msg or f"Earshot Plus returned an error ({resp.status_code})."
    elif slug == "cap_reached" and retry_after_days:
        msg = f"{msg} It resets in {int(retry_after_days)} day(s)."
    return CloudError(msg)


def transcribe(
    audio_path: str | Path,
    *,
    base_url: str,
    token: str,
    language: str = "en",
    timeout: Optional[float] = None,
) -> dict:
    """POST one audio file to the proxy and return {"text", "segments"}.

    Callers route long meetings through transcription.chunker first (each chunk
    is ≤ the 24 MB planning cap), so this only ever handles single-request files.
    """
    if not token:
        raise CloudError("You're not signed in to Earshot Plus — sign in from the Account page.")
    base = (base_url or "").rstrip("/")
    if not base:
        raise CloudError("No Earshot Plus server URL configured.")
    url = base + "/v1/transcribe"

    audio_path = Path(audio_path)
    if audio_path.stat().st_size > MAX_UPLOAD_BYTES:
        raise CloudError(
            "That audio is too large for Earshot Plus (max 25 MB per request). Very long "
            "meetings are split automatically — this shouldn't normally happen."
        )
    mime = _MIME.get(audio_path.suffix.lower(), "application/octet-stream")

    # Contract: multipart with file field `file` and optional form field `language`.
    # Pass the form as a DICT (never a list of tuples): httpx 0.28 rejects a
    # list-of-tuples `data=` alongside `files=` (see the openai_client regression
    # in tests/test_transcription_options.py — the v0.22.1 crash).
    form: dict[str, str] = {}
    if language:
        form["language"] = language

    try:
        with open(audio_path, "rb") as fh:
            resp = httpx.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                data=form,
                files={"file": (audio_path.name, fh, mime)},
                timeout=_timeout(timeout),
            )
    except httpx.ConnectError as e:
        raise CloudError(SERVERS_NOT_LIVE) from e
    except httpx.ConnectTimeout as e:
        raise CloudError(SERVERS_NOT_LIVE) from e
    except httpx.HTTPError as e:
        raise CloudError(f"Could not reach Earshot Plus: {e}") from e

    if resp.status_code != 200:
        raise _friendly(resp)

    try:
        data = resp.json()
    except ValueError as e:
        raise CloudError("Earshot Plus returned an unexpected (non-JSON) response.") from e
    if not isinstance(data, dict):
        raise CloudError("Earshot Plus returned an unexpected response shape.")

    raw_segments = data.get("segments")
    segments = [
        {"start": float(s.get("start") or 0.0), "end": float(s.get("end") or 0.0), "text": s.get("text") or ""}
        for s in (raw_segments if isinstance(raw_segments, list) else [])
        if isinstance(s, dict)
    ]
    text = data.get("text") or ""
    if not segments and text.strip():
        segments = [{"start": 0.0, "end": 0.0, "text": text}]
    return {"text": text, "segments": segments}


def get_me(base_url: str, token: str, *, timeout: float = 12.0) -> dict:
    """GET /v1/me — the account/usage snapshot. Raises CloudError on any failure
    (connection or non-200), with friendly copy. Returns the parsed JSON dict."""
    if not token:
        raise CloudError("You're not signed in to Earshot Plus.")
    base = (base_url or "").rstrip("/")
    if not base:
        raise CloudError("No Earshot Plus server URL configured.")
    try:
        resp = httpx.get(
            base + "/v1/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(timeout, connect=10.0),
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        raise CloudError(SERVERS_NOT_LIVE) from e
    except httpx.HTTPError as e:
        raise CloudError(f"Could not reach Earshot Plus: {e}") from e
    if resp.status_code != 200:
        raise _friendly(resp)
    try:
        data = resp.json()
    except ValueError as e:
        raise CloudError("Earshot Plus returned an unexpected (non-JSON) response.") from e
    if not isinstance(data, dict):
        raise CloudError("Earshot Plus returned an unexpected response shape.")
    return data


def ping(base_url: str, token: str, *, timeout: float = 12.0) -> bool:
    """True if GET /v1/me returns 200 (used by Settings/Test-connection)."""
    try:
        get_me(base_url, token, timeout=timeout)
        return True
    except CloudError:
        return False


def request_device_code(base_url: str, *, app_version: str, device_name: str,
                        timeout: float = 15.0) -> dict:
    """POST /v1/device/code → {code, poll_token, verify_url, expires_in, interval}.
    Unauthenticated (per contract). Raises CloudError on failure."""
    base = (base_url or "").rstrip("/")
    if not base:
        raise CloudError("No Earshot Plus server URL configured.")
    try:
        resp = httpx.post(
            base + "/v1/device/code",
            json={"app_version": app_version, "device_name": device_name},
            timeout=httpx.Timeout(timeout, connect=10.0),
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        raise CloudError(SERVERS_NOT_LIVE) from e
    except httpx.HTTPError as e:
        raise CloudError(f"Could not reach Earshot Plus: {e}") from e
    if resp.status_code != 200:
        raise _friendly(resp)
    try:
        data = resp.json()
    except ValueError as e:
        raise CloudError("Earshot Plus returned an unexpected (non-JSON) response.") from e
    if not isinstance(data, dict) or not data.get("poll_token"):
        raise CloudError("Earshot Plus returned an unexpected response shape.")
    return data


def poll_device(base_url: str, *, poll_token: str, timeout: float = 15.0) -> dict:
    """POST /v1/device/poll → {"status": "pending"} while waiting, or
    {"status": "ok", device_token, email, plan, sub_status} once approved.
    A 410 (expired/denied) is surfaced as {"status": "expired"}; other failures
    raise CloudError. Unauthenticated (per contract)."""
    base = (base_url or "").rstrip("/")
    if not base:
        raise CloudError("No Earshot Plus server URL configured.")
    try:
        resp = httpx.post(
            base + "/v1/device/poll",
            json={"poll_token": poll_token},
            timeout=httpx.Timeout(timeout, connect=10.0),
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        raise CloudError(SERVERS_NOT_LIVE) from e
    except httpx.HTTPError as e:
        raise CloudError(f"Could not reach Earshot Plus: {e}") from e
    if resp.status_code == 410:
        return {"status": "expired"}
    if resp.status_code != 200:
        raise _friendly(resp)
    try:
        data = resp.json()
    except ValueError as e:
        raise CloudError("Earshot Plus returned an unexpected (non-JSON) response.") from e
    if not isinstance(data, dict):
        raise CloudError("Earshot Plus returned an unexpected response shape.")
    return data


def revoke(base_url: str, token: str, *, timeout: float = 8.0) -> None:
    """Best-effort POST /v1/device/revoke to sign the device out server-side.
    Never raises — sign-out clears the local token regardless."""
    base = (base_url or "").rstrip("/")
    if not base or not token:
        return
    try:
        httpx.post(
            base + "/v1/device/revoke",
            headers={"Authorization": f"Bearer {token}"},
            json={},
            timeout=httpx.Timeout(timeout, connect=6.0),
        )
    except httpx.HTTPError:
        pass  # server unreachable / already gone — the local sign-out still happens
