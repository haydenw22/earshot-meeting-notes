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
- **API keys are stored in plaintext** in `%LOCALAPPDATA%\Earshot\config.json` on
  Windows and `~/Library/Application Support/Earshot/config.json` on macOS,
  like most local CLI/desktop tools (aws, gh, gcloud). Anything running as your
  user can read them. Windows Credential Manager / macOS Keychain support is planned.
  Prefer environment variables on shared accounts.
- **The home Whisper server is typically plain HTTP on your LAN** and
  whisper-asr-webservice has no authentication — run it on a trusted network or
  behind a TLS/auth reverse proxy.
- **Webhook / online transcription / AI providers** send meeting content to the
  URL/provider *you* configure. Configure them only with endpoints you trust.
- Same-user malware can edit `config.json` (e.g. point the webhook somewhere
  hostile). Earshot cannot defend against an attacker already running as you.

## Supported versions

Only the latest release receives fixes.

## Automatic updates

Packaged Windows and macOS builds ask the public GitHub Releases API
on launch whether a newer version exists; no account or meeting data is sent.
Updating is one click, never silent or unattended. Before anything is executed,
the updater (0.33.0 and later) requires that the download comes from GitHub
over HTTPS and that the installer matches the SHA-256 digest published as a
release asset; a download that fails verification is discarded. Releases
without a digest asset are never auto-installed. On macOS the updater also
requires the replacement to have the Earshot bundle identifier, a newer version,
a valid Gatekeeper-accepted Developer ID signature and the same designated signing
identity as the running app. It retains the old bundle until the replacement has
launched and stayed alive, then removes the rollback copy. It refuses to update an
app running from a disk image or a location the user cannot modify. Tagged Mac
releases fail closed unless signing and Apple notarization succeed. The Windows
installer is not yet Authenticode-signed; signing is planned. Dev checkouts never
auto-update.
