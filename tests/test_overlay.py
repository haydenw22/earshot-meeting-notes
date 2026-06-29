"""Tests for the always-on-top recording overlay: multi-monitor placement,
opacity clamping, timer/level updates, config round-trip, and record-page wiring.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_overlay.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from meeting_notes.config import OVERLAY_AUTO_POS, Config  # noqa: E402
from meeting_notes.storage import db as dbmod  # noqa: E402
from meeting_notes.storage.repository import MeetingRepository  # noqa: E402
from meeting_notes.ui.overlay import RecordingOverlay, _Light  # noqa: E402
from meeting_notes.ui.page_record import RecordPage  # noqa: E402
from meeting_notes.ui.theme_controller import ThemeController  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


PRIMARY = QRect(0, 0, 1920, 1080)
SIZE = (220, 56)


def main() -> int:
    print("== placement (pure logic, multi-monitor) ==")
    place = RecordingOverlay.place

    # QRect.right() == left + width - 1, so the right edge of a 1920-wide screen is 1919
    auto = place(OVERLAY_AUTO_POS, OVERLAY_AUTO_POS, SIZE, [PRIMARY], PRIMARY)
    check("auto-place → top-right of primary", auto == (1919 - 220 - 24, 24))

    on = place(800, 400, SIZE, [PRIMARY], PRIMARY)
    check("on-screen position kept", on == (800, 400))

    two = [PRIMARY, QRect(1920, 0, 1920, 1080)]
    second = place(2000, 100, SIZE, two, PRIMARY)
    check("position on a second monitor is kept", second == (2000, 100))

    gone = place(3000, 100, SIZE, [PRIMARY], PRIMARY)
    check("position on a disconnected monitor falls back to default", gone == (1675, 24))

    clamped = place(1850, 100, SIZE, [PRIMARY], PRIMARY)
    check("partially off-screen is pulled fully on-screen", clamped == (1700, 100))

    neg = place(-1900, 50, SIZE, [QRect(-1920, 0, 1920, 1080), PRIMARY], PRIMARY)
    check("left-of-primary monitor (negative x) supported", neg == (-1900, 50))

    print("== opacity clamp ==")
    c = RecordingOverlay._clamp_opacity
    check("normal opacity kept", c(0.95) == 0.95)
    check("over 1 clamps to 1", c(1.5) == 1.0)
    check("under floor clamps to 0.4", c(0.1) == 0.4)
    check("garbage → default 0.95", c("abc") == 0.95 and c(None) == 0.95)

    print("== _Light level clamp ==")
    app = QApplication.instance() or QApplication([])  # noqa: F841
    light = _Light(QColor(0, 255, 0))
    light.set_level(2.0)
    check("level clamps to 1.0", light.level == 1.0)
    light.set_level(-1.0)
    check("level clamps to 0.0", light.level == 0.0)

    print("== overlay widget ==")
    cfg = Config()
    ov = RecordingOverlay(cfg)
    ov.update_time(0)
    check("timer 00:00", ov.timer_label.text() == "00:00")
    ov.update_time(65)
    check("timer mm:ss", ov.timer_label.text() == "01:05")
    ov.update_time(3725)
    check("timer rolls to h:mm:ss past an hour", ov.timer_label.text() == "1:02:05")
    ov.update_levels(0.6, 0.0)
    check("mic light tracks mic level", abs(ov.mic_light.level - 0.6) < 1e-6)
    check("system light stays at 0 when silent", ov.sys_light.level == 0.0)
    ov.set_opacity(2.0)
    check("set_opacity clamps to 1.0", abs(ov.windowOpacity() - 1.0) < 1e-6)
    ov.set_opacity(0.1)
    check("set_opacity clamps to floor", abs(ov.windowOpacity() - 0.4) < 1e-6)

    print("== config round-trip ==")
    cfg2 = Config.load()
    cfg2.overlay_enabled = False
    cfg2.overlay_opacity = 0.7
    cfg2.overlay_pos_x = 123
    cfg2.overlay_pos_y = 456
    cfg2.save()
    reloaded = Config.load()
    check("overlay fields persist",
          reloaded.overlay_enabled is False and reloaded.overlay_opacity == 0.7
          and reloaded.overlay_pos_x == 123 and reloaded.overlay_pos_y == 456)

    print("== record-page wiring ==")
    theme = ThemeController(Config())
    theme.apply()
    repo = MeetingRepository(dbmod.connect(Path(tempfile.mkdtemp()) / "w.db"))
    cfg3 = Config()
    cfg3.overlay_enabled = True
    page = RecordPage(object(), repo, cfg3, theme)
    page._show_overlay()
    check("overlay created when enabled", isinstance(page.overlay, RecordingOverlay))
    page.overlay.update_levels(0.3, 0.9)  # simulate a poll tick
    page.overlay.update_time(42)
    check("overlay updates from a poll tick", page.overlay.timer_label.text() == "00:42")
    page._hide_overlay()
    check("overlay cleared on hide", page.overlay is None)
    cfg3.overlay_enabled = False
    page._show_overlay()
    check("no overlay when disabled", page.overlay is None)

    repo.close()
    print("\nOVERLAY TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
