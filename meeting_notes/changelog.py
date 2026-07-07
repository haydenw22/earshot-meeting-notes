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
        "0.29.1", "2026-07-07", "Icon rail, friendlier dates, snappier About",
        (
            ("Changed", (
                "Collapsing the sidebar now leaves a slim icon rail (record, import, "
                "Overview, Ask, theme, Settings, Help, account) instead of hiding it "
                "completely.",
                "Trial and renewal dates read like a human wrote them: \"July 20\" "
                "instead of \"2026-07-20\", in the sidebar card, on Plans & Billing "
                "and on the Account page.",
                "Settings, then About now shows only the three most recent releases, "
                "which keeps the page quick. A \"Full release history\" button and the "
                "Help menu's \"What's new\" open the complete changelog on GitHub.",
            )),
        ),
    ),
    Release(
        "0.29.0", "2026-07-07", "UI overhaul: unified Settings, Plans & Billing, Help Center",
        (
            ("Added", (
                "Settings is now one place for everything, with sections down the side: "
                "General, Audio, Transcription, AI, Integrations and About, plus a new "
                "ACCOUNT area. The Account and Integrations pages moved in from the sidebar.",
                "A new Plans & Billing page shows the Free and Plus plans side by side, "
                "your trial or renewal status, and this month's transcription usage.",
                "A Help button in the sidebar opens the new built-in Help Center: a "
                "getting-started guide plus tips for recording, transcription, AI notes, "
                "projects and troubleshooting.",
                "The sidebar can be collapsed with the panel button at its top, giving "
                "your notes the whole window. A floating button brings it back.",
                "A Plus or Plus Trial badge appears next to the logo when you're signed "
                "in, and a card above Settings reminds you to upgrade before a trial "
                "ends or to renew when auto-renewal is off.",
            )),
            ("Changed", (
                "Meeting notes no longer crowd the sidebar. A collapsible PROJECTS "
                "dropdown lists just your projects; clicking one opens it in the main "
                "window with all of its meetings. Notes that aren't in a project live "
                "in a built-in Uncategorized project, and every meeting row has the "
                "usual open / move / delete menu.",
                "Searching now opens a results page in the main window instead of "
                "squeezing matches into the sidebar.",
                "The app opens in a larger window (1336 x 843) so the new layout has "
                "room to breathe.",
                "The sidebar is leaner and calmer: the light/dark switch is a small "
                "button at the top, and the version number now lives in Settings.",
            )),
        ),
    ),
    Release(
        "0.28.1", "2026-07-07", "Test connection no longer freezes the app",
        (
            ("Fixed", (
                "\"Test connection\" buttons (Transcription, AI, Integrations and the setup "
                "guide) ran on the UI thread and could freeze the whole app for up to ~16 "
                "seconds against an unreachable server. They now run in the background: the "
                "button shows \"Testing…\" and the app stays responsive.",
            )),
        ),
    ),
    Release(
        "0.28.0", "2026-07-07", "Bug-hunt fixes across the app",
        (
            ("Fixed", (
                "Signing in to Earshot Plus from the first-run setup guide could leave the "
                "wizard stuck with no way forward — a successful sign-in now advances to the "
                "finish screen.",
                "Quitting the app while a transcription, summary or import was still running "
                "could crash on exit — background work is now stopped cleanly first.",
                "Dismissing a stale \"call ended\" prompt after you'd already stopped a "
                "recording could freeze the record button on \"Processing…\" — it's now a no-op.",
                "Signing out of Earshot Plus is instant and no longer briefly freezes the app "
                "when the server is unreachable, and can't error if a usage refresh was still "
                "in flight.",
                "Earshot Plus transcribes both audio channels in parallel again (it had "
                "silently gone sequential for cloud accounts, doubling the wait).",
                "The device name you type when linking a PC is now used even if you edit it "
                "before entering the code; the code screen no longer stays red after a "
                "failed-then-successful retry.",
                "Fixed a memory leak when switching account mode or re-running the setup guide.",
            )),
        ),
    ),
    Release(
        "0.27.0", "2026-07-07", "Sign-in fixed; named devices",
        (
            ("Fixed", (
                "Signing in to Earshot Plus actually completes now. Approving the code on "
                "the website worked, but the app never noticed — an internal naming clash "
                "meant the success signal never reached the handler that saves your sign-in. "
                "A test now drives the real sign-in dialog end to end so this can't regress.",
            )),
            ("Added", (
                "The sign-in code can be copied with one click (and selected with the mouse).",
                "Name each device when you link it — the name shows up in the Devices list "
                "on your account page at tryearshot.app, where you can also sign devices "
                "out remotely.",
            )),
        ),
    ),
    Release(
        "0.26.2", "2026-07-07", "Consistent sidebar rows",
        (
            ("Fixed", (
                "Meetings inside a project now look exactly like meetings in the MEETING "
                "NOTES list: one line, trimmed with \"…\", same compact height. Long titles "
                "no longer wrap into oversized rows.",
            )),
        ),
    ),
    Release(
        "0.26.1", "2026-07-07", "Earshot Plus pricing update",
        (
            ("Changed", (
                "Earshot Plus is now $9/month (was $15) — the setup guide and Account page "
                "reflect the new price.",
            )),
        ),
    ),
    Release(
        "0.26.0", "2026-07-07", "Recording indicator & sidebar polish",
        (
            ("Added", (
                "The sidebar button becomes a live recording indicator while you record: "
                "it reads \"Recording\" and the record dot pulses until you stop.",
                "Earshot Plus beta subscriptions show a proper \"Beta\" badge on the "
                "Account page.",
            )),
            ("Changed", (
                "Project rows in the sidebar now use exactly the same rounded hover pills "
                "as the meeting list below them.",
                "Removed the duplicate \"New recording\" button from the home page header "
                "— the sidebar button and the big record card already cover it.",
            )),
        ),
    ),
    Release(
        "0.25.1", "2026-07-06", "Scroll-safe settings",
        (
            ("Fixed", (
                "Scrolling a settings page (or the record/integrations pages) no longer "
                "changes whatever the wheel passes over — overlay opacity, model pickers, "
                "device dropdowns. The wheel scrolls the page; a control only responds to "
                "the wheel after you click into it.",
            )),
        ),
    ),
    Release(
        "0.25.0", "2026-07-06", "Mandatory setup guide & sidebar overhaul",
        (
            ("Changed", (
                "The first-run setup guide now runs before the app opens and must be "
                "completed — no closing it, no app in the background. The choice screen "
                "leads with Earshot Plus (recommended) while self-hosting stays free and "
                "fully supported; \"skip\" on the Plus page became \"Use my own keys "
                "instead\". The Settings re-run stays dismissable.",
                "The sidebar's PROJECTS and MEETING NOTES sections now flow in one "
                "smoothly-scrolling column instead of fighting for space with two "
                "cramped scrollbars on smaller windows.",
            )),
        ),
    ),
    Release(
        "0.24.0", "2026-07-06", "Setup guide & Earshot Plus groundwork",
        (
            ("Added", (
                "A first-run setup guide: a quick tour of what Earshot does, then a choice — "
                "self-host free forever with your own keys (guided setup with connection "
                "tests), or sign in to Earshot Plus. Re-run it anytime from Settings → General.",
                "Earshot Plus groundwork: sign in by opening tryearshot.app and entering a "
                "short code — no passwords typed into the app. In Plus mode transcription and "
                "AI run through the managed service (no keys needed), the Transcription and AI "
                "settings tabs step aside, and the Account page shows your plan, renewal date "
                "and a live usage meter with manage-billing and sign-out.",
            )),
            ("Notes", (
                "Earshot Plus servers aren't live yet — the app says so politely if you try. "
                "Self-hosting stays exactly as it was, free forever.",
            )),
        ),
    ),
    Release(
        "0.23.1", "2026-07-06", "Header icon alignment",
        (
            ("Fixed", (
                "The collapse chevrons and ⋮ menu buttons on the home page (To do card, "
                "Meetings header, and each meeting row) were pushed into a corner of their "
                "button instead of centred — they now sit properly centred in their tap "
                "targets with even spacing.",
            )),
        ),
    ),
    Release(
        "0.23.0", "2026-07-06", "Collapsible meeting list",
        (
            ("Added", (
                "The home page's meeting list has a \"Meetings\" header with a collapse toggle "
                "— fold it away to see your whole to-do list without scrolling (handy on "
                "vertical monitors, where the to-dos sit below the list). The choice is "
                "remembered between sessions.",
            )),
            ("Changed", (
                "On narrow windows the stacked layout no longer leaves a large empty gap "
                "between the meeting list and the To do card — spare space now falls below "
                "the cards instead of between them.",
            )),
        ),
    ),
    Release(
        "0.22.2", "2026-07-06", "To-dos stay visible on narrow windows",
        (
            ("Fixed", (
                "On a narrow window (e.g. a vertical monitor) the home page's To do and "
                "AI insights cards disappeared entirely. They now reflow full-width underneath "
                "the meeting list instead of hiding.",
                "Long meeting titles no longer push the home page wider than the window "
                "(content was getting clipped at the right edge on narrow screens) — row "
                "titles now shorten with \"…\" and show the full title in a tooltip.",
            )),
        ),
    ),
    Release(
        "0.22.1", "2026-07-03", "Fix online transcription (Groq / OpenAI / Mistral)",
        (
            ("Fixed", (
                "Transcribing with an online provider (Groq, OpenAI or Mistral) crashed with "
                "\"sequence item 1: expected a bytes-like object, tuple found\" and never uploaded "
                "the audio. The upload form is now built in the shape newer httpx requires, so "
                "online transcription — including re-transcribing a meeting when the home Whisper "
                "server is unreachable — works. A regression test now exercises the real upload "
                "encoder so this can't silently break again.",
            )),
        ),
    ),
    Release(
        "0.22.0", "2026-07-03", "Sidebar projects polish",
        (
            ("Changed", (
                "The PROJECTS section now flows in the sidebar like the mockup: no boxed panel, "
                "rounded hover rows, and long meeting titles wrap onto extra lines instead of "
                "being cut off with \"…\". The section takes exactly the height its content "
                "needs (and scrolls gracefully when the window is short).",
            )),
            ("Removed", (
                "The \"Meeting notes\" card in the home page's right rail — it duplicated the "
                "meeting list sitting right next to it.",
            )),
        ),
    ),
    Release(
        "0.21.0", "2026-07-03", "Home redesign, projects, due dates & accounts preview",
        (
            ("Added", (
                "The home page was redesigned: a big \"Record a new meeting\" hero card, filter "
                "chips, compact meeting rows with a ⋮ menu (Open, Move to project, Delete), and "
                "a right-hand rail with your to-do list, recent meeting notes and an AI-insights "
                "shortcut. The rail steps aside automatically on narrow windows.",
                "Action items can carry a due date. The AI only fills one in when the meeting "
                "actually states a deadline — and like every suggestion it still needs your "
                "approval. Add or change a date on any item with the little calendar button (on "
                "the meeting page or the home to-do list); chips show Overdue / Today / the date, "
                "and due dates are passed to Todoist.",
                "The to-do card shows how much you've completed and can switch between a top-6 "
                "view and everything; Mark-all-done and Clear-all moved into its ⋮ menu.",
                "New Integrations page in the sidebar: Todoist and the automation webhook moved "
                "there from Settings, with Slack, Notion and Calendar marked as coming soon "
                "(Zapier and Make already work today via the webhook).",
                "New Account page and sidebar account card: set a display name now; Earshot Cloud "
                "(sync across devices, hosted transcription) is shown as a preview — no account "
                "is required and everything stays on your PC.",
                "A light/dark toggle slider in the sidebar replaces the old theme button.",
            )),
            ("Changed", (
                "Folders are now called projects everywhere in the app (your data is unchanged).",
                "\"Home\" in the sidebar is now \"Overview\", and the header's New-recording "
                "button matches the app's indigo accent.",
            )),
            ("Fixed", (
                "The Move-to-project submenu now uses exactly the same row spacing as the More "
                "menu it opens from, so the two read as one control.",
            )),
        ),
    ),
    Release(
        "0.20.0", "2026-07-03", "Calmer call detection & menu polish",
        (
            ("Changed", (
                "Call detection no longer prompts for games, dictation tools or anything else that "
                "merely uses the microphone. Only known meeting apps trigger it (Zoom, Teams, Webex, "
                "Slack — after ~8 s), and browsers (a possible Google Meet tab) only after ~24 s of "
                "sustained mic use, so a quick voice note never interrupts you.",
                "All dropdown menus, combo lists and multi-select pickers got a visual overhaul: "
                "rounded corners, sidebar-style rounded hover pills, aligned icon columns, and clean "
                "checkbox styling. \"Move to folder\" now marks the current folder with a check in "
                "the folder's colour.",
            )),
            ("Fixed", (
                "The More button no longer shows two dropdown arrows.",
            )),
        ),
    ),
    Release(
        "0.19.0", "2026-07-03", "Webhook folder routing",
        (
            ("Added", (
                "The webhook payload now includes the meeting's folder (id, name and colour; null "
                "when unfiled), so Zapier/n8n/Make automations can route meetings by client or team. "
                "Existing payload fields are unchanged.",
            )),
        ),
    ),
    Release(
        "0.18.0", "2026-07-03", "Folders & fit-and-finish",
        (
            ("Added", (
                "Folders: organise meetings by client, team or project — colour-coded (8 colours), "
                "in their own collapsible sidebar section. Pick or create a folder when starting a "
                "recording, move meetings later (More → Move to folder, or drag-and-drop in the "
                "sidebar), and filter the home page by folder.",
                "Ask Earshot can now be scoped to a folder, a single meeting, or everything.",
                "Prep briefs have an Advanced mode (Settings → AI): choose a folder or hand-pick the "
                "past meetings the brief is built from.",
                "The home to-do list is collapsible and gained Mark-all-done and Clear-all actions.",
            )),
            ("Changed", (
                "Pick the AI model that powers the whole app — Anthropic, OpenAI (or compatible "
                "cloud), or a local server — in one place, with only the chosen provider's settings "
                "shown. Keys for every provider stay saved when you switch. Ask Earshot now follows "
                "this choice too (it was Claude-only).",
                "**Bold** text now renders properly everywhere — meeting pages, the to-do list, "
                "copied notes, shared HTML — and AI-action results (like follow-up emails) display "
                "as formatted text instead of raw markdown.",
            )),
            ("Fixed", (
                "Editing an action item no longer makes it vanish — it now edits in place, and the "
                "edit button is a proper pencil icon.",
                "The meeting-page buttons no longer squash on narrow windows (rarely-used actions "
                "moved into the More menu).",
                "Home and a meeting no longer show as selected in the sidebar at the same time.",
            )),
        ),
    ),
    Release(
        "0.17.0", "2026-07-02", "Action items you control",
        (
            ("Changed", (
                "AI-generated action items now arrive as SUGGESTIONS: keep ✓ the real ones (only "
                "those join the Home to-do dashboard and Todoist), edit ✎ any item's wording or "
                "owner, or dismiss ✕ the misses. Suggestions are marked \"(suggested)\" in copies "
                "and shares. Items on existing meetings are treated as already accepted.",
                "The default summary is much shorter — 1-2 tight sentences instead of a paragraph. "
                "(Want it longer or different? Settings → AI → Custom instructions overrides it.)",
            )),
        ),
    ),
    Release(
        "0.16.0", "2026-07-02", "Built for marathon meetings",
        (
            ("Added", (
                "No more meeting-length limits on online transcription: files over a provider's cap "
                "are automatically split at quiet moments, transcribed in parts, and stitched back "
                "with exact timestamps — a 6-hour meeting just works.",
                "Optional Opus upload format (Settings → Transcription): ~10 MB per hour of audio — "
                "about 4× smaller than FLAC — with negligible accuracy impact. Roughly 2.5 hours now "
                "fits in a single request even on capped providers.",
                "Cloud transcription now sends both channels (you + them) in parallel, roughly halving "
                "transcription wall-clock time on Groq, Mistral, OpenAI and Deepgram. (The home server "
                "processes one at a time by design, so it stays sequential.)",
            )),
            ("Changed", (
                "Echo cancellation and audio preparation now stream file-to-file with flat memory use "
                "— multi-hour recordings can no longer exhaust RAM anywhere in the pipeline. The "
                "streamed echo canceller is bit-identical to the previous implementation.",
            )),
        ),
    ),
    Release(
        "0.15.0", "2026-07-02", "Cheaper transcription options",
        (
            ("Added", (
                "One-click provider presets for online transcription: Groq Whisper large-v3-turbo "
                "(~$0.04 per hour of audio — the cheapest accurate option), Mistral Voxtral Mini "
                "(~$0.18/hr, top-tier accuracy, 3-hour uploads), Groq large-v3 and OpenAI whisper-1. "
                "Pick one in Settings → Transcription and paste that provider's key in Settings → AI.",
                "Compatibility with Mistral's stricter API (automatic parameter fallback), so "
                "Voxtral works through the same OpenAI-compatible client.",
            )),
            ("Changed", (
                "Transcription uploads are now FLAC instead of WAV — lossless but ~3× smaller, so "
                "uploads are faster everywhere and roughly 40–60 minutes of speech now fits the "
                "25 MB OpenAI/Groq cap (previously ~13 minutes).",
                "Deepgram uploads stream from disk instead of loading the whole file into memory.",
            )),
        ),
    ),
    Release(
        "0.14.0", "2026-07-02", "Todoist sync",
        (
            ("Added", (
                "Send open action items to Todoist in one click from a meeting's page — each becomes "
                "a task with the owner and meeting in its description. Items remember they've been "
                "sent, so you can never create duplicates. Connect your token in Settings → General.",
            )),
        ),
    ),
    Release(
        "0.13.0", "2026-07-02", "Pre-meeting briefs",
        (
            ("Added", (
                "\"Prep brief from past meetings\" on the recording screen: Earshot finds your related "
                "meetings (same people or template), and writes a short brief — what was decided last "
                "time, what's still open, suggested talking points — straight into the agenda box, "
                "which also gives the AI better context for this meeting's notes.",
            )),
        ),
    ),
    Release(
        "0.12.0", "2026-07-02", "Share meetings as a file",
        (
            ("Added", (
                "Share… on a meeting's page exports a beautiful, self-contained HTML file (notes, "
                "action items, optionally the full transcript) that opens in any browser — send it to "
                "anyone, no account needed. Prints cleanly to PDF too.",
            )),
        ),
    ),
    Release(
        "0.11.0", "2026-07-02", "Bring your own AI",
        (
            ("Added", (
                "Notes and AI actions can now run on ANY OpenAI-compatible model — including fully "
                "local ones via Ollama, LM Studio or vLLM — instead of Claude. Pick the provider in "
                "Settings → AI; with a local model, meetings never leave your machine at all. "
                "(Ask Earshot still uses Claude.)",
            )),
        ),
    ),
    Release(
        "0.10.0", "2026-07-02", "Never forget to record",
        (
            ("Added", (
                "Call detection: when another app starts using your microphone (Zoom, Teams, a Meet "
                "tab…), Earshot pops a small corner prompt offering to record — one click and it's "
                "rolling with your saved devices. When the call ends mid-recording, it offers to stop "
                "and process. Toggle in Settings → General; nothing is ever captured until you accept.",
            )),
        ),
    ),
    Release(
        "0.9.0", "2026-07-02", "Bulletproof recordings",
        (
            ("Changed", (
                "Recording finalisation is now streaming and crash-safe: audio is converted to WAV in "
                "small blocks (flat memory even on multi-hour meetings — previously a 2-hour call could "
                "need gigabytes of RAM and fail), every file is written atomically, and the raw capture "
                "is only deleted after the WAVs are verified on disk. A failure mid-save can no longer "
                "destroy the meeting.",
                "In-progress audio now spools into the meeting's own folder (not the system temp dir), "
                "so it lives on your chosen recordings drive.",
                "On 5.1/7.1 output devices, the other side's audio is no longer quietened by the "
                "surround downmix.",
            )),
            ("Added", (
                "Crash recovery: if Earshot (or Windows) dies mid-recording, the next launch salvages "
                "the captured audio into a playable, transcribable meeting automatically.",
            )),
        ),
    ),
    Release(
        "0.8.0", "2026-07-02", "Hardening & polish",
        (
            ("Fixed", (
                "Background tasks (transcription, imports, AI actions) can no longer crash the app "
                "when they overlap or when you quit mid-task.",
                "Meetings interrupted by a crash or force-quit are now recovered on the next launch "
                "instead of getting stuck on \"Recording\" or \"Transcribing\" forever.",
                "Your settings can no longer be lost to a half-written file; a corrupted config is "
                "kept aside (not silently wiped) so nothing is destroyed.",
                "If a custom recordings folder is on a disconnected drive, recordings fall back to the "
                "default folder instead of losing the meeting.",
                "Home-server transcription no longer hangs forever if the server wedges or Wi-Fi drops, "
                "and the connection test no longer reports success against the wrong service.",
                "A finished recording no longer keeps its identity, so editing attendees for the next "
                "meeting can't overwrite the previous one.",
            )),
            ("Changed", (
                "Security hardening ahead of open-sourcing: meeting content is treated as untrusted "
                "data by the AI (resisting spoken \"prompt injection\"), AI-generated text is rendered "
                "as plain text, and delete/webhook actions are guarded.",
                "UI polish pass: clearer hover / pressed / focus / disabled states throughout, a "
                "distinct green for completed meetings, a call-to-action on the empty home screen, and "
                "tidier sliders, scrollbars and long-title handling.",
            )),
        ),
    ),
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
