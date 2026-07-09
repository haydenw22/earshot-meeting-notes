"""Settings: everything in one place, Wispr-Flow style.

A left nav rail (SETTINGS: General / Audio / Transcription / AI / Integrations /
About, ACCOUNT: Account / Plans & Billing) with a stacked pane per section on
the right. The Account, Plans and Integrations panes are their own page classes
embedded here; they persist across mode changes while the mode-dependent panes
(Transcription and AI hide in cloud mode) are rebuilt by refresh_tabs().
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, changelog, paths
from ..audio import devices as dev
from ..capture import screen as screen_capture
from ..transcription import whisper_client
from . import icons
from .named_list import NamedListManager
from .page_account import AccountPage
from .page_integrations import IntegrationsPage
from .page_plans import PlansPage
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

# panes that edit config through the shared "Save changes" button
_EDITABLE = {"general", "audio", "transcription", "ai"}


class SettingsPage(QWidget):
    def __init__(self, shell, repo, cfg, theme):
        super().__init__()
        self.shell = shell
        self.repo = repo
        self.cfg = cfg
        self.theme = theme
        # persistent embedded pages — they manage their own state/refresh and
        # must survive refresh_tabs() (a sign-in can be triggered FROM them)
        self.account_page = AccountPage(shell, repo, cfg, theme)
        self.plans_page = PlansPage(shell, repo, cfg, theme)
        self.integrations_page = IntegrationsPage(shell, repo, cfg, theme)
        self._nav_buttons: dict[str, QPushButton] = {}
        self._pane_widgets: dict[str, QWidget] = {}
        self._current_key = "general"
        self._build()

    # ---------- construction ----------
    def _build(self) -> None:
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.nav = QFrame()
        self.nav.setObjectName("SettingsNav")
        self.nav.setFixedWidth(208)
        self._nav_lay = QVBoxLayout(self.nav)
        self._nav_lay.setContentsMargins(14, 22, 14, 14)
        self._nav_lay.setSpacing(3)
        row.addWidget(self.nav)

        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(0)
        self.stack = QStackedWidget()
        rlay.addWidget(self.stack, 1)

        self.save_bar = QWidget()
        bar = QHBoxLayout(self.save_bar)
        bar.setContentsMargins(40, 8, 40, 20)
        bar.addStretch(1)
        self.save_btn = QPushButton("Save changes")
        self.save_btn.setProperty("variant", "primary")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.clicked.connect(self._save)
        bar.addWidget(self.save_btn)
        rlay.addWidget(self.save_bar)
        row.addWidget(right, 1)

        self._build_sections()
        self.apply_theme()

    def _build_sections(self) -> None:
        """(Re)build the nav rail + pane stack for the current account mode. In
        cloud mode the Transcription and AI panes are omitted — Earshot Plus
        manages both. The embedded Account/Plans/Integrations pages are re-used,
        never destroyed; the config-editing panes are rebuilt fresh."""
        # clear the nav rail
        while self._nav_lay.count():
            item = self._nav_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        # clear the stack; delete only the rebuildable panes
        persistent = {self.account_page, self.plans_page, self.integrations_page}
        while self.stack.count():
            w = self.stack.widget(0)
            self.stack.removeWidget(w)
            if w in persistent:
                w.setParent(None)
            else:
                w.setParent(None)
                w.deleteLater()
        self._nav_buttons.clear()
        self._pane_widgets.clear()
        # The Transcription pane (and its test_btn) is omitted in cloud mode, so
        # after the rebuild below self.test_btn would point at a deleted C++
        # widget while the Python attribute lingers — apply_theme()'s
        # hasattr(test_btn) guard then passes and .setIcon() raises "Internal C++
        # object already deleted". Drop the stale attr so it's only present when
        # the pane (and button) is actually rebuilt. (Crash on first sign-in.)
        if hasattr(self, "test_btn"):
            del self.test_btn

        self._cloud_mode = self.cfg.account_mode == "cloud"

        sections: list[tuple[str, str, str, QWidget]] = [
            ("general", "General", "sliders", self._general_pane()),
            ("audio", "Audio", "volume", self._audio_pane()),
        ]
        if not self._cloud_mode:
            sections.append(("transcription", "Transcription", "globe", self._transcription_pane()))
            sections.append(("ai", "AI", "sparkles", self._ai_pane()))
        sections.append(("integrations", "Integrations", "zap", self.integrations_page))
        sections.append(("about", "About", "info", self._about_pane()))
        account_sections: list[tuple[str, str, str, QWidget]] = [
            ("account", "Account", "user", self.account_page),
            ("plans", "Plans & Billing", "credit-card", self.plans_page),
        ]

        head = QLabel("SETTINGS")
        head.setObjectName("SectionLabel")
        head.setContentsMargins(10, 0, 0, 4)
        self._nav_lay.addWidget(head)
        for key, title, icon_name, widget in sections:
            self._add_section(key, title, icon_name, widget)

        acc_head = QLabel("ACCOUNT")
        acc_head.setObjectName("SectionLabel")
        acc_head.setContentsMargins(10, 14, 0, 4)
        self._nav_lay.addWidget(acc_head)
        for key, title, icon_name, widget in account_sections:
            self._add_section(key, title, icon_name, widget)

        self._nav_lay.addStretch(1)
        ver = QLabel(f"Earshot v{__version__}")
        ver.setObjectName("Faint")
        ver.setContentsMargins(10, 0, 0, 0)
        self._nav_lay.addWidget(ver)

        # scrolling a pane must never change a control it passes over
        calm_scroll_children(self.stack)

        if self._current_key not in self._pane_widgets:
            self._current_key = "general"
        self.show_section(self._current_key)

    def _add_section(self, key: str, title: str, icon_name: str, widget: QWidget) -> None:
        self.stack.addWidget(widget)
        self._pane_widgets[key] = widget
        # "&" is Qt's mnemonic marker in button text — escape it or "Plans &
        # Billing" renders as "Plans Billing"
        btn = QPushButton("  " + title.replace("&", "&&"))
        btn.setProperty("variant", "ghost")
        btn.setProperty("icon_name", icon_name)
        btn.setProperty("section_title", title)
        btn.setCheckable(True)
        btn.setMinimumHeight(36)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _=False, k=key: self.show_section(k))
        self._nav_buttons[key] = btn
        self._nav_lay.addWidget(btn)

    # ---------- navigation ----------
    def show_section(self, key: str) -> None:
        """Switch to a settings section by key: general / audio / transcription /
        ai / integrations / about / account / plans."""
        if key not in self._pane_widgets:
            key = "general"
        self._current_key = key
        self.stack.setCurrentWidget(self._pane_widgets[key])
        for k, b in self._nav_buttons.items():
            b.setChecked(k == key)
        self._retint_nav()
        self.save_bar.setVisible(key in _EDITABLE)
        if key == "account":
            self.account_page.refresh()
        elif key == "plans":
            self.plans_page.refresh()

    def _retint_nav(self) -> None:
        for _key, btn in self._nav_buttons.items():
            name = btn.property("icon_name") or "settings"
            color = "primary" if btn.isChecked() else "text_muted"
            btn.setIcon(icons.icon(name, self.theme.color(color), 16))

    def section_titles(self) -> list[str]:
        """Visible section names, in nav order (used by tests)."""
        return [b.property("section_title") for b in self._nav_buttons.values()]

    def refresh_tabs(self) -> None:
        """Rebuild the sections after the account mode changes (sign in / out) so
        the Transcription and AI panes appear or disappear immediately."""
        self._build_sections()
        self.apply_theme()

    # ---------- pane scaffolding ----------
    def _pane(self, title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(40, 28, 40, 8)
        outer.setSpacing(6)
        head = QLabel(title)
        head.setObjectName("H1")
        outer.addWidget(head)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("Muted")
            sub.setWordWrap(True)
            outer.addWidget(sub)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(2, 14, 2, 2)
        lay.setSpacing(16)
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)
        return page, lay

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

    # ---------- General ----------
    def _general_pane(self) -> QWidget:
        w, lay = self._pane("General", "How Earshot looks and behaves.")
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
        ov = getattr(getattr(self.shell, "record", None), "overlay", None)
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
    def _audio_pane(self) -> QWidget:
        w, lay = self._pane("Audio", "Devices and where recordings are stored.")

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
    def _transcription_pane(self) -> QWidget:
        w, lay = self._pane("Transcription", "Where your audio gets transcribed.")

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
    def _ai_pane(self) -> QWidget:
        w, lay = self._pane("AI", "The model that writes your notes, briefs and answers.")

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
    def _about_pane(self) -> QWidget:
        w, lay = self._pane("About", "Version and release notes.")
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

        # What's new — the LAST THREE releases only (the full history built
        # hundreds of QLabels and made the pane laggy); the complete changelog
        # lives on GitHub, one click away.
        wn_card, wncl = self._card("What's new", "The three most recent updates.")
        for rel in changelog.RELEASES[:3]:
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
        self.full_history_btn = QPushButton("  Full release history")
        self.full_history_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.full_history_btn.clicked.connect(self._open_full_changelog)
        wncl.addSpacing(6)
        wncl.addWidget(self.full_history_btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(wn_card)

        lay.addStretch(1)
        return w

    def _open_full_changelog(self) -> None:
        import webbrowser

        from .page_help import CHANGELOG_URL
        try:
            webbrowser.open(CHANGELOG_URL)
        except Exception:
            pass

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
        # In cloud mode the Transcription + AI panes aren't built, so their widgets
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
        ov = getattr(getattr(self.shell, "record", None), "overlay", None)
        if ov is not None:
            ov.set_opacity(self.cfg.overlay_opacity)
        # webhook_url / webhook_when / todoist_token live on the Integrations
        # pane — saved by IntegrationsPage._save(), not here.
        self.cfg.save()
        home = getattr(self.shell, "home", None)
        if home is not None:
            home.refresh()  # reflect the dashboard toggle immediately
        self.save_btn.setText("Saved ✓")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1400, lambda: self.save_btn.setText("Save changes"))

    def apply_theme(self) -> None:
        self.light_btn.setIcon(self.theme.icon("sun", "text", 18))
        self.dark_btn.setIcon(self.theme.icon("moon", "text", 18))
        self.side_left_btn.setIcon(self.theme.icon("chevron-left", "text", 16))
        self.side_right_btn.setIcon(self.theme.icon("chevron-right", "text", 16))
        # the Transcription pane (and its Test button) is absent in cloud mode
        if hasattr(self, "test_btn"):
            self.test_btn.setIcon(self.theme.icon("globe", "text_muted", 16))
        if hasattr(self, "full_history_btn"):
            self.full_history_btn.setIcon(self.theme.icon("external-link", "text_muted", 15))
        self._retint_nav()
        self._sync_theme_buttons()
        self._sync_side_buttons()
        # cascade to the embedded pages (the shell only calls ours)
        self.account_page.apply_theme()
        self.plans_page.apply_theme()
        self.integrations_page.apply_theme()
