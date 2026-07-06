"""Notes + AI actions via the Earshot Plus cloud proxy.

The proxy holds ALL system prompts — the client sends data only (per the API
contract in PLAN-plus.md):
  - POST /v1/notes → the meeting-notes JSON object matching schema.MeetingNotes.
  - POST /v1/action → {"text": "..."}.

Everything is validated with the same pydantic model the other providers use, so
the rest of the app can't tell the difference. Connection errors surface the
"servers aren't live yet" copy so nothing crashes when the base URL is
unreachable.
"""
from __future__ import annotations

from typing import Optional

import httpx
from pydantic import ValidationError

from ..transcription.earshot_client import SERVERS_NOT_LIVE, CloudError, _friendly
from .schema import MeetingNotes


def _timeout() -> httpx.Timeout:
    # notes/actions are quick relative to transcription, but the upstream model
    # can still take a while on a long transcript — a generous read timeout.
    return httpx.Timeout(300.0, connect=15.0)


def _post_json(base_url: str, token: str, path: str, payload: dict) -> dict:
    if not token:
        raise CloudError("You're not signed in to Earshot Plus — sign in from the Account page.")
    base = (base_url or "").rstrip("/")
    if not base:
        raise CloudError("No Earshot Plus server URL configured.")
    try:
        resp = httpx.post(
            base + path,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=_timeout(),
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


def generate_notes(
    transcript: str,
    *,
    base_url: str,
    token: str,
    attendees: list[str] | None = None,
    agenda: str = "",
    human_date: str = "",
    extra_instructions: str = "",
) -> MeetingNotes:
    if not transcript.strip():
        raise ValueError("Transcript is empty; nothing to summarise.")
    # Contract body for /v1/notes. The agenda isn't part of the contract's notes
    # payload; fold it into extra_instructions so it still reaches the model
    # (the server owns the actual prompt structure).
    extra = extra_instructions or ""
    if (agenda or "").strip():
        extra = (extra + "\n\nPre-meeting agenda (context):\n" + agenda.strip()).strip()
    payload = {
        "transcript": transcript,
        "attendees": attendees or [],
        "human_date": human_date or "",
        "extra_instructions": extra,
    }
    data = _post_json(base_url, token, "/v1/notes", payload)
    try:
        return MeetingNotes.model_validate(data)
    except ValidationError as e:
        raise CloudError(f"Earshot Plus returned notes we couldn't read: {e}") from e


def run_action(
    instruction: str,
    *,
    base_url: str,
    token: str,
    transcript: str = "",
    notes_text: str = "",
    title: str = "",
) -> str:
    if not instruction.strip():
        raise ValueError("No instruction given.")
    # Contract body for /v1/action is {instruction, context}. Assemble the same
    # meeting context the local/anthropic paths build, as one context string.
    context_parts = []
    if title:
        context_parts.append(f"Meeting: {title}")
    if notes_text.strip():
        context_parts.append(f"Notes:\n{notes_text.strip()}")
    if transcript.strip():
        context_parts.append(f"Transcript:\n{transcript.strip()}")
    context = "\n\n".join(context_parts) or "(no meeting content)"
    data = _post_json(base_url, token, "/v1/action", {"instruction": instruction.strip(), "context": context})
    text = (data.get("text") or "").strip()
    return text or "(no output)"


def ask(
    question: str,
    *,
    base_url: str,
    token: str,
    context_blocks: list[str],
    today: str = "",
    timeout: Optional[float] = None,
) -> dict:
    """POST /v1/ask → {"answer_html": "...", "citations": [{meeting_title, quote}]}.
    Returns the raw parsed dict; the caller verifies citations itself."""
    payload = {
        "question": question,
        "context_blocks": context_blocks,
        "today": today or "",
    }
    return _post_json(base_url, token, "/v1/ask", payload)
