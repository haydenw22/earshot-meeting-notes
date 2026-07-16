"""Help Center: an in-app guide to recording, transcription, AI notes and
everything in between, plus links for support.

Opened from the sidebar Help button. Static content only: no network, nothing
to save.
"""
from __future__ import annotations

import sys
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .widgets import Card, clear_layout

WEBSITE_URL = "https://tryearshot.app"
ISSUES_URL = "https://github.com/haydenw22/earshot-meeting-notes/issues"
CHANGELOG_URL = "https://github.com/haydenw22/earshot-meeting-notes/blob/main/CHANGELOG.md"

# (title, [(step/bullet heading, body), ...]) rendered as numbered steps
_GETTING_STARTED = [
    ("Set up transcription",
     "Open Settings, then Transcription, and pick where your audio gets transcribed: "
     "your own home server, an online service like Groq or Deepgram, or Earshot Plus "
     "if you'd rather skip the setup entirely."),
    ("Start a recording",
     "Click New recording in the sidebar. Earshot captures your microphone and the "
     "other side's audio on separate channels, so the transcript always knows who "
     "said what."),
    ("Let the notes write themselves",
     "When you stop, the recording is transcribed and the AI writes a summary, the "
     "decisions and suggested action items. You approve, edit or dismiss each "
     "suggestion, so nothing lands on your to-do list without your say-so."),
    ("Ask Earshot anything",
     "Use Ask Earshot in the sidebar to question your whole meeting history: "
     "\"what did we decide about pricing?\" or \"what's still open with Sam?\"."),
]

_SECTIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("mic", "Recording", [
        ("Dual-channel capture",
         "Your mic and the system audio are recorded separately. Wear headphones for "
         "the cleanest split; if you must use speakers, Earshot's echo cancellation "
         "keeps the channels apart."),
        ("Call detection",
         "When another app starts using your microphone (Zoom, Teams, a Meet tab), "
         "Earshot offers to record so you never forget to hit the button. Nothing is "
         "captured until you accept. Turn this off in Settings, then General."),
        ("The recording overlay",
         "A small always-on-top bar shows a timer and two lights that glow with your "
         "mic and the system audio, so you can see at a glance that both sides are "
         "being heard. Drag it anywhere."),
        ("Import existing files",
         "Already have a recording? Import file in the sidebar accepts audio and "
         "video (mp3, wav, m4a, mp4 and more) and runs it through the same "
         "transcription and notes pipeline."),
    ]),
    ("globe", "Transcription", [
        ("Home server",
         "Point Earshot at your own whisper-asr-webservice and transcription never "
         "leaves your network. Skip-silence (VAD) makes dual-channel jobs much "
         "faster."),
        ("Online services",
         "Any OpenAI-compatible audio API works: Groq is the cheapest at roughly "
         "$0.04 an hour, Mistral Voxtral has top accuracy, and Deepgram handles "
         "very long meetings with no upload cap."),
        ("No length limits",
         "Marathon meetings are split at natural silences and transcribed in "
         "parallel chunks, so even multi-hour recordings go through online "
         "providers."),
    ]),
    ("sparkles", "AI notes and actions", [
        ("Bring your own model",
         "Notes, briefs and Ask Earshot run on the provider you choose: Anthropic, "
         "any OpenAI-compatible cloud, or a fully local model through Ollama or "
         "LM Studio."),
        ("Action items are suggestions",
         "The AI proposes action items; you keep, edit or dismiss each one. Only "
         "accepted items reach your to-do list and Todoist."),
        ("Templates and custom instructions",
         "Create note templates per meeting type (Sales call, Standup, 1:1) and add "
         "global custom instructions like \"Use British English\" in Settings, "
         "then AI."),
        ("Prep briefs",
         "Before a meeting, generate a brief built from your past meetings so you "
         "walk in knowing what was said last time."),
    ]),
    ("folder", "Projects and organisation", [
        ("Projects",
         "Group meetings into colour-coded projects in the sidebar. Drag a meeting "
         "onto a project to file it, or drag it back out to unfile it."),
        ("Search everything",
         "The sidebar search looks through titles, transcripts, notes, attendees "
         "and agendas, not just meeting names."),
    ]),
    ("zap", "Integrations", [
        ("Todoist",
         "Send accepted action items (due dates included) to Todoist from any "
         "meeting's page. Set your token in Settings, then Integrations."),
        ("Webhook",
         "POST each finished meeting as JSON to any URL. Zapier, Make, n8n, Slack "
         "or your own automation can take it from there."),
    ]),
    ("info", "Troubleshooting", [
        ("A channel shows no audio",
         "Check the two lights on the recording overlay. If the system-audio light "
         "stays dark, " + (
             "make sure Earshot is allowed under System Settings, then Privacy and "
             "Security, then Screen and System Audio Recording."
             if sys.platform == "darwin" else
             "pick the right loopback device in Settings, then Audio.")),
        ("Transcription fails",
         "Use Test connection in Settings, then Transcription. It checks the exact "
         "server and key you've entered without freezing the app."),
        ("Where is my data?",
         "Everything lives on this computer. Recordings and screenshots are in the "
         "storage folder (Settings, then Account, then Open storage folder); the "
         "database and settings sit in the app's data folder."),
        ("A crash interrupted a recording",
         "Earshot recovers interrupted recordings on the next launch. Open the "
         "meeting and hit Re-transcribe to pick up where it left off."),
    ]),
]


