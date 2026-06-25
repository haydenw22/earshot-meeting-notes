"""Transcription provider dispatcher.

Routes a transcription request to the configured backend — the self-hosted
Whisper server (default) or an online OpenAI-compatible service — so the rest of
the pipeline doesn't care which is in use. Both return {"text", "segments"}.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..config import Config
from . import openai_client, whisper_client


def transcribe(audio_path: str | Path, cfg: Config, *, timeout: Optional[float] = None) -> dict:
    if cfg.transcription_provider == "online":
        return openai_client.transcribe(
            audio_path,
            base_url=cfg.online_base_url,
            api_key=cfg.resolved_online_key(),
            model=cfg.online_model,
            language=cfg.whisper_language,
            timeout=timeout,
        )
    return whisper_client.transcribe(
        audio_path,
        base_url=cfg.whisper_url,
        language=cfg.whisper_language,
        word_timestamps=cfg.whisper_word_timestamps,
        timeout=timeout,
    )


def test_connection(cfg: Config) -> bool:
    if cfg.transcription_provider == "online":
        return openai_client.ping(cfg.online_base_url, cfg.resolved_online_key())
    return whisper_client.ping(cfg.whisper_url)
