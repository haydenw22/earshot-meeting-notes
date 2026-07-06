"""Online transcription via an OpenAI-compatible /audio/transcriptions endpoint.

Works with OpenAI (whisper-1), Groq (whisper-large-v3-turbo — ~$0.04/hour, the
cheapest accurate option), and Mistral (voxtral-mini-latest — very accurate,
with a param-compat retry because Mistral rejects the OpenAI-only form fields).
Returns the same {"text", "segments"} shape as the home-server client so the
two-channel merge is identical.

Use a model that returns segment timestamps (Whisper family / Voxtral). If the
response has no segments, we fall back to one whole-file segment.
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


_MIME = {".wav": "audio/wav", ".flac": "audio/flac", ".mp3": "audio/mpeg",
         ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".webm": "audio/webm"}


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
    # NB: pass the form as a DICT, not a list of tuples. httpx (0.28) rejects a
    # list-of-tuples `data=` alongside `files=` with a cryptic multipart-encoding
    # error ("sequence item N: expected a bytes-like object, tuple found"); a
    # Mapping is required for multipart. No duplicate keys here, so a dict is fine.
    form = {
        "model": model,
        "response_format": "verbose_json",
        "timestamp_granularities[]": "segment",
    }
    # Some OpenAI-compatible servers (e.g. Mistral Voxtral) reject the OpenAI-only
    # params — this variant asks for segments their way instead.
    form_compat = {"model": model, "timestamp_granularities": "segment"}
    if language:
        form["language"] = language
        form_compat["language"] = language

    audio_path = Path(audio_path)
    if audio_path.stat().st_size > MAX_UPLOAD_BYTES:
        raise OnlineTranscriptionError(
            "Audio exceeds the 25 MB per-request limit of this provider (roughly 40–60 min "
            "of speech as FLAC). Use the home Whisper server or Deepgram for very long meetings."
        )
    mime = _MIME.get(audio_path.suffix.lower(), "application/octet-stream")

    def _post(payload):
        with open(audio_path, "rb") as fh:
            return httpx.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                data=payload,
                files={"file": (audio_path.name, fh, mime)},
                timeout=_timeout(timeout),
            )

    try:
        resp = _post(form)
        if resp.status_code in (400, 422):
            resp = _post(form_compat)
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

    if not isinstance(data, dict):
        raise OnlineTranscriptionError(f"unexpected response shape: {str(data)[:200]}")
    raw_segments = data.get("segments")
    segments = [
        {"start": float(s.get("start") or 0.0), "end": float(s.get("end") or 0.0), "text": s.get("text") or ""}
        for s in (raw_segments if isinstance(raw_segments, list) else [])
        if isinstance(s, dict)
    ]
    text = data.get("text") or ""
    if not segments and text.strip():
        segments = [{"start": 0.0, "end": float(data.get("duration") or 0.0), "text": text}]
    return {"text": text, "segments": segments}
