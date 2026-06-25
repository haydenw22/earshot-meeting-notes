"""Concurrent dual-stream WASAPI capture: microphone ("me") + output loopback
("them"), each on its own stream, collected via PortAudio callbacks.

Callbacks stay tiny — they only stash raw bytes and a running level — so the
audio threads never block. Alignment/AEC/resampling all happen on stop(), which
is the robust "offline" model (we keep the raw streams as the source of truth).
"""
from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pyaudiowpatch as pyaudio

from .writer import TARGET_RATE, resample, to_mono


@dataclass
class RecordingResult:
    me_48k: np.ndarray          # float32 mono @ 48 kHz (microphone)
    them_48k: np.ndarray        # float32 mono @ 48 kHz (system audio)
    samplerate: int
    duration_secs: float
    mic_rate: int
    loop_rate: int


class _Stream:
    """One capture stream. Frames are written straight to a temp file in the
    callback, so RAM stays flat even on multi-hour recordings (no in-memory
    accumulation)."""

    def __init__(self, p, index: int, channels: int, rate: int):
        self.channels = max(1, int(channels))
        self.rate = int(rate)
        self.level = 0.0  # 0..1 RMS for the meter
        fd, self._path = tempfile.mkstemp(suffix=".raw", prefix="earshot_")
        self._file = os.fdopen(fd, "wb")
        self._stream = p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.rate,
            frames_per_buffer=1024,
            input=True,
            input_device_index=index,
            stream_callback=self._cb,
        )

    def _cb(self, in_data, frame_count, time_info, status):
        try:
            self._file.write(in_data)  # OS-buffered; fast enough for the audio thread
        except (ValueError, OSError):
            pass
        try:
            arr = np.frombuffer(in_data, dtype=np.int16)
            if arr.size:
                rms = float(np.sqrt(np.mean((arr.astype(np.float32) / 32768.0) ** 2)))
                self.level = min(1.0, rms * 3.0)
        except (ValueError, FloatingPointError):
            pass
        return (None, pyaudio.paContinue)

    def _close(self) -> None:
        try:
            self._stream.stop_stream()
            self._stream.close()
        except OSError:
            pass
        try:
            self._file.close()
        except OSError:
            pass

    def discard(self) -> None:
        """Close and delete the temp file without reading (start-failure cleanup)."""
        self._close()
        try:
            os.unlink(self._path)
        except OSError:
            pass

    def stop(self) -> np.ndarray:
        self._close()
        try:
            arr = np.fromfile(self._path, dtype=np.int16)
        except OSError:
            arr = np.zeros(0, dtype=np.int16)
        finally:
            try:
                os.unlink(self._path)
            except OSError:
                pass
        if arr.size == 0:
            return np.zeros(0, dtype=np.float32)
        usable = (arr.size // self.channels) * self.channels
        arr = arr[:usable].reshape(-1, self.channels)
        return to_mono(arr)  # float32 mono @ self.rate


class DualStreamRecorder:
    """Open mic + loopback, record until stop(), return aligned 48 kHz mono pair."""

    def __init__(
        self,
        mic_index: int,
        mic_channels: int,
        mic_rate: int,
        loop_index: int,
        loop_channels: int,
        loop_rate: int,
    ):
        self.mic_index = mic_index
        self.mic_channels = min(2, max(1, mic_channels))
        self.mic_rate = mic_rate
        self.loop_index = loop_index
        self.loop_channels = max(1, loop_channels)
        self.loop_rate = loop_rate

        self._p: Optional[pyaudio.PyAudio] = None
        self._mic: Optional[_Stream] = None
        self._loop: Optional[_Stream] = None
        self._t0 = 0.0
        self._running = False

    def start(self) -> None:
        self._p = pyaudio.PyAudio()
        try:
            # Open loopback first so we don't miss the start of their audio.
            self._loop = _Stream(self._p, self.loop_index, self.loop_channels, self.loop_rate)
            self._mic = _Stream(self._p, self.mic_index, self.mic_channels, self.mic_rate)
        except Exception:
            # clean up any partial allocation so a failed start can't leak streams
            if self._mic is not None:
                self._mic.discard()
                self._mic = None
            if self._loop is not None:
                self._loop.discard()
                self._loop = None
            try:
                self._p.terminate()
            except Exception:
                pass
            self._p = None
            raise
        self._t0 = time.monotonic()
        self._running = True

    @property
    def running(self) -> bool:
        return self._running

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._t0 if self._running else 0.0

    @property
    def mic_level(self) -> float:
        return self._mic.level if self._mic else 0.0

    @property
    def them_level(self) -> float:
        return self._loop.level if self._loop else 0.0

    def stop(self) -> RecordingResult:
        if not self._running:
            raise RuntimeError("recorder not running")
        duration = self.elapsed
        self._running = False
        me = self._mic.stop() if self._mic else np.zeros(0, dtype=np.float32)
        them = self._loop.stop() if self._loop else np.zeros(0, dtype=np.float32)
        if self._p is not None:
            self._p.terminate()
            self._p = None

        me_48k = resample(me, self.mic_rate, TARGET_RATE) if me.size else me
        them_48k = resample(them, self.loop_rate, TARGET_RATE) if them.size else them

        # Pad the shorter stream with silence so the two channels share a timeline.
        n = max(len(me_48k), len(them_48k))
        if n:
            me_48k = _pad(me_48k, n)
            them_48k = _pad(them_48k, n)

        return RecordingResult(
            me_48k=me_48k,
            them_48k=them_48k,
            samplerate=TARGET_RATE,
            duration_secs=duration,
            mic_rate=self.mic_rate,
            loop_rate=self.loop_rate,
        )


def _pad(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) >= n:
        return x[:n]
    return np.concatenate([x, np.zeros(n - len(x), dtype=np.float32)])
