"""Transcription provider dispatcher.

Routes a transcription request to the configured backend — the self-hosted
Whisper server (default), an online OpenAI-compatible service, or Deepgram — so
the rest of the pipeline doesn't care which is in use. All return {"text", "segments"}.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..config import Config
from . import deepgram_client, openai_client, whisper_client


def transcribe(audio_path: str | Path, cfg: Config, *, timeout: Optional[float] = None,
               progress=None) -> dict:
    # Earshot Plus (cloud): the proxy holds the provider creds — route every
    # request through the same chunker used for the online provider (each chunk
    # fits the 24 MB planning cap, under the proxy's 25 MB hard limit).
    if cfg.account_mode == "cloud":
        from . import chunker, earshot_client

        def one_cloud(p):
            return earshot_client.transcribe(
                p,
                base_url=cfg.cloud_api_base,
                token=cfg.cloud_token,
                language=cfg.whisper_language,
                timeout=timeout,
            )

        return chunker.transcribe_chunked(audio_path, one_cloud, max_bytes=24_000_000,
                                          progress=progress)
    if cfg.transcription_provider == "online":
        from . import chunker

        def one(p):
            return openai_client.transcribe(
                p,
                base_url=cfg.online_base_url,
                api_key=cfg.resolved_online_key(),
                model=cfg.online_model,
                language=cfg.whisper_language,
                timeout=timeout,
            )

        # Split at quiet points when over the provider caps (25 MB OpenAI/Groq,
        # 3 h Mistral) — meeting length stops being limited by the provider.
        return chunker.transcribe_chunked(audio_path, one, max_bytes=24_000_000,
                                          progress=progress)
    if cfg.transcription_provider == "deepgram":
        return deepgram_client.transcribe(
            audio_path,
            api_key=cfg.resolved_deepgram_key(),
            model=cfg.deepgram_model,
            language=cfg.whisper_language,
            timeout=timeout,
        )
    return whisper_client.transcribe(
        audio_path,
        base_url=cfg.whisper_url,
        language=cfg.whisper_language,
        word_timestamps=cfg.whisper_word_timestamps,
        vad_filter=cfg.whisper_vad_filter,
        timeout=timeout,
    )


def test_connection(cfg: Config) -> bool:
    if cfg.account_mode == "cloud":
        from . import earshot_client
        return earshot_client.ping(cfg.cloud_api_base, cfg.cloud_token)
    if cfg.transcription_provider == "online":
        return openai_client.ping(cfg.online_base_url, cfg.resolved_online_key())
    if cfg.transcription_provider == "deepgram":
        return deepgram_client.ping(cfg.resolved_deepgram_key())
    return whisper_client.ping(cfg.whisper_url)
