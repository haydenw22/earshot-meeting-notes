"""Release history — the single source of truth.

Rendered in-app under Settings → About ("What's new") and mirrored to the
repo's CHANGELOG.md. The newest release must sit first and its version must
match meeting_notes.__version__ (enforced by tests/test_changelog.py).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Release:
    version: str
    date: str                       # ISO yyyy-mm-dd
    title: str = ""                 # short headline
    sections: tuple = ()            # ((heading, (bullet, ...)), ...)


RELEASES: tuple[Release, ...] = (
    Release(
        "0.7.0", "2026-06-29", "Cleaner speaker attribution",
        (
            ("Changed", (
                "Much better Me/Them separation when recording on speakers. Faint residual bleed of "
                "the other side into your mic was being transcribed and labelled as you; the crosstalk "
                "de-duper now uses time-windowed word containment (robust to run-on segments) and the "
                "mic-vs-system loudness, so the other side's words no longer show up as \"Me\" — while "
                "your own speech is kept even when it echoes theirs. (Headphones still give the cleanest "
                "result.)",
            )),
        ),
    ),
    Release(
        "0.6.0", "2026-06-29", "Faster local transcription",
        (
            ("Added", (
                "Skip-silence (VAD) option for the home Whisper server: it transcribes only speech and "
                "skips silent stretches. Because each channel of a dual-channel recording is silent "
                "while the other side talks, this is a big speed-up. On by default — toggle it in "
                "Settings → Transcription (needs the faster-whisper engine on the server).",
            )),
        ),
    ),
    Release(
        "0.5.0", "2026-06-26", "Recording overlay",
        (
            ("Added", (
                "An always-on-top recording overlay: a small floating bar with a pulsing REC dot, a "
                "live timer, and two lights that glow with your mic and the system audio — so you can "
                "see Earshot is capturing while you're in another app.",
                "Multi-monitor friendly and repositionable — drag it onto any screen and the spot is "
                "remembered (with a Reset position button if it ever ends up off-screen).",
                "Customisable in Settings → General: turn it on/off and set its opacity.",
            )),
        ),
    ),
    Release(
        "0.4.0", "2026-06-26", "Recording safeguards",
        (
            ("Added", (
                "A live warning while recording if a channel goes completely silent — catches a muted "
                "mic or mis-routed system audio before you lose the meeting. It names the affected side "
                "and clears the moment sound is detected.",
            )),
        ),
    ),
    Release(
        "0.3.0", "2026-06-26", "Deepgram transcription",
        (
            ("Added", (
                "Optional Deepgram transcription — a fast, accurate cloud option with no upload-size "
                "limit (great for long meetings). Choose it in Settings → Transcription.",
            )),
        ),
    ),
    Release(
        "0.2.0", "2026-06-26", "Power-user features",
        (
            ("Added", (
                "Home dashboard of pending action items pulled from every past meeting, so nothing "
                "slips through — toggle it on/off in Settings → General.",
                "Custom AI instructions: append your own guidance (tone, perspective, British English, "
                "what to emphasise) to every summary. Off by default — Settings → AI.",
                "Webhook: POST each finished meeting as JSON to your own automation (Slack, Notion, "
                "Zapier, n8n, a CRM…). Choose to fire after transcription or after the AI summary — "
                "Settings → General.",
                "Note templates per meeting type (Sales call, Standup, 1:1…) that steer the AI notes; "
                "pick one on the recording screen. Manage them in Settings → AI.",
                "Full-text search across transcripts, notes, attendees and agendas — not just titles.",
                "Saved AI actions: one-click prompts (e.g. \"Draft a follow-up email\", \"List the "
                "decisions\") you can run on any finished meeting from its page.",
                "Bookmarks: flag key moments while recording with the button or Ctrl+B, then jump "
                "straight to them from the transcript.",
                "Import existing audio or video files and transcribe + summarise them like a recording.",
                "Talk-time analytics: an approximate \"you vs them\" split shown on each meeting.",
                "This What's-new / changelog view, here in Settings → About.",
            )),
        ),
    ),
    Release(
        "0.1.0", "2026-06-25", "Initial release",
        (
            ("Added", (
                "Dual-channel recording — your mic and the system/meeting audio captured separately, "
                "with echo cancellation.",
                "Transcription via your own home Whisper server or any OpenAI-compatible API.",
                "AI meeting notes with Claude: title, summary, attendees, action items and sections.",
                "Modern PySide6 desktop UI with light and dark themes and a resizable, movable sidebar.",
                "Agenda field and Notion-style notes with checkable action items.",
                "Ask Earshot — ask questions across all your meetings and get answers with citations.",
                "Screen capture — change-detected screenshots saved alongside each meeting.",
                "Clean \"Copy summary\" that pastes rich, markdown-free notes into Notion, email, anywhere.",
                "Settings for default devices, storage folder, auto-transcribe / auto-summary toggles, "
                "and an OpenAI key field.",
                "Packaged Windows installer (Earshot-Setup).",
            )),
        ),
    ),
)


def latest() -> Release:
    return RELEASES[0]


def to_markdown() -> str:
    """Render the release history as Keep-a-Changelog style markdown."""
    out = [
        "# Changelog",
        "",
        "All notable changes to Earshot are documented here.",
        "",
        "The format follows [Keep a Changelog](https://keepachangelog.com/), and the "
        "project aims to follow [Semantic Versioning](https://semver.org/).",
        "",
    ]
    for rel in RELEASES:
        head = f"## [{rel.version}] — {rel.date}"
        if rel.title:
            head += f" · {rel.title}"
        out.append(head)
        out.append("")
        for heading, bullets in rel.sections:
            out.append(f"### {heading}")
            out.append("")
            out.extend(f"- {b}" for b in bullets)
            out.append("")
    return "\n".join(out).rstrip() + "\n"
