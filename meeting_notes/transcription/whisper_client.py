"""Client for the home server's whisper-asr-webservice (onerahmet image).

POSTs an audio file to /asr and returns the parsed JSON (text + segments, and
word-level timestamps when requested). Designed for long meetings: the read
timeout is disabled by default because a 1-hour file can take minutes server-side.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx


class WhisperError(RuntimeError):
    pass


def _timeout(timeout: Optional[float]) -> httpx.Timeout:
    if timeout is None:
        # no read/write ceiling (long meetings); still bound the connect attempt
        return httpx.Timeout(None, connect=15.0)
    return httpx.Timeout(timeout, connect=15.0)


def ping(base_url: str, timeout: float = 8.0) -> bool:
    """True if the service answers (checks the OpenAPI doc the image serves)."""
    if not (base_url or "").strip():
        return False
    base = base_url.rstrip("/")
    for path in ("/openapi.json", "/docs"):
        try:
            r = httpx.get(base + path, timeout=timeout)
            if r.status_code < 500:
                return True
        except httpx.HTTPError:
            continue
    return False


def transcribe(
    audio_path: str | Path,
    *,
    base_url: str,
    language: str = "en",
    word_timestamps: bool = True,
    vad_filter: bool = False,
    output: str = "json",
    timeout: Optional[float] = None,
) -> dict:
    if not (base_url or "").strip():
        raise WhisperError("No Whisper server URL configured. Set it in Settings → Transcription.")
    base = base_url.rstrip("/")
    params = {"task": "transcribe", "output": output, "encode": "true"}
    if language:
        params["language"] = language
    if word_timestamps:
        params["word_timestamps"] = "true"
    if vad_filter:
        # server skips silent stretches (faster-whisper engine) — big win for our
        # dual-channel recordings, where each channel is ~half silence.
        params["vad_filter"] = "true"

    audio_path = Path(audio_path)
    try:
        with open(audio_path, "rb") as fh:
            files = {"audio_file": (audio_path.name, fh, "audio/wav")}
            resp = httpx.post(
                base + "/asr", params=params, files=files, timeout=_timeout(timeout)
            )
    except httpx.HTTPError as e:
        raise WhisperError(f"could not reach Whisper at {base}: {e}") from e

    if resp.status_code != 200:
        raise WhisperError(f"Whisper returned {resp.status_code}: {resp.text[:300]}")

    if output == "json":
        try:
            return resp.json()
        except ValueError as e:
            raise WhisperError(f"Whisper returned non-JSON: {resp.text[:300]}") from e
    return {"text": resp.text, "segments": []}
