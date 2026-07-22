"""Concurrent dual-stream WASAPI capture: microphone ("me") + output loopback
("them"), each on its own stream, collected via PortAudio callbacks.

Callbacks stay tiny — they only stash raw bytes and a running level — so the
audio threads never block. Crucially, stop() does NOT load the audio into RAM:
it returns the spool-file paths and their formats, and writer.finalize_recording
streams them to WAV in small blocks. The raw spools are the source of truth and
are only deleted after the WAVs are durably on disk — a crash, MemoryError or
disk-full during finalisation can no longer destroy the meeting.

When a spool_dir is given (the meeting's own folder), a `spool.json` sidecar
records the stream formats so an interrupted recording can be salvaged on the
next launch.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pyaudiowpatch as pyaudio

from .writer import SIDECAR, RecordingSpool, SpoolInfo


class _Stream:
    """One capture stream. Frames are written straight to a spool file in the
    callback, so RAM stays flat even on multi-hour recordings (no in-memory
    accumulation)."""

    def __init__(self, p, index: int, channels: int, rate: int,
                 spool_path: Optional[Path] = None):
        self.channels = max(1, int(channels))
        self.rate = int(rate)
        self.level = 0.0  # 0..1 RMS for the meter
        # Latched on the first failed spool write (disk full, folder gone,
        # permissions). The meter keeps working off the in-memory buffer, so
        # without this latch a dead spool looks exactly like a healthy recording.
        self.write_error: Optional[str] = None
        if spool_path is not None:
            spool_path.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(spool_path)
            self._file = open(self._path, "wb")
        else:
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

    @property
    def path(self) -> str:
        return self._path

    def _cb(self, in_data, frame_count, time_info, status):
        try:
            self._file.write(in_data)  # OS-buffered; fast enough for the audio thread
        except (ValueError, OSError) as e:
            if self.write_error is None:  # latch the FIRST failure; UI polls it
                self.write_error = f"{type(e).__name__}: {e}"
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
        """Close and delete the spool without reading (start-failure cleanup)."""
        self._close()
        try:
            os.unlink(self._path)
        except OSError:
            pass

    def detach(self) -> SpoolInfo:
        """Close the stream and hand over the spool file — nothing is read into
        memory and nothing is deleted here."""
        self._close()
        return SpoolInfo(path=self._path, rate=self.rate, channels=self.channels)


class DualStreamRecorder:
    """Open mic + loopback, record until stop(), hand back the spool files."""

    def __init__(
        self,
        mic_index: int,
        mic_channels: int,
        mic_rate: int,
        loop_index: int,
        loop_channels: int,
        loop_rate: int,
        spool_dir: Optional[Path] = None,
    ):
        self.mic_index = mic_index
        self.mic_channels = min(2, max(1, mic_channels))
        self.mic_rate = mic_rate
        self.loop_index = loop_index
        self.loop_channels = max(1, loop_channels)
        self.loop_rate = loop_rate
        self.spool_dir = Path(spool_dir) if spool_dir else None

        self._p: Optional[pyaudio.PyAudio] = None
        self._mic: Optional[_Stream] = None
        self._loop: Optional[_Stream] = None
        self._sidecar: Optional[str] = None
        self._t0 = 0.0
        self._running = False

    def start(self) -> None:
        self._p = pyaudio.PyAudio()
        me_path = (self.spool_dir / "spool_me.raw") if self.spool_dir else None
        them_path = (self.spool_dir / "spool_them.raw") if self.spool_dir else None
        try:
            # Open loopback first so we don't miss the start of their audio.
            self._loop = _Stream(self._p, self.loop_index, self.loop_channels, self.loop_rate,
                                 spool_path=them_path)
            self._mic = _Stream(self._p, self.mic_index, self.mic_channels, self.mic_rate,
                                spool_path=me_path)
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
        if self.spool_dir is not None:
            # sidecar lets a crashed recording be salvaged on the next launch
            self._sidecar = str(self.spool_dir / SIDECAR)
            try:
                Path(self._sidecar).write_text(json.dumps({
                    "me": {"path": self._mic.path, "rate": self.mic_rate,
                           "channels": self._mic.channels},
                    "them": {"path": self._loop.path, "rate": self.loop_rate,
                             "channels": self._loop.channels},
                    "started_at": time.time(),
                }, indent=2), encoding="utf-8")
            except OSError:
                self._sidecar = None
        self._t0 = time.monotonic()
        self._running = True

    @property
    def running(self) -> bool:
        return self._running

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._t0 if self._running else 0.0

    @property
    def started_at(self) -> float:
        """Shared monotonic origin used by screenshots and bookmarks."""
        return self._t0

    @property
    def mic_level(self) -> float:
        return self._mic.level if self._mic else 0.0

    @property
    def them_level(self) -> float:
        return self._loop.level if self._loop else 0.0

    @property
    def write_error(self) -> Optional[str]:
        """First spool-persistence failure on either channel (labelled with the
        affected side), or None while both files are still accepting writes."""
        for label, s in (("your microphone", self._mic), ("their audio", self._loop)):
            if s is not None and s.write_error:
                return f"{label} ({s.write_error})"
        return None

    def stop(self) -> RecordingSpool:
        if not self._running:
            raise RuntimeError("recorder not running")
        duration = self.elapsed
        self._running = False
        me = self._mic.detach() if self._mic else SpoolInfo("", 48000, 1)
        them = self._loop.detach() if self._loop else SpoolInfo("", 48000, 1)
        if self._p is not None:
            self._p.terminate()
            self._p = None
        return RecordingSpool(me=me, them=them, duration_secs=duration,
                              sidecar_path=self._sidecar)
