"""Periodic screen capture during a recording.

A background thread grabs the chosen monitor every couple of seconds and saves a
JPEG only when the screen has meaningfully changed (plus an occasional heartbeat),
so an hour of meeting yields dozens of frames rather than thousands. Frames are
named by elapsed-milliseconds relative to the recording start, so they line up
with transcript timestamps.

Manual on/off only — there is no reliable Windows signal for "someone is sharing
their screen".
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np


def list_monitors() -> list[str]:
    """Human labels for the available monitors (index 1 = primary)."""
    try:
        import mss

        with mss.MSS() as sct:
            out = []
            for i, m in enumerate(sct.monitors):
                if i == 0:
                    continue  # the virtual "all monitors" entry
                out.append(f"Monitor {i} — {m['width']}×{m['height']}")
            return out or ["Primary monitor"]
    except Exception:
        return ["Primary monitor"]


class ScreenRecorder:
    """Captures the screen to `out_dir` on a background thread until stop()."""

    def __init__(
        self,
        out_dir: Path,
        *,
        start_monotonic: float,
        monitor: int = 1,
        interval: float = 2.0,
        change_threshold: float = 4.0,   # mean abs diff of a downscaled grey frame
        heartbeat_secs: float = 60.0,    # save at least this often even if static
        max_edge: int = 1600,            # downscale long edge of saved JPEGs
    ):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._start = start_monotonic
        self.monitor = monitor
        self.interval = interval
        self.change_threshold = change_threshold
        self.heartbeat_secs = heartbeat_secs
        self.max_edge = max_edge
        self.count = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="ScreenRecorder", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        try:
            import mss
            from PIL import Image
        except Exception:
            return
        with mss.MSS() as sct:
            mons = sct.monitors
            mon = mons[self.monitor] if 0 < self.monitor < len(mons) else mons[-1]
            last_small: np.ndarray | None = None
            last_saved = -1e9
            while not self._stop.is_set():
                try:
                    grab = sct.grab(mon)
                    img = Image.frombytes("RGB", grab.size, grab.rgb)
                    small = np.asarray(img.resize((160, 90)).convert("L"), dtype=np.int16)
                    now = time.monotonic() - self._start
                    changed = last_small is None or float(np.mean(np.abs(small - last_small))) > self.change_threshold
                    if changed or (now - last_saved) >= self.heartbeat_secs:
                        save = img.copy()
                        save.thumbnail((self.max_edge, self.max_edge))
                        save.save(self.out_dir / f"{int(max(0, now) * 1000):09d}.jpg", "JPEG", quality=80)
                        last_small = small
                        last_saved = now
                        self.count += 1
                except Exception:
                    pass
                self._stop.wait(self.interval)


def list_screenshots(meeting_dir: Path) -> list[tuple[int, Path]]:
    """(elapsed_ms, path) for a meeting's screenshots, sorted by time."""
    folder = Path(meeting_dir) / "screenshots"
    if not folder.is_dir():
        return []
    out = []
    for p in folder.glob("*.jpg"):
        try:
            out.append((int(p.stem), p))
        except ValueError:
            continue
    out.sort(key=lambda t: t[0])
    return out
