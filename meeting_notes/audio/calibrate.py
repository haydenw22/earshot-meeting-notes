"""Estimate the speaker -> microphone delay so the echo canceller knows how far
'their' audio lags before it re-enters the mic.

We cross-correlate the two recorded channels offline (we have the whole file, so
this is more accurate than any live guess). A positive result means 'them' leads
'me' — i.e. the mic hears their audio that many ms later.
"""
from __future__ import annotations

import numpy as np

RATE = 48000


def estimate_delay_ms(me_48k: np.ndarray, them_48k: np.ndarray, *, max_ms: int = 500,
                      window_secs: float = 12.0) -> float:
    n = int(min(len(me_48k), len(them_48k)))
    if n < RATE:  # need at least ~1s to bother
        return 0.0
    from scipy.signal import correlate

    w = min(n, int(window_secs * RATE))
    a = np.asarray(me_48k[:w], dtype=np.float64)
    b = np.asarray(them_48k[:w], dtype=np.float64)
    a -= a.mean()
    b -= b.mean()
    if not np.any(a) or not np.any(b):
        return 0.0
    corr = correlate(a, b, mode="full", method="fft")
    lags = np.arange(-(w - 1), w)
    max_lag = int(max_ms / 1000 * RATE)
    mask = (lags >= 0) & (lags <= max_lag)
    if not mask.any():
        return 0.0
    best_lag = lags[mask][int(np.argmax(corr[mask]))]
    return float(best_lag) / RATE * 1000.0


def estimate_delay_ms_files(me_path, them_path, *, max_ms: int = 500,
                            window_secs: float = 12.0) -> float:
    """File variant: reads ONLY the correlation window (~12 s) from each file,
    so multi-hour recordings never get loaded into memory for calibration."""
    import soundfile as sf

    frames = int(window_secs * RATE)
    with sf.SoundFile(str(me_path)) as a, sf.SoundFile(str(them_path)) as b:
        me = a.read(frames, dtype="float32")
        them = b.read(frames, dtype="float32")
    if me.ndim == 2:
        me = me[:, 0]
    if them.ndim == 2:
        them = them[:, 0]
    return estimate_delay_ms(me, them, max_ms=max_ms, window_secs=window_secs)
