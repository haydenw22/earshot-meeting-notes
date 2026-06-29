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
    # online service (OpenAI-compatible: OpenAI, Groq, …)
    online_base_url: str = "https://api.openai.com/v1"
    online_api_key: str = ""
    online_model: str = "whisper-1"
    # Deepgram (cloud STT — fast, accurate, no 25 MB cap; good for long meetings)
    deepgram_api_key: str = ""
    deepgram_model: str = "nova-2"

    # --- Notes (Anthropic) ---
    anthropic_api_key: str = ""           # read from env ANTHROPIC_API_KEY if blank
    anthropic_model: str = "claude-sonnet-4-6"  # richer notes than Haiku; user can change in Settings
    auto_summary: bool = True             # generate AI notes automatically after transcription
    # AI customisation
    custom_instructions_enabled: bool = False
    custom_instructions: str = ""         # appended to every notes prompt when enabled
    templates: list = field(default_factory=list)   # [{name, instructions}] note templates per meeting type
    ai_actions: list = field(default_factory=list)  # [{name, prompt}] saved prompts for the workbench

    # --- Automation ---
    webhook_url: str = ""                 # POST the meeting here when ready
    webhook_when: str = "summary"         # "transcript" (after transcript) | "summary" (after notes)

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

    # --- Recording overlay (always-on-top mic/system lights + timer) ---
    overlay_enabled: bool = True
    overlay_opacity: float = 0.95         # 0.4–1.0 window opacity
    overlay_pos_x: int = OVERLAY_AUTO_POS  # OVERLAY_AUTO_POS = auto-place top-right
    overlay_pos_y: int = OVERLAY_AUTO_POS

    # --- Misc ---
    keep_audio: bool = True               # retain raw + 2-channel audio after summary

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
            return cls()
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in data.items() if k in known}
        unknown = {k: v for k, v in data.items() if k not in known and k != "extra"}
        cfg = cls(**clean)
        if unknown:  # preserve forward/unknown keys instead of dropping them
            cfg.extra.update(unknown)
        return cfg

    def save(self) -> None:
        config_path().write_text(
            json.dumps(asdict(self), indent=2), encoding="utf-8"
        )

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
