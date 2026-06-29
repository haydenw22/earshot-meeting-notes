"""Online transcription via Deepgram's pre-recorded /v1/listen API.

Deepgram is fast and accurate and — unlike the OpenAI/Groq audio API — has no
~25 MB upload cap, so it's a good cloud option for long meetings without a home
server.

Each physical channel is sent separately, so we do NOT use Deepgram diarization;
the channel is the speaker (merge.py assigns Me/Them). We request `utterances` so
the response carries sentence-level segments with start/end times, matching the
{"text", "segments"} shape the rest of the pipeline expects.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx

API_URL = "https://api.deepgram.com/v1/listen"
PROJECTS_URL = "https://api.deepgram.com/v1/projects"


class DeepgramError(RuntimeError):
    pass


def _timeout(timeout: Optional[float]) -> httpx.Timeout:
    if timeout is None:
        return httpx.Timeout(600.0, connect=15.0)
    return httpx.Timeout(timeout, connect=15.0)


def ping(api_key: str, timeout: float = 10.0) -> bool:
    """True if the key authenticates (lists Deepgram projects)."""
    if not (api_key or "").strip():
        return False
    try:
        r = httpx.get(PROJECTS_URL, headers={"Authorization": f"Token {api_key}"}, timeout=timeout)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def transcribe(
    audio_path: str | Path,
    *,
    api_key: str,
    model: str = "nova-2",
    language: str = "en",
    timeout: Optional[float] = None,
) -> dict:
    if not (api_key or "").strip():
        raise DeepgramError("No Deepgram API key set. Add it in Settings → Transcription.")

    params = {"model": model or "nova-2", "smart_format": "true", "utterances": "true"}
    if language:
        params["language"] = language
    else:
        params["detect_language"] = "true"

    audio_path = Path(audio_path)
    try:
        data = audio_path.read_bytes()
    except OSError as e:
        raise DeepgramError(f"could not read audio file: {e}") from e

    try:
        resp = httpx.post(
            API_URL,
            params=params,
            headers={"Authorization": f"Token {api_key}", "Content-Type": "audio/wav"},
            content=data,
            timeout=_timeout(timeout),
        )
    except httpx.HTTPError as e:
        raise DeepgramError(f"could not reach Deepgram: {e}") from e

    if resp.status_code != 200:
        code = resp.status_code
        if code in (401, 403):
            raise DeepgramError("Invalid Deepgram API key or insufficient permissions.")
        if code == 429:
            raise DeepgramError("Deepgram rate-limited the request. Wait a moment and retry.")
        if code == 400:
            raise DeepgramError(f"Deepgram rejected the request (bad model or parameters): {resp.text[:300]}")
        if code >= 500:
            raise DeepgramError(f"Deepgram server error ({code}). Please retry shortly.")
        raise DeepgramError(f"Deepgram returned {code}: {resp.text[:300]}")

    try:
        payload = resp.json()
    except ValueError as e:
        raise DeepgramError(f"non-JSON response from Deepgram: {resp.text[:200]}") from e

    return parse_response(payload)


def parse_response(payload: dict) -> dict:
    """Map Deepgram's JSON to {"text", "segments":[{start,end,text}]}."""
    results = payload.get("results") or {}
    channels = results.get("channels") or []
    alt: dict = {}
    if channels:
        alts = channels[0].get("alternatives") or []
        if alts:
            alt = alts[0] or {}
    text = (alt.get("transcript") or "").strip()

    segments = []
    for u in results.get("utterances") or []:
        t = (u.get("transcript") or "").strip()
        if not t:
            continue
        segments.append(
            {"start": float(u.get("start") or 0.0), "end": float(u.get("end") or 0.0), "text": t}
        )

    if not segments and text:
        duration = float((payload.get("metadata") or {}).get("duration") or 0.0)
        segments = [{"start": 0.0, "end": duration, "text": text}]
    if not text and segments:
        text = " ".join(s["text"] for s in segments)

    return {"text": text, "segments": segments}
