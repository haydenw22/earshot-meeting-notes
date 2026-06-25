"""Settings page: appearance (light/dark), transcription server, notes API, and
an About tab. Cards inside tabs, matching the rest of the app.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..transcription import whisper_client
from .widgets import Card


class SettingsPage(QWidget):
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

        head = QLabel("Settings")
        head.setObjectName("H1")
        root.addWidget(head)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._general_tab(), "General")
        self.tabs.addTab(self._transcription_tab(), "Transcription")
        self.tabs.addTab(self._notes_tab(), "Notes")
        self.tabs.addTab(self._about_tab(), "About")
        root.addWidget(self.tabs, 1)

        bar = QHBoxLayout()
        bar.addStretch(1)
        self.save_btn = QPushButton("Save changes")
        self.save_btn.setProperty("variant", "primary")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.clicked.connect(self._save)
        bar.addWidget(self.save_btn)
        root.addLayout(bar)
        self.apply_theme()

    def _card(self, title: str, subtitle: str = "") -> tuple[Card, QVBoxLayout]:
        card = Card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(22, 18, 22, 20)
        lay.setSpacing(12)
        t = QLabel(title)
        t.setObjectName("H3")
        lay.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("Muted")
            s.setWordWrap(True)
            lay.addWidget(s)
        return card, lay

    def _general_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 16, 2, 2)
        lay.setSpacing(16)
        card, cl = self._card("Appearance", "Choose how the app looks.")
        row = QHBoxLayout()
        self.light_btn = QPushButton("  Light")
        self.dark_btn = QPushButton("  Dark")
        for b in (self.light_btn, self.dark_btn):
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(40)
        self.light_btn.clicked.connect(lambda: self._set_theme("light"))
        self.dark_btn.clicked.connect(lambda: self._set_theme("dark"))
        row.addWidget(self.light_btn)
        row.addWidget(self.dark_btn)
        row.addStretch(1)
        cl.addLayout(row)
        lay.addWidget(card)

        side_card, scl = self._card("Side menu", "Drag its edge to resize. Choose which side it sits on.")
        srow = QHBoxLayout()
        self.side_left_btn = QPushButton("  Left")
        self.side_right_btn = QPushButton("  Right")
        for b in (self.side_left_btn, self.side_right_btn):
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(40)
        self.side_left_btn.clicked.connect(lambda: self._set_side("left"))
        self.side_right_btn.clicked.connect(lambda: self._set_side("right"))
        srow.addWidget(self.side_left_btn)
        srow.addWidget(self.side_right_btn)
        srow.addStretch(1)
        scl.addLayout(srow)
        lay.addWidget(side_card)

        lay.addStretch(1)
        return w

    def _set_side(self, side: str) -> None:
        self.shell.set_sidebar_side(side)
        self._sync_side_buttons()

    def _sync_side_buttons(self) -> None:
        self.side_left_btn.setChecked(self.cfg.sidebar_side != "right")
        self.side_right_btn.setChecked(self.cfg.sidebar_side == "right")

    def _transcription_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 16, 2, 2)
        lay.setSpacing(16)

        # source selector + shared language
        src_card, scl = self._card("Transcription source", "Where your audio gets transcribed.")
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("Home server (self-hosted Whisper)", "home")
        self.provider_combo.addItem("Online service (OpenAI / Groq)", "online")
        self.provider_combo.setCurrentIndex(1 if self.cfg.transcription_provider == "online" else 0)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.language = QLineEdit(self.cfg.whisper_language)
        self.language.setPlaceholderText("en  (blank = auto-detect)")
        form0 = QFormLayout()
        form0.setSpacing(10)
        form0.addRow("Source", self.provider_combo)
        form0.addRow("Language", self.language)
        scl.addLayout(form0)
        lay.addWidget(src_card)

        # home server group
        self.home_card, hcl = self._card("Home server", "Your self-hosted whisper-asr-webservice on the LAN.")
        hform = QFormLayout()
        hform.setSpacing(10)
        self.whisper_url = QLineEdit(self.cfg.whisper_url)
        self.whisper_url.setPlaceholderText("http://<your-server-ip>:9000")
        hform.addRow("Server URL", self.whisper_url)
        hcl.addLayout(hform)
        lay.addWidget(self.home_card)

        # online service group
        self.online_card, ocl = self._card("Online service", "Any OpenAI-compatible audio API. Use a Whisper model so timestamps come back.")
        oform = QFormLayout()
        oform.setSpacing(10)
        self.online_base_url = QLineEdit(self.cfg.online_base_url)
        self.online_key = QLineEdit(self.cfg.online_api_key)
        self.online_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.online_key.setPlaceholderText("sk-…  (or set OPENAI_API_KEY)")
        self.online_model = QLineEdit(self.cfg.online_model)
        oform.addRow("Base URL", self.online_base_url)
        oform.addRow("API key", self.online_key)
        oform.addRow("Model", self.online_model)
        ocl.addLayout(oform)
        hint = QLabel("OpenAI → https://api.openai.com/v1 · whisper-1      Groq → https://api.groq.com/openai/v1 · whisper-large-v3")
        hint.setObjectName("Faint")
        hint.setWordWrap(True)
        ocl.addWidget(hint)
        lay.addWidget(self.online_card)

        # test connection (uses the selected source)
        test_row = QHBoxLayout()
        self.test_btn = QPushButton("Test connection")
        self.test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test_btn.clicked.connect(self._test)
        self.test_label = QLabel("")
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.test_label)
        test_row.addStretch(1)
        lay.addLayout(test_row)
        lay.addStretch(1)
        self._on_provider_changed()
        return w

    def _provider(self) -> str:
        return self.provider_combo.currentData()

    def _on_provider_changed(self, *_) -> None:
        online = self._provider() == "online"
        self.home_card.setVisible(not online)
        self.online_card.setVisible(online)

    def _notes_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 16, 2, 2)
        lay.setSpacing(16)
        card, cl = self._card("Claude (Anthropic)", "Used to turn the transcript into notes. Stored locally.")
        form = QFormLayout()
        form.setSpacing(10)
        self.api_key = QLineEdit(self.cfg.anthropic_api_key)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("sk-ant-…  (or set ANTHROPIC_API_KEY)")
        self.model = QLineEdit(self.cfg.anthropic_model)
        form.addRow("API key", self.api_key)
        form.addRow("Model", self.model)
        cl.addLayout(form)
        lay.addWidget(card)
        lay.addStretch(1)
        return w

    def _about_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 16, 2, 2)
        lay.setSpacing(16)
        card, cl = self._card("Earshot", f"Version {__version__}")
        body = QLabel(
            "Earshot records you and the other side on separate channels, transcribes on "
            "your home server or an online service, and writes notes with Claude Haiku. "
            "Your meetings stay on this PC."
        )
        body.setObjectName("Muted")
        body.setWordWrap(True)
        cl.addWidget(body)
        lay.addWidget(card)
        lay.addStretch(1)
        return w

    # ---------- behaviour ----------
    def _set_theme(self, mode: str) -> None:
        self.theme.set_mode(mode)
        self._sync_theme_buttons()

    def _sync_theme_buttons(self) -> None:
        self.light_btn.setChecked(self.theme.mode == "light")
        self.dark_btn.setChecked(self.theme.mode == "dark")

    def _test(self) -> None:
        import os

        self.test_label.setText("Testing…")
        self.test_label.repaint()
        if self._provider() == "online":
            from ..transcription import openai_client
            key = self.online_key.text().strip() or os.environ.get("OPENAI_API_KEY", "")
            ok = openai_client.ping(self.online_base_url.text().strip(), key)
        else:
            ok = whisper_client.ping(self.whisper_url.text().strip())
        self.test_label.setText("✓ Connected" if ok else "✗ Could not connect")
        self.test_label.setStyleSheet(
            f"color:{self.theme.color('primary' if ok else 'danger')}; font-weight:600;"
        )

    def _save(self) -> None:
        self.cfg.transcription_provider = self._provider()
        self.cfg.whisper_language = self.language.text().strip()
        self.cfg.whisper_url = self.whisper_url.text().strip() or self.cfg.whisper_url
        self.cfg.online_base_url = self.online_base_url.text().strip() or self.cfg.online_base_url
        self.cfg.online_api_key = self.online_key.text().strip()
        self.cfg.online_model = self.online_model.text().strip() or "whisper-1"
        self.cfg.anthropic_api_key = self.api_key.text().strip()
        self.cfg.anthropic_model = self.model.text().strip() or "claude-haiku-4-5"
        self.cfg.save()
        self.save_btn.setText("Saved ✓")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1400, lambda: self.save_btn.setText("Save changes"))

    def apply_theme(self) -> None:
        self.light_btn.setIcon(self.theme.icon("sun", "text", 18))
        self.dark_btn.setIcon(self.theme.icon("moon", "text", 18))
        self.side_left_btn.setIcon(self.theme.icon("chevron-left", "text", 16))
        self.side_right_btn.setIcon(self.theme.icon("chevron-right", "text", 16))
        self.test_btn.setIcon(self.theme.icon("globe", "text_muted", 16))
        self._sync_theme_buttons()
        self._sync_side_buttons()
