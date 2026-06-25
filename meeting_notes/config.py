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


@dataclass
class Config:
    # --- Transcription ---
    transcription_provider: str = "home"  # "home" (self-hosted Whisper) | "online" (OpenAI-compatible)
    whisper_language: str = "en"          # "" => auto-detect (applies to both providers)
    # home server
    whisper_url: str = DEFAULT_WHISPER_URL
    whisper_word_timestamps: bool = True
    # online service (OpenAI-compatible: OpenAI, Groq, …)
    online_base_url: str = "https://api.openai.com/v1"
    online_api_key: str = ""
    online_model: str = "whisper-1"

    # --- Notes (Anthropic) ---
    anthropic_api_key: str = ""           # read from env ANTHROPIC_API_KEY if blank
    anthropic_model: str = "claude-sonnet-4-6"  # richer notes than Haiku; user can change in Settings

    # --- Audio devices (stored by name; re-resolved to index at record time) ---
    mic_device_name: Optional[str] = None
    loopback_device_name: Optional[str] = None
    headphones_mode: bool = True          # True => skip echo cancellation (no bleed)

    # --- Output (Notion deferred; kept for forward-compatibility) ---
    notion_token: str = ""
    notion_database_id: str = ""

    # --- Appearance ---
    theme_mode: str = "light"             # "light" | "dark"
    sidebar_width: int = 258              # resizable; persisted
    sidebar_side: str = "left"            # "left" | "right"

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
