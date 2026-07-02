"""Tests for the transcription-options batch: FLAC uploads, the provider
presets, the Mistral param-compat retry, and response-shape guards.

Run:  python tests/test_transcription_options.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from meeting_notes.audio import writer  # noqa: E402
from meeting_notes.transcription import openai_client as oc  # noqa: E402
from meeting_notes.ui.page_settings import ONLINE_PRESETS  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = "err"

    def json(self):
        return self._payload


def main() -> int:
    print("== FLAC upload preparation ==")
    tmp = Path(tempfile.mkdtemp())
    tone = 0.3 * np.sin(2 * np.pi * 440 * np.arange(48000, dtype=np.float32) / 48000)
    out = writer.prepare_for_transcription(tone, tmp / "me_16k.flac")
    info = sf.info(str(out))
    check("flac written at 16 kHz mono", out.suffix == ".flac" and info.samplerate == 16000
          and info.channels == 1)
    wav = writer.prepare_for_transcription(tone, tmp / "me_16k.wav")
    check("flac smaller than wav (lossless win)",
          out.stat().st_size < wav.stat().st_size * 0.9)
    back, sr = sf.read(str(out), dtype="float32")
    check("flac round-trips losslessly-ish", sr == 16000 and len(back) > 15000)

    print("== provider presets ==")
    check("presets exist", len(ONLINE_PRESETS) >= 3)
    labels = " ".join(lbl for lbl, _b, _m in ONLINE_PRESETS)
    check("Groq turbo preset present", any(m == "whisper-large-v3-turbo" for _l, _b, m in ONLINE_PRESETS))
    check("Mistral Voxtral preset present", any("mistral.ai" in b for _l, b, _m in ONLINE_PRESETS))
    check("prices shown for orientation", "$" in labels)
    for _label, base, model in ONLINE_PRESETS:
        check(f"preset sane: {model}", base.startswith("https://") and model)

    print("== Mistral param-compat retry ==")
    calls = []

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        calls.append(list(data))
        # first (OpenAI-style) form is rejected like Mistral does; the compat
        # retry must succeed and carry Mistral-style timestamp_granularities
        if any(k == "response_format" for k, _v in data):
            return _Resp(422)
        return _Resp(200, {"text": "hello there",
                           "segments": [{"start": 0.0, "end": 1.2, "text": "hello there"}]})

    small = tmp / "a.flac"
    sf.write(str(small), tone[:16000], 16000, subtype="PCM_16")
    orig_post = oc.httpx.post
    oc.httpx.post = fake_post
    try:
        result = oc.transcribe(small, base_url="https://api.mistral.ai/v1", api_key="k",
                               model="voxtral-mini-latest")
        check("retry produced a transcript", result["text"] == "hello there")
        check("two attempts made", len(calls) == 2)
        check("second attempt drops OpenAI-only params",
              not any(k == "response_format" for k, _v in calls[1])
              and ("timestamp_granularities", "segment") in calls[1])

        calls.clear()

        def fake_ok(url, headers=None, data=None, files=None, timeout=None):
            calls.append((files["file"][0], files["file"][2]))
            return _Resp(200, {"text": "x", "segments": []})

        oc.httpx.post = fake_ok
        oc.transcribe(small, base_url="https://api.groq.com/openai/v1", api_key="k",
                      model="whisper-large-v3-turbo")
        check("flac gets audio/flac mime", calls[0] == ("a.flac", "audio/flac"))
    finally:
        oc.httpx.post = orig_post

    print("== shape guard ==")
    def fake_bad(url, headers=None, data=None, files=None, timeout=None):
        return _Resp(200, {"text": "x", "segments": ["not-a-dict", {"start": 1, "end": 2, "text": "ok"}]})
    oc.httpx.post = fake_bad
    try:
        r = oc.transcribe(small, base_url="https://x/v1", api_key="k")
        check("non-dict segments skipped, dict kept", len(r["segments"]) == 1 and r["segments"][0]["text"] == "ok")
    finally:
        oc.httpx.post = orig_post

    print("\nTRANSCRIPTION OPTIONS TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
