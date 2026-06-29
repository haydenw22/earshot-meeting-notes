"""Lightweight talk-time analytics from the speaker-labelled transcript.

Uses a word-count proxy (Me vs Them) — no audio re-analysis, no AI, no cloud.
Approximate, but a useful read on how much of the call you spoke.
"""
from __future__ import annotations

import re

_LINE = re.compile(r"^\s*\[[\d:]+\]\s*(Me|Them|Speaker)\s*:\s*(.*)$", re.IGNORECASE)


def talk_time(transcript: str) -> dict:
    me = them = other = 0
    for line in (transcript or "").splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        speaker = m.group(1).lower()
        words = len(m.group(2).split())
        if speaker == "me":
            me += words
        elif speaker == "them":
            them += words
        else:
            other += words
    total = me + them + other
    return {
        "me_words": me,
        "them_words": them,
        "total_words": total,
        "me_pct": round(100 * me / total) if total else 0,
        "them_pct": round(100 * them / total) if total else 0,
        "has_speakers": (me + them) > 0,  # False for single-speaker imports
    }
