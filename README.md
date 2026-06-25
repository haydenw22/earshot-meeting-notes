# Earshot

A local Windows desktop app that records a meeting on **two separate channels**
— your microphone ("me") and your system audio ("them") — then:

1. (optionally) cancels the echo of *their* audio out of *your* mic so nothing is
   double-recorded, while keeping your voice when you talk over someone;
2. offloads transcription to your home server's **Whisper** container;
3. merges the two channels into one speaker-labelled transcript;
4. uses **Claude Haiku** to write structured meeting notes + a one-sentence title;
5. stores every meeting in a local library.

Built in Python 3.12 + PySide6. Notion sync is designed-for (a `notion_page_id`
column exists) but intentionally out of scope for now.

## Requirements

- Windows 10/11, **Python 3.12** (wheels for the audio/Qt stack are reliable there).
- A transcription source (configured in Settings — nothing is preconfigured):
  - **Home server:** a reachable `onerahmet/openai-whisper-asr-webservice` container
    (e.g. `http://<your-server-ip>:9000`). No local ffmpeg needed — the server decodes uploads.
  - **or Online:** an OpenAI-compatible API (OpenAI `whisper-1`, or Groq `whisper-large-v3`).
- An Anthropic API key for note generation (transcription works without it).

> **Keys are stored locally** in `%LOCALAPPDATA%\Earshot\config.json` (plaintext, protected
> by your Windows account). Prefer the `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` environment
> variables on shared machines, and never commit `config.json`.

## Setup

```sh
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

## Run

```sh
.venv/Scripts/python main.py
```

On first launch, open **Settings → Transcription** and choose a source:
- **Home server** — enter your Whisper server URL, e.g. `http://<your-server-ip>:9000`; use *Test connection*.
- **Online** — enter the base URL + API key + model.

Then **Settings → Notes** → your **Anthropic API key** (or set the `ANTHROPIC_API_KEY` environment variable).

Then **New recording** → pick your mic + the output device to capture → tick
"on headphones" if you are → **Start**. Edit attendees any time during the call.
**Stop** finalises the audio and runs transcription → notes automatically.

## How it works (key design points)

- **Two channels, no diarization.** Mic = "Me", loopback = "Them". Each channel is
  transcribed separately, so the speaker label is ground truth.
- **Echo cancellation, not gating.** When not on headphones, the loopback is used as
  the far-end reference for WebRTC AEC3 (via `livekit`), subtracting their echo from
  your mic *without* dropping your voice during double-talk. Runs offline on the
  finished recording (raw streams are kept, so it can be re-run).
- **Nothing heavy runs locally.** Transcription is on the server; the PC only records,
  does light resampling/AEC, and calls two HTTP APIs.
- **Saved first, synced later.** Notes are written to SQLite before anything else.

Data lives under `%LOCALAPPDATA%\Earshot\` (DB + per-meeting audio folders).

## Smoke-test the audio hardware (no GUI)

```sh
.venv/Scripts/python tools/smoke_record.py 4
```
Records 4s from the default mic + loopback, writes a stereo file, and runs the AEC
path — useful to confirm capture works on a given machine.

## Tests

```sh
.venv/Scripts/python tests/test_core.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python tests/test_ui_smoke.py
```

## Build the standalone app (no terminal needed afterwards)

One command builds the `.exe`, installs it to `%LOCALAPPDATA%\Programs\Earshot`,
and creates Desktop + Start Menu shortcuts:

```powershell
powershell -ExecutionPolicy Bypass -File "packaging\build_and_install.ps1"
```

After that, just double-click the **Earshot** desktop icon. Re-run the script
any time the code changes to update the installed app.

Optional — a distributable single-file installer (for putting it on another PC):
install [Inno Setup](https://jrsoftware.org/isdl.php), then `iscc packaging\installer.iss`
(after building once with `pyinstaller packaging/meeting_notes.spec`).

## Project layout

```
meeting_notes/
  config.py, paths.py
  audio/        devices, capture, writer, calibrate, aec
  transcription/ whisper_client, merge
  notes/        schema, anthropic_client
  storage/      db, repository
  pipeline/     processing
  ui/           main_window, recording_view, meeting_detail_view, settings_dialog, workers
  app.py
main.py            entry point
tools/smoke_record.py
tests/
packaging/         PyInstaller spec + Inno Setup script
```
