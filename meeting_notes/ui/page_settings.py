"""Settings: appearance, audio devices + storage, transcription, AI, and About.

Cards inside tabs, matching the rest of the app.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, changelog, paths
from ..audio import devices as dev
from ..capture import screen as screen_capture
from ..transcription import whisper_client
from .named_list import NamedListManager
from .widgets import Card, calm_scroll_children, run_connection_test

# One-click fills for the OpenAI-compatible transcription provider.
# (label, base_url, model) — prices as of mid-2026, shown for orientation.
ONLINE_PRESETS = [
    ("Groq — Whisper large-v3-turbo  (~$0.04/hr — cheapest)",
     "https://api.groq.com/openai/v1", "whisper-large-v3-turbo"),
    ("Mistral — Voxtral Mini  (~$0.18/hr — top accuracy)",
     "https://api.mistral.ai/v1", "voxtral-mini-latest"),
    ("Groq — Whisper large-v3  (~$0.11/hr)",
     "https://api.groq.com/openai/v1", "whisper-large-v3"),
    ("OpenAI — whisper-1  (~$0.36/hr)",
     "https://api.openai.com/v1", "whisper-1"),
]


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
        self._build_tabs()
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

    def _build_tabs(self) -> None:
        """(Re)build the tab set for the current account mode. In cloud mode the
        Transcription and AI tabs are hidden — Earshot Plus manages both, so
        there's nothing to configure — leaving General / Audio / About."""
        # clear() detaches the old pages but never destroys them — they stay
        # alive parented under the tab widget's internal stack, so every
        # sign-in/out and "Run setup guide again" leaked ~one full tab set.
        # Delete them explicitly.
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.tabs.clear()
        self._cloud_mode = self.cfg.account_mode == "cloud"
        self.tabs.addTab(self._general_tab(), "General")
        self.tabs.addTab(self._audio_tab(), "Audio")
        if not self._cloud_mode:
            self.tabs.addTab(self._transcription_tab(), "Transcription")
            self.tabs.addTab(self._ai_tab(), "AI")
        self.tabs.addTab(self._about_tab(), "About")
        # scrolling the page must never change a control it passes over.
        # Sweep self.tabs (not self): at initial build the tab widget isn't
        # parented into the page yet, so findChildren(self) would find nothing.
        calm_scroll_children(self.tabs)

    def refresh_tabs(self) -> None:
        """Rebuild the tabs after the account mode changes (sign in / out), so the
        Transcription and AI tabs appear or disappear immediately."""
        self._build_tabs()
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

    def _tab(self) -> tuple[QWidget, QVBoxLayout]:
        outer_w = QWidget()
        outer = QVBoxLayout(outer_w)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(2, 16, 2, 2)
        lay.setSpacing(16)
        scroll.setWidget(host)
        outer.addWidget(scroll)
        return outer_w, lay

    # ---------- General ----------
    def _general_tab(self) -> QWidget:
        w, lay = self._tab()
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

        home_card, hcl = self._card("Home page", "What appears on the home page.")
        self.dashboard_toggle = QCheckBox("Show pending action items from past meetings")
        self.dashboard_toggle.setChecked(self.cfg.show_dashboard)
        hcl.addWidget(self.dashboard_toggle)
        lay.addWidget(home_card)

        cd_card, cdl = self._card(
            "Call detection",
            "When another app starts using your microphone (Zoom, Teams, a Meet tab…), Earshot "
            "offers to record — so you never forget to hit Record. Nothing is captured until "
            "you accept.",
        )
        self.call_detect = QCheckBox("Offer to record when a call starts")
        self.call_detect.setChecked(self.cfg.call_detect_enabled)
        cdl.addWidget(self.call_detect)
        lay.addWidget(cd_card)

        ov_card, ovl = self._card(
            "Recording overlay",
            "A small always-on-top bar shown while recording: a timer plus two lights that glow "
            "with your mic and the system audio. Drag it anywhere — it works across monitors.",
        )
        self.overlay_enabled = QCheckBox("Show the recording overlay while recording")
        self.overlay_enabled.setChecked(self.cfg.overlay_enabled)
        ovl.addWidget(self.overlay_enabled)
        op_row = QHBoxLayout()
        op_lbl = QLabel("Opacity")
        op_lbl.setObjectName("Muted")
        op_lbl.setFixedWidth(64)
        self.overlay_opacity = QSlider(Qt.Orientation.Horizontal)
        self.overlay_opacity.setRange(40, 100)
        self.overlay_opacity.setValue(int(round(self.cfg.overlay_opacity * 100)))
        self.overlay_opacity_val = QLabel(f"{self.overlay_opacity.value()}%")
        self.overlay_opacity_val.setObjectName("Faint")
        self.overlay_opacity_val.setFixedWidth(44)
        self.overlay_opacity.valueChanged.connect(
            lambda v: self.overlay_opacity_val.setText(f"{v}%")
        )
        op_row.addWidget(op_lbl)
        op_row.addWidget(self.overlay_opacity, 1)
        op_row.addWidget(self.overlay_opacity_val)
        ovl.addLayout(op_row)
        reset_row = QHBoxLayout()
        self.overlay_reset_btn = QPushButton("Reset position")
        self.overlay_reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.overlay_reset_btn.clicked.connect(self._reset_overlay_position)
        self.overlay_reset_label = QLabel("")
        self.overlay_reset_label.setObjectName("Faint")
        reset_row.addWidget(self.overlay_reset_btn)
        reset_row.addWidget(self.overlay_reset_label)
        reset_row.addStretch(1)
        ovl.addLayout(reset_row)
        lay.addWidget(ov_card)

        guide_card, gcl = self._card(
            "Setup guide",
            "Walk through choosing self-host or Earshot Plus and setting up transcription and AI "
            "again — your current settings are prefilled and never overwritten unless you change them.",
        )
        self.run_guide_btn = QPushButton("Run setup guide again")
        self.run_guide_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_guide_btn.clicked.connect(self._run_setup_guide)
        gcl.addWidget(self.run_guide_btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(guide_card)

        lay.addStretch(1)
        return w

    def _run_setup_guide(self) -> None:
        if hasattr(self.shell, "run_onboarding"):
            self.shell.run_onboarding()

    def _on_online_preset(self, *_) -> None:
        data = self.online_preset.currentData()
        if data:
            base, model = data
            self.online_base_url.setText(base)
            self.online_model.setText(model)

    def _on_notes_provider_changed(self, *_) -> None:
        provider = self.notes_provider_combo.currentData()
        self.claude_card.setVisible(provider == "anthropic")
        self.llm_card.setVisible(provider == "openai")
        self.local_card.setVisible(provider == "local")

    def _test_llm(self) -> None:
        from ..notes import openai_llm
        base, key = self.llm_base_url.text().strip(), self.llm_key.text().strip()
        run_connection_test(self, self.llm_test_btn, self.llm_test_label, self.theme,
                            lambda: openai_llm.ping(base, key))

    def _test_local_llm(self) -> None:
        from ..notes import openai_llm
        base, key = self.local_base_url.text().strip(), self.local_key.text().strip()
        run_connection_test(self, self.local_test_btn, self.local_test_label, self.theme,
                            lambda: openai_llm.ping(base, key))

    def _reset_overlay_position(self) -> None:
        from ..config import OVERLAY_AUTO_POS
        self.cfg.overlay_pos_x = OVERLAY_AUTO_POS
        self.cfg.overlay_pos_y = OVERLAY_AUTO_POS
        self.cfg.save()
        # if an overlay is currently up (recording), move it back immediately
        ov = getattr(self.shell.record, "overlay", None)
        if ov is not None:
            ov._place()
        self.overlay_reset_label.setText("Moved back to the top-right.")

    def _set_side(self, side: str) -> None:
        self.shell.set_sidebar_side(side)
        self._sync_side_buttons()

    def _sync_side_buttons(self) -> None:
        self.side_left_btn.setChecked(self.cfg.sidebar_side != "right")
        self.side_right_btn.setChecked(self.cfg.sidebar_side == "right")

    # ---------- Audio ----------
    def _audio_tab(self) -> QWidget:
        w, lay = self._tab()

        card, cl = self._card("Default devices", "Pre-selected when you start a new recording.")
        form = QFormLayout()
        form.setSpacing(10)
        self.mic_combo = QComboBox()
        self.them_combo = QComboBox()
        self._fill_device_combo(self.mic_combo, dev.list_input_devices(), self.cfg.mic_device_name)
        self._fill_device_combo(self.them_combo, dev.list_loopback_devices(), self.cfg.loopback_device_name)
        form.addRow("Microphone", self.mic_combo)
        form.addRow("System audio", self.them_combo)
        self.monitor_combo = QComboBox()
        for i, label in enumerate(screen_capture.list_monitors(), start=1):
            self.monitor_combo.addItem(label, i)
        idx = max(0, self.cfg.screen_monitor - 1)
        if idx < self.monitor_combo.count():
            self.monitor_combo.setCurrentIndex(idx)
        form.addRow("Screen-capture monitor", self.monitor_combo)
        cl.addLayout(form)
        lay.addWidget(card)

        store_card, stl = self._card(
            "Recordings & screenshots folder",
            "Where audio and screenshots are saved. A subfolder is created per meeting. The "
            "database and settings stay in the app folder.",
        )
        self.data_dir_label = QLabel(self.cfg.data_dir or "(default app folder)")
        self.data_dir_label.setObjectName("Muted")
        self.data_dir_label.setWordWrap(True)
        stl.addWidget(self.data_dir_label)
        brow = QHBoxLayout()
        pick = QPushButton("Choose folder…")
        pick.setCursor(Qt.CursorShape.PointingHandCursor)
        pick.clicked.connect(self._pick_data_dir)
        reset = QPushButton("Use default")
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(lambda: self._set_data_dir(""))
        brow.addWidget(pick)
        brow.addWidget(reset)
        brow.addStretch(1)
        stl.addLayout(brow)
        lay.addWidget(store_card)
        lay.addStretch(1)
        return w

    @staticmethod
    def _fill_device_combo(combo: QComboBox, devices: list, current_name) -> None:
        combo.addItem("System default", None)
        for d in devices:
            combo.addItem(d.name, d.name)
        if current_name:
            i = combo.findData(current_name)
            if i >= 0:
                combo.setCurrentIndex(i)

    def _pick_data_dir(self) -> None:
        start = self.cfg.data_dir or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Choose recordings folder", start)
        if folder:
            self._set_data_dir(folder)

    def _set_data_dir(self, folder: str) -> None:
        self.cfg.data_dir = folder
        paths.set_recordings_dir(folder)
        self.cfg.save()
        self.data_dir_label.setText(folder or "(default app folder)")

    # ---------- Transcription ----------
    def _transcription_tab(self) -> QWidget:
        w, lay = self._tab()

        src_card, scl = self._card("Transcription source", "Where your audio gets transcribed.")
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("Home server (self-hosted Whisper)", "home")
        self.provider_combo.addItem("Online service (OpenAI / Groq)", "online")
        self.provider_combo.addItem("Deepgram", "deepgram")
        _pi = self.provider_combo.findData(self.cfg.transcription_provider)
        self.provider_combo.setCurrentIndex(_pi if _pi >= 0 else 0)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.language = QLineEdit(self.cfg.whisper_language)
        self.language.setPlaceholderText("en  (blank = auto-detect)")
        self.upload_codec = QComboBox()
        self.upload_codec.addItem("FLAC — lossless (default)", "flac")
        self.upload_codec.addItem("Opus — ~4× smaller uploads (~10 MB/hour)", "opus")
        self.upload_codec.setCurrentIndex(1 if self.cfg.upload_codec == "opus" else 0)
        form0 = QFormLayout()
        form0.setSpacing(10)
        form0.addRow("Source", self.provider_combo)
        form0.addRow("Language", self.language)
        form0.addRow("Upload format", self.upload_codec)
        scl.addLayout(form0)
        self.auto_transcribe = QCheckBox("Automatically transcribe when a recording stops")
        self.auto_transcribe.setChecked(self.cfg.auto_transcribe)
        scl.addWidget(self.auto_transcribe)
        lay.addWidget(src_card)

        self.home_card, hcl = self._card("Home server", "Your self-hosted whisper-asr-webservice on the LAN.")
        hform = QFormLayout()
        hform.setSpacing(10)
        self.whisper_url = QLineEdit(self.cfg.whisper_url)
        self.whisper_url.setPlaceholderText("http://<your-server-ip>:9000")
        hform.addRow("Server URL", self.whisper_url)
        hcl.addLayout(hform)
        self.whisper_vad = QCheckBox("Skip silence (VAD) — faster transcription")
        self.whisper_vad.setChecked(self.cfg.whisper_vad_filter)
        hcl.addWidget(self.whisper_vad)
        vad_hint = QLabel(
            "Transcribes only speech and skips silent stretches — a big speed-up for dual-channel "
            "recordings (each side is silent while the other talks). Needs the faster-whisper engine "
            "on the server; ignored otherwise."
        )
        vad_hint.setObjectName("Faint")
        vad_hint.setWordWrap(True)
        hcl.addWidget(vad_hint)
        lay.addWidget(self.home_card)

        self.online_card, ocl = self._card(
            "Online service",
            "Any OpenAI-compatible audio API — pick a preset or enter your own. "
            "The API key lives in the AI tab (\"Online transcription key\").",
        )
        oform = QFormLayout()
        oform.setSpacing(10)
        self.online_preset = QComboBox()
        self.online_preset.addItem("Custom…", None)
        for label, base, model in ONLINE_PRESETS:
            self.online_preset.addItem(label, (base, model))
        self.online_base_url = QLineEdit(self.cfg.online_base_url)
        self.online_model = QLineEdit(self.cfg.online_model)
        # reflect the saved config in the preset combo when it matches one
        for i, (_l, base, model) in enumerate(ONLINE_PRESETS, start=1):
            if self.cfg.online_base_url == base and self.cfg.online_model == model:
                self.online_preset.setCurrentIndex(i)
                break
        self.online_preset.currentIndexChanged.connect(self._on_online_preset)
        oform.addRow("Preset", self.online_preset)
        oform.addRow("Base URL", self.online_base_url)
        oform.addRow("Model", self.online_model)
        ocl.addLayout(oform)
        hint = QLabel(
            "Uploads are 16 kHz FLAC (~40–60 min of speech fits the 25 MB OpenAI/Groq cap). "
            "For very long meetings use the home server or Deepgram. Paste the matching "
            "provider's API key in Settings → AI."
        )
        hint.setObjectName("Faint")
        hint.setWordWrap(True)
        ocl.addWidget(hint)
        lay.addWidget(self.online_card)

        self.deepgram_card, dcl = self._card(
            "Deepgram", "Fast, accurate cloud transcription with no upload-size limit — good for long meetings."
        )
        dform = QFormLayout()
        dform.setSpacing(10)
        self.deepgram_key = QLineEdit(self.cfg.deepgram_api_key)
        self.deepgram_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.deepgram_key.setPlaceholderText("Deepgram API key  (or set DEEPGRAM_API_KEY)")
        self.deepgram_model = QLineEdit(self.cfg.deepgram_model)
        self.deepgram_model.setPlaceholderText("nova-2")
        dform.addRow("API key", self.deepgram_key)
        dform.addRow("Model", self.deepgram_model)
        dcl.addLayout(dform)
        dhint = QLabel("Models: nova-2 (recommended, multilingual) · nova-3 (latest, English). Get a key at deepgram.com.")
        dhint.setObjectName("Faint")
        dhint.setWordWrap(True)
        dcl.addWidget(dhint)
        lay.addWidget(self.deepgram_card)

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
        provider = self._provider()
        self.home_card.setVisible(provider == "home")
        self.online_card.setVisible(provider == "online")
        self.deepgram_card.setVisible(provider == "deepgram")

    def _test(self) -> None:
        # capture inputs on the GUI thread; the ping runs in a worker so a dead
        # server can't freeze the app for the timeout
        provider = self._provider()
        if provider == "online":
            from ..transcription import openai_client
            base = self.online_base_url.text().strip()
            key = self.online_key.text().strip() or os.environ.get("OPENAI_API_KEY", "")
            probe = lambda: openai_client.ping(base, key)  # noqa: E731
        elif provider == "deepgram":
            from ..transcription import deepgram_client
            key = self.deepgram_key.text().strip() or os.environ.get("DEEPGRAM_API_KEY", "")
            probe = lambda: deepgram_client.ping(key)  # noqa: E731
        else:
            url = self.whisper_url.text().strip()
            probe = lambda: whisper_client.ping(url)  # noqa: E731
        run_connection_test(self, self.test_btn, self.test_label, self.theme, probe)

    # ---------- AI ----------
    def _ai_tab(self) -> QWidget:
        w, lay = self._tab()

        prov_card, pcl = self._card(
            "AI model",
            "Powers meeting notes, AI actions, briefs and Ask Earshot. Transcription is "
            "configured separately.",
        )
        self.notes_provider_combo = QComboBox()
        self.notes_provider_combo.addItem("Anthropic (Claude)", "anthropic")
        self.notes_provider_combo.addItem("OpenAI or compatible cloud", "openai")
        self.notes_provider_combo.addItem("Local (Ollama, LM Studio…)", "local")
        _npi = self.notes_provider_combo.findData(self.cfg.notes_provider)
        self.notes_provider_combo.setCurrentIndex(_npi if _npi >= 0 else 0)
        self.notes_provider_combo.currentIndexChanged.connect(self._on_notes_provider_changed)
        pform = QFormLayout()
        pform.setSpacing(10)
        pform.addRow("Provider", self.notes_provider_combo)
        pcl.addLayout(pform)
        self.auto_summary = QCheckBox("Automatically generate notes after transcription")
        self.auto_summary.setChecked(self.cfg.auto_summary)
        pcl.addWidget(self.auto_summary)
        lay.addWidget(prov_card)

        card, cl = self._card("Anthropic (Claude)", "Uses your Anthropic API key. Stored locally.")
        form = QFormLayout()
        form.setSpacing(10)
        self.api_key = QLineEdit(self.cfg.anthropic_api_key)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("sk-ant-…  (or set ANTHROPIC_API_KEY)")
        self.model = QLineEdit(self.cfg.anthropic_model)
        form.addRow("API key", self.api_key)
        form.addRow("Model", self.model)
        cl.addLayout(form)
        self.claude_card = card
        lay.addWidget(card)

        self.llm_card, lcl = self._card(
            "OpenAI or compatible cloud",
            "Any hosted OpenAI-compatible chat API — OpenAI itself, or a compatible gateway.",
        )
        lform = QFormLayout()
        lform.setSpacing(10)
        self.llm_base_url = QLineEdit(self.cfg.llm_base_url)
        self.llm_base_url.setPlaceholderText("https://api.openai.com/v1")
        self.llm_model = QLineEdit(self.cfg.llm_model)
        self.llm_model.setPlaceholderText("gpt-4o-mini  ·  or any model your endpoint serves")
        self.llm_key = QLineEdit(self.cfg.llm_api_key)
        self.llm_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.llm_key.setPlaceholderText("API key")
        lform.addRow("Base URL", self.llm_base_url)
        lform.addRow("Model", self.llm_model)
        lform.addRow("API key", self.llm_key)
        lcl.addLayout(lform)
        llm_row = QHBoxLayout()
        self.llm_test_btn = QPushButton("Test connection")
        self.llm_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.llm_test_btn.clicked.connect(self._test_llm)
        self.llm_test_label = QLabel("")
        llm_row.addWidget(self.llm_test_btn)
        llm_row.addWidget(self.llm_test_label)
        llm_row.addStretch(1)
        lcl.addLayout(llm_row)
        lay.addWidget(self.llm_card)

        self.local_card, loc_cl = self._card(
            "Local",
            "Run notes fully locally — Ollama, LM Studio, vLLM — or any other OpenAI-compatible "
            "server on your machine or LAN.",
        )
        loc_form = QFormLayout()
        loc_form.setSpacing(10)
        self.local_base_url = QLineEdit(self.cfg.local_llm_base_url)
        self.local_base_url.setPlaceholderText("http://localhost:11434/v1")
        self.local_model = QLineEdit(self.cfg.local_llm_model)
        self.local_model.setPlaceholderText("llama3.1  ·  qwen2.5:14b")
        self.local_key = QLineEdit(self.cfg.local_llm_api_key)
        self.local_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.local_key.setPlaceholderText("optional — local servers usually need none")
        loc_form.addRow("Base URL", self.local_base_url)
        loc_form.addRow("Model", self.local_model)
        loc_form.addRow("API key", self.local_key)
        loc_cl.addLayout(loc_form)
        loc_row = QHBoxLayout()
        self.local_test_btn = QPushButton("Test connection")
        self.local_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.local_test_btn.clicked.connect(self._test_local_llm)
        self.local_test_label = QLabel("")
        loc_row.addWidget(self.local_test_btn)
        loc_row.addWidget(self.local_test_label)
        loc_row.addStretch(1)
        loc_cl.addLayout(loc_row)
        lhint = QLabel("Ollama: install from ollama.com, then `ollama pull llama3.1` — the base URL above is its default.")
        lhint.setObjectName("Faint")
        lhint.setWordWrap(True)
        loc_cl.addWidget(lhint)
        lay.addWidget(self.local_card)
        self._on_notes_provider_changed()

        oa_card, ocl = self._card(
            "Online transcription key",
            "The API key for the online transcription provider chosen in the Transcription tab "
            "(Groq, Mistral or OpenAI — paste whichever provider's key you use).",
        )
        oform = QFormLayout()
        oform.setSpacing(10)
        self.online_key = QLineEdit(self.cfg.online_api_key)
        self.online_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.online_key.setPlaceholderText("sk-…  (or set OPENAI_API_KEY)")
        oform.addRow("API key", self.online_key)
        ocl.addLayout(oform)
        lay.addWidget(oa_card)

        ci_card, cicl = self._card(
            "Custom instructions",
            "Append your own guidance to every AI summary (tone, perspective, British English, "
            "what to emphasise). Off by default — the standard notes are used.",
        )
        self.ci_toggle = QCheckBox("Use my custom instructions")
        self.ci_toggle.setChecked(self.cfg.custom_instructions_enabled)
        cicl.addWidget(self.ci_toggle)
        self.ci_text = QPlainTextEdit(self.cfg.custom_instructions)
        self.ci_text.setPlaceholderText(
            "e.g. Use British English. Emphasise decisions and numbers. Refer to me as 'the vendor', not the buyer."
        )
        self.ci_text.setMinimumHeight(90)
        cicl.addWidget(self.ci_text)
        lay.addWidget(ci_card)

        brief_card, bcl = self._card(
            "Prep brief",
            "How \"Prep brief from past meetings\" on the recording screen picks which past "
            "meetings to draw from.",
        )
        self.brief_mode_combo = QComboBox()
        self.brief_mode_combo.addItem("Basic — last/related 3 meetings", "basic")
        self.brief_mode_combo.addItem("Advanced — choose a folder or specific meetings", "advanced")
        _bmi = self.brief_mode_combo.findData(self.cfg.brief_mode)
        self.brief_mode_combo.setCurrentIndex(_bmi if _bmi >= 0 else 0)
        bform = QFormLayout()
        bform.setSpacing(10)
        bform.addRow("Mode", self.brief_mode_combo)
        bcl.addLayout(bform)
        lay.addWidget(brief_card)

        tpl_card, tplcl = self._card(
            "Note templates",
            "Per-meeting-type instructions you can choose on the recording screen (Sales call, "
            "Standup, 1:1, …). The chosen template steers that meeting's notes.",
        )
        self.templates_mgr = NamedListManager(
            self.cfg.templates, text_key="instructions",
            name_ph="Template name (e.g. Sales call)",
            text_ph="Instructions for this meeting type — e.g. 'Focus on objections, budget, next steps, and the decision-maker.'",
        )
        tplcl.addWidget(self.templates_mgr)
        lay.addWidget(tpl_card)

        act_card, actcl = self._card(
            "Saved AI actions",
            "Prompts you can run on a finished meeting from its page (e.g. 'Draft a follow-up "
            "email'). Empty = use the built-in set.",
        )
        self.actions_mgr = NamedListManager(
            self.cfg.ai_actions, text_key="prompt",
            name_ph="Action name (e.g. Follow-up email)",
            text_ph="The instruction — e.g. 'Draft a concise follow-up email with the action items.'",
        )
        actcl.addWidget(self.actions_mgr)
        lay.addWidget(act_card)

        lay.addStretch(1)
        return w

    # ---------- About ----------
    def _about_tab(self) -> QWidget:
        w, lay = self._tab()
        card, cl = self._card("Earshot", f"Version {__version__}")
        body = QLabel(
            "Earshot records you and the other side on separate channels, transcribes on "
            "your home server or an online service, and writes notes with the AI model you "
            "choose in Settings. Your meetings stay on this PC."
        )
        body.setObjectName("Muted")
        body.setWordWrap(True)
        cl.addWidget(body)
        lay.addWidget(card)

        # What's new — the release history (single source of truth: meeting_notes.changelog)
        wn_card, wncl = self._card("What's new", "Recent updates to Earshot.")
        for rel in changelog.RELEASES:
            head = QLabel(f"Version {rel.version}  ·  {rel.date}" + (f"  ·  {rel.title}" if rel.title else ""))
            head.setObjectName("H3")
            head.setContentsMargins(0, 10, 0, 2)
            wncl.addWidget(head)
            multi = len(rel.sections) > 1
            for heading, bullets in rel.sections:
                if heading and (multi or heading.lower() != "added"):
                    sh = QLabel(heading)
                    sh.setObjectName("Faint")
                    wncl.addWidget(sh)
                for b in bullets:
                    wncl.addWidget(self._changelog_bullet(b))
        lay.addWidget(wn_card)

        lay.addStretch(1)
        return w

    def _changelog_bullet(self, text: str) -> QWidget:
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(2, 0, 0, 0)
        rl.setSpacing(8)
        dot = QLabel("•")
        dot.setObjectName("Muted")
        rl.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        lbl = QLabel(text)
        lbl.setObjectName("Muted")
        lbl.setWordWrap(True)
        rl.addWidget(lbl, 1)
        return row

    # ---------- theme ----------
    def _set_theme(self, mode: str) -> None:
        self.theme.set_mode(mode)
        self._sync_theme_buttons()

    def _sync_theme_buttons(self) -> None:
        self.light_btn.setChecked(self.theme.mode == "light")
        self.dark_btn.setChecked(self.theme.mode == "dark")

    # ---------- save ----------
    def _save(self) -> None:
        # In cloud mode the Transcription + AI tabs aren't built, so their widgets
        # don't exist — skip persisting them (Earshot Plus manages both). The
        # underlying cfg keys are left untouched so signing out restores them.
        if not getattr(self, "_cloud_mode", False):
            self.cfg.transcription_provider = self._provider()
            self.cfg.whisper_language = self.language.text().strip()
            self.cfg.whisper_url = self.whisper_url.text().strip()
            self.cfg.whisper_vad_filter = self.whisper_vad.isChecked()
            self.cfg.upload_codec = self.upload_codec.currentData() or "flac"
            self.cfg.online_base_url = self.online_base_url.text().strip() or self.cfg.online_base_url
            self.cfg.online_model = self.online_model.text().strip() or "whisper-1"
            self.cfg.online_api_key = self.online_key.text().strip()
            self.cfg.deepgram_api_key = self.deepgram_key.text().strip()
            self.cfg.deepgram_model = self.deepgram_model.text().strip() or "nova-2"
            self.cfg.anthropic_api_key = self.api_key.text().strip()
            self.cfg.anthropic_model = self.model.text().strip() or "claude-sonnet-4-6"
            self.cfg.notes_provider = self.notes_provider_combo.currentData() or "anthropic"
            # Persist ALL three provider cards regardless of which is currently visible —
            # this is what makes switching providers lossless (keys/URLs for the other two
            # are kept, not wiped, when you switch away and back).
            self.cfg.llm_base_url = self.llm_base_url.text().strip()
            self.cfg.llm_model = self.llm_model.text().strip()
            self.cfg.llm_api_key = self.llm_key.text().strip()
            self.cfg.local_llm_base_url = self.local_base_url.text().strip()
            self.cfg.local_llm_model = self.local_model.text().strip()
            self.cfg.local_llm_api_key = self.local_key.text().strip()
            self.cfg.brief_mode = self.brief_mode_combo.currentData() or "basic"
            self.cfg.auto_transcribe = self.auto_transcribe.isChecked()
            self.cfg.auto_summary = self.auto_summary.isChecked()
            self.cfg.custom_instructions_enabled = self.ci_toggle.isChecked()
            self.cfg.custom_instructions = self.ci_text.toPlainText().strip()
            self.cfg.templates = self.templates_mgr.items()
            self.cfg.ai_actions = self.actions_mgr.items()
        self.cfg.mic_device_name = self.mic_combo.currentData()
        self.cfg.loopback_device_name = self.them_combo.currentData()
        self.cfg.screen_monitor = self.monitor_combo.currentData() or 1
        # automation + AI customisation + home + overlay
        self.cfg.show_dashboard = self.dashboard_toggle.isChecked()
        self.cfg.call_detect_enabled = self.call_detect.isChecked()
        self.cfg.overlay_enabled = self.overlay_enabled.isChecked()
        self.cfg.overlay_opacity = self.overlay_opacity.value() / 100.0
        ov = getattr(self.shell.record, "overlay", None)
        if ov is not None:
            ov.set_opacity(self.cfg.overlay_opacity)
        # webhook_url / webhook_when / todoist_token now live on the Integrations
        # page (moved in Phase D) — saved by IntegrationsPage._save(), not here.
        # (Custom instructions / templates / saved AI actions are persisted in the
        # self-host branch above, alongside the rest of the AI-tab fields.)
        self.cfg.save()
        self.shell.home.refresh()  # reflect the dashboard toggle immediately
        self.save_btn.setText("Saved ✓")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1400, lambda: self.save_btn.setText("Save changes"))

    def apply_theme(self) -> None:
        self.light_btn.setIcon(self.theme.icon("sun", "text", 18))
        self.dark_btn.setIcon(self.theme.icon("moon", "text", 18))
        self.side_left_btn.setIcon(self.theme.icon("chevron-left", "text", 16))
        self.side_right_btn.setIcon(self.theme.icon("chevron-right", "text", 16))
        # the Transcription tab (and its Test button) is absent in cloud mode
        if hasattr(self, "test_btn"):
            self.test_btn.setIcon(self.theme.icon("globe", "text_muted", 16))
        self._sync_theme_buttons()
        self._sync_side_buttons()
