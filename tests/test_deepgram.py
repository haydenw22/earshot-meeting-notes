"""Tests for the Deepgram transcription provider — response parsing, config,
dispatch routing, and that its output merges into the speaker-labelled transcript.

No network: the parser is tested directly and the dispatch is monkeypatched.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meeting_notes.config import Config  # noqa: E402
from meeting_notes.transcription import deepgram_client, merge, service  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


# --- realistic Deepgram pre-recorded response (utterances=true, smart_format=true) ---
SAMPLE = {
    "metadata": {"duration": 6.4, "channels": 1},
    "results": {
        "channels": [
            {"alternatives": [
                {"transcript": "Hello there. How are you doing today?", "confidence": 0.99,
                 "words": [{"word": "hello", "start": 0.1, "end": 0.4, "punctuated_word": "Hello"}]}
            ]}
        ],
        "utterances": [
            {"start": 0.1, "end": 1.2, "transcript": "Hello there.", "confidence": 0.99, "channel": 0},
            {"start": 1.5, "end": 3.0, "transcript": "How are you doing today?", "confidence": 0.98, "channel": 0},
        ],
    },
}


def main() -> int:
    print("== parse_response ==")
    out = deepgram_client.parse_response(SAMPLE)
    check("text from channel alternative", out["text"] == "Hello there. How are you doing today?")
    check("two utterance segments", len(out["segments"]) == 2)
    check("segment start/end/text", out["segments"][0] == {"start": 0.1, "end": 1.2, "text": "Hello there."})
    check("segment shape matches contract", set(out["segments"][1]) == {"start", "end", "text"})

    print("== parse fallbacks ==")
    no_utt = {"metadata": {"duration": 9.0},
              "results": {"channels": [{"alternatives": [{"transcript": "Just one line"}]}]}}
    o2 = deepgram_client.parse_response(no_utt)
    check("no utterances → single whole-file segment",
          len(o2["segments"]) == 1 and o2["segments"][0]["end"] == 9.0 and o2["segments"][0]["text"] == "Just one line")
    empty = deepgram_client.parse_response({})
    check("empty payload → empty result", empty == {"text": "", "segments": []})
    check("missing key never raises", deepgram_client.parse_response({"results": {}})["text"] == "")

    print("== config ==")
    os.environ.pop("DEEPGRAM_API_KEY", None)
    cfg = Config()
    cfg.deepgram_api_key = "dg-secret"
    cfg.deepgram_model = "nova-3"
    check("resolved_deepgram_key from config", cfg.resolved_deepgram_key() == "dg-secret")
    cfg.deepgram_api_key = ""
    os.environ["DEEPGRAM_API_KEY"] = "from-env"
    check("resolved_deepgram_key falls back to env", cfg.resolved_deepgram_key() == "from-env")
    os.environ.pop("DEEPGRAM_API_KEY", None)

    print("== dispatch routing ==")
    captured = {}

    def fake_transcribe(audio_path, *, api_key, model, language, timeout=None):
        captured.update(api_key=api_key, model=model, language=language, path=str(audio_path))
        return {"text": "ok", "segments": []}

    orig = deepgram_client.transcribe
    service.deepgram_client.transcribe = fake_transcribe
    try:
        cfg2 = Config()
        cfg2.transcription_provider = "deepgram"
        cfg2.deepgram_api_key = "k123"
        cfg2.deepgram_model = "nova-2"
        cfg2.whisper_language = "en"
        result = service.transcribe("meeting.wav", cfg2)
        check("dispatch routes to deepgram", result == {"text": "ok", "segments": []})
        check("dispatch passes key/model/language",
              captured == {"api_key": "k123", "model": "nova-2", "language": "en", "path": "meeting.wav"})
    finally:
        service.deepgram_client.transcribe = orig

    print("== merge with deepgram output ==")
    me = deepgram_client.parse_response(SAMPLE)
    them = {"text": "Yes, all good thanks.",
            "segments": [{"start": 0.5, "end": 2.0, "text": "Yes, all good thanks."}]}
    merged = merge.merge_transcripts(me, them, dedupe=False)
    check("merge labels Me and Them", "Me:" in merged["text"] and "Them:" in merged["text"])
    check("merge ordered by time", merged["segments"][0]["start"] <= merged["segments"][1]["start"])
    check("timestamp formatting present", merged["text"].startswith("[00:00]"))

    print("\nALL DEEPGRAM TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
