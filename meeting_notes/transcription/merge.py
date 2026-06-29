"""Merge two single-channel Whisper transcripts (me + them) into one
speaker-labelled transcript, ordered by time.

Because each physical channel is transcribed separately, the speaker label is
ground truth — no diarization. Overlaps (you interrupting) are preserved because
the channels were transcribed independently. A light cross-talk dedupe drops
near-identical lines that appear on both channels in an overlapping window (an
insurance against residual bleed when not on headphones).
"""
from __future__ import annotations

import re


def _segments(whisper_json: dict, speaker: str) -> list[dict]:
    out = []
    for s in whisper_json.get("segments") or []:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        seg = {
            "speaker": speaker,
            "start": float(s.get("start") or 0.0),
            "end": float(s.get("end") or 0.0),
            "text": text,
        }
        # optional per-segment mic/system loudness, used by the crosstalk de-duper
        if s.get("me_rms") is not None:
            seg["me_rms"] = float(s.get("me_rms") or 0.0)
        if s.get("them_rms") is not None:
            seg["them_rms"] = float(s.get("them_rms") or 0.0)
        out.append(seg)
    return out


def _fmt_ts(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


_WORD = re.compile(r"\w+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def _dedupe_crosstalk(
    segments: list[dict],
    *,
    window: float = 6.0,
    min_words: int = 4,
    contain: float = 0.7,
    keep_ratio: float = 2.0,
) -> list[dict]:
    """Drop a 'Me' segment whose words mostly reappear in nearby 'Them' speech.

    On speakers (not headphones) the remote audio bleeds faintly into the mic;
    AEC knocks its volume right down, but Whisper can still transcribe the residue,
    so chunks of the other side resurface on the 'Me' channel. We treat a 'Me'
    segment as bleed when most of its words also occur in 'Them' text within a few
    seconds — token containment, robust to run-on 'Them' segments (a whole-string
    similarity ratio misses these because the lengths differ wildly).

    When per-segment loudness is available (``me_rms`` / ``them_rms``), a text
    match is overridden if the mic clearly dominated (``me_rms > them_rms *
    keep_ratio``) — that's you genuinely speaking words that happen to echo the
    other side, not bleed. Short backchannels ('okay', 'yeah') are always kept.
    """
    them = [s for s in segments if s["speaker"] == "Them"]
    keep = []
    for s in segments:
        if s["speaker"] == "Me":
            toks = _tokens(s["text"])
            if len(toks) >= min_words:
                lo, hi = s["start"] - window, s["end"] + window
                near: set[str] = set()
                for t in them:
                    if t["start"] <= hi and t["end"] >= lo:
                        near.update(_tokens(t["text"]))
                contained = bool(near) and sum(1 for w in toks if w in near) / len(toks) >= contain
                me_rms, them_rms = s.get("me_rms"), s.get("them_rms")
                mic_dominant = (
                    me_rms is not None and them_rms is not None and me_rms > (them_rms or 0.0) * keep_ratio
                )
                if contained and not mic_dominant:
                    continue  # bleed — words already on 'Them', and the mic didn't dominate
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
