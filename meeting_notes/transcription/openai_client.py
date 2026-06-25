"""Online transcription via an OpenAI-compatible /audio/transcriptions endpoint.

Works with OpenAI (base https://api.openai.com/v1, model whisper-1) and Groq
(base https://api.groq.com/openai/v1, model whisper-large-v3) — anything that
speaks the OpenAI audio API. Returns the same {"text", "segments"} shape as the
home-server client so the two-channel merge is identical.

Use a Whisper-family model: only those return verbose_json segment timestamps.
If the response has no segments, we fall back to one whole-file segment.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx


class OnlineTranscriptionError(RuntimeError):
    pass


MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # OpenAI/Groq /audio/transcriptions limit


def _timeout(timeout: Optional[float]) -> httpx.Timeout:
    # Online APIs shouldn't hang forever, so use a finite read timeout (unlike the
    # home server, which can legitimately take many minutes).
    if timeout is None:
        return httpx.Timeout(600.0, connect=15.0)
    return httpx.Timeout(timeout, connect=15.0)


def ping(base_url: str, api_key: str, timeout: float = 10.0) -> bool:
    """True if the endpoint authenticates (lists models)."""
    if not api_key:
        return False
    base = base_url.rstrip("/")
    try:
        r = httpx.get(base + "/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def transcribe(
    audio_path: str | Path,
    *,
    base_url: str,
    api_key: str,
    model: str = "whisper-1",
    language: str = "en",
    timeout: Optional[float] = None,
) -> dict:
    if not api_key:
        raise OnlineTranscriptionError("No API key set for the online transcription service.")
    base = base_url.rstrip("/")
    url = base + "/audio/transcriptions"
    # repeated key for timestamp_granularities[] => list of tuples
    form = [
        ("model", model),
        ("response_format", "verbose_json"),
        ("timestamp_granularities[]", "segment"),
    ]
    if language:
        form.append(("language", language))

    audio_path = Path(audio_path)
    if audio_path.stat().st_size > MAX_UPLOAD_BYTES:
        raise OnlineTranscriptionError(
            "Audio exceeds the 25 MB online limit (~13 min at 16 kHz). Use the home "
            "Whisper server for long meetings, or record shorter sessions."
        )
    try:
        with open(audio_path, "rb") as fh:
            files = {"file": (audio_path.name, fh, "audio/wav")}
            resp = httpx.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                data=form,
                files=files,
                timeout=_timeout(timeout),
            )
    except httpx.HTTPError as e:
        raise OnlineTranscriptionError(f"could not reach {base}: {e}") from e

    if resp.status_code != 200:
        code = resp.status_code
        if code in (401, 403):
            raise OnlineTranscriptionError("Invalid API key or insufficient permissions for the transcription service.")
        if code == 429:
            raise OnlineTranscriptionError("Transcription service rate-limited the request. Wait a moment and retry.")
        if code == 413:
            raise OnlineTranscriptionError("Audio file too large for the transcription service (max ~25 MB).")
        if code >= 500:
            raise OnlineTranscriptionError(f"Transcription service error ({code}). Please retry shortly.")
        raise OnlineTranscriptionError(f"transcription API returned {code}: {resp.text[:300]}")

    try:
        data = resp.json()
    except ValueError as e:
        raise OnlineTranscriptionError(f"non-JSON response: {resp.text[:200]}") from e

    segments = [
        {"start": float(s.get("start") or 0.0), "end": float(s.get("end") or 0.0), "text": s.get("text") or ""}
        for s in (data.get("segments") or [])
    ]
    text = data.get("text") or ""
    if not segments and text.strip():
        segments = [{"start": 0.0, "end": float(data.get("duration") or 0.0), "text": text}]
    return {"text": text, "segments": segments}
