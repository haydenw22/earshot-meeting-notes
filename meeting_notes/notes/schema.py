"""Structured shape of the meeting notes Haiku returns.

We use a JSON Schema (driven from these Pydantic models) as an Anthropic *tool*
input schema and force the model to call it, so the reply is always a single
validated object the app can store and render directly.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ActionItem(BaseModel):
    task: str = Field(description="The action to be done.")
    owner: Optional[str] = Field(
        default=None,
        description="Who owns it, inferred from the speaker labels. null if genuinely unclear — never invent a name.",
    )
    due: Optional[str] = Field(
        default=None, description="Natural-language due date if one was stated, else null."
    )


class MeetingNotes(BaseModel):
    title: str = Field(description="A single sentence of at most ~12 words capturing the meeting's purpose or outcome.")
    summary: str = Field(description="A 2-5 sentence prose overview of the meeting.")
    attendees: list[str] = Field(default_factory=list, description="People present, inferred from speakers and names mentioned.")
    decisions: list[str] = Field(default_factory=list, description="Only decisions actually agreed, not mere discussion.")
    action_items: list[ActionItem] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list, description="Short topic/agenda tags.")


def notes_tool_schema() -> dict:
    """Self-contained JSON Schema for the `record_meeting_notes` tool input.

    Written inline (rather than via pydantic's model_json_schema, which emits
    $ref/$defs for the nested ActionItem) because Anthropic tool input schemas
    are happiest with a flat, dereferenced schema.
    """
    return {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "A single sentence of at most ~12 words capturing the meeting's purpose or outcome.",
            },
            "summary": {
                "type": "string",
                "description": "A 2-5 sentence prose overview of the meeting.",
            },
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "People present, inferred from speakers and names mentioned.",
            },
            "decisions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only decisions actually agreed, not mere discussion.",
            },
            "action_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "The action to be done."},
                        "owner": {
                            "type": ["string", "null"],
                            "description": "Who owns it, inferred from speaker labels. null if genuinely unclear — never invent a name.",
                        },
                        "due": {
                            "type": ["string", "null"],
                            "description": "Natural-language due date if stated, else null.",
                        },
                    },
                    "required": ["task"],
                },
            },
            "topics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Short topic/agenda tags.",
            },
        },
        "required": ["title", "summary"],
    }
