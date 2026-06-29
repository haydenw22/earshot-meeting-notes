"""The post-recording pipeline: clean -> transcribe -> merge -> summarise -> save.

Framework-agnostic (takes a `progress` callback), so it can run on a Qt worker
thread or be driven from a script/test. Every step updates the meeting row, and
notes are written to SQLite before anything leaves the machine.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf

from ..audio import aec, calibrate, writer
from ..config import Config
from ..notes import anthropic_client
from ..storage.repository import MeetingRepository
from ..transcription import merge as merge_mod
from ..transcription import service as transcription_service

Progress = Callable[[str], None]


def _read_mono(path: Path) -> np.ndarray:
    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim == 2:
        data = data.mean(axis=1)
    if sr != writer.TARGET_RATE:
        data = writer.resample(data, sr, writer.TARGET_RATE)
    return data


def _prep_clean_me(audio_dir: Path, headphones_mode: bool, progress: Progress) -> np.ndarray:
    me = _read_mono(audio_dir / writer.RAW_ME)
    if headphones_mode:
        return me  # no acoustic bleed -> nothing to cancel
    if not aec.is_available():
        progress("Echo canceller unavailable — using raw mic")
        return me
    them = _read_mono(audio_dir / writer.RAW_THEM)
    progress("Cancelling echo")
    delay = calibrate.estimate_delay_ms(me, them)
    try:
        return aec.cancel_echo(me, them, delay_ms=delay)
    except Exception as e:  # never let AEC failure lose a recording
        progress(f"Echo cancellation failed ({e}); using raw mic")
        return me


def process_recording(
    repo: MeetingRepository,
    meeting_id: int,
    cfg: Config,
    *,
    progress: Optional[Progress] = None,
    summarize: bool = True,
) -> None:
    progress = progress or (lambda _m: None)
    m = repo.get(meeting_id)
    if not m.audio_dir:
        raise ValueError("meeting has no audio")
    audio_dir = Path(m.audio_dir)

    try:
        # 0. Confirm the recording is actually on disk before we mark progress.
        if not (audio_dir / writer.RAW_ME).exists() or not (audio_dir / writer.RAW_THEM).exists():
            raise FileNotFoundError(f"recording audio not found in {audio_dir}")
        # 1. Clean mic (AEC offline) unless on headphones.
        repo.update(meeting_id, status="Transcribing", error=None)
        clean_me = _prep_clean_me(audio_dir, m.headphones_mode, progress)
        them = _read_mono(audio_dir / writer.RAW_THEM)

        # 2. 16 kHz mono files for upload.
        me_16k = writer.prepare_for_transcription(clean_me, audio_dir / "me_16k.wav")
        them_16k = writer.prepare_for_transcription(them, audio_dir / "them_16k.wav")

        # 3. Transcribe each channel separately (home server or online service).
        progress("Transcribing you")
        me_json = transcription_service.transcribe(me_16k, cfg)
        progress("Transcribing them")
        them_json = transcription_service.transcribe(them_16k, cfg)

        # 4. Merge into one speaker-labelled transcript.
        progress("Merging transcript")
        merged = merge_mod.merge_transcripts(
            me_json, them_json, dedupe=not m.headphones_mode
        )
        repo.update(meeting_id, transcript=merged["text"])
        if cfg.webhook_when == "transcript":
            _fire_webhook(repo, meeting_id, cfg, progress)

        # 5. Summarise (+ webhook on completion).
        _summarise(repo, meeting_id, cfg, m, merged["text"], summarize, progress)
    except Exception as e:
        repo.update(meeting_id, status="Error", error=str(e))
        raise


def _summarise(repo, meeting_id, cfg, m, transcript: str, summarize: bool, progress) -> None:
    """Shared summary step for recordings and imports."""
    api_key = cfg.resolved_anthropic_key()
    if summarize and api_key and transcript.strip():
        repo.update(meeting_id, status="Summarizing")
        progress("Writing notes")
        try:
            notes = anthropic_client.generate_notes(
                transcript,
                api_key=api_key,
                attendees=m.attendees,
                agenda=m.agenda,
                human_date=m.date_text,
                model=cfg.anthropic_model,
                extra_instructions=cfg.notes_instructions(m.template),
            )
        except Exception as note_err:
            repo.update(
                meeting_id,
                title=(m.title or f"Meeting — {m.date_text}"),
                status="Transcribed",
                error=f"Notes failed: {note_err}",
            )
            progress(f"Transcribed — notes failed ({note_err})")
            if cfg.webhook_when == "summary":
                _fire_webhook(repo, meeting_id, cfg, progress)
            return
        repo.update(
            meeting_id,
            title=notes.title,
            notes_json=notes.model_dump_json(),
            attendees=notes.attendees or m.attendees,
            status="Done",
            error=None,
        )
        progress("Done")
    else:
        repo.update(meeting_id, title=(m.title or f"Meeting — {m.date_text}"), status="Transcribed")
        progress("Transcribed" if api_key else "Transcribed (no Anthropic key — notes skipped)")
    if cfg.webhook_when == "summary":
        _fire_webhook(repo, meeting_id, cfg, progress)


def _fire_webhook(repo, meeting_id, cfg, progress) -> None:
    if not (cfg.webhook_url or "").strip():
        return
    try:
        from ..integrations import webhook
        webhook.send(cfg.webhook_url, webhook.build_payload(repo.get(meeting_id)))
        progress("Sent to webhook")
    except Exception as e:  # never fail the pipeline because a webhook is down
        progress(f"Webhook failed: {e}")


def _fmt_ts(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    mnt, s = divmod(rem, 60)
    return f"{h:d}:{mnt:02d}:{s:02d}" if h else f"{mnt:02d}:{s:02d}"


def _single_speaker_transcript(result: dict) -> str:
    lines = []
    for seg in result.get("segments") or []:
        text = (seg.get("text") or "").strip()
        if text:
            lines.append(f"[{_fmt_ts(float(seg.get('start') or 0))}] {text}")
    if not lines and (result.get("text") or "").strip():
        return result["text"].strip()
    return "\n".join(lines)


def process_imported_file(repo, meeting_id, cfg, file_path: str, *, progress=None, summarize: bool = True) -> None:
    """Transcribe an imported audio/video file (single speaker) and summarise it."""
    progress = progress or (lambda _m: None)
    m = repo.get(meeting_id)
    try:
        repo.update(meeting_id, status="Transcribing", error=None)
        progress("Transcribing file")
        result = transcription_service.transcribe(file_path, cfg)
        text = _single_speaker_transcript(result)
        if not text.strip():
            raise ValueError("No speech found in the file.")
        repo.update(meeting_id, transcript=text)
        if cfg.webhook_when == "transcript":
            _fire_webhook(repo, meeting_id, cfg, progress)
        _summarise(repo, meeting_id, cfg, m, text, summarize, progress)
    except Exception as e:
        repo.update(meeting_id, status="Error", error=str(e))
        raise
