"""Publish/unpublish a meeting as a public Earshot Plus page.

POST /v1/share uploads the meeting's structured notes; the server renders and
hosts a branded page reachable only by its unguessable URL. DELETE
/v1/share/{meeting_id} takes it down. Both raise CloudError with friendly copy
on failure (same conventions as the other cloud providers).
"""
from __future__ import annotations

import httpx

from ..transcription.earshot_client import SERVERS_NOT_LIVE, CloudError, _friendly
from .earshot_llm import _post_json
from .share import to_share_payload


def publish(m, *, base_url: str, token: str, include_transcript: bool = False) -> str:
    """Create or update the public page for meeting `m`. Returns the share URL
    (stable across re-shares of the same meeting)."""
    payload = to_share_payload(m, include_transcript=include_transcript)
    data = _post_json(base_url, token, "/v1/share", payload)
    url = (data.get("url") or "").strip()
    # https only, except loopback (mirrors the cloud_api_base rule for dev)
    ok = url.startswith("https://") or url.startswith(("http://127.0.0.1", "http://localhost"))
    if not ok:
        raise CloudError("Earshot Plus returned an unexpected share URL.")
    return url


def unpublish(meeting_id: int, *, base_url: str, token: str) -> None:
    """Take the meeting's public page down. Idempotent."""
    if not token:
        raise CloudError("You're not signed in to Earshot Plus — sign in from the Account page.")
    base = (base_url or "").rstrip("/")
    if not base:
        raise CloudError("No Earshot Plus server URL configured.")
    try:
        resp = httpx.delete(
            f"{base}/v1/share/{int(meeting_id)}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(30.0, connect=15.0),
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        raise CloudError(SERVERS_NOT_LIVE) from e
    except httpx.HTTPError as e:
        raise CloudError(f"Could not reach Earshot Plus: {e}") from e
    if resp.status_code != 200:
        raise _friendly(resp)
