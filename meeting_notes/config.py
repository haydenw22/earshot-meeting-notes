"""User configuration: persisted as JSON under the app data dir.

Holds the Whisper server URL, Anthropic API key, audio device preferences and
processing options. Device preferences are stored by NAME (not PortAudio index)
because indices reshuffle when you plug/unplug headphones mid-session.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Optional

from .paths import config_path

# No personal/default server ships — the user configures their own in Settings.
DEFAULT_WHISPER_URL = ""

# Sentinel for the recording-overlay position meaning "auto-place" (top-right of
# the primary screen). A real window top-left never lands on exactly this value.
OVERLAY_AUTO_POS = -32000

# Seeded saved AI actions (the prompt workbench) when the user hasn't made any.
DEFAULT_AI_ACTIONS = [
    {"name": "Follow-up email",
     "prompt": "Draft a concise, friendly follow-up email summarising the meeting and "
               "listing the agreed action items with their owners. Professional but warm."},
    {"name": "Key decisions",
     "prompt": "List only the concrete decisions made in this meeting, each as a short bullet."},
    {"name": "Risks & blockers",
     "prompt": "List any risks, blockers or open questions raised, each as a short bullet."},
]


@dataclass
class Config:
    # --- Storage ---
    data_dir: str = ""                    # custom folder for recordings + screenshots; "" = app default

    # --- Transcription ---
    # "home" (self-hosted Whisper) | "online" (OpenAI-compatible) | "deepgram"
    transcription_provider: str = "home"
    auto_transcribe: bool = True          # transcribe automatically when a recording stops
    whisper_language: str = "en"          # "" => auto-detect (applies to all providers)
    # home server
    whisper_url: str = DEFAULT_WHISPER_URL
    whisper_word_timestamps: bool = True
    whisper_vad_filter: bool = True       # skip silence server-side (faster; needs faster-whisper engine)
    # online service (OpenAI-compatible: OpenAI, Groq, …)
    online_base_url: str = "https://api.openai.com/v1"
    online_api_key: str = ""
    online_model: str = "whisper-1"
    # Deepgram (cloud STT — fast, accurate, no 25 MB cap; good for long meetings)
    deepgram_api_key: str = ""
    deepgram_model: str = "nova-2"
    # upload format for transcription: "flac" (lossless) | "opus" (~4x smaller —
    # ~10 MB/hour — with negligible accuracy impact; great for cloud providers)
    upload_codec: str = "flac"

    # --- Notes / AI ---
    # Which model powers notes, AI actions, prep briefs AND Ask Earshot:
    # "anthropic" (Claude) | "openai" (any OpenAI-compatible cloud endpoint) |
    # "local" (fully local: Ollama, LM Studio, vLLM…). Keys/URLs/models for all
    # three are kept even when not selected, so switching providers is lossless.
    notes_provider: str = "anthropic"
    anthropic_api_key: str = ""           # read from env ANTHROPIC_API_KEY if blank
    anthropic_model: str = "claude-sonnet-4-6"  # richer notes than Haiku; user can change in Settings
    llm_base_url: str = "https://api.openai.com/v1"  # OpenAI / OpenAI-compatible cloud default
    llm_api_key: str = ""
    llm_model: str = ""                   # e.g. "gpt-4o-mini"
    local_llm_base_url: str = "http://localhost:11434/v1"  # Ollama default
    local_llm_api_key: str = ""           # optional (local servers usually need none)
    local_llm_model: str = ""             # e.g. "llama3.1", "qwen2.5:14b"
    auto_summary: bool = True             # generate AI notes automatically after transcription
    brief_mode: str = "basic"             # "basic" (last/related 3) | "advanced" (folder/pick)
    # AI customisation
    custom_instructions_enabled: bool = False
    custom_instructions: str = ""         # appended to every notes prompt when enabled
    templates: list = field(default_factory=list)   # [{name, instructions}] note templates per meeting type
    ai_actions: list = field(default_factory=list)  # [{name, prompt}] saved prompts for the workbench

    # --- Automation ---
    webhook_url: str = ""                 # POST the meeting here when ready
    webhook_when: str = "summary"         # "transcript" (after transcript) | "summary" (after notes)
    todoist_token: str = ""               # push open action items into Todoist

    # --- Audio devices (stored by name; re-resolved to index at record time) ---
    mic_device_name: Optional[str] = None
    loopback_device_name: Optional[str] = None
    headphones_mode: bool = True          # True => skip echo cancellation (no bleed)

    # --- Screen capture ---
    capture_screen: bool = False          # periodic screenshots while recording
    screen_monitor: int = 1               # 1 = primary monitor

    # --- Output (Notion deferred; kept for forward-compatibility) ---
    notion_token: str = ""
    notion_database_id: str = ""

    # --- Appearance ---
    theme_mode: str = "light"             # "light" | "dark"
    sidebar_width: int = 258              # resizable; persisted
    sidebar_side: str = "left"            # "left" | "right"
    show_dashboard: bool = True           # show the pending-action-items dashboard on Home
    dashboard_collapsed: bool = False     # collapsed/expanded state of the to-do card on Home
    meetings_collapsed: bool = False      # collapsed/expanded state of the Home meeting list
    folders_collapsed: bool = False       # collapsed/expanded state of the sidebar FOLDERS tree

    # --- Call detection (prompt to record when another app starts using the mic) ---
    call_detect_enabled: bool = True

    # --- Recording overlay (always-on-top mic/system lights + timer) ---
    overlay_enabled: bool = True
    overlay_opacity: float = 0.95         # 0.4–1.0 window opacity
    overlay_pos_x: int = OVERLAY_AUTO_POS  # OVERLAY_AUTO_POS = auto-place top-right
    overlay_pos_y: int = OVERLAY_AUTO_POS

    # --- Misc ---
    keep_audio: bool = True               # retain raw + 2-channel audio after summary

    # --- Account (local-only today; groundwork for future hosted sync) ---
    account_name: str = ""                # display name for the sidebar account card; "" -> "Guest"

    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Config":
        p = config_path()
        if not p.exists():
            cfg = cls()
            cfg.save()
            return cfg
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Keep the damaged file as evidence instead of silently overwriting
            # the user's settings (keys, server URL) on the next save.
            try:
                p.replace(p.with_suffix(".json.bad"))
            except OSError:
                pass
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in data.items() if k in known}
        unknown = {k: v for k, v in data.items() if k not in known and k != "extra"}
        # container fields must have the right container type or defaults win
        for key, want in (("templates", list), ("ai_actions", list), ("extra", dict)):
            if key in clean and not isinstance(clean[key], want):
                del clean[key]
        cfg = cls(**clean)
        if unknown:  # preserve forward/unknown keys instead of dropping them
            cfg.extra.update(unknown)
        # re-promote fields a newer version wrote and an older version demoted
        for k in [k for k in cfg.extra if k in known]:
            setattr(cfg, k, cfg.extra.pop(k))
        # One-time migration (S3): "openai" used to mean "any OpenAI-compatible
        # endpoint", which is how existing Ollama users ended up with
        # notes_provider="openai" pointing at localhost. Now "openai" means the
        # hosted OpenAI-compatible-cloud provider and "local" is separate — move
        # such configs onto the new local_* fields so nothing breaks unattended.
        if cfg.notes_provider == "openai" and (
            "localhost" in (cfg.llm_base_url or "") or "127.0.0.1" in (cfg.llm_base_url or "")
        ):
            if cfg.local_llm_base_url == cls.__dataclass_fields__["local_llm_base_url"].default:
                cfg.local_llm_base_url = cfg.llm_base_url
            if not cfg.local_llm_api_key:
                cfg.local_llm_api_key = cfg.llm_api_key
            if not cfg.local_llm_model:
                cfg.local_llm_model = cfg.llm_model
            cfg.notes_provider = "local"
            cfg.llm_base_url = cls.__dataclass_fields__["llm_base_url"].default
            cfg.llm_model = ""
        return cfg

    def save(self) -> None:
        # Atomic: a crash/power-loss mid-write must never leave a torn file
        # (which would reset every setting on the next launch).
        p = config_path()
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(p)

    def resolved_anthropic_key(self) -> str:
        import os

        return (self.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")).strip()

    def resolved_online_key(self) -> str:
        import os

        return (self.online_api_key or os.environ.get("OPENAI_API_KEY", "")).strip()

    def resolved_deepgram_key(self) -> str:
        import os

        return (self.deepgram_api_key or os.environ.get("DEEPGRAM_API_KEY", "")).strip()

    def effective_ai_actions(self) -> list:
        return self.ai_actions if self.ai_actions else list(DEFAULT_AI_ACTIONS)

    def template_instructions(self, name: str) -> str:
        for t in self.templates or []:
            if t.get("name") == name:
                return t.get("instructions", "") or ""
        return ""

    def notes_instructions(self, template_name: str = "") -> str:
        """Combined extra instructions for note generation: global custom + template."""
        parts = []
        if self.custom_instructions_enabled and (self.custom_instructions or "").strip():
            parts.append(self.custom_instructions.strip())
        ti = self.template_instructions(template_name)
        if ti.strip():
            parts.append(ti.strip())
        return "\n\n".join(parts)
