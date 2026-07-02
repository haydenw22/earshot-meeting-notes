"""The post-recording pipeline: clean -> transcribe -> merge -> summarise -> save.

Framework-agnostic (takes a `progress` callback), so it can run on a Qt worker
thread or be driven from a script/test. Every step updates the meeting row, and
notes are written to SQLite before anything leaves the machine.

Multi-hour-safe: every audio step here is file→file streaming (AEC, resampling,
per-segment loudness) — no stage ever loads a whole recording into memory.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf

from ..audio import aec, calibrate, writer
from ..config import Config
from ..storage.repository import MeetingRepository
from ..transcription import merge as merge_mod
from ..transcription import service as transcription_service

Progress = Callable[[str], None]


def _attach_channel_energy(me_json: dict, me_path: Path, them_path: Path) -> None:
    """Tag each 'me' segment with its mic + system RMS over the segment's span.

    Lets the merge tell genuine speech (mic dominant) from faint residual bleed
    (system dominant). Seeks and reads ONLY each segment's span, so the cost is
    proportional to speech, not recording length. Best-effort: never fail
    transcription over a loudness calc.
    """
    try:
        with sf.SoundFile(str(me_path)) as fme, sf.SoundFile(str(them_path)) as fthem:
            sr = fme.samplerate

            def _rms(f: sf.SoundFile, t0: float, t1: float) -> float:
                start = min(max(0, int(t0 * sr)), f.frames)
                end = min(max(start + 1, int(t1 * sr)), f.frames)
                if end <= start:
                    return 0.0
                f.seek(start)
                seg = f.read(end - start, dtype="float32")
                if seg.ndim == 2:
                    seg = seg[:, 0]
                return float(np.sqrt((seg ** 2).mean())) if len(seg) else 0.0

            for s in me_json.get("segments") or []:
                if not isinstance(s, dict):
                    continue
                st = float(s.get("start") or 0.0)
                en = float(s.get("end") or st)
                s["me_rms"] = _rms(fme, st, en)
                s["them_rms"] = _rms(fthem, st, en)
    except Exception:
        return


def _prepare_clean_me_file(audio_dir: Path, headphones_mode: bool, progress: Progress) -> tuple[Path, bool]:
    """The mic source for transcription: the raw file (headphones / AEC missing
    or failed) or a streamed echo-cancelled copy. Returns (path, is_temporary)."""
    raw_me = audio_dir / writer.RAW_ME
    if headphones_mode:
        return raw_me, False  # no acoustic bleed -> nothing to cancel
    if not aec.is_available():
        progress("Echo canceller unavailable — using raw mic")
        return raw_me, False
    raw_them = audio_dir / writer.RAW_THEM
    progress("Cancelling echo")
    try:
        delay = calibrate.estimate_delay_ms_files(raw_me, raw_them)
        tmp = audio_dir / "me_clean.wav.tmp"
        out = audio_dir / "me_clean.wav"
        aec.cancel_echo_files(raw_me, raw_them, tmp, delay_ms=delay)
        os.replace(tmp, out)
        return out, True
    except Exception as e:  # never let AEC failure lose a recording
        progress(f"Echo cancellation failed ({e}); using raw mic")
        return raw_me, False


def _transcribe_channels(me_path: Path, them_path: Path, cfg: Config, progress: Progress) -> tuple[dict, dict]:
    """Transcribe both channels. Cloud providers are massively parallel, so the
    two channels go out CONCURRENTLY (≈ halves wall-clock time); the home server
    serialises on one model lock, so it stays sequential (parallel = no gain,
    messier progress)."""
    if cfg.transcription_provider == "home":
        progress("Transcribing you")
        me_json = transcription_service.transcribe(me_path, cfg, progress=progress)
        progress("Transcribing them")
        them_json = transcription_service.transcribe(them_path, cfg, progress=progress)
        return me_json, them_json
    progress("Transcribing both channels in parallel")
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_me = ex.submit(transcription_service.transcribe, me_path, cfg, progress=progress)
        f_them = ex.submit(transcription_service.transcribe, them_path, cfg)
        return f_me.result(), f_them.result()


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
        # 1. Clean mic (AEC offline, streamed file→file) unless on headphones.
        repo.update(meeting_id, status="Transcribing", error=None)
        clean_src, clean_is_temp = _prepare_clean_me_file(audio_dir, m.headphones_mode, progress)

        # 2. 16 kHz mono upload files, streamed (FLAC lossless by default, or
        #    Opus at ~4x smaller when enabled in Settings).
        ext = writer.transcription_ext(cfg.upload_codec)
        progress("Preparing audio for transcription")
        me_16k = writer.prepare_for_transcription_file(clean_src, audio_dir / f"me_16k.{ext}")
        them_16k = writer.prepare_for_transcription_file(audio_dir / writer.RAW_THEM,
                                                         audio_dir / f"them_16k.{ext}")
        if clean_is_temp:
            try:
                os.unlink(clean_src)  # re-derivable from the raws; don't double storage
            except OSError:
                pass

        # 3. Transcribe both channels (parallel on cloud providers).
        me_json, them_json = _transcribe_channels(me_16k, them_16k, cfg, progress)

        # Annotate each 'me' segment with mic-vs-system loudness so the merge can
        # distinguish your speech from faint bleed (only matters off headphones).
        if not m.headphones_mode:
            _attach_channel_energy(me_json, me_16k, them_16k)

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
    from ..notes import service as notes_service
    if summarize and notes_service.ready(cfg) and transcript.strip():
        repo.update(meeting_id, status="Summarizing")
        progress("Writing notes")
        try:
            notes = notes_service.generate_notes(
                transcript,
                cfg,
                attendees=m.attendees,
                agenda=m.agenda,
                human_date=m.date_text,
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
        progress("Transcribed" if notes_service.ready(cfg)
                 else f"Transcribed (notes skipped — {notes_service.missing_hint(cfg)})")
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
        result = transcription_service.transcribe(file_path, cfg, progress=progress)
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
