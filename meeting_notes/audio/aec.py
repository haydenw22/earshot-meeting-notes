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
        far = them_i[i:i + FRAME]
        near = bytearray(me_i[i:i + FRAME].tobytes())  # mutable: APM writes in place
        far_frame = rtc.AudioFrame(
            data=far.tobytes(), sample_rate=RATE, num_channels=1, samples_per_channel=FRAME
        )
        near_frame = rtc.AudioFrame(
            data=near, sample_rate=RATE, num_channels=1, samples_per_channel=FRAME
        )
        module.process_reverse_stream(far_frame)
        module.set_stream_delay_ms(delay)
        module.process_stream(near_frame)
        out[i:i + FRAME] = np.frombuffer(near_frame.data, dtype=np.int16)

    return out.astype(np.float32) / 32768.0
