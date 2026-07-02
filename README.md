# Earshot

**A local-first meeting recorder and AI note-taker for Windows. No bot joins
your call, and no cloud touches your audio unless you choose one.**

Earshot records a meeting on **two separate channels** — your microphone ("me")
and your system audio ("them") — then:

1. (optionally) cancels the echo of *their* audio out of *your* mic so nothing is
   double-recorded, while keeping your voice when you talk over someone;
2. transcribes on **your own Whisper server** (or Deepgram / any OpenAI-compatible
   API, if you opt into a cloud);
3. merges the two channels into one speaker-labelled transcript — the speaker
   label is ground truth, no diarization guesswork;
4. writes structured notes with Claude: title, summary, action items, sections.

Because the capture is channel-based, there is **no bot participant** in your
meetings — nothing for Teams to block, nothing for clients to side-eye.

## Features

- Dual-channel recording with offline WebRTC AEC3 echo cancellation
- Transcription providers: self-hosted `whisper-asr-webservice`, Deepgram, or any
  OpenAI-compatible audio API — with VAD skip-silence for dual-channel speed
- Structured AI notes with per-meeting-type **templates** and global **custom
  instructions**; one-click saved **AI actions** (e.g. draft the follow-up email)
- **Ask your meetings**: natural-language Q&A across every meeting, with
  citations verified verbatim against the transcripts
- Cross-meeting **pending action items** dashboard; full-text search over
  transcripts, notes, attendees and agendas
- **Bookmarks** while recording, talk-time analytics, screen-capture context
  screenshots, import of existing audio/video files
- Always-on-top **recording overlay** (REC dot, timer, per-channel level lights)
- Live "no input detected" warning; crash recovery salvages interrupted
  recordings on the next launch
- **Webhook**: POST each finished meeting as JSON into your own automation
- Clean rich-text copy (pastes perfectly into Notion / email), light + dark UI

## Requirements

- Windows 10/11, **Python 3.12** (wheels for the audio/Qt stack are reliable there).
- A transcription source (configured in Settings — nothing is preconfigured):
  - **Home server:** a reachable `onerahmet/openai-whisper-asr-webservice`
    container (e.g. `http://<your-server-ip>:9000`). Use `ASR_ENGINE=faster_whisper`
    to unlock VAD skip-silence.
  - **or Deepgram**, **or** any OpenAI-compatible API (OpenAI `whisper-1`, Groq
    `whisper-large-v3`).
- An Anthropic API key for note generation (transcription works without it).

## Setup

```sh
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.lock.txt   # pinned, known-good
# (or `-r requirements.txt` for the loose ranges)
.venv/Scripts/python main.py
```

On first launch: **Settings → Transcription** (pick a source, *Test connection*),
then **Settings → AI** (Anthropic key or `ANTHROPIC_API_KEY` env var). Then
**New recording** → pick mic + output device → **Start**.

## Security & privacy

Read [SECURITY.md](SECURITY.md) for the full policy and threat model. The short
version:

- **Local by default.** Audio, transcripts, notes and the SQLite library live
  under `%LOCALAPPDATA%\Earshot\`. Nothing leaves your machine except the
  transcription/AI providers **you** configure and the optional webhook.
- **Keys are stored in plaintext** in `%LOCALAPPDATA%\Earshot\config.json`
  (like most local dev tools). Prefer the `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`
  / `DEEPGRAM_API_KEY` environment variables on shared machines.
- **LAN Whisper is plain HTTP** and unauthenticated by default — run it on a
  trusted network or behind a TLS/auth reverse proxy.
- AI prompts treat meeting content as untrusted data (spoken "prompt injection"
  is fenced), and AI-generated text is rendered as plain text in the UI.

## Smoke-test the audio hardware (no GUI)

```sh
.venv/Scripts/python tools/smoke_record.py 4
```

## Tests

```sh
.venv/Scripts/python tests/test_core.py          # + the other tests/test_*.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python tests/test_ui_smoke.py
```

CI runs the full suite on every push/PR (see `.github/workflows/ci.yml`).

## Build the standalone app

One command builds the `.exe`, installs it to `%LOCALAPPDATA%\Programs\Earshot`,
and creates Desktop + Start Menu shortcuts:

```powershell
powershell -ExecutionPolicy Bypass -File "packaging\build_and_install.ps1"
```

Optional — a distributable installer: install [Inno Setup](https://jrsoftware.org/isdl.php),
then `iscc packaging\installer.iss` (after building once with
`pyinstaller packaging/meeting_notes.spec`).

## Project layout

```
meeting_notes/
  config.py, paths.py, changelog.py
  audio/         devices, capture, writer, calibrate, aec
  transcription/ whisper_client, openai_client, deepgram_client, service, merge
  notes/         schema, anthropic_client, actions, render
  qa/            ask (two-pass Q&A with verified citations)
  integrations/  webhook
  storage/       db, repository (SQLite + FTS5)
  pipeline/      processing
  ui/            shell + pages, overlay, theme, workers
main.py          entry point
tools/           smoke_record, screenshots
tests/           self-contained test scripts (no framework needed)
packaging/       PyInstaller spec + Inno Setup script + build_and_install.ps1
```

## License

[MIT](LICENSE)
