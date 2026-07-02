"""Offline acoustic echo cancellation.

Removes the echo of the other party's audio ('them', the WASAPI loopback) from
the microphone channel ('me') using WebRTC's AEC3 via the LiveKit APM, with
'them' supplied as the far-end reference. This is what lets us drop their voice
off the mic channel WITHOUT gating — so the user's own speech survives even when
they talk over someone (the double-talk case).

Runs offline on the finished recording (we keep the raw streams), which is more
robust than live AEC: we can cross-correlate the whole file for the delay and
re-run if needed. If LiveKit isn't importable, callers fall back to the raw mic.
"""
from __future__ import annotations

import numpy as np

from .writer import f32_to_i16

RATE = 48000
FRAME = RATE * 10 // 1000  # 480 samples = 10 ms (APM requires exactly 10 ms frames)


def is_available() -> bool:
    try:
        from livekit.rtc import apm  # noqa: F401

        return True
    except Exception:
        return False


def cancel_echo(me_48k: np.ndarray, them_48k: np.ndarray, *, delay_ms: float = 0.0) -> np.ndarray:
    """Return a cleaned mic signal (float32 mono @ 48 kHz).

    me_48k / them_48k: float32 mono @ 48 kHz, ideally equal length.
    """
    from livekit import rtc
    from livekit.rtc.apm import AudioProcessingModule

    module = AudioProcessingModule(
        echo_cancellation=True,
        noise_suppression=False,
        high_pass_filter=True,
        auto_gain_control=False,
    )

    me_i = f32_to_i16(me_48k)
    them_i = f32_to_i16(them_48k)
    n = min(len(me_i), len(them_i))
    pad = (-n) % FRAME
    me_i = np.concatenate([me_i[:n], np.zeros(pad, np.int16)]) if pad else me_i[:n]
    them_i = np.concatenate([them_i[:n], np.zeros(pad, np.int16)]) if pad else them_i[:n]
    total = len(me_i)

    out = np.empty(total, dtype=np.int16)
    delay = max(0, int(delay_ms))
    for i in range(0, total, FRAME):
        out[i:i + FRAME] = _process_frame(rtc, module, me_i[i:i + FRAME], them_i[i:i + FRAME], delay)

    return out.astype(np.float32) / 32768.0


def _process_frame(rtc, module, near_i16: np.ndarray, far_i16: np.ndarray, delay: int) -> np.ndarray:
    near = bytearray(near_i16.tobytes())  # mutable: APM writes in place
    far_frame = rtc.AudioFrame(
        data=far_i16.tobytes(), sample_rate=RATE, num_channels=1, samples_per_channel=FRAME
    )
    near_frame = rtc.AudioFrame(
        data=near, sample_rate=RATE, num_channels=1, samples_per_channel=FRAME
    )
    module.process_reverse_stream(far_frame)
    module.set_stream_delay_ms(delay)
    module.process_stream(near_frame)
    return np.frombuffer(near_frame.data, dtype=np.int16)


def cancel_echo_files(me_path, them_path, out_path, *, delay_ms: float = 0.0,
                      block_frames: int = 3000) -> None:
    """Streaming file→file echo cancellation: identical output to cancel_echo()
    (same 10 ms frame sequence through one APM instance) but flat RAM, so
    multi-hour recordings can't OOM. Inputs must be mono WAV/FLAC @ 48 kHz;
    output is mono PCM_16 at the same rate.
    """
    import soundfile as sf
    from livekit import rtc
    from livekit.rtc.apm import AudioProcessingModule

    module = AudioProcessingModule(
        echo_cancellation=True,
        noise_suppression=False,
        high_pass_filter=True,
        auto_gain_control=False,
    )
    delay = max(0, int(delay_ms))
    block = block_frames * FRAME  # ~30 s per read at the default

    from pathlib import Path

    from .writer import _open_out  # WAV/FLAC by extension, PCM_16

    with sf.SoundFile(str(me_path)) as fme, sf.SoundFile(str(them_path)) as fthem:
        if fme.samplerate != RATE or fthem.samplerate != RATE:
            raise ValueError(f"AEC expects {RATE} Hz inputs")
        n = min(fme.frames, fthem.frames)
        with _open_out(Path(out_path), RATE) as out:
            done = 0
            while done < n:
                # `block` is a multiple of FRAME, so every read except the last
                # is frame-aligned; only the final partial frame gets zero-padded
                # (matching cancel_echo()'s array semantics exactly).
                want = min(block, n - done)
                me_b = f32_to_i16(_mono(fme.read(want, dtype="float32")))
                them_b = f32_to_i16(_mono(fthem.read(want, dtype="float32")))
                m = min(len(me_b), len(them_b))
                if m == 0:
                    break
                me_b, them_b = me_b[:m], them_b[:m]
                pad = (-m) % FRAME
                if pad:
                    me_b = np.concatenate([me_b, np.zeros(pad, np.int16)])
                    them_b = np.concatenate([them_b, np.zeros(pad, np.int16)])
                cleaned = np.empty(len(me_b), dtype=np.int16)
                for i in range(0, len(me_b), FRAME):
                    cleaned[i:i + FRAME] = _process_frame(
                        rtc, module, me_b[i:i + FRAME], them_b[i:i + FRAME], delay)
                out.write(cleaned)
                done += m


def _mono(x: np.ndarray) -> np.ndarray:
    if x.ndim == 2:
        return x[:, :2].mean(axis=1).astype(np.float32) if x.shape[1] > 1 else x[:, 0]
    return x