class HelpPage(QWidget):
    def __init__(self, shell, repo, cfg, theme):
        super().__init__()
        self.shell = shell
        self.cfg = cfg
        self.theme = theme
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 28, 40, 28)
        root.setSpacing(16)

        head = QLabel("Help Center")
        head.setObjectName("H1")
        root.addWidget(head)
        sub = QLabel("Everything you need to get the most out of Earshot.")
        sub.setObjectName("Muted")
        root.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        host = QWidget()
        self._host_lay = QVBoxLayout(host)
        self._host_lay.setContentsMargins(2, 16, 2, 2)
        self._host_lay.setSpacing(16)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        self._rebuild_cards()

    def _rebuild_cards(self) -> None:
        """(Re)build the guide cards. Step badges and section icons bake theme
        colours into stylesheets/pixmaps, so a theme flip rebuilds the content."""
        lay = self._host_lay
        clear_layout(lay)
        lay.addWidget(self._getting_started_card())
        for icon_name, title, entries in _SECTIONS:
            lay.addWidget(self._section_card(icon_name, title, entries))
        lay.addWidget(self._contact_card())
        lay.addStretch(1)
        self.site_btn.setIcon(icons.icon("external-link", self.theme.color("text_muted"), 15))
        self.issue_btn.setIcon(icons.icon("alert-triangle", self.theme.color("text_muted"), 15))

    def _card(self, title: str, subtitle: str = "") -> tuple[Card, QVBoxLayout]:
        card = Card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(22, 18, 22, 20)
        cl.setSpacing(12)
        t = QLabel(title)
        t.setObjectName("H3")
        cl.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("Muted")
            s.setWordWrap(True)
            cl.addWidget(s)
        return card, cl

    # ---------- getting started (numbered steps) ----------
    def _getting_started_card(self) -> Card:
        card, cl = self._card("Getting started", "From zero to your first meeting notes in four steps.")
        for i, (title, body) in enumerate(_GETTING_STARTED, start=1):
            cl.addWidget(self._step_row(i, title, body))
        return card

    def _step_row(self, number: int, title: str, body: str) -> QWidget:
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 4, 0, 4)
        rl.setSpacing(14)

        badge = QLabel(str(number))
        badge.setFixedSize(28, 28)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background:{self.theme.color('primary_soft')}; color:{self.theme.color('primary')};"
            f"border-radius:14px; font-size:13px; font-weight:700;"
        )
        rl.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        t = QLabel(title)
        t.setObjectName("H3")
        texts.addWidget(t)
        b = QLabel(body)
        b.setObjectName("Muted")
        b.setWordWrap(True)
        texts.addWidget(b)
        rl.addLayout(texts, 1)
        return row

    # ---------- topic sections ----------
    def _section_card(self, icon_name: str, title: str, entries: list[tuple[str, str]]) -> Card:
        card = Card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(22, 18, 22, 20)
        cl.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(icons.pixmap(icon_name, self.theme.color("primary"), 18))
        head.addWidget(ic)
        t = QLabel(title)
        t.setObjectName("H3")
        head.addWidget(t)
        head.addStretch(1)
        cl.addLayout(head)

        for sub_title, body in entries:
            st = QLabel(sub_title)
            st.setStyleSheet(f"font-weight:600; color:{self.theme.color('text')};")
            cl.addWidget(st)
            b = QLabel(body)
            b.setObjectName("Muted")
            b.setWordWrap(True)
            b.setContentsMargins(0, 0, 0, 6)
            cl.addWidget(b)
        return card

    # ---------- get in touch ----------
    def _contact_card(self) -> Card:
        card, cl = self._card(
            "Still stuck?",
            "The website has more guides, and bug reports and feature requests are always welcome.",
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        self.site_btn = QPushButton("  Visit tryearshot.app")
        self.site_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.site_btn.clicked.connect(lambda: self._open_url(WEBSITE_URL))
        row.addWidget(self.site_btn)
        self.issue_btn = QPushButton("  Report an issue")
        self.issue_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.issue_btn.clicked.connect(lambda: self._open_url(ISSUES_URL))
        row.addWidget(self.issue_btn)
        row.addStretch(1)
        cl.addLayout(row)
        return card

    def _open_url(self, url: str) -> None:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def apply_theme(self) -> None:
        self._rebuild_cards()
