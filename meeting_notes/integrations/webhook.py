"""Fire a finished (or transcribed) meeting at a user-configured webhook URL.

One POST of the whole meeting as JSON — the user can wire it into their own
Slack/Notion/Zapier/n8n/CRM flow without Earshot needing any integration. Data
leaves the machine, so it's opt-in (blank URL = off).
"""
from __future__ import annotations

from urllib.parse import urlparse

import httpx


def build_payload(m, folder=None) -> dict:
    """The outbound JSON for one meeting.

    `folder` is the resolved Folder the meeting lives in (or None) — passed in
    by the caller so this module stays repository-free. The "folder" key lets
    automations route by client/team; it is null for unfiled meetings, and the
    rest of the payload is unchanged (backward-compatible).
    """
    return {
        "id": m.id,
        "title": m.title,
        "date": m.date_text,
        "date_iso": m.date_iso,
        "attendees": m.attendees,
        "agenda": m.agenda,
        "template": m.template,
        "folder": (
            {"id": folder.id, "name": folder.name, "color": folder.color}
            if folder is not None else None
        ),
        "duration_secs": m.duration_secs,
        "status": m.status,
        "transcript": m.transcript,
        "notes": m.notes,
        "bookmarks": m.bookmarks,
    }


def send(url: str, payload: dict, *, timeout: float = 20.0) -> None:
    url = (url or "").strip()
    if not url:
        return
    # only ever POST to http(s) — never file://, etc.
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("Webhook URL must start with http:// or https://")
    # follow redirects so a plain http endpoint that 301s to https still delivers
    # (otherwise every meeting silently fails on the redirect)
    resp = httpx.post(url, json=payload, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
