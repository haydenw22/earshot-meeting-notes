"""Merge two single-channel Whisper transcripts (me + them) into one
speaker-labelled transcript, ordered by time.

Because each physical channel is transcribed separately, the speaker label is
ground truth — no diarization. Overlaps (you interrupting) are preserved because
the channels were transcribed independently. A light cross-talk dedupe drops
near-identical lines that appear on both channels in an overlapping window (an
insurance against residual bleed when not on headphones).
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Optional


def _segments(whisper_json: dict, speaker: str) -> list[dict]:
    out = []
    for s in whisper_json.get("segments") or []:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        out.append(
            {
                "speaker": speaker,
                "start": float(s.get("start") or 0.0),
                "end": float(s.get("end") or 0.0),
                "text": text,
            }
        )
    return out


def _fmt_ts(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _overlaps(a: dict, b: dict) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"]


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _dedupe_crosstalk(segments: list[dict], *, threshold: float = 0.8) -> list[dict]:
    """Drop a 'Me' segment that closely matches an overlapping 'Them' segment
    (their voice bled into the mic). Prefer keeping the 'Them' original."""
    them = [s for s in segments if s["speaker"] == "Them"]
    keep = []
    for s in segments:
        if s["speaker"] == "Me":
            bleed = any(
                _overlaps(s, t) and _similar(s["text"], t["text"]) >= threshold
                for t in them
            )
            if bleed:
                continue
        keep.append(s)
    return keep


def merge_transcripts(
    me_json: dict, them_json: dict, *, dedupe: bool = True
) -> dict:
    segments = _segments(me_json, "Me") + _segments(them_json, "Them")
    # sort by start; on ties let 'Them' come first (usually the prompt/question)
    segments.sort(key=lambda s: (s["start"], 0 if s["speaker"] == "Them" else 1))
    if dedupe:
        segments = _dedupe_crosstalk(segments)

    lines = [f"[{_fmt_ts(s['start'])}] {s['speaker']}: {s['text']}" for s in segments]
    return {"segments": segments, "text": "\n".join(lines)}
