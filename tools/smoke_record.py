"""Quick hardware smoke test for the dual-stream recorder (no GUI).

Records N seconds from the default mic + default loopback, runs the offline
echo-cancellation path, and prints level stats + the written file. Use this to
confirm capture works on a given machine.

Usage:  python tools/smoke_record.py [seconds]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meeting_notes.audio import aec, calibrate, devices, writer  # noqa: E402
from meeting_notes.audio.capture import DualStreamRecorder  # noqa: E402


def _rms_db(x: np.ndarray) -> float:
    if x.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    return 20.0 * np.log10(rms + 1e-9)


def main() -> int:
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
    mic = devices.default_input()
    loop = devices.default_loopback()
    if not mic or not loop:
        print("No mic or loopback device available.")
        return 1
    print(f"Mic:      [{mic.index}] {mic.name} ({mic.channels}ch @ {mic.default_samplerate})")
    print(f"Loopback: [{loop.index}] {loop.name} ({loop.channels}ch @ {loop.default_samplerate})")

    rec = DualStreamRecorder(
        mic_index=mic.index, mic_channels=mic.channels, mic_rate=mic.default_samplerate,
        loop_index=loop.index, loop_channels=loop.channels, loop_rate=loop.default_samplerate,
    )
    print(f"Recording {secs:.0f}s... (play some audio to exercise the loopback)")
    rec.start()
    t_end = time.monotonic() + secs
    while time.monotonic() < t_end:
        time.sleep(0.2)
        print(f"  you={rec.mic_level:4.2f}  them={rec.them_level:4.2f}", end="\r")
    spool = rec.stop()
    print()

    out_dir = Path(__file__).resolve().parent.parent / "recordings" / "_smoke"
    paths = writer.finalize_recording(spool, out_dir)  # streaming, crash-safe path
    print(f"Captured: {paths['frames']} frames @ {writer.TARGET_RATE} Hz "
          f"({spool.duration_secs:.1f}s)")
    print(f"Wrote:    {paths['meeting']}")

    import soundfile as sf
    me_48k, _ = sf.read(paths["me"], dtype="float32")
    them_48k, _ = sf.read(paths["them"], dtype="float32")
    print(f"Levels:   me={_rms_db(me_48k):6.1f} dBFS   them={_rms_db(them_48k):6.1f} dBFS")

    print(f"AEC available: {aec.is_available()}")
    if aec.is_available():
        delay = calibrate.estimate_delay_ms(me_48k, them_48k)
        cleaned = aec.cancel_echo(me_48k, them_48k, delay_ms=delay)
        print(f"AEC ran OK: delay~{delay:.0f}ms, cleaned {len(cleaned)} samples, "
              f"me_clean={_rms_db(cleaned):.1f} dBFS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
