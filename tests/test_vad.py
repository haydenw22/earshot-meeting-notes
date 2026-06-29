"""VAD (skip-silence) wiring for the home Whisper server: the `vad_filter` query
param is sent when enabled, omitted when off, and flows from config through the
service dispatcher. The HTTP layer is stubbed — no server needed.

Run:  python tests/test_vad.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meeting_notes.config import Config  # noqa: E402
from meeting_notes.transcription import service as svc  # noqa: E402
from meeting_notes.transcription import whisper_client as wc  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


class _FakeResp:
    status_code = 200

    def json(self):
        return {"text": "hello", "segments": []}


def main() -> int:
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["params"] = dict(kwargs.get("params") or {})
        return _FakeResp()

    wc.httpx.post = fake_post  # stub the HTTP call

    audio = Path(tempfile.mkdtemp()) / "a.wav"
    audio.write_bytes(b"RIFFfake")  # content irrelevant — post is stubbed

    print("== whisper_client param ==")
    wc.transcribe(audio, base_url="http://server:9000", vad_filter=True)
    check("vad_filter=true sent when enabled", captured["params"].get("vad_filter") == "true")
    check("hits the /asr endpoint", captured["url"].endswith("/asr"))

    wc.transcribe(audio, base_url="http://server:9000", vad_filter=False)
    check("vad_filter omitted when disabled", "vad_filter" not in captured["params"])

    print("== service routes config.whisper_vad_filter ==")
    cfg = Config()
    cfg.transcription_provider = "home"
    cfg.whisper_url = "http://server:9000"
    cfg.whisper_vad_filter = True
    svc.transcribe(audio, cfg)
    check("service forwards VAD from config (on)", captured["params"].get("vad_filter") == "true")

    cfg.whisper_vad_filter = False
    svc.transcribe(audio, cfg)
    check("service forwards VAD from config (off)", "vad_filter" not in captured["params"])

    print("== config round-trip + default ==")
    check("VAD defaults on", Config().whisper_vad_filter is True)
    c2 = Config.load()
    c2.whisper_vad_filter = False
    c2.save()
    check("VAD setting persists", Config.load().whisper_vad_filter is False)

    print("\nVAD TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
