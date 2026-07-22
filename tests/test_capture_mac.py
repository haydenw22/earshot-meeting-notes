"""macOS capture regressions without touching real microphones or TCC state.

Covers permission preflight parsing, callback-to-worker spooling, helper EOF
startup failure, shared-timeline offsets, and conservative call detection.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meeting_notes.audio import _capture_mac as capture  # noqa: E402
from meeting_notes.capture import screen  # noqa: E402
from meeting_notes.util import mic_usage  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


class _Completed:
    def __init__(self, code=0, stdout=""):
        self.returncode = code
        self.stdout = stdout
        self.stderr = ""


def test_permission_preflight() -> None:
    print("== microphone permission preflight ==")
    helper = Path(tempfile.mkdtemp()) / "helper"
    helper.touch()
    old_path, old_run = capture._helper_path, capture.subprocess.run
    had_frozen = hasattr(capture.sys, "frozen")
    old_frozen = getattr(capture.sys, "frozen", None)
    try:
        capture.sys.frozen = True
        capture._helper_path = lambda: str(helper)
        capture.subprocess.run = lambda *a, **k: _Completed(
            stdout=json.dumps({"event": "permission", "granted": True}) + "\n")
        capture.ensure_capture_permissions()
        check("granted permission accepted", True)
        capture.subprocess.run = lambda *a, **k: _Completed(
            2, json.dumps({"event": "permission", "granted": False}) + "\n")
        try:
            capture.ensure_capture_permissions()
            check("denied permission rejected", False)
        except RuntimeError as exc:
            check("denial gives actionable Microphone path", "Microphone" in str(exc))
    finally:
        capture._helper_path, capture.subprocess.run = old_path, old_run
        if had_frozen:
            capture.sys.frozen = old_frozen
        else:
            delattr(capture.sys, "frozen")


def test_mic_callback_queue() -> None:
    print("== microphone callback only queues; worker writes ==")

    class FakeRawInputStream:
        latest = None

        def __init__(self, **kwargs):
            FakeRawInputStream.latest = self
            self.kwargs = kwargs

        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

    previous = sys.modules.get("sounddevice")
    sys.modules["sounddevice"] = types.SimpleNamespace(RawInputStream=FakeRawInputStream)
    path = Path(tempfile.mkdtemp()) / "mic.raw"
    try:
        stream = capture._MicStream(1, 1, 48000, path)
        fake = FakeRawInputStream.latest
        check("uses adaptive blocksize=0", fake.kwargs["blocksize"] == 0)
        payload = (b"\x01\x00\xff\x7f") * 512
        stream._cb(memoryview(payload), 1024, None, False)
        deadline = time.monotonic() + 2
        while path.stat().st_size < len(payload) and time.monotonic() < deadline:
            time.sleep(0.01)
        info = stream.detach(stream.started_at)
        check("worker persisted exact callback bytes", path.read_bytes() == payload)
        check("detach records stream offset", info.start_offset_secs == 0.0)
    finally:
        if previous is None:
            sys.modules.pop("sounddevice", None)
        else:
            sys.modules["sounddevice"] = previous


class _FakeInput:
    def close(self):
        return None


class _FakeProcess:
    def __init__(self, lines, rc):
        self.stdout = iter(lines)
        self.stdin = _FakeInput()
        self._rc = rc

    def wait(self, timeout=None):
        return self._rc

    def terminate(self):
        return None

    def kill(self):
        return None


def test_helper_startup_eof() -> None:
    print("== helper EOF before start is fatal ==")
    root = Path(tempfile.mkdtemp())
    helper = root / "helper"
    helper.touch()
    spool = root / "them.raw"
    old_path, old_popen = capture._helper_path, capture.subprocess.Popen
    try:
        capture._helper_path = lambda: str(helper)
        capture.subprocess.Popen = lambda *a, **k: _FakeProcess([], 7)
        try:
            capture._SystemAudioTap(spool, root)
            check("pre-start EOF rejected", False)
        except RuntimeError as exc:
            check("pre-start EOF rejected with helper error", "before capture started" in str(exc))
        check("failed startup spool removed", not spool.exists())
    finally:
        capture._helper_path, capture.subprocess.Popen = old_path, old_popen


def test_shared_timeline() -> None:
    print("== recorder measures and persists per-stream offsets ==")
    events = []

    class FakeTap:
        def __init__(self, *a, **k):
            events.append("tap")
            self.path, self.rate, self.channels = "them.raw", 48000, 2
            self.started_at, self.level = 10.0, 0.0
            self.write_error = self.permission_warning = None

        def discard(self):
            return None

        def detach(self, origin):
            return capture.SpoolInfo(self.path, self.rate, self.channels,
                                     self.started_at - origin)

    class FakeMic(FakeTap):
        def __init__(self, *a, **k):
            events.append("mic")
            self.path, self.rate, self.channels = "me.raw", 48000, 1
            self.started_at, self.level = 10.25, 0.0
            self.write_error = None

    old_permission = capture.ensure_capture_permissions
    old_tap, old_mic = capture._SystemAudioTap, capture._MicStream
    try:
        capture.ensure_capture_permissions = lambda *_args: events.append("permission")
        capture._SystemAudioTap, capture._MicStream = FakeTap, FakeMic
        recorder = capture.DualStreamRecorder(1, 1, 48000, -1, 2, 48000)
        recorder.start()
        check("permission resolves before either stream", events == ["permission", "tap", "mic"])
        check("earliest stream defines shared origin", recorder.started_at == 10.0)
        spool = recorder.stop()
        check("later mic carries front-pad offset", spool.me.start_offset_secs == 0.25)
        check("earlier system stream has zero offset", spool.them.start_offset_secs == 0.0)
    finally:
        capture.ensure_capture_permissions = old_permission
        capture._SystemAudioTap, capture._MicStream = old_tap, old_mic


def _bare_tap():
    """A _SystemAudioTap with just the state _handle_event needs (no process)."""
    import threading

    tap = object.__new__(capture._SystemAudioTap)
    tap.rate, tap.channels = capture.TAP_RATE, capture.TAP_CHANNELS
    tap.level, tap.started_at = 0.0, 0.0
    tap.write_error = tap.permission_warning = None
    tap._started, tap._error_msg = False, None
    tap._handshake = threading.Event()
    tap._io_latched = False
    tap._seen_frames = tap._seen_failures = 0
    tap._latch_frames = tap._latch_failures = 0
    return tap


def test_tap_transient_error_recovery() -> None:
    print("== transient tap hiccups clear after sustained clean capture ==")
    rate = capture.TAP_RATE
    clean = rate * capture._RECOVERY_SECS

    tap = _bare_tap()
    tap._handle_event({"event": "start", "rate": rate, "channels": 2})
    tap._handle_event({"event": "level", "rms": 0.1, "frames": rate, "failures": 0})
    tap._handle_event({"event": "error", "code": "overflow",
                       "message": "system audio callback dropped 3 buffers"})
    check("overflow latches the write error", tap.write_error is not None)
    tap._handle_event({"event": "level", "rms": 0.1, "frames": rate * 2, "failures": 3})
    check("still latched shortly after the hiccup", tap.write_error is not None)
    tap._handle_event({"event": "level", "rms": 0.1,
                       "frames": rate * 2 + clean, "failures": 3})
    check("clears after sustained clean capture", tap.write_error is None)

    # an ongoing problem keeps the latch: failures keep climbing
    tap._handle_event({"event": "error", "code": "io", "message": "spool write failed"})
    tap._handle_event({"event": "level", "rms": 0.1,
                       "frames": rate * 3 + clean, "failures": 4})
    tap._handle_event({"event": "level", "rms": 0.1,
                       "frames": rate * 3 + clean * 2, "failures": 9})
    check("still latched while failures keep growing", tap.write_error is not None)

    # non-transient error codes never auto-clear
    tap2 = _bare_tap()
    tap2._handle_event({"event": "start", "rate": rate, "channels": 2})
    tap2._handle_event({"event": "error", "code": "tap_failed", "message": "boom"})
    tap2._handle_event({"event": "level", "rms": 0.1,
                        "frames": clean * 5, "failures": 0})
    check("hard errors never auto-clear", tap2.write_error is not None)

    # old helper without counters: latched errors stay latched (no false clear)
    tap3 = _bare_tap()
    tap3._handle_event({"event": "start", "rate": rate, "channels": 2})
    tap3._handle_event({"event": "error", "code": "io", "message": "spool write failed"})
    tap3._handle_event({"event": "level", "rms": 0.1})
    check("counter-less level events never clear a latch", tap3.write_error is not None)


def test_mac_call_detection_is_conservative() -> None:
    print("== macOS call detection excludes browsers ==")
    old_busy, old_run = mic_usage._mac_mic_in_use, mic_usage.subprocess.run
    try:
        mic_usage._mac_mic_in_use = lambda: True
        mic_usage.subprocess.run = lambda *a, **k: types.SimpleNamespace(
            stdout="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome\n"
                   "/Applications/zoom.us.app/Contents/MacOS/zoom.us\n")
        result = mic_usage._mac_apps_classified()
        check("dedicated meeting app remains detectable", ("Zoom", "meeting") in result)
        check("browser is excluded from approximate attribution",
              not any(category == "browser" for _name, category in result))
    finally:
        mic_usage._mac_mic_in_use, mic_usage.subprocess.run = old_busy, old_run


def test_screen_capture_failure_is_visible() -> None:
    print("== screen capture worker latches failures ==")

    class BrokenMSS:
        def __enter__(self):
            raise PermissionError("screen recording denied")

        def __exit__(self, *args):
            return False

    previous = sys.modules.get("mss")
    sys.modules["mss"] = types.SimpleNamespace(MSS=BrokenMSS)
    try:
        recorder = screen.ScreenRecorder(
            Path(tempfile.mkdtemp()) / "screens", start_monotonic=time.monotonic())
        recorder._run()
        check("worker exposes the permission failure",
              recorder.error is not None and "denied" in recorder.error)
    finally:
        if previous is None:
            sys.modules.pop("mss", None)
        else:
            sys.modules["mss"] = previous


def main() -> int:
    test_permission_preflight()
    test_mic_callback_queue()
    test_helper_startup_eof()
    test_shared_timeline()
    test_tap_transient_error_recovery()
    test_mac_call_detection_is_conservative()
    test_screen_capture_failure_is_visible()
    print("\nMAC CAPTURE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
