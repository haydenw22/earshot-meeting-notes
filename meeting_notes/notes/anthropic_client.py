"""Turn a speaker-labelled transcript into structured meeting notes with Claude
Haiku (claude-haiku-4-5), via a forced tool call so the reply is always one
validated object: title + summary + attendees + decisions + action items + topics.

The one-sentence title is a field of that same object, so notes and title come
back in a single request.
"""
from __future__ import annotations

import json

import anthropic
from pydantic import ValidationError

from .schema import MeetingNotes, notes_tool_schema

SYSTEM_PROMPT = (
    "You are a meeting-notes assistant. You are given a speaker-labelled transcript "
    "produced by automatic speech recognition, so expect transcription errors, filler "
    "words, and imperfect speaker labels. Produce accurate, concise structured notes by "
    "calling the record_meeting_notes tool. Rules: "
    "(1) title is a single sentence of at most ~12 words capturing the meeting's "
    "purpose or outcome. "
    "(2) Only record decisions that were actually agreed, not mere discussion. "
    "(3) For each action item, infer the owner from the speaker labels or who committed "
    "to it; use null if genuinely unclear — never invent a name. "
    "(4) Infer attendees from the distinct speakers and any named people. "
    "(5) Do not hallucinate content that is not supported by the transcript. "
    "(6) Keep topics to short tags."
)

TOOL = {
    "name": "record_meeting_notes",
    "description": "Record the structured notes for this meeting.",
    "input_schema": notes_tool_schema(),
}


def generate_notes(
    transcript: str,
    *,
    api_key: str,
    attendees: list[str] | None = None,
    human_date: str = "",
    model: str = "claude-haiku-4-5",
    max_tokens: int = 4000,
) -> MeetingNotes:
    if not api_key:
        raise ValueError("No Anthropic API key configured.")
    if not transcript.strip():
        raise ValueError("Transcript is empty; nothing to summarise.")

    client = anthropic.Anthropic(api_key=api_key)
    attendees_csv = ", ".join(attendees or []) or "(none entered)"
    user = (
        f"Meeting date: {human_date or '(unknown)'}\n"
        f"Known attendees (entered during recording): {attendees_csv}\n\n"
        f"Transcript:\n{transcript}"
    )

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": "record_meeting_notes"},
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.APIStatusError as e:  # also covers RateLimitError (a subclass)
        hint = " — the transcript may be too long to summarise; try a shorter meeting." if e.status_code == 400 else ""
        raise RuntimeError(f"Anthropic API error ({e.status_code}): {e}{hint}") from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError(f"Could not reach the Anthropic API: {e}") from e

    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "record_meeting_notes":
            data = block.input
            if not isinstance(data, (dict, str)):
                raise RuntimeError(f"Unexpected tool-input type from Claude: {type(data).__name__}")
            try:
                if isinstance(data, str):  # be tolerant of string-encoded tool input
                    data = json.loads(data)
                return MeetingNotes.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as e:
                raise RuntimeError(f"Could not parse Claude's notes: {e}") from e

    raise RuntimeError("Claude did not return structured meeting notes.")
