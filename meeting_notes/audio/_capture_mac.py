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
from collections import deque
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
_START_TIMEOUT_SECS = 120.0

# A transiently-latched capture problem (dropped buffers during a system
# stall, one failed spool write) clears itself after this many seconds of
# provably clean capture; helper death never clears.
_RECOVERY_SECS = 10

_PERMISSION_HINT = (
    "Earshot needs permission to record system audio. Approve it under "
    "System Settings > Privacy & Security > Screen & System Audio Recording, "
    "then start the recording again."
)

_MIC_PERMISSION_HINT = (
    "Earshot needs permission to record your microphone. Approve it under "
    "System Settings > Privacy & Security > Microphone, then start the recording again."
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


def ensure_capture_permissions(mic_index: Optional[int] = None) -> None:
    """Request microphone permission before either live stream starts.

    The system-audio permission prompt is raised by AudioDeviceStart inside the
    helper and has no public preflight API. Requesting the microphone first
    prevents its prompt from appearing after the system tap is already writing;
    measured per-stream offsets handle the remaining device-start latency.
    """
    if not getattr(sys, "frozen", False):
        # A command-line Swift helper has no enclosing bundle/usage string in a
        # source checkout. Let PortAudio make a throwaway probe under the same
        # Python/Terminal identity that will perform the real mic capture.
        try:
            import sounddevice as sd

            probe = sd.RawInputStream(
                device=mic_index, channels=1, dtype="int16", blocksize=0,
                callback=lambda *_args: None,
            )
            probe.start()
            probe.stop()
            probe.close()
            return
        except Exception as exc:
            raise RuntimeError(f"{_MIC_PERMISSION_HINT} ({exc})") from exc

    helper = _helper_path()
    if not os.path.isfile(helper):
        raise RuntimeError(
            "The system audio component (earshot-audiotap) is missing. "
            "Reinstall Earshot to restore it."
        )
    try:
        proc = subprocess.run(
            [helper, "--request-microphone"], capture_output=True, text=True,
            timeout=125.0, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not request microphone permission: {exc}") from exc
    event = None
    for line in (proc.stdout or "").splitlines():
        try:
            candidate = json.loads(line)
        except (TypeError, ValueError):
            continue
        if candidate.get("event") == "permission":
            event = candidate
    if proc.returncode or not event or not event.get("granted"):
        raise RuntimeError(_MIC_PERMISSION_HINT)


class _AudioBlock:
    """One reusable microphone callback buffer; allocated before capture."""

    __slots__ = ("data", "used")

    def __init__(self, size: int):
        self.data = bytearray(size)
        self.used = 0


class _MicStream:
    """Microphone capture with a bounded preallocated callback queue.

    PortAudio's real-time callback only copies into a reusable block. File I/O,
    NumPy allocation and level calculation happen on the writer thread.
    """

    def __init__(self, index: int, channels: int, rate: int,
                 spool_path: Optional[Path] = None):
        self.channels = max(1, int(channels))
        self.rate = int(rate)
        self.level = 0.0  # 0..1 RMS for the meter
        self.started_at = 0.0
        # Latched on the first failed spool write (disk full, folder gone,
        # permissions). The meter keeps working off the in-memory buffer, so
        # without this latch a dead spool looks exactly like a healthy recording.
        self.write_error: Optional[str] = None
        self._stream = None
        self._file = None
        self._stopping = False
        self._wake = threading.Event()
        self._dropped_blocks = 0
        self._callback_issue: Optional[str] = None
        block_bytes = max(256 * 1024, self.rate * self.channels * 2)
        self._free = deque(_AudioBlock(block_bytes) for _ in range(32))
        self._ready = deque()
        self._writer = threading.Thread(
            target=self._write_loop, name="earshot-mic-writer", daemon=True
        )
        try:
            if spool_path is not None:
                spool_path.parent.mkdir(parents=True, exist_ok=True)
                self._path = str(spool_path)
                self._file = open(self._path, "wb")
            else:
                fd, self._path = tempfile.mkstemp(suffix=".raw", prefix="earshot_")
                self._file = os.fdopen(fd, "wb")
            import sounddevice as sd

            self._writer.start()
            self._stream = sd.RawInputStream(
                device=index,
                channels=self.channels,
                samplerate=self.rate,
                dtype="int16",
                blocksize=0,
                callback=self._cb,
            )
            # PortAudio may invoke the first callback before start() returns;
            # this is the earliest point at which mic frames can enter our queue.
            self.started_at = time.monotonic()
            self._stream.start()
        except Exception:
            self._stopping = True
            self._wake.set()
            if self._writer.is_alive():
                self._writer.join(timeout=2.0)
            if self._file is not None:
                try:
                    self._file.close()
                except OSError:
                    pass
            path = getattr(self, "_path", "")
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            raise

    @property
    def path(self) -> str:
        return self._path

    def _cb(self, in_data, frame_count, time_info, status):
        if status:
            self._callback_issue = str(status)
        try:
            block = self._free.popleft()
        except IndexError:
            self._dropped_blocks += 1
            return None
        size = len(in_data)
        if size > len(block.data):
            self._free.append(block)
            self._dropped_blocks += 1
            return None
        block.data[:size] = in_data
        block.used = size
        self._ready.append(block)
        self._wake.set()
        return None  # sounddevice callbacks return nothing

    def _write_loop(self) -> None:
        while not self._stopping or self._ready:
            try:
                block = self._ready.popleft()
            except IndexError:
                self._wake.clear()
                self._wake.wait(0.1)
                continue
            view = memoryview(block.data)[:block.used]
            try:
                self._file.write(view)
            except (ValueError, OSError) as exc:
                if self.write_error is None:
                    self.write_error = f"{type(exc).__name__}: {exc}"
            try:
                arr = np.frombuffer(view, dtype=np.int16)
                if arr.size:
                    scaled = arr.astype(np.float32) / 32768.0
                    rms = float(np.sqrt(np.mean(scaled * scaled)))
                    self.level = min(1.0, rms * 3.0)
            except (ValueError, FloatingPointError):
                pass
            block.used = 0
            self._free.append(block)
            if self.write_error is None and (self._dropped_blocks or self._callback_issue):
                detail = self._callback_issue or f"{self._dropped_blocks} callback blocks dropped"
                self.write_error = f"audio input overflow ({detail})"

    def _close(self) -> None:
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception:
            pass
        self._stopping = True
        self._wake.set()
        if self._writer.is_alive():
            self._writer.join(timeout=5.0)
        if self._writer.is_alive() and self.write_error is None:
            self.write_error = "microphone writer did not stop cleanly"
        try:
            if self._file is not None:
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

    def detach(self, timeline_start: float = 0.0) -> SpoolInfo:
        """Close the stream and hand over the spool file — nothing is read into
        memory and nothing is deleted here."""
        self._close()
        return SpoolInfo(
            path=self._path, rate=self.rate, channels=self.channels,
            start_offset_secs=max(0.0, self.started_at - timeline_start),
        )


class _SystemAudioTap:
    """System-audio capture via the earshot-audiotap helper process. Exposes
    the same surface as a capture stream (level / write_error / detach /
    discard) so DualStreamRecorder treats both channels alike."""

    def __init__(self, spool_path: Optional[Path], log_dir: Optional[Path] = None):
        self.channels = TAP_CHANNELS
        self.rate = TAP_RATE
        self.level = 0.0
        self.started_at = 0.0
        self.write_error: Optional[str] = None
        self.permission_warning: Optional[str] = None
        if spool_path is not None:
            spool_path.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(spool_path)
        else:
            fd, self._path = tempfile.mkstemp(suffix=".raw", prefix="earshot_")
            os.close(fd)

        helper = _helper_path()
        if not os.path.exists(helper):
            try:
                os.unlink(self._path)
            except OSError:
                pass
            raise RuntimeError(
                "The system audio component (earshot-audiotap) is missing. "
                "Reinstall Earshot to restore it."
            )
        log_path = (Path(log_dir) / "audiotap.log") if log_dir else \
            Path(tempfile.gettempdir()) / "earshot_audiotap.log"
        self._log = open(log_path, "ab")
        self._stopping = False
        self._started = False
        self._error_msg: Optional[str] = None
        self._handshake = threading.Event()
        # Transient-failure tracking: helper level events carry cumulative
        # frames/failures counters, so a latched write_error from a one-off
        # hiccup (dropped buffers during a system stall, a single failed
        # write) can clear itself once capture has provably been healthy
        # again for _RECOVERY_SECS. Hard failures (helper death) never clear.
        self._io_latched = False
        self._seen_frames = 0
        self._seen_failures = 0
        self._latch_frames = 0
        self._latch_failures = 0
        try:
            self._proc = subprocess.Popen(
                [helper, "--out", self._path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._log,
                close_fds=True,
            )
        except Exception:
            self._log.close()
            try:
                os.unlink(self._path)
            except OSError:
                pass
            raise
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
            try:
                os.unlink(self._path)
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
                self._handle_event(ev)
        except (OSError, ValueError):
            pass
        rc = proc.wait()
        self.level = 0.0
        # EOF before we asked it to stop = the helper died mid-recording. Latch
        # it so the UI shows the recording-problem banner; the mic keeps going
        # and finalize pads "them" with silence. This latch is HARD: unlike a
        # transient io hiccup it never auto-clears.
        if not self._stopping:
            if not self._started:
                self._error_msg = (
                    f"System audio helper exited before capture started (code {rc}). "
                    "Reinstall Earshot if this continues."
                )
            elif self.write_error is None:
                self.write_error = f"OSError: system audio helper exited unexpectedly (code {rc})"
                self._io_latched = False
            self._handshake.set()

    def _handle_event(self, ev: dict) -> None:
        """One helper stdout event. Split out of the reader loop so the
        transient-failure recovery logic is directly testable."""
        kind = ev.get("event")
        if kind == "start":
            try:
                self.rate = int(ev.get("rate") or TAP_RATE)
                self.channels = int(ev.get("channels") or TAP_CHANNELS)
            except (TypeError, ValueError):
                pass
            received_at = time.monotonic()
            try:
                helper_uptime = float(ev.get("started_uptime"))
            except (TypeError, ValueError):
                helper_uptime = received_at
            # Python's monotonic clock and ProcessInfo.systemUptime are
            # both based on the Mac's boot-time monotonic clock. Keep a
            # defensive range check for mocked/changed helper protocols.
            self.started_at = (helper_uptime if abs(helper_uptime - received_at) < 5.0
                               else received_at)
            self._started = True
            self._handshake.set()
        elif kind == "level":
            try:
                raw_level = float(ev.get("rms") or 0.0)
                self.level = min(1.0, raw_level * 3.0)
                if raw_level > 0.001:
                    self.permission_warning = None
            except (TypeError, ValueError):
                pass
            try:
                self._seen_frames = int(ev.get("frames"))
                self._seen_failures = int(ev.get("failures"))
            except (TypeError, ValueError):
                return  # old helper without counters: latched errors stay latched
            if self._io_latched:
                if self._seen_failures > self._latch_failures:
                    # still failing: restart the clean-capture clock
                    self._latch_failures = self._seen_failures
                    self._latch_frames = self._seen_frames
                elif self._seen_frames >= self._latch_frames + self.rate * _RECOVERY_SECS:
                    # capture has been provably healthy since the hiccup:
                    # clear the warning instead of crying wolf for the rest
                    # of the meeting (a moment of audio was lost, not the
                    # recording).
                    self.write_error = None
                    self._io_latched = False
        elif kind == "error":
            code = ev.get("code")
            message = str(ev.get("message") or "system audio capture failed")
            if code == "permission":
                message = _PERMISSION_HINT
            if self._handshake.is_set():
                if self.write_error is None:
                    self.write_error = f"OSError: {message}"
                    # io/overflow hiccups may recover (see the level handler);
                    # anything else stays latched for good.
                    self._io_latched = code in ("io", "overflow")
                    self._latch_frames = self._seen_frames
                    self._latch_failures = self._seen_failures
            else:
                self._error_msg = message
            self._handshake.set()
        elif kind == "warning" and ev.get("code") == "permission_silence":
            self.permission_warning = (
                "System audio is completely silent. Earshot may be missing permission under "
                "System Settings > Privacy & Security > Screen & System Audio Recording."
            )

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

    def detach(self, timeline_start: float = 0.0) -> SpoolInfo:
        """Stop the helper and hand over the spool file."""
        self._close()
        return SpoolInfo(
            path=self._path, rate=self.rate, channels=self.channels,
            start_offset_secs=max(0.0, self.started_at - timeline_start),
        )


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
        # Explicitly resolve the microphone TCC prompt before the system tap
        # starts producing frames. This is intentionally repeated here even
        # though the UI preflights it, so non-UI callers get the same guarantee.
        ensure_capture_permissions(self.mic_index)
        me_path = (self.spool_dir / "spool_me.raw") if self.spool_dir else None
        them_path = (self.spool_dir / "spool_them.raw") if self.spool_dir else None
        try:
            # The microphone permission is already settled, so starting the tap
            # first cannot create a prompt-sized channel offset.
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
        self._t0 = min(self._mic.started_at, self._loop.started_at)
        if self.spool_dir is not None:
            # sidecar lets a crashed recording be salvaged on the next launch
            self._sidecar = str(self.spool_dir / SIDECAR)
            try:
                Path(self._sidecar).write_text(json.dumps({
                    "me": {"path": self._mic.path, "rate": self.mic_rate,
                           "channels": self._mic.channels,
                           "start_offset_secs": max(0.0, self._mic.started_at - self._t0)},
                    "them": {"path": self._loop.path, "rate": self._loop.rate,
                             "channels": self._loop.channels,
                             "start_offset_secs": max(0.0, self._loop.started_at - self._t0)},
                    "started_at": time.time(),
                }, indent=2), encoding="utf-8")
            except OSError:
                self._sidecar = None
        self._running = True

    @property
    def running(self) -> bool:
        return self._running

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._t0 if self._running else 0.0

    @property
    def started_at(self) -> float:
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

    @property
    def capture_warning(self) -> Optional[str]:
        return self._loop.permission_warning if self._loop else None

    def stop(self) -> RecordingSpool:
        if not self._running:
            raise RuntimeError("recorder not running")
        duration = self.elapsed
        self._running = False
        me = self._mic.detach(self._t0) if self._mic else SpoolInfo("", 48000, 1)
        them = self._loop.detach(self._t0) if self._loop else SpoolInfo("", 48000, 1)
        return RecordingSpool(me=me, them=them, duration_secs=duration,
                              sidecar_path=self._sidecar)
