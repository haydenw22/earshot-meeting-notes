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
        # Long meetings can take minutes server-side, so the read ceiling is
        # generous — but NOT infinite: a wedged container / dropped Wi-Fi with no
        # RST would otherwise hang the worker forever (meeting stuck "Transcribing",
        # Record button disabled, and quitting then aborts the process). A 30-min
        # read stall is never legitimate even for a multi-hour file.
        return httpx.Timeout(1800.0, connect=15.0, write=300.0)
    return httpx.Timeout(timeout, connect=15.0)


def ping(base_url: str, timeout: float = 8.0) -> bool:
    """True if the service answers (checks the OpenAPI doc the image serves)."""
    if not (base_url or "").strip():
        return False
    base = base_url.rstrip("/")
    for path in ("/openapi.json", "/docs"):
        try:
            r = httpx.get(base + path, timeout=timeout)
            # require a real 200 — a 404 from some other LAN service (e.g. a
            # mistyped port hitting the NAS web UI) must NOT report "connected"
            if r.status_code == 200:
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
    mime = {".wav": "audio/wav", ".flac": "audio/flac"}.get(audio_path.suffix.lower(), "audio/wav")
    try:
        with open(audio_path, "rb") as fh:
            files = {"audio_file": (audio_path.name, fh, mime)}
            resp = httpx.post(
                base + "/asr", params=params, files=files, timeout=_timeout(timeout)
            )
    except httpx.HTTPError as e:
        raise WhisperError(f"could not reach Whisper at {base}: {e}") from e

    if resp.status_code != 200:
        raise WhisperError(f"Whisper returned {resp.status_code}: {resp.text[:300]}")

    if output == "json":
        try:
            data = resp.json()
        except ValueError as e:
            raise WhisperError(f"Whisper returned non-JSON: {resp.text[:300]}") from e
        if not isinstance(data, dict) or not isinstance(data.get("segments", []), list):
            raise WhisperError(
                "unexpected response shape — is this URL really a whisper-asr-webservice?"
            )
        return data
    return {"text": resp.text, "segments": []}
