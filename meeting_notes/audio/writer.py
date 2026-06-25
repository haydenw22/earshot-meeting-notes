"""Audio post-processing + file IO.

Helpers to downmix to mono, resample to a common rate, write WAV files, and
assemble the final 2-channel meeting file (ch1 = me, ch2 = them).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

TARGET_RATE = 48000
TRANSCRIBE_RATE = 16000


def to_mono(data: np.ndarray) -> np.ndarray:
    """(N, channels) or (N,) int16/float -> (N,) float32 in [-1, 1]."""
    if data.dtype == np.int16:
        data = data.astype(np.float32) / 32768.0
    else:
        data = data.astype(np.float32)
    if data.ndim == 2 and data.shape[1] > 1:
        data = data.mean(axis=1)
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


def prepare_for_transcription(src_mono_48k: np.ndarray, out_path: Path) -> Path:
    """Down to 16 kHz mono for a smaller upload that matches Whisper's internals."""
    down = resample(src_mono_48k, TARGET_RATE, TRANSCRIBE_RATE)
    return write_wav(out_path, down, TRANSCRIBE_RATE)


# Canonical filenames within a meeting's audio folder.
RAW_ME = "raw_me.wav"        # source-of-truth mic (un-processed) @ 48 kHz
RAW_THEM = "raw_them.wav"    # source-of-truth system audio @ 48 kHz
MEETING = "meeting.wav"      # 2-channel archive/playback (L=me, R=them) @ 48 kHz


def save_recording(me_48k: np.ndarray, them_48k: np.ndarray, audio_dir: Path) -> dict:
    """Persist the raw streams plus the 2-channel meeting file. Returns paths."""
    audio_dir = Path(audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)
    me_path = audio_dir / RAW_ME
    them_path = audio_dir / RAW_THEM
    meeting_path = audio_dir / MEETING
    write_wav(me_path, me_48k, TARGET_RATE)
    write_wav(them_path, them_48k, TARGET_RATE)
    write_stereo(meeting_path, me_48k, them_48k, TARGET_RATE)
    return {"me": str(me_path), "them": str(them_path), "meeting": str(meeting_path)}
