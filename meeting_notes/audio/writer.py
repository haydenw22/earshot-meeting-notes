"""Audio post-processing + file IO.

Helpers to downmix to mono, resample to a common rate, write WAV files, and
assemble the final 2-channel meeting file (ch1 = me, ch2 = them).

The finalisation path is STREAMING and crash-safe:
- raw int16 spool files are converted to WAV in small blocks (flat RAM even for
  multi-hour recordings — the old whole-file conversion could need gigabytes),
- every WAV is written to a .tmp and moved into place only when complete,
- the raw spools (the only copy of the meeting) are deleted ONLY after all three
  WAVs exist and verify — any failure leaves the spools on disk for salvage.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

TARGET_RATE = 48000
TRANSCRIBE_RATE = 16000

# Canonical filenames within a meeting's audio folder.
RAW_ME = "raw_me.wav"        # source-of-truth mic (un-processed) @ 48 kHz
RAW_THEM = "raw_them.wav"    # source-of-truth system audio @ 48 kHz
MEETING = "meeting.wav"      # 2-channel archive/playback (L=me, R=them) @ 48 kHz
SIDECAR = "spool.json"       # in-progress recording metadata (crash recovery)

_BLOCK_FRAMES = 262_144      # ~5.5 s @ 48 kHz — a few MB per read, never gigabytes


@dataclass
class SpoolInfo:
    path: str          # raw interleaved int16 PCM
    rate: int
    channels: int


@dataclass
class RecordingSpool:
    """Everything finalize_recording() needs — no audio data in memory."""
    me: SpoolInfo
    them: SpoolInfo
    duration_secs: float
    sidecar_path: Optional[str] = None


def to_mono(data: np.ndarray) -> np.ndarray:
    """(N, channels) or (N,) int16/float -> (N,) float32 in [-1, 1].

    Multichannel is downmixed from the FIRST TWO channels only: on a 5.1/7.1
    loopback device speech lives in FL/FR, and averaging all 6-8 channels
    (mostly silent) attenuates it by up to ~12 dB.
    """
    if data.dtype == np.int16:
        data = data.astype(np.float32) / 32768.0
    else:
        data = data.astype(np.float32)
    if data.ndim == 2 and data.shape[1] > 1:
        data = data[:, :2].mean(axis=1)
    elif data.ndim == 2:
        data = data[:, 0]
    return data


def resample(mono: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return mono
    import soxr

    return soxr.resample(mono, src_rate, dst_rate)


def f32_to_i16(mono: np.ndarray) -> np.ndarray:
    return np.clip(mono * 32768.0, -32768, 32767).astype(np.int16)


def write_wav(path: Path, mono: np.ndarray, samplerate: int) -> Path:
    sf.write(str(path), mono, samplerate, subtype="PCM_16")
    return path


def write_stereo(path: Path, left: np.ndarray, right: np.ndarray, samplerate: int) -> Path:
    n = min(len(left), len(right))
    stereo = np.stack([left[:n], right[:n]], axis=1)
    sf.write(str(path), stereo, samplerate, subtype="PCM_16")
    return path


def opus_supported() -> bool:
    """Can this libsndfile build write Ogg/Opus? (1.2.x on Windows: yes.)"""
    try:
        return "OPUS" in sf.available_subtypes("OGG")
    except Exception:
        return False


def transcription_ext(codec: str) -> str:
    """Upload container for the configured codec: 'opus' → .ogg (≈4× smaller
    than FLAC for speech), anything else / unsupported → lossless .flac."""
    if codec == "opus" and opus_supported():
        return "ogg"
    return "flac"


def _open_out(path: Path, samplerate: int, channels: int = 1) -> sf.SoundFile:
    """Open an output SoundFile with the format chosen by extension (Opus for
    .ogg, else PCM_16), explicit so .tmp names also work."""
    suffix = path.suffix.lower()
    if suffix == ".ogg":
        return sf.SoundFile(str(path), "w", samplerate=samplerate, channels=channels,
                            format="OGG", subtype="OPUS")
    fmt = "FLAC" if suffix in (".flac",) else "WAV"
    return sf.SoundFile(str(path), "w", samplerate=samplerate, channels=channels,
                        format=fmt, subtype="PCM_16")


def prepare_for_transcription(src_mono_48k: np.ndarray, out_path: Path) -> Path:
    """(array variant) Down to 16 kHz mono; format follows the extension."""
    down = resample(src_mono_48k, TARGET_RATE, TRANSCRIBE_RATE)
    out_path = Path(out_path)
    with _open_out(out_path, TRANSCRIBE_RATE) as out:
        out.write(down)
    return out_path


def prepare_for_transcription_file(src_path: Path, out_path: Path) -> Path:
    """Streaming file→file 48 kHz → 16 kHz mono for upload. Flat RAM regardless
    of length (a 4-hour meeting never touches a whole-file array)."""
    import soxr

    src_path, out_path = Path(src_path), Path(out_path)
    with sf.SoundFile(str(src_path)) as src:
        rs = None
        if src.samplerate != TRANSCRIBE_RATE:
            rs = soxr.ResampleStream(src.samplerate, TRANSCRIBE_RATE, 1, dtype="float32")
        with _open_out(out_path, TRANSCRIBE_RATE) as out:
            while True:
                block = src.read(_BLOCK_FRAMES, dtype="float32")
                if len(block) == 0:
                    break
                if block.ndim == 2:
                    block = block[:, :2].mean(axis=1) if block.shape[1] > 1 else block[:, 0]
                if rs is not None:
                    block = rs.resample_chunk(block)
                if len(block):
                    out.write(block)
            if rs is not None:
                tail = rs.resample_chunk(np.zeros(0, dtype=np.float32), last=True)
                if len(tail):
                    out.write(tail)
    return out_path


# ---------------------------------------------------------------------------
# Streaming finalisation (spool .raw -> canonical WAVs)
# ---------------------------------------------------------------------------

def _stream_raw_to_wav(spool: SpoolInfo, out_tmp: Path) -> int:
    """Convert one raw int16 spool to a 48 kHz mono PCM_16 WAV, block by block.
    Returns the number of frames written."""
    import soxr

    channels = max(1, int(spool.channels))
    frame_bytes = 2 * channels
    rs = None
    if spool.rate != TARGET_RATE:
        rs = soxr.ResampleStream(spool.rate, TARGET_RATE, 1, dtype="float32")

    written = 0
    carry = b""
    out_tmp.parent.mkdir(parents=True, exist_ok=True)
    with sf.SoundFile(str(out_tmp), "w", samplerate=TARGET_RATE, channels=1,
                      subtype="PCM_16", format="WAV") as out:
        exists = spool.path and os.path.exists(spool.path)
        if exists:
            with open(spool.path, "rb") as fh:
                while True:
                    buf = carry + fh.read(_BLOCK_FRAMES * frame_bytes)
                    if not buf:
                        break
                    usable = (len(buf) // frame_bytes) * frame_bytes
                    carry = buf[usable:]
                    if not usable:
                        break
                    arr = np.frombuffer(buf[:usable], dtype=np.int16).reshape(-1, channels)
                    mono = to_mono(arr)
                    if rs is not None:
                        mono = rs.resample_chunk(mono)
                    if len(mono):
                        out.write(mono)
                        written += len(mono)
        if rs is not None:  # flush the resampler's tail
            tail = rs.resample_chunk(np.zeros(0, dtype=np.float32), last=True)
            if len(tail):
                out.write(tail)
                written += len(tail)
    return written


def _append_silence(path: Path, frames: int) -> None:
    if frames <= 0:
        return
    with sf.SoundFile(str(path), "r+") as f:
        f.seek(0, sf.SEEK_END)
        left = frames
        block = np.zeros(min(_BLOCK_FRAMES, left), dtype=np.float32)
        while left > 0:
            n = min(len(block), left)
            f.write(block[:n])
            left -= n


def _write_meeting_stereo(me_path: Path, them_path: Path, out_tmp: Path) -> None:
    with sf.SoundFile(str(me_path)) as a, sf.SoundFile(str(them_path)) as b, \
         sf.SoundFile(str(out_tmp), "w", samplerate=TARGET_RATE, channels=2,
                      subtype="PCM_16", format="WAV") as out:
        while True:
            la = a.read(_BLOCK_FRAMES, dtype="float32")
            lb = b.read(_BLOCK_FRAMES, dtype="float32")
            n = min(len(la), len(lb))
            if n == 0:
                break
            out.write(np.stack([la[:n], lb[:n]], axis=1))


def finalize_recording(spool: RecordingSpool, audio_dir: Path) -> dict:
    """Stream the spooled raw audio into the canonical WAVs.

    Order of operations is the data-safety contract: the spools are deleted only
    after all three WAVs are fully written, moved into place and re-verified.
    """
    audio_dir = Path(audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)
    me_tmp = audio_dir / (RAW_ME + ".tmp")
    them_tmp = audio_dir / (RAW_THEM + ".tmp")
    meeting_tmp = audio_dir / (MEETING + ".tmp")

    me_frames = _stream_raw_to_wav(spool.me, me_tmp)
    them_frames = _stream_raw_to_wav(spool.them, them_tmp)

    # Pad the shorter stream with silence so the two channels share a timeline.
    n = max(me_frames, them_frames)
    _append_silence(me_tmp, n - me_frames)
    _append_silence(them_tmp, n - them_frames)

    _write_meeting_stereo(me_tmp, them_tmp, meeting_tmp)

    me_path, them_path, meeting_path = audio_dir / RAW_ME, audio_dir / RAW_THEM, audio_dir / MEETING
    os.replace(me_tmp, me_path)
    os.replace(them_tmp, them_path)
    os.replace(meeting_tmp, meeting_path)

    # verify before letting go of the source of truth
    for p in (me_path, them_path, meeting_path):
        info = sf.info(str(p))
        if info.frames != n and n > 0:
            raise IOError(f"finalised audio failed verification: {p.name}")

    for raw in (spool.me.path, spool.them.path):
        if raw:
            try:
                os.unlink(raw)
            except OSError:
                pass
    if spool.sidecar_path:
        try:
            os.unlink(spool.sidecar_path)
        except OSError:
            pass

    return {"me": str(me_path), "them": str(them_path), "meeting": str(meeting_path),
            "frames": n, "duration_secs": (n / TARGET_RATE) if n else 0.0}


def salvage_spool(audio_dir: Path) -> Optional[dict]:
    """Recover a recording interrupted by a crash: if the folder holds a
    spool sidecar with surviving raw files, finalise them into WAVs."""
    audio_dir = Path(audio_dir)
    sc = audio_dir / SIDECAR
    if not sc.exists():
        return None
    try:
        meta = json.loads(sc.read_text(encoding="utf-8"))
        me = meta.get("me") or {}
        them = meta.get("them") or {}
        spool = RecordingSpool(
            me=SpoolInfo(path=str(me.get("path") or ""), rate=int(me.get("rate") or TARGET_RATE),
                         channels=int(me.get("channels") or 1)),
            them=SpoolInfo(path=str(them.get("path") or ""), rate=int(them.get("rate") or TARGET_RATE),
                           channels=int(them.get("channels") or 1)),
            duration_secs=0.0,
            sidecar_path=str(sc),
        )
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return None
    if not (os.path.exists(spool.me.path) or os.path.exists(spool.them.path)):
        try:
            sc.unlink()
        except OSError:
            pass
        return None
    return finalize_recording(spool, audio_dir)


def save_recording(me_48k: np.ndarray, them_48k: np.ndarray, audio_dir: Path) -> dict:
    """(Legacy/test helper) persist in-memory streams plus the 2-channel meeting
    file. The live recording path uses finalize_recording() instead."""
    audio_dir = Path(audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)
    me_path = audio_dir / RAW_ME
    them_path = audio_dir / RAW_THEM
    meeting_path = audio_dir / MEETING
    write_wav(me_path, me_48k, TARGET_RATE)
    write_wav(them_path, them_48k, TARGET_RATE)
    write_stereo(meeting_path, me_48k, them_48k, TARGET_RATE)
    return {"me": str(me_path), "them": str(them_path), "meeting": str(meeting_path)}
