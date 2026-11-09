"""A headset that reconnects between meetings must not break the next recording.

PortAudio snapshots the system's devices when it initialises and keeps each
device's Core Audio object ID. AirPods going back into the case and coming out
again reappear under a NEW object ID, so before 0.37.0 the second meeting in a
session failed with "Error opening RawInputStream: Internal PortAudio error
[PaErrorCode -9986]" until Earshot was quit and reopened. Covers:
  - the macOS device listing re-reads the system (and never while a mic stream
    is open, since re-initialising PortAudio would close it mid-recording);
  - the record page re-binds the picked devices by name right before opening
    them, and explains a device that has gone away instead of failing.

Run:  QT_QPA_PLATFORM=cocoa python tests/test_device_reconnect.py   (macOS)
      QT_QPA_PLATFORM=offscreen python tests/test_device_reconnect.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from meeting_notes.audio.devices import AudioDevice  # noqa: E402  (before the backend: import order)
from meeting_notes.audio import _capture_mac as capture  # noqa: E402
from meeting_notes.audio import _devices_mac as devices_mac  # noqa: E402
from meeting_notes.config import Config  # noqa: E402
from meeting_notes.storage import db as dbmod  # noqa: E402
from meeting_notes.storage.repository import MeetingRepository  # noqa: E402
from meeting_notes.ui import page_record  # noqa: E402
from meeting_notes.ui.theme_controller import ThemeController  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


class FakePortAudio:
    """sounddevice stand-in with PortAudio's snapshot semantics: devices are
    read at _initialize(); opening a snapshot entry whose Core Audio identity
    is no longer live fails exactly like the real -9986 error."""

    def __init__(self):
        self._next_id = 100
        self.live = [self._dev("MacBook Pro Microphone"), self._dev("AirPods Pro")]
        self.snapshot = list(self.live)
        self.terminations = 0
        self.default = types.SimpleNamespace(device=[0, 0])
        pa = self

        class RawInputStream:
            def __init__(self, device=None, **_kwargs):
                entry = pa.snapshot[device]
                if entry["id"] not in {d["id"] for d in pa.live}:
                    raise RuntimeError("Error opening RawInputStream: Internal PortAudio "
                                       "error [PaErrorCode -9986]")
                self.device = device

            def start(self):
                return None

            def stop(self):
                return None

            def close(self):
                return None

        self.RawInputStream = RawInputStream

    def _dev(self, name):
        self._next_id += 1
        return {"name": name, "id": self._next_id, "max_input_channels": 1,
                "default_samplerate": 48000}

    def reconnect(self, name):
        """The headset leaves and comes back: same name, new identity, and an
        iPhone Continuity mic appears in front of it (indices shift too)."""
        self.live = [d for d in self.live if d["name"] != name]
        self.live.insert(0, self._dev("Haydens iphone Microphone"))
        self.live.append(self._dev(name))

    def _terminate(self):
        self.terminations += 1

    def _initialize(self):
        self.snapshot = list(self.live)

    def query_devices(self):
        return [dict(d) for d in self.snapshot]


def test_listing_rereads_devices_after_reconnect() -> None:
    print("== macOS device listing survives a headset reconnect ==")
    fake = FakePortAudio()
    previous = sys.modules.get("sounddevice")
    sys.modules["sounddevice"] = fake
    tmp = Path(tempfile.mkdtemp())
    try:
        first =next(d for d in devices_mac.list_input_devices() if d.name == "AirPods Pro")
        stream = capture._MicStream(first.index, 1, 48000, tmp / "one.raw")
        check("first meeting opens the AirPods", True)

        terminations = fake.terminations
        devices_mac.list_input_devices()
        check("no PortAudio re-init while a mic stream is open",
              fake.terminations == terminations)
        stream.detach()

        fake.reconnect("AirPods Pro")
        second = next(d for d in devices_mac.list_input_devices() if d.name == "AirPods Pro")
        check("relisting finds the reconnected headset at its new index", second.index == 2)
        try:
            capture._MicStream(second.index, 1, 48000, tmp / "two.raw").detach()
            opened = True
        except RuntimeError as exc:
            print("      ", exc)
            opened = False
        check("second meeting opens the reconnected AirPods", opened)
    finally:
        if previous is None:
            sys.modules.pop("sounddevice", None)
        else:
            sys.modules["sounddevice"] = previous


class _Shell:
    def __init__(self):
        self.recording = None

    def set_recording(self, active):
        self.recording = active

    def notify_data_changed(self):
        return None

    def open_meeting(self, _mid):
        return None


class _FakeRecorder:
    created: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.running = False
        self.started_at = 0.0
        _FakeRecorder.created.append(self)

    def start(self):
        self.running = True


class _Boxes:
    def __init__(self):
        self.shown = []

    def warning(self, _parent, title, text):
        self.shown.append(("warning", title, text))

    def critical(self, _parent, title, text):
        self.shown.append(("critical", title, text))


def test_record_page_rebinds_by_name() -> None:
    print("== record page re-binds the picked devices right before opening ==")
    app = QApplication.instance() or QApplication([])  # noqa: F841
    cfg = Config()
    cfg.overlay_enabled = False
    cfg.capture_screen = False
    repo = MeetingRepository(dbmod.connect(Path(tempfile.mkdtemp()) / "r.db"))
    theme = ThemeController(cfg)
    theme.apply()
    page = page_record.RecordPage(_Shell(), repo, cfg, theme)
    page.capture_screen.setChecked(False)

    stale_mic = AudioDevice(1, "AirPods Pro", 1, 24000, False)
    them = AudioDevice(-1, "System audio", 2, 48000, True, True)
    live = [AudioDevice(0, "Haydens iphone Microphone", 1, 48000, False),
            AudioDevice(1, "MacBook Pro Microphone", 1, 48000, False, True),
            AudioDevice(2, "AirPods Pro", 1, 24000, False)]
    saved = (page_record.dev.list_input_devices, page_record.dev.list_loopback_devices,
             page_record.DualStreamRecorder, page_record.ensure_capture_permissions,
             page_record.QMessageBox)
    boxes = _Boxes()
    page_record.dev.list_input_devices = lambda: list(live)
    page_record.dev.list_loopback_devices = lambda: [them]
    page_record.DualStreamRecorder = _FakeRecorder
    page_record.ensure_capture_permissions = lambda *_a: None
    page_record.QMessageBox = boxes
    try:
        page.mic_combo.clear()
        page.mic_combo.addItem(stale_mic.name, stale_mic)
        page.them_combo.clear()
        page.them_combo.addItem(them.name, them)
        page._start()
        check("a recording started", bool(_FakeRecorder.created) and page.recorder is not None)
        check("it opened the AirPods at their CURRENT index (2), not the stale one (1)",
              _FakeRecorder.created[-1].kwargs.get("mic_index") == 2)
        check("no error box", boxes.shown == [])
        page.poll.stop()
        page.recorder = None
        page.meeting_id = None

        _FakeRecorder.created.clear()
        meetings_before = len(repo.list())
        live[:] = [d for d in live if d.name != "AirPods Pro"]  # back in the case
        page.mic_combo.clear()
        page.mic_combo.addItem(stale_mic.name, stale_mic)
        page._start()
        check("a gone headset does not start a recording", not _FakeRecorder.created)
        check("the user is told which device is missing",
              any("AirPods Pro" in text for _k, _t, text in boxes.shown))
        check("no orphan meeting row is created", len(repo.list()) == meetings_before)
    finally:
        (page_record.dev.list_input_devices, page_record.dev.list_loopback_devices,
         page_record.DualStreamRecorder, page_record.ensure_capture_permissions,
         page_record.QMessageBox) = saved
        page.poll.stop()


def main() -> int:
    test_listing_rereads_devices_after_reconnect()
    test_record_page_rebinds_by_name()
    print("\nDEVICE RECONNECT TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
