"""Chunked transcription: split an over-limit audio file at quiet points, send
the pieces, and stitch the results back with corrected timestamps — so provider
upload caps (25 MB on OpenAI/Groq, 3 h on Mistral) stop being a meeting-length
limit. Everything streams block-wise; a 6-hour file never touches RAM whole.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf

_BLOCK = 262_144           # frames per IO block while copying
_SEARCH_S = 12.0           # look ±12 s around a nominal cut for the quietest spot
_RMS_WIN_S = 0.2           # candidate windows of 200 ms


def _quiet_cut(f: sf.SoundFile, nominal: int) -> int:
    """Refine a cut position to the quietest 200 ms window within ±12 s, so we
    split between words/turns rather than through them."""
    sr = f.samplerate
    half = int(_SEARCH_S * sr)
    start = max(0, nominal - half)
    end = min(f.frames, nominal + half)
    f.seek(start)
    win = f.read(end - start, dtype="float32")
    if win.ndim == 2:
        win = win[:, 0]
    if len(win) < sr:  # window too small to matter
        return nominal
    step = int(_RMS_WIN_S * sr)
    n = (len(win) // step) * step
    rms = np.sqrt((win[:n].reshape(-1, step) ** 2).mean(axis=1))
    return start + int(np.argmin(rms)) * step + step // 2


def plan_chunks(path: Path, *, max_bytes: int, max_secs: float) -> list[tuple[int, int]]:
    """Frame ranges [(start, end), ...] each fitting under both limits.
    Returns a single full-range chunk when the file already fits."""
    path = Path(path)
    size = path.stat().st_size
    with sf.SoundFile(str(path)) as f:
        frames, sr = f.frames, f.samplerate
        duration = frames / sr if sr else 0.0
        if size <= max_bytes and duration <= max_secs:
            return [(0, frames)]
        bytes_per_sec = size / max(duration, 1e-6)
        # This is only a PLANNING estimate (the source may be compressed
        # differently from the FLAC chunks we write) — the hard cap is enforced
        # after writing, by measuring and recursively splitting in
        # transcribe_chunked. The floor only guards against degenerate pieces.
        chunk_secs = min(max_secs, (max_bytes * 0.9) / bytes_per_sec)
        chunk_secs = max(10.0, chunk_secs)
        cuts = [0]
        pos = int(chunk_secs * sr)
        while pos < frames - int(30 * sr):  # a trailing sliver joins the last chunk
            c = _quiet_cut(f, pos)
            # cuts must strictly advance — a quiet spot behind the previous cut
            # would create empty/crossing ranges (or loop forever)
            c = max(c, cuts[-1] + int(10 * sr))
            if c >= frames:
                break
            cuts.append(c)
            pos = c + int(chunk_secs * sr)
        cuts.append(frames)
    return [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1)]


def _extract(src: sf.SoundFile, start: int, end: int, out_path: Path) -> None:
    """Write [start, end) as MONO FLAC at the source rate. Always FLAC: it
    accepts any sample rate (Opus doesn't) and its size tracks content, unlike
    a WAV re-encode of a compressed source. Multichannel sources are downmixed
    from the first two channels — never silently dropped to channel 0."""
    src.seek(start)
    left = end - start
    with sf.SoundFile(str(out_path), "w", samplerate=src.samplerate, channels=1,
                      format="FLAC", subtype="PCM_16") as out:
        while left > 0:
            block = src.read(min(_BLOCK, left), dtype="float32")
            if len(block) == 0:
                break
            if block.ndim == 2:
                block = block[:, :2].mean(axis=1) if block.shape[1] > 1 else block[:, 0]
            out.write(block)
            left -= len(block)


def transcribe_chunked(
    audio_path: str | Path,
    transcribe_one: Callable[[Path], dict],
    *,
    max_bytes: int,
    max_secs: float = 170 * 60,
    progress: Optional[Callable[[str], None]] = None,
) -> dict:
    """Transcribe `audio_path`, splitting first if it exceeds the limits.
    Each chunk's segment timestamps are shifted by the chunk's start time, so
    the stitched result is indistinguishable from a single-request transcript.

    Files soundfile can't parse (e.g. imported .mp4) are sent whole, unchanged.
    """
    audio_path = Path(audio_path)
    try:
        chunks = plan_chunks(audio_path, max_bytes=max_bytes, max_secs=max_secs)
    except (sf.LibsndfileError, RuntimeError, OSError):
        return transcribe_one(audio_path)  # not splittable — old behaviour
    if len(chunks) == 1:
        return transcribe_one(audio_path)

    segments: list[dict] = []
    texts: list[str] = []
    done = {"parts": 0}
    with sf.SoundFile(str(audio_path)) as src:
        sr = src.samplerate

        def do_range(start: int, end: int, depth: int = 0) -> None:
            fd, tmp_name = tempfile.mkstemp(suffix=".flac", prefix="earshot_chunk_")
            os.close(fd)
            tmp = Path(tmp_name)
            try:
                _extract(src, start, end, tmp)
                # The plan was an estimate; the cap is enforced on MEASURED
                # bytes. Density-skewed VBR or transcode growth → split in half
                # at a quiet point and recurse.
                if tmp.stat().st_size > max_bytes:
                    if depth >= 6 or (end - start) < int(20 * sr):
                        raise ValueError(
                            "a section of this audio cannot be made small enough for the "
                            "provider's upload limit — use the home server or Deepgram"
                        )
                    mid = _quiet_cut(src, (start + end) // 2)
                    mid = min(max(mid, start + int(10 * sr)), end - int(10 * sr))
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                    do_range(start, mid, depth + 1)
                    do_range(mid, end, depth + 1)
                    return
                done["parts"] += 1
                if progress:
                    progress(f"Transcribing part {done['parts']}…")
                result = transcribe_one(tmp)
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            offset = start / sr
            for s in result.get("segments") or []:
                if not isinstance(s, dict):
                    continue
                segments.append({
                    "start": float(s.get("start") or 0.0) + offset,
                    "end": float(s.get("end") or 0.0) + offset,
                    "text": s.get("text") or "",
                })
            t = (result.get("text") or "").strip()
            if t:
                texts.append(t)

        for start, end in chunks:
            do_range(start, end)
    return {"text": " ".join(texts), "segments": segments}
