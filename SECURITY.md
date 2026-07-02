# Security Policy

Earshot is a local-first meeting recorder: your audio, transcripts and notes
stay on your machine (and your own transcription server) unless you explicitly
configure an outbound integration.

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Instead, use
GitHub's **"Report a vulnerability"** (Security tab → Private vulnerability
reporting) on this repository. You'll get an acknowledgement within a few days.
Please include reproduction steps and the version (Settings → About).

## Scope & threat model

In scope:
- The Earshot desktop app and its packaged installer.
- Data-handling defects: anything that sends meeting content somewhere the user
  didn't configure, corrupts/loses recordings, or leaks API keys.
- Prompt-injection paths that let meeting content alter app behaviour beyond
  polluting AI-generated text.

Out of scope / known trade-offs (documented deliberately):
- **API keys are stored in plaintext** in `%LOCALAPPDATA%\Earshot\config.json`,
  like most local CLI/desktop tools (aws, gh, gcloud). Anything running as your
  Windows user can read them. Windows Credential Manager support is planned.
- **The home Whisper server is typically plain HTTP on your LAN** and
  whisper-asr-webservice has no authentication — run it on a trusted network or
  behind a TLS/auth reverse proxy.
- **Webhook / online transcription / AI providers** send meeting content to the
  URL/provider *you* configure. Configure them only with endpoints you trust.
- Same-user malware can edit `config.json` (e.g. point the webhook somewhere
  hostile). Earshot cannot defend against an attacker already running as you.

## Supported versions

Only the latest release receives fixes. There is no auto-update yet — watch the
repository's Releases to hear about security updates.
