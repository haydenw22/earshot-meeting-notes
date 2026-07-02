"""Notes-provider dispatcher: Claude (Anthropic) or any OpenAI-compatible LLM
(Ollama, LM Studio, vLLM, OpenRouter…), chosen in Settings → AI. Both return the
same validated MeetingNotes, so the pipeline doesn't care which is in use.
"""
from __future__ import annotations

from ..config import Config
from .schema import MeetingNotes


def ready(cfg: Config) -> bool:
    """Can we generate notes at all with the current settings?"""
    if cfg.notes_provider == "openai":
        return bool((cfg.llm_base_url or "").strip() and (cfg.llm_model or "").strip())
    return bool(cfg.resolved_anthropic_key())


def missing_hint(cfg: Config) -> str:
    if cfg.notes_provider == "openai":
        return "Set the LLM server URL and model in Settings → AI."
    return "Add an Anthropic API key in Settings → AI."


def generate_notes(
    transcript: str,
    cfg: Config,
    *,
    attendees: list[str] | None = None,
    agenda: str = "",
    human_date: str = "",
    extra_instructions: str = "",
) -> MeetingNotes:
    if cfg.notes_provider == "openai":
        from . import openai_llm
        notes = openai_llm.generate_notes(
            transcript,
            base_url=cfg.llm_base_url,
            api_key=cfg.llm_api_key,
            model=cfg.llm_model,
            attendees=attendees,
            agenda=agenda,
            human_date=human_date,
            extra_instructions=extra_instructions,
        )
    else:
        from . import anthropic_client
        notes = anthropic_client.generate_notes(
            transcript,
            api_key=cfg.resolved_anthropic_key(),
            attendees=attendees,
            agenda=agenda,
            human_date=human_date,
            model=cfg.anthropic_model,
            extra_instructions=extra_instructions,
        )
    # AI-generated action items are SUGGESTIONS until the user accepts them —
    # enforced here (not trusted from any model output) for both providers.
    for a in notes.action_items:
        a.confirmed = False
    return notes


def run_action(
    instruction: str,
    cfg: Config,
    *,
    transcript: str = "",
    notes_text: str = "",
    title: str = "",
) -> str:
    if cfg.notes_provider == "openai":
        from . import openai_llm
        return openai_llm.run_action(
            instruction,
            base_url=cfg.llm_base_url,
            api_key=cfg.llm_api_key,
            model=cfg.llm_model,
            transcript=transcript,
            notes_text=notes_text,
            title=title,
        )
    from . import actions
    return actions.run_action(
        instruction,
        transcript=transcript,
        notes_text=notes_text,
        title=title,
        api_key=cfg.resolved_anthropic_key(),
        model=cfg.anthropic_model,
    )
