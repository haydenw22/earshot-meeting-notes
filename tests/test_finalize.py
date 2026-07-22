"""Tests for the crash-safe streaming audio finalisation (the P0 fix).

Covers: streaming raw→WAV correctness (incl. resampling), channel padding,
the deletion contract (spools survive any failure; deleted only on success),
crash salvage from the sidecar, and the 5.1 downmix fix.

Run:  python tests/test_finalize.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from meeting_notes.audio import writer  # noqa: E402
from meeting_notes.audio.writer import RecordingSpool, SpoolInfo  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


def _make_raw(path: Path, seconds: float, rate: int, channels: int, freq: float = 440.0) -> np.ndarray:
    """Write an interleaved int16 sine spool; return the float32 mono reference."""
    n = int(seconds * rate)
    t = np.arange(n, dtype=np.float32) / rate
    mono = 0.5 * np.sin(2 * np.pi * freq * t).astype(np.float32)
    data = np.repeat(mono[:, None], channels, axis=1) if channels > 1 else mono[:, None]
    i16 = np.clip(data * 32768.0, -32768, 32767).astype(np.int16)
    path.write_bytes(i16.tobytes())
    return mono


def main() -> int:
    print("== streaming finalise: correctness ==")
    tmp = Path(tempfile.mkdtemp())
    me_ref = _make_raw(tmp / "me.raw", 3.0, 44100, 2)     # stereo 44.1k → resampled
    _make_raw(tmp / "them.raw", 2.0, 48000, 1, freq=880)  # mono 48k, shorter
    spool = RecordingSpool(
        me=SpoolInfo(str(tmp / "me.raw"), 44100, 2),
        them=SpoolInfo(str(tmp / "them.raw"), 48000, 1),
        duration_secs=3.0,
    )
    out = tmp / "meeting_000001"
    res = writer.finalize_recording(spool, out)
    me, sr = sf.read(res["me"], dtype="float32")
    them, _ = sf.read(res["them"], dtype="float32")
    mix = sf.info(res["meeting"])
    check("me resampled to 48k", sr == 48000)
    expected = int(3.0 * 48000)
    check("me length ≈ 3s @48k", abs(len(me) - expected) < 4800)
    check("channels padded to equal length", len(me) == len(them) == res["frames"])
    check("meeting.wav is stereo, same length", mix.channels == 2 and mix.frames == res["frames"])
    check("them tail is padded silence", float(np.abs(them[-24000:]).max()) < 1e-4)
    # content sanity: the resampled sine matches a whole-file reference resample
    ref = writer.resample(me_ref, 44100, 48000)
    n = min(len(ref), len(me)) - 4800
    err = float(np.max(np.abs(ref[4800:n] - me[4800:n])))
    check(f"streamed resample matches reference (max err {err:.4f})", err < 0.02)
    check("spools deleted after success", not (tmp / "me.raw").exists() and not (tmp / "them.raw").exists())

    print("== measured stream offsets are padded at the front ==")
    _make_raw(tmp / "me_offset.raw", 0.5, 48000, 1)
    _make_raw(tmp / "them_offset.raw", 0.5, 48000, 1, freq=880)
    offset_res = writer.finalize_recording(RecordingSpool(
        me=SpoolInfo(str(tmp / "me_offset.raw"), 48000, 1, start_offset_secs=0.25),
        them=SpoolInfo(str(tmp / "them_offset.raw"), 48000, 1),
        duration_secs=0.75,
    ), tmp / "meeting_offset")
    offset_me, _ = sf.read(offset_res["me"], dtype="float32")
    check("later mic begins with measured silence",
          float(np.abs(offset_me[:11000]).max()) < 1e-4)
    check("mic signal begins after the front pad",
          float(np.abs(offset_me[12500:18000]).max()) > 0.1)

    print("== deletion contract: failure preserves the spools ==")
    _make_raw(tmp / "me2.raw", 1.0, 48000, 1)
    _make_raw(tmp / "them2.raw", 1.0, 48000, 1)
    spool2 = RecordingSpool(
        me=SpoolInfo(str(tmp / "me2.raw"), 48000, 1),
        them=SpoolInfo(str(tmp / "them2.raw"), 48000, 1),
        duration_secs=1.0,
    )
    orig = writer._write_meeting_stereo

    def boom(*a, **k):
        raise IOError("simulated crash mid-finalise")

    writer._write_meeting_stereo = boom
    try:
        failed = False
        try:
            writer.finalize_recording(spool2, tmp / "meeting_000002")
        except IOError:
            failed = True
        check("finalise raised on simulated crash", failed)
        check("spools SURVIVE the failure", (tmp / "me2.raw").exists() and (tmp / "them2.raw").exists())
        check("no half-written canonical wav", not (tmp / "meeting_000002" / writer.RAW_ME).exists()
              or sf.info(str(tmp / "meeting_000002" / writer.RAW_ME)).frames > 0)
    finally:
        writer._write_meeting_stereo = orig

    print("== crash salvage from sidecar ==")
    folder = tmp / "meeting_000003"
    folder.mkdir()
    _make_raw(folder / "spool_me.raw", 1.5, 48000, 1)
    _make_raw(folder / "spool_them.raw", 1.5, 48000, 2)
    (folder / writer.SIDECAR).write_text(json.dumps({
        "me": {"path": str(folder / "spool_me.raw"), "rate": 48000, "channels": 1},
        "them": {"path": str(folder / "spool_them.raw"), "rate": 48000, "channels": 2},
    }), encoding="utf-8")
    got = writer.salvage_spool(folder)
    check("salvage produced wavs", got is not None and Path(got["meeting"]).exists())
    check("salvage duration ≈ 1.5s", abs(got["duration_secs"] - 1.5) < 0.1)
    check("sidecar + spools cleaned up", not (folder / writer.SIDECAR).exists()
          and not (folder / "spool_me.raw").exists())
    check("salvage on clean folder → None", writer.salvage_spool(tmp / "meeting_000001") is None)

    print("== misc ==")
    # 5.1 downmix must not attenuate speech living in FL/FR
    six = np.zeros((1000, 6), dtype=np.float32)
    six[:, 0] = six[:, 1] = 0.5
    check("5.1 downmix keeps FL/FR level", abs(float(writer.to_mono(six).max()) - 0.5) < 1e-6)
    # zero-length spool doesn't explode; other channel padded
    empty = tmp / "empty.raw"
    empty.write_bytes(b"")
    _make_raw(tmp / "them3.raw", 0.5, 48000, 1)
    res3 = writer.finalize_recording(RecordingSpool(
        me=SpoolInfo(str(empty), 48000, 1),
        them=SpoolInfo(str(tmp / "them3.raw"), 48000, 1), duration_secs=0.5,
    ), tmp / "meeting_000004")
    check("empty channel padded to match", res3["frames"] == sf.info(res3["me"]).frames > 0)

    print("\nFINALIZE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
