"""Tests for the marathon-meetings batch: Opus uploads, streaming file→file
resample, streaming AEC equivalence with the array implementation, chunked
transcription with timestamp re-offsetting, and parallel channel transcription.

Run:  python tests/test_longmeeting.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from meeting_notes.audio import aec, calibrate, writer  # noqa: E402
from meeting_notes.config import Config  # noqa: E402
from meeting_notes.pipeline import processing  # noqa: E402
from meeting_notes.transcription import chunker  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    rng = np.random.default_rng(7)

    print("== opus / streaming resample ==")
    check("opus supported by bundled libsndfile", writer.opus_supported())
    check("codec→ext mapping", writer.transcription_ext("opus") == "ogg"
          and writer.transcription_ext("flac") == "flac"
          and writer.transcription_ext("nonsense") == "flac")
    # speech-like noise (a sine compresses unrealistically well)
    speech = (rng.standard_normal(48000 * 20).astype(np.float32) * 0.1
              * np.abs(np.sin(np.arange(48000 * 20) / 24000)))
    src48 = tmp / "src48.wav"
    sf.write(str(src48), speech, 48000, subtype="PCM_16")
    flac16 = writer.prepare_for_transcription_file(src48, tmp / "u.flac")
    opus16 = writer.prepare_for_transcription_file(src48, tmp / "u.ogg")
    fi, oi = sf.info(str(flac16)), sf.info(str(opus16))
    check("streamed 48k→16k flac", fi.samplerate == 16000 and abs(fi.frames - 16000 * 20) < 1600)
    check("streamed 48k→16k opus", oi.samplerate == 16000)
    check("opus much smaller than flac",
          opus16.stat().st_size < flac16.stat().st_size * 0.6)
    # streamed output ≈ whole-array output
    ref = writer.prepare_for_transcription(speech, tmp / "ref.flac")
    a, _ = sf.read(str(flac16), dtype="float32")
    b, _ = sf.read(str(ref), dtype="float32")
    n = min(len(a), len(b))
    check("streamed == array resample (max err {:.5f})".format(
        float(np.max(np.abs(a[:n] - b[:n])))), float(np.max(np.abs(a[:n] - b[:n]))) < 0.01)

    print("== streaming AEC ≡ array AEC ==")
    if not aec.is_available():
        print("  (livekit APM unavailable — skipping equivalence check)")
    else:
        secs = 8
        them = (rng.standard_normal(48000 * secs).astype(np.float32) * 0.2)
        echo = np.concatenate([np.zeros(4800, np.float32), them[:-4800] * 0.4])
        me = (rng.standard_normal(48000 * secs).astype(np.float32) * 0.05) + echo
        me_p, them_p = tmp / "aec_me.wav", tmp / "aec_them.wav"
        sf.write(str(me_p), me, 48000, subtype="PCM_16")
        sf.write(str(them_p), them, 48000, subtype="PCM_16")
        # array path (read the PCM_16 files back so both paths see identical input)
        me_r, _ = sf.read(str(me_p), dtype="float32")
        them_r, _ = sf.read(str(them_p), dtype="float32")
        ref_out = aec.cancel_echo(me_r, them_r, delay_ms=100)
        out_p = tmp / "aec_out.wav"
        aec.cancel_echo_files(me_p, them_p, out_p, delay_ms=100, block_frames=101)  # odd block size on purpose
        got, _ = sf.read(str(out_p), dtype="float32")
        n = min(len(got), len(ref_out))
        diff = float(np.max(np.abs(got[:n] - ref_out[:n])))
        check(f"file-streamed AEC matches array AEC (max diff {diff:.6f})", diff <= (2.0 / 32768.0))
        # and it actually cancelled: echo band should shrink vs raw mic
        raw_rms = float(np.sqrt((me_r[:n] ** 2).mean()))
        out_rms = float(np.sqrt((got[:n] ** 2).mean()))
        check(f"echo energy reduced (raw {raw_rms:.4f} → clean {out_rms:.4f})", out_rms < raw_rms)
        d = calibrate.estimate_delay_ms_files(me_p, them_p)
        check(f"file delay estimate ≈ 100ms (got {d:.0f})", 60 <= d <= 140)

    print("== chunked transcription ==")
    long = np.concatenate([
        rng.standard_normal(16000 * 40).astype(np.float32) * 0.2,
        np.zeros(16000 * 2, np.float32),                       # silence at ~40-42s
        rng.standard_normal(16000 * 40).astype(np.float32) * 0.2,
    ])
    lf = tmp / "long.flac"
    sf.write(str(lf), long, 16000, subtype="PCM_16")
    size = lf.stat().st_size
    calls: list[tuple[str, float]] = []

    def fake_one(p: Path) -> dict:
        info = sf.info(str(p))
        calls.append((str(p), info.frames / info.samplerate))
        i = len(calls)
        return {"text": f"part{i}", "segments": [{"start": 1.0, "end": 2.0, "text": f"part{i}"}]}

    out = chunker.transcribe_chunked(lf, fake_one, max_bytes=size // 2 + 1)
    check("split into multiple chunks", len(calls) >= 2)
    check("chunk boundary found the silence",
          any(abs(sum(d for _p, d in calls[:k]) - 41.0) < 13.0 for k in range(1, len(calls))))
    starts = [s["start"] for s in out["segments"]]
    check("timestamps re-offset ascending", starts == sorted(starts) and starts[0] == 1.0
          and starts[1] > 40.0 - 13.0)
    check("texts stitched", out["text"] == " ".join(f"part{i+1}" for i in range(len(calls))))
    check("chunk temp files cleaned", not list(Path(tempfile.gettempdir()).glob("earshot_chunk_*")))
    calls.clear()
    small = chunker.transcribe_chunked(lf, fake_one, max_bytes=size * 2)
    check("under-limit file sent whole", len(calls) == 1 and small["text"] == "part1")

    print("== chunker hardening (adversarial-review fixes) ==")
    # stereo 44.1 kHz source with content ONLY in the right channel: chunking
    # must downmix (not silently drop to channel 0), and 44.1 kHz must not
    # crash the chunk writer (always FLAC now, never Opus-at-invalid-rate)
    sr2 = 44100
    right = rng.standard_normal(sr2 * 60).astype(np.float32) * 0.3
    stereo = np.stack([np.zeros_like(right), right], axis=1)
    stf = tmp / "stereo.flac"
    sf.write(str(stf), stereo, sr2, subtype="PCM_16")
    rms_seen: list[float] = []

    def measuring_one(p: Path) -> dict:
        d, _ = sf.read(str(p), dtype="float32")
        rms_seen.append(float(np.sqrt((d ** 2).mean())))
        assert p.suffix == ".flac"
        return {"text": "s", "segments": []}

    chunker.transcribe_chunked(stf, measuring_one, max_bytes=stf.stat().st_size // 2 + 1)
    check("stereo chunks keep right-channel content",
          len(rms_seen) >= 2 and min(rms_seen) > 0.05)

    # density-skewed file (half silence, half dense): average-based planning
    # under-splits, so the MEASURED-size backstop must recurse until every
    # actual chunk fits the cap
    dense = np.concatenate([np.zeros(16000 * 60, np.float32),
                            rng.standard_normal(16000 * 60).astype(np.float32) * 0.3])
    df = tmp / "dense.flac"
    sf.write(str(df), dense, 16000, subtype="PCM_16")
    cap = int(df.stat().st_size * 0.45)
    sizes: list[int] = []

    def size_one(p: Path) -> dict:
        sizes.append(p.stat().st_size)
        return {"text": "d", "segments": []}

    chunker.transcribe_chunked(df, size_one, max_bytes=cap)
    check(f"every MEASURED chunk under the cap (max {max(sizes)} ≤ {cap})",
          bool(sizes) and max(sizes) <= cap)
    check("no chunk temp files leaked", not list(Path(tempfile.gettempdir()).glob("earshot_chunk_*")))

    print("== parallel channel transcription ==")
    cfg = Config()
    cfg.transcription_provider = "deepgram"  # any non-home provider → parallel

    def slow_transcribe(path, c, progress=None, **kw):
        time.sleep(0.25)
        return {"text": Path(path).stem, "segments": []}

    orig = processing.transcription_service.transcribe
    processing.transcription_service.transcribe = slow_transcribe
    try:
        t0 = time.monotonic()
        me_j, them_j = processing._transcribe_channels(Path("me.flac"), Path("them.flac"), cfg, lambda _m: None)
        wall = time.monotonic() - t0
        check(f"channels ran concurrently ({wall:.2f}s for 2×0.25s)", wall < 0.42)
        check("results keep channel identity", me_j["text"] == "me" and them_j["text"] == "them")
        cfg.transcription_provider = "home"
        t0 = time.monotonic()
        processing._transcribe_channels(Path("me.flac"), Path("them.flac"), cfg, lambda _m: None)
        check("home server stays sequential", time.monotonic() - t0 >= 0.45)
    finally:
        processing.transcription_service.transcribe = orig

    print("\nLONG-MEETING TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
