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
    "You are an expert meeting-notes writer. You are given a speaker-labelled transcript "
    "produced by automatic speech recognition (expect errors, filler, casual language and "
    "imperfect speaker labels), and optionally a pre-meeting agenda. Produce polished, "
    "specific, well-structured notes by calling the record_meeting_notes tool.\n\n"
    "Quality bar — match a great human notetaker:\n"
    "- BE SPECIFIC: capture exact figures, names, dates, decisions and rationale that were "
    "actually said. Concrete details over vague generalities.\n"
    "- Organise the body into topic SECTIONS with short headings (by project/theme), each "
    "with detailed bullets. Use **markdown bold** for key figures and decisions in bullets.\n"
    "- ACTION ITEMS: concrete, self-contained next steps. Infer an owner from the speakers "
    "when clear; use null if not — never invent a name. Set done=true ONLY if the item was "
    "actually completed or resolved during the meeting; otherwise false.\n"
    "- TITLE: one sentence, at most ~12 words, capturing the purpose/outcome.\n"
    "- SUMMARY: 2-4 sentences of prose overview.\n"
    "- If an AGENDA is provided, use it to structure and prioritise the sections and to add "
    "context, but only record what the transcript actually supports — do not invent.\n"
    "- Do not hallucinate. Ignore filler and banter. Keep it professional even if the "
    "transcript is casual or contains profanity."
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
    agenda: str = "",
    human_date: str = "",
    model: str = "claude-haiku-4-5",
    max_tokens: int = 8000,
) -> MeetingNotes:
    if not api_key:
        raise ValueError("No Anthropic API key configured.")
    if not transcript.strip():
        raise ValueError("Transcript is empty; nothing to summarise.")

    client = anthropic.Anthropic(api_key=api_key)
    attendees_csv = ", ".join(attendees or []) or "(none entered)"
    parts = [
        f"Meeting date: {human_date or '(unknown)'}",
        f"Known attendees (entered during recording): {attendees_csv}",
    ]
    if (agenda or "").strip():
        parts.append(f"\nPre-meeting agenda (context — structure the notes around this where relevant):\n{agenda.strip()}")
    parts.append(f"\nTranscript:\n{transcript}")
    user = "\n".join(parts)

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
