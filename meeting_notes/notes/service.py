"""Notes-provider dispatcher: Claude (Anthropic), a hosted OpenAI-compatible
cloud endpoint ("openai"), or a fully local OpenAI-compatible server ("local" —
Ollama, LM Studio, vLLM…), chosen in Settings → AI. All three return the same
validated MeetingNotes, so the pipeline doesn't care which is in use.
"""
from __future__ import annotations

from ..config import Config
from .schema import MeetingNotes


def _llm_fields(cfg: Config) -> tuple[str, str, str]:
    """(base_url, api_key, model) for the "openai" (hosted) provider."""
    return cfg.llm_base_url, cfg.llm_api_key, cfg.llm_model


def _local_fields(cfg: Config) -> tuple[str, str, str]:
    """(base_url, api_key, model) for the "local" provider."""
    return cfg.local_llm_base_url, cfg.local_llm_api_key, cfg.local_llm_model


def ready(cfg: Config) -> bool:
    """Can we generate notes at all with the current settings?"""
    if cfg.account_mode == "cloud":
        return bool(cfg.cloud_token)
    if cfg.notes_provider == "openai":
        base, key, model = _llm_fields(cfg)
        return bool((base or "").strip() and (key or "").strip() and (model or "").strip())
    if cfg.notes_provider == "local":
        base, _key, model = _local_fields(cfg)
        return bool((base or "").strip() and (model or "").strip())
    return bool(cfg.resolved_anthropic_key())


def missing_hint(cfg: Config) -> str:
    if cfg.account_mode == "cloud":
        return "Sign in to Earshot Plus (Account page)."
    if cfg.notes_provider == "openai":
        return "Add your OpenAI (or compatible cloud) details in Settings → AI."
    if cfg.notes_provider == "local":
        return "Add your local AI details in Settings → AI."
    return "Add your Anthropic details in Settings → AI."


def generate_notes(
    transcript: str,
    cfg: Config,
    *,
    attendees: list[str] | None = None,
    agenda: str = "",
    human_date: str = "",
    extra_instructions: str = "",
) -> MeetingNotes:
    if cfg.account_mode == "cloud":
        from . import earshot_llm
        notes = earshot_llm.generate_notes(
            transcript,
            base_url=cfg.cloud_api_base,
            token=cfg.cloud_token,
            attendees=attendees,
            agenda=agenda,
            human_date=human_date,
            extra_instructions=extra_instructions,
        )
    elif cfg.notes_provider in ("openai", "local"):
        from . import openai_llm
        base, key, model = _llm_fields(cfg) if cfg.notes_provider == "openai" else _local_fields(cfg)
        notes = openai_llm.generate_notes(
            transcript,
            base_url=base,
            api_key=key,
            model=model,
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
    # enforced here (not trusted from any model output) for all providers.
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
    if cfg.account_mode == "cloud":
        from . import earshot_llm
        return earshot_llm.run_action(
            instruction,
            base_url=cfg.cloud_api_base,
            token=cfg.cloud_token,
            transcript=transcript,
            notes_text=notes_text,
            title=title,
        )
    if cfg.notes_provider in ("openai", "local"):
        from . import openai_llm
        base, key, model = _llm_fields(cfg) if cfg.notes_provider == "openai" else _local_fields(cfg)
        return openai_llm.run_action(
            instruction,
            base_url=base,
            api_key=key,
            model=model,
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
