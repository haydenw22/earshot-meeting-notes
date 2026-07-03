"""Structured shape of the meeting notes the model returns.

Modelled on a high-quality human/AI meeting summary: a one-line title, a short
overview, checkbox-style action items (with a done flag), and the body organised
into topic sections of detailed bullets (bullets may use **markdown bold** for
key figures). Driven into the model as a forced tool call so the reply is always
one validated object.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ActionItem(BaseModel):
    task: str = Field(description="The action, specific and self-contained.")
    owner: Optional[str] = Field(default=None, description="Who owns it, or null if unclear — never invent a name.")
    done: bool = Field(default=False, description="True only if completed/resolved during the meeting itself.")
    due: Optional[str] = Field(
        default=None,
        description="ISO date YYYY-MM-DD if an explicit deadline was stated; null otherwise.",
    )
    # Not part of the model-facing tool schema: AI-generated items start as
    # SUGGESTIONS (confirmed=False) and only become real to-dos when the user
    # accepts them. Legacy items without the key are treated as confirmed.
    confirmed: bool = Field(default=False)

    @field_validator("due", mode="before")
    @classmethod
    def _normalise_due(cls, v):
        """Tolerant of model/user junk: never raise — bad input just becomes
        None rather than crashing the notes pipeline. Only a strict
        YYYY-MM-DD string survives."""
        if v is None:
            return None
        if not isinstance(v, str):
            return None
        s = v.strip()
        if not s:
            return None
        try:
            _dt.date.fromisoformat(s)
        except ValueError:
            return None
        return s


class Section(BaseModel):
    heading: str = Field(description="Short topic heading, e.g. 'Webinar Performance'.")
    bullets: list[str] = Field(default_factory=list, description="Detailed bullets; use **bold** for key figures/decisions.")


class MeetingNotes(BaseModel):
    title: str = Field(description="A single sentence of at most ~12 words capturing the meeting's purpose or outcome.")
    summary: str = Field(description="A crisp 1-2 sentence overview (max ~40 words).")
    attendees: list[str] = Field(default_factory=list, description="People present, inferred from speakers and names mentioned.")
    action_items: list[ActionItem] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list, description="The body, organised by topic.")


def notes_tool_schema() -> dict:
    """Self-contained JSON Schema for the `record_meeting_notes` tool input."""
    return {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "A single sentence of at most ~12 words capturing the meeting's purpose or outcome.",
            },
            "summary": {
                "type": "string",
                "description": "A crisp 1-2 sentence overview (max ~40 words). Detail belongs in sections, not here.",
            },
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "People present, inferred from speakers and any names mentioned.",
            },
            "action_items": {
                "type": "array",
                "description": "Concrete next steps / tasks, as checkboxes.",
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "The action, specific and self-contained."},
                        "owner": {
                            "type": ["string", "null"],
                            "description": "Who owns it, inferred from speakers; null if unclear — never invent a name.",
                        },
                        "done": {
                            "type": "boolean",
                            "description": "true ONLY if the item was actually completed/resolved during the meeting; otherwise false.",
                        },
                        "due": {
                            "type": ["string", "null"],
                            "description": (
                                "ISO date YYYY-MM-DD, ONLY if an explicit deadline was stated in the "
                                "meeting ('by Friday', 'end of month'); resolve relative dates using "
                                "the meeting date; null otherwise — never invent one."
                            ),
                        },
                    },
                    "required": ["task", "done"],
                },
            },
            "sections": {
                "type": "array",
                "description": "The body of the notes, organised into topic sections.",
                "items": {
                    "type": "object",
                    "properties": {
                        "heading": {"type": "string", "description": "Short topic heading, e.g. 'Webinar Performance'."},
                        "bullets": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Detailed, specific bullets. Use **markdown bold** for key figures and decisions.",
                        },
                    },
                    "required": ["heading", "bullets"],
                },
            },
        },
        "required": ["title", "summary", "action_items", "sections"],
    }
