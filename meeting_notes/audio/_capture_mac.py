"""Concurrent dual-stream capture on macOS: microphone ("me") via sounddevice
(PortAudio) + system audio ("them") via the bundled earshot-audiotap helper,
which drives a Core Audio process tap (macOS 14.4+).

The contract mirrors the Windows backend exactly: raw interleaved int16 spool
files land on disk as audio arrives (flat RAM on multi-hour recordings), a
spool.json sidecar makes interrupted recordings salvageable, stop() hands the
spool paths to writer.finalize_recording without loading audio into memory,
and write failures latch into write_error for the UI to surface.

The helper prints line-delimited JSON events on stdout:
    {"event":"start","rate":48000,"channels":2,"format":"int16"}    once
    {"event":"level","rms":0.03}                                    ~8 Hz
    {"event":"error","code":"permission|tap_failed|io","message":"..."}
    {"event":"stop","frames":123456}                                clean exit
It exits on SIGTERM or when its stdin reaches EOF, so a crashed app can never
leave a headless helper recording forever; the spool + sidecar it leaves
behind are salvaged on the next launch like any interrupted recording.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

from .writer import SIDECAR, RecordingSpool, SpoolInfo

# The helper always converts to this fixed on-disk format, so the spool can
# never change rate mid-file even if the output device switches. Single source
# of truth: _devices_mac derives the synthetic "System audio" entry from it.
TAP_RATE = 48000
TAP_CHANNELS = 2

# The first run can sit on the macOS "System Audio Recording" permission
# prompt, so the start handshake window is generous.
_START_TIMEOUT_SECS = 30.0

_PERMISSION_HINT = (
    "Earshot needs permission to record system audio. Approve it under "
    "System Settings > Privacy & Security > Screen & System Audio Recording, "
    "then start the recording again."
)


def _helper_path() -> str:
    """Locate the earshot-audiotap binary: env override, then the PyInstaller
    bundle, then the dev build location."""
    env = os.environ.get("EARSHOT_AUDIOTAP")
    if env:
        return env
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "earshot-audiotap")
    root = Path(__file__).resolve().parent.parent.parent
    return str(root / "packaging" / "mac" / "bin" / "earshot-audiotap")


class _MicStream:
    """Microphone capture stream. Frames are written straight to a spool file
    in the callback, so RAM stays flat even on multi-hour recordings (no
    in-memory accumulation)."""

    def __init__(self, index: int, channels: int, rate: int,
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
        import sounddevice as sd

        self._stream = sd.InputStream(
            device=index,
            channels=self.channels,
            samplerate=self.rate,
            dtype="int16",
            blocksize=1024,
            callback=self._cb,
        )
        self._stream.start()

    @property
    def path(self) -> str:
        return self._path

    def _cb(self, in_data, frame_count, time_info, status):
        data = in_data.tobytes() if hasattr(in_data, "tobytes") else bytes(in_data)
        try:
            self._file.write(data)  # OS-buffered; fast enough for the audio thread
        except (ValueError, OSError) as e:
            if self.write_error is None:  # latch the FIRST failure; UI polls it
                self.write_error = f"{type(e).__name__}: {e}"
        try:
            arr = np.frombuffer(data, dtype=np.int16)
            if arr.size:
                rms = float(np.sqrt(np.mean((arr.astype(np.float32) / 32768.0) ** 2)))
                self.level = min(1.0, rms * 3.0)
        except (ValueError, FloatingPointError):
            pass
        return None  # sounddevice callbacks return nothing

    def _close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
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


class _SystemAudioTap:
    """System-audio capture via the earshot-audiotap helper process. Exposes
    the same surface as a capture stream (level / write_error / detach /
    discard) so DualStreamRecorder treats both channels alike."""

    def __init__(self, spool_path: Optional[Path], log_dir: Optional[Path] = None):
        self.channels = TAP_CHANNELS
        self.rate = TAP_RATE
        self.level = 0.0
        self.write_error: Optional[str] = None
        if spool_path is not None:
            spool_path.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(spool_path)
        else:
            fd, self._path = tempfile.mkstemp(suffix=".raw", prefix="earshot_")
            os.close(fd)

        helper = _helper_path()
        if not os.path.exists(helper):
            raise RuntimeError(
                "The system audio component (earshot-audiotap) is missing. "
                "Reinstall Earshot to restore it."
            )
        log_path = (Path(log_dir) / "audiotap.log") if log_dir else \
            Path(tempfile.gettempdir()) / "earshot_audiotap.log"
        self._log = open(log_path, "ab")
        self._stopping = False
        self._error_msg: Optional[str] = None
        self._handshake = threading.Event()
        self._proc = subprocess.Popen(
            [helper, "--out", self._path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log,
            close_fds=True,
        )
        self._reader = threading.Thread(
            target=self._read_events, name="earshot-audiotap-reader", daemon=True
        )
        self._reader.start()

        ok = self._handshake.wait(timeout=_START_TIMEOUT_SECS)
        if not ok or self._error_msg:
            msg = self._error_msg or _PERMISSION_HINT
            self._terminate()
            try:
                self._log.close()
            except OSError:
                pass
            raise RuntimeError(msg)

    @property
    def path(self) -> str:
        return self._path

    def _read_events(self) -> None:
        proc = self._proc
        try:
            for raw in proc.stdout:  # ends at helper exit (EOF)
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                kind = ev.get("event")
                if kind == "start":
                    try:
                        self.rate = int(ev.get("rate") or TAP_RATE)
                        self.channels = int(ev.get("channels") or TAP_CHANNELS)
                    except (TypeError, ValueError):
                        pass
                    self._handshake.set()
                elif kind == "level":
                    try:
                        self.level = min(1.0, float(ev.get("rms") or 0.0) * 3.0)
                    except (TypeError, ValueError):
                        pass
                elif kind == "error":
                    self._error_msg = str(ev.get("message") or "system audio capture failed")
                    if ev.get("code") == "permission":
                        self._error_msg = _PERMISSION_HINT
                    if self._handshake.is_set() and self.write_error is None:
                        self.write_error = f"OSError: {self._error_msg}"
                    self._handshake.set()
        except (OSError, ValueError):
            pass
        rc = proc.wait()
        self.level = 0.0
        # EOF before we asked it to stop = the helper died mid-recording. Latch
        # it so the UI shows the recording-problem banner; the mic keeps going
        # and finalize pads "them" with silence.
        if not self._stopping:
            if self._handshake.is_set() and self.write_error is None and self._error_msg is None:
                self.write_error = f"OSError: system audio helper exited unexpectedly (code {rc})"
            self._handshake.set()

    def _terminate(self) -> None:
        self._stopping = True
        proc = self._proc
        try:
            if proc.stdin:
                proc.stdin.close()  # polite ask: stdin EOF triggers clean shutdown
        except OSError:
            pass
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                proc.terminate()
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass

    def _close(self) -> None:
        self._terminate()
        try:
            self._log.close()
        except OSError:
            pass

    def discard(self) -> None:
        """Stop the helper and delete the spool (start-failure cleanup)."""
        self._close()
        try:
            os.unlink(self._path)
        except OSError:
            pass

    def detach(self) -> SpoolInfo:
        """Stop the helper and hand over the spool file."""
        self._close()
        return SpoolInfo(path=self._path, rate=self.rate, channels=self.channels)


class DualStreamRecorder:
    """Open system-audio tap + mic, record until stop(), hand back the spools."""

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
        # loop_* mirror the Windows signature; on macOS the tap helper defines
        # the real format and loop_index is the synthetic-device sentinel (-1).
        self.loop_index = loop_index
        self.loop_channels = max(1, loop_channels)
        self.loop_rate = loop_rate
        self.spool_dir = Path(spool_dir) if spool_dir else None

        self._mic: Optional[_MicStream] = None
        self._loop: Optional[_SystemAudioTap] = None
        self._sidecar: Optional[str] = None
        self._t0 = 0.0
        self._running = False

    def start(self) -> None:
        me_path = (self.spool_dir / "spool_me.raw") if self.spool_dir else None
        them_path = (self.spool_dir / "spool_them.raw") if self.spool_dir else None
        try:
            # Open the tap first so we don't miss the start of their audio.
            self._loop = _SystemAudioTap(them_path, log_dir=self.spool_dir)
            self._mic = _MicStream(self.mic_index, self.mic_channels, self.mic_rate,
                                   spool_path=me_path)
        except Exception:
            # clean up any partial allocation so a failed start can't leak streams
            if self._mic is not None:
                self._mic.discard()
                self._mic = None
            if self._loop is not None:
                self._loop.discard()
                self._loop = None
            raise
        if self.spool_dir is not None:
            # sidecar lets a crashed recording be salvaged on the next launch
            self._sidecar = str(self.spool_dir / SIDECAR)
            try:
                Path(self._sidecar).write_text(json.dumps({
                    "me": {"path": self._mic.path, "rate": self.mic_rate,
                           "channels": self._mic.channels},
                    "them": {"path": self._loop.path, "rate": self._loop.rate,
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
        return RecordingSpool(me=me, them=them, duration_secs=duration,
                              sidecar_path=self._sidecar)
