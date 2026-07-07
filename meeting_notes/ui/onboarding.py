"""First-run setup wizard (also re-runnable from Settings → General).

A themed QDialog wrapping a QStackedWidget:
  Welcome (hero) → 3 tour slides → a choice split-screen (Self-host vs Earshot
  Plus) → the chosen path's pages → Finish.

  * Self-host: a Transcription page and an AI page that write the SAME cfg keys
    Settings uses. Fields are PREFILLED from the current cfg and non-empty values
    are never clobbered — an existing user just clicks through.
  * Earshot Plus: the device-link flow (or "Skip for now").

Fully navigable offline (nothing here blocks on the network except the optional
Plus sign-in, which fails soft). Finishing OR closing the dialog sets
cfg.onboarding_done so the wizard never nags twice.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .page_home import PaintedBackdrop
from .widgets import Card, make_chip, run_connection_test

# tour slides: (icon, title, body)
_TOUR = [
    ("mic", "Record both sides, privately",
     "Earshot captures your mic and the system audio on separate channels, right here on "
     "your PC. Nothing is uploaded unless you choose a cloud transcription option."),
    ("sparkles", "AI notes that write themselves",
     "Every meeting becomes a clean summary with topic sections and action-item suggestions — "
     "complete with owners and due dates you can accept in one click."),
    ("message", "Projects, Ask and integrations",
     "Organise meetings into projects, ask questions across everything you've recorded, and "
     "push action items out to the tools you already use."),
]


class _ChoiceCard(Card):
    """A large clickable card for the self-host / Earshot Plus split screen."""

    def __init__(self, on_click):
        super().__init__(shadow=True)
        self._on_click = on_click
        self.setProperty("clickable", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):  # noqa: N802 (Qt override)
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())
                and self._on_click is not None):
            self._on_click()
        super().mouseReleaseEvent(event)


class OnboardingDialog(QDialog):
    # page indices in the stack
    WELCOME, TOUR1, TOUR2, TOUR3, CHOICE, SELF_TR, SELF_AI, PLUS, FINISH = range(9)

    def __init__(self, parent, cfg, theme, *, shell=None, mandatory=False):
        super().__init__(parent)
        self.cfg = cfg
        self.theme = theme
        self.shell = shell
        self.mandatory = mandatory  # first run: must be completed, can't be dismissed
        self._path = None  # "selfhost" | "plus" once chosen
        self._finished_marked = False

        self.setWindowTitle("Welcome to Earshot")
        if mandatory:
            # no title-bar close button: finishing the wizard is the only way out
            self.setWindowFlags(Qt.WindowType.Dialog
                                | Qt.WindowType.CustomizeWindowHint
                                | Qt.WindowType.WindowTitleHint)
        self.setMinimumSize(640, 560)
        self.resize(640, 560)
        self._build()
        self._show_page(self.WELCOME)

    # ---------- construction ----------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.stack = QStackedWidget()
        # order MUST match the index constants above
        self.stack.addWidget(self._welcome_page())
        for i in range(3):
            self.stack.addWidget(self._tour_page(i))
        self.stack.addWidget(self._choice_page())
        self.stack.addWidget(self._selfhost_transcription_page())
        self.stack.addWidget(self._selfhost_ai_page())
        self.stack.addWidget(self._plus_page())
        self.stack.addWidget(self._finish_page())
        root.addWidget(self.stack, 1)

        # nav bar
        bar = QWidget()
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(24, 12, 24, 16)
        self.back_btn = QPushButton("Back")
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.clicked.connect(self._on_back)
        bar_lay.addWidget(self.back_btn)
        bar_lay.addStretch(1)
        self.skip_btn = QPushButton("Skip")
        self.skip_btn.setProperty("variant", "ghost")
        self.skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.skip_btn.clicked.connect(self._on_skip)
        bar_lay.addWidget(self.skip_btn)
        self.next_btn = QPushButton("Next")
        self.next_btn.setProperty("variant", "primary")
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn.clicked.connect(self._on_next)
        bar_lay.addWidget(self.next_btn)
        root.addWidget(bar)

    def _page_shell(self) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(36, 32, 36, 12)
        lay.setSpacing(14)
        return w, lay

    def _welcome_page(self) -> QWidget:
        w, lay = self._page_shell()
        hero = QWidget()
        hero.setMinimumHeight(200)
        hv = QVBoxLayout(hero)
        hv.setContentsMargins(0, 0, 0, 0)
        backdrop = PaintedBackdrop(self.theme, variant="hero")
        hv.addWidget(backdrop)
        inner = QVBoxLayout(backdrop)
        inner.setContentsMargins(28, 24, 28, 24)
        inner.addStretch(1)
        htitle = QLabel("Welcome to Earshot")
        htitle.setStyleSheet("color:#FFFFFF; font-size:26px; font-weight:800;")
        inner.addWidget(htitle)
        hsub = QLabel("Record, transcribe and summarise your meetings — your way.")
        hsub.setStyleSheet("color:rgba(255,255,255,0.85); font-size:14px;")
        hsub.setWordWrap(True)
        inner.addWidget(hsub)
        inner.addStretch(1)
        lay.addWidget(hero)
        body = QLabel(
            "Let's take a quick tour, then get you set up. It only takes a minute, and you can "
            "change everything later in Settings."
        )
        body.setObjectName("Muted")
        body.setWordWrap(True)
        lay.addWidget(body)
        lay.addStretch(1)
        return w

    def _tour_page(self, i: int) -> QWidget:
        w, lay = self._page_shell()
        icon_name, title, text = _TOUR[i]
        lay.addStretch(1)
        ic = QLabel()
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ic.setPixmap(icons.pixmap(icon_name, self.theme.color("primary"), 48))
        lay.addWidget(ic)
        t = QLabel(title)
        t.setObjectName("H2")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        b = QLabel(text)
        b.setObjectName("Muted")
        b.setWordWrap(True)
        b.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(b)
        lay.addStretch(2)
        return w

    def _choice_page(self) -> QWidget:
        w, lay = self._page_shell()
        t = QLabel("How do you want to run Earshot?")
        t.setObjectName("H2")
        lay.addWidget(t)
        s = QLabel("Most people pick Earshot Plus — nothing to configure, first week free. "
                   "Prefer to run your own AI? Self-hosting is free forever. Switch anytime.")
        s.setObjectName("Muted")
        s.setWordWrap(True)
        lay.addWidget(s)

        cards = QHBoxLayout()
        cards.setSpacing(16)

        # ---- Earshot Plus: FIRST and visually recommended ----
        plus_card = _ChoiceCard(lambda: self._choose("plus"))
        self.plus_choice_card = plus_card
        plus_card.setStyleSheet(
            f"QFrame#Card{{border: 2px solid {self.theme.color('primary')};}}"
        )
        pcl = QVBoxLayout(plus_card)
        pcl.setContentsMargins(20, 20, 20, 20)
        pcl.setSpacing(8)
        phead = QHBoxLayout()
        pic = QLabel()
        pic.setPixmap(icons.pixmap("cloud", self.theme.color("primary"), 30))
        phead.addWidget(pic)
        phead.addStretch(1)
        self.plus_badge = make_chip("Recommended", fg=self.theme.color("on_primary"),
                                    bg=self.theme.color("primary"))
        phead.addWidget(self.plus_badge)
        pcl.addLayout(phead)
        pt = QLabel("Earshot Plus")
        pt.setObjectName("H3")
        pcl.addWidget(pt)
        pp = QLabel("Managed transcription + AI · 7-day free trial · $9/mo")
        pp.setObjectName("Muted")
        pp.setWordWrap(True)
        pcl.addWidget(pp)
        pcl.addSpacing(6)
        for line in ("No API keys, no setup — sign in and go",
                     "Fast, accurate managed models",
                     "40 hours of transcription a month",
                     "Cloud sync & hosted sharing as they roll out",
                     "Priority support"):
            b = QLabel(f"•  {line}")
            b.setObjectName("Faint")
            b.setWordWrap(True)
            pcl.addWidget(b)
        pcl.addStretch(1)
        pbtn = QPushButton("Start with Plus")
        pbtn.setProperty("variant", "primary")
        pbtn.setCursor(Qt.CursorShape.PointingHandCursor)
        pbtn.clicked.connect(lambda: self._choose("plus"))
        pcl.addWidget(pbtn)
        cards.addWidget(plus_card, 1)

        # ---- Self-host: the free path, visually secondary ----
        self_card = _ChoiceCard(lambda: self._choose("selfhost"))
        scl = QVBoxLayout(self_card)
        scl.setContentsMargins(20, 20, 20, 20)
        scl.setSpacing(8)
        sic = QLabel()
        sic.setPixmap(icons.pixmap("settings", self.theme.color("text_muted"), 30))
        scl.addWidget(sic)
        st = QLabel("Self-host")
        st.setObjectName("H3")
        scl.addWidget(st)
        sp = QLabel("Free forever · bring your own keys")
        sp.setObjectName("Muted")
        sp.setWordWrap(True)
        scl.addWidget(sp)
        scl.addSpacing(6)
        for line in ("Your own AI keys or home Whisper server",
                     "Everything stays on this PC",
                     "Open source — audit it, fork it",
                     "All core features, no account"):
            b = QLabel(f"•  {line}")
            b.setObjectName("Faint")
            b.setWordWrap(True)
            scl.addWidget(b)
        scl.addStretch(1)
        sbtn = QPushButton("Set up my own keys")
        sbtn.setCursor(Qt.CursorShape.PointingHandCursor)
        sbtn.clicked.connect(lambda: self._choose("selfhost"))
        scl.addWidget(sbtn)
        cards.addWidget(self_card, 1)

        lay.addLayout(cards, 1)
        return w

    # ---------- self-host: transcription ----------
    def _selfhost_transcription_page(self) -> QWidget:
        w, lay = self._page_shell()
        t = QLabel("Transcription")
        t.setObjectName("H2")
        lay.addWidget(t)
        s = QLabel("Where your audio gets transcribed. Your current settings are prefilled.")
        s.setObjectName("Muted")
        s.setWordWrap(True)
        lay.addWidget(s)

        form = QFormLayout()
        form.setSpacing(10)
        self.tr_form = form
        self.tr_provider = QComboBox()
        self.tr_provider.addItem("Home server (self-hosted Whisper)", "home")
        self.tr_provider.addItem("Online service (OpenAI / Groq)", "online")
        self.tr_provider.addItem("Deepgram", "deepgram")
        _i = self.tr_provider.findData(self.cfg.transcription_provider)
        self.tr_provider.setCurrentIndex(_i if _i >= 0 else 0)
        self.tr_provider.currentIndexChanged.connect(self._on_tr_provider_changed)
        form.addRow("Source", self.tr_provider)

        self.tr_url = QLineEdit(self.cfg.whisper_url)
        self.tr_url.setPlaceholderText("http://<your-server-ip>:9000")
        form.addRow("Home server URL", self.tr_url)

        self.tr_online_base = QLineEdit(self.cfg.online_base_url)
        self.tr_online_base.setPlaceholderText("https://api.groq.com/openai/v1")
        form.addRow("Online base URL", self.tr_online_base)
        self.tr_online_key = QLineEdit(self.cfg.online_api_key)
        self.tr_online_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.tr_online_key.setPlaceholderText("API key (or set OPENAI_API_KEY)")
        form.addRow("Online API key", self.tr_online_key)

        self.tr_deepgram_key = QLineEdit(self.cfg.deepgram_api_key)
        self.tr_deepgram_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.tr_deepgram_key.setPlaceholderText("Deepgram API key (or set DEEPGRAM_API_KEY)")
        form.addRow("Deepgram API key", self.tr_deepgram_key)
        lay.addLayout(form)

        row = QHBoxLayout()
        self.tr_test_btn = QPushButton("Test connection")
        self.tr_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tr_test_btn.clicked.connect(self._test_transcription)
        self.tr_test_label = QLabel("")
        row.addWidget(self.tr_test_btn)
        row.addWidget(self.tr_test_label)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        self._on_tr_provider_changed()
        return w

    def _on_tr_provider_changed(self, *_) -> None:
        # setRowVisible hides the QFormLayout row LABEL along with the field —
        # setVisible on the field alone leaves orphaned labels floating
        p = self.tr_provider.currentData()
        self.tr_form.setRowVisible(self.tr_url, p == "home")
        self.tr_form.setRowVisible(self.tr_online_base, p == "online")
        self.tr_form.setRowVisible(self.tr_online_key, p == "online")
        self.tr_form.setRowVisible(self.tr_deepgram_key, p == "deepgram")

    def _test_transcription(self) -> None:
        # capture inputs here; the ping runs off-thread so a dead server can't
        # freeze the wizard for the timeout (worst on the mandatory first run)
        p = self.tr_provider.currentData()
        if p == "online":
            from ..transcription import openai_client
            base, key = self.tr_online_base.text().strip(), self.tr_online_key.text().strip()
            probe = lambda: openai_client.ping(base, key)  # noqa: E731
        elif p == "deepgram":
            from ..transcription import deepgram_client
            key = self.tr_deepgram_key.text().strip()
            probe = lambda: deepgram_client.ping(key)  # noqa: E731
        else:
            from ..transcription import whisper_client
            url = self.tr_url.text().strip()
            probe = lambda: whisper_client.ping(url)  # noqa: E731
        run_connection_test(self, self.tr_test_btn, self.tr_test_label, self.theme, probe)

    # ---------- self-host: AI ----------
    def _selfhost_ai_page(self) -> QWidget:
        w, lay = self._page_shell()
        t = QLabel("AI model")
        t.setObjectName("H2")
        lay.addWidget(t)
        s = QLabel("Powers notes, actions and Ask Earshot. Your current settings are prefilled.")
        s.setObjectName("Muted")
        s.setWordWrap(True)
        lay.addWidget(s)

        form = QFormLayout()
        form.setSpacing(10)
        self.ai_form = form
        self.ai_provider = QComboBox()
        self.ai_provider.addItem("Anthropic (Claude)", "anthropic")
        self.ai_provider.addItem("OpenAI or compatible cloud", "openai")
        self.ai_provider.addItem("Local (Ollama, LM Studio…)", "local")
        _i = self.ai_provider.findData(self.cfg.notes_provider)
        self.ai_provider.setCurrentIndex(_i if _i >= 0 else 0)
        self.ai_provider.currentIndexChanged.connect(self._on_ai_provider_changed)
        form.addRow("Provider", self.ai_provider)

        self.ai_anthropic_key = QLineEdit(self.cfg.anthropic_api_key)
        self.ai_anthropic_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_anthropic_key.setPlaceholderText("sk-ant-… (or set ANTHROPIC_API_KEY)")
        form.addRow("Anthropic key", self.ai_anthropic_key)

        self.ai_llm_base = QLineEdit(self.cfg.llm_base_url)
        self.ai_llm_base.setPlaceholderText("https://api.openai.com/v1")
        form.addRow("Cloud base URL", self.ai_llm_base)
        self.ai_llm_model = QLineEdit(self.cfg.llm_model)
        self.ai_llm_model.setPlaceholderText("gpt-4o-mini")
        form.addRow("Cloud model", self.ai_llm_model)
        self.ai_llm_key = QLineEdit(self.cfg.llm_api_key)
        self.ai_llm_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_llm_key.setPlaceholderText("API key")
        form.addRow("Cloud API key", self.ai_llm_key)

        self.ai_local_base = QLineEdit(self.cfg.local_llm_base_url)
        self.ai_local_base.setPlaceholderText("http://localhost:11434/v1")
        form.addRow("Local base URL", self.ai_local_base)
        self.ai_local_model = QLineEdit(self.cfg.local_llm_model)
        self.ai_local_model.setPlaceholderText("llama3.1")
        form.addRow("Local model", self.ai_local_model)
        lay.addLayout(form)

        row = QHBoxLayout()
        self.ai_test_btn = QPushButton("Test connection")
        self.ai_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ai_test_btn.clicked.connect(self._test_ai)
        self.ai_test_label = QLabel("")
        row.addWidget(self.ai_test_btn)
        row.addWidget(self.ai_test_label)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        self._on_ai_provider_changed()
        return w

    def _on_ai_provider_changed(self, *_) -> None:
        # setRowVisible: hide the form row's label together with its field
        p = self.ai_provider.currentData()
        self.ai_form.setRowVisible(self.ai_anthropic_key, p == "anthropic")
        for wgt in (self.ai_llm_base, self.ai_llm_model, self.ai_llm_key):
            self.ai_form.setRowVisible(wgt, p == "openai")
        for wgt in (self.ai_local_base, self.ai_local_model):
            self.ai_form.setRowVisible(wgt, p == "local")

    def _test_ai(self) -> None:
        p = self.ai_provider.currentData()
        if p == "openai":
            from ..notes import openai_llm
            base, key = self.ai_llm_base.text().strip(), self.ai_llm_key.text().strip()
            probe = lambda: openai_llm.ping(base, key)  # noqa: E731
        elif p == "local":
            from ..notes import openai_llm
            base = self.ai_local_base.text().strip()
            probe = lambda: openai_llm.ping(base, "")  # noqa: E731
        else:
            # Anthropic has no cheap ping endpoint — a non-empty key (or the env
            # var) counts as "configured". Instant, but routed uniformly.
            has_key = bool(self.ai_anthropic_key.text().strip() or self.cfg.resolved_anthropic_key())
            probe = lambda: has_key  # noqa: E731
        run_connection_test(self, self.ai_test_btn, self.ai_test_label, self.theme, probe)

    # ---------- Earshot Plus ----------
    def _plus_page(self) -> QWidget:
        w, lay = self._page_shell()
        lay.addStretch(1)
        ic = QLabel()
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ic.setPixmap(icons.pixmap("cloud", self.theme.color("primary"), 44))
        lay.addWidget(ic)
        t = QLabel("Earshot Plus")
        t.setObjectName("H2")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        b = QLabel(
            "Managed transcription and AI — no keys, no home server. Sign in to link this PC, "
            "or skip for now and do it later from the Account page."
        )
        b.setObjectName("Muted")
        b.setWordWrap(True)
        b.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(b)

        row = QHBoxLayout()
        row.addStretch(1)
        self.plus_signin_btn = QPushButton("  Sign in to Earshot Plus")
        self.plus_signin_btn.setProperty("variant", "primary")
        self.plus_signin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.plus_signin_btn.setIcon(icons.icon("cloud", self.theme.color("on_primary"), 15))
        self.plus_signin_btn.clicked.connect(self._plus_sign_in)
        row.addWidget(self.plus_signin_btn)
        # the alternative isn't "skip setup" — it's the free path
        self.plus_skip_btn = QPushButton("Use my own keys instead")
        self.plus_skip_btn.setProperty("variant", "ghost")
        self.plus_skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.plus_skip_btn.clicked.connect(lambda: self._choose("selfhost"))
        row.addWidget(self.plus_skip_btn)
        row.addStretch(1)
        lay.addLayout(row)

        self.plus_status = QLabel("")
        self.plus_status.setObjectName("Faint")
        self.plus_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.plus_status)
        lay.addStretch(2)
        return w

    def _plus_sign_in(self) -> None:
        from .cloud_link import CloudLinkDialog
        dlg = CloudLinkDialog(self, self.cfg, self.theme, on_linked=self._on_plus_linked)
        dlg.exec()
        if dlg.linked_ok:
            # Advance to Finish. Without this the user is stranded: on the
            # mandatory first run Next AND Skip are hidden on the Plus page, so
            # a successful sign-in would otherwise soft-lock the wizard.
            self.plus_status.setText("Signed in to Earshot Plus.")
            self._show_page(self.FINISH)

    def _on_plus_linked(self, _data: dict) -> None:
        if self.shell is not None and hasattr(self.shell, "on_account_changed"):
            self.shell.on_account_changed()

    # ---------- finish ----------
    def _finish_page(self) -> QWidget:
        w, lay = self._page_shell()
        lay.addStretch(1)
        ic = QLabel()
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ic.setPixmap(icons.pixmap("check", self.theme.color("success"), 48))
        lay.addWidget(ic)
        t = QLabel("You're all set")
        t.setObjectName("H2")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        b = QLabel("Hit New recording whenever you're ready. You can revisit this guide any time "
                   "from Settings → General.")
        b.setObjectName("Muted")
        b.setWordWrap(True)
        b.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(b)
        lay.addStretch(2)
        return w

    # ---------- navigation ----------
    def _choose(self, path: str) -> None:
        self._path = path
        if path == "selfhost":
            self._show_page(self.SELF_TR)
        else:
            self._show_page(self.PLUS)

    def _show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self._update_nav(index)

    def _update_nav(self, index: int) -> None:
        self.back_btn.setEnabled(index != self.WELCOME)
        is_choice = index == self.CHOICE
        is_finish = index == self.FINISH
        is_plus = index == self.PLUS
        # the choice page advances by clicking a card; the plus page has its own
        # buttons — hide the generic Next there
        self.next_btn.setVisible(not (is_choice or is_plus))
        if is_finish:
            self.next_btn.setText("Finish")
        else:
            self.next_btn.setText("Next")
        # Skip is offered everywhere except the finish page (where Finish is the
        # action) — and never on the mandatory first run
        self.skip_btn.setVisible(not is_finish and not self.mandatory)

    def _on_back(self) -> None:
        index = self.stack.currentIndex()
        if index in (self.SELF_TR, self.PLUS):
            self._show_page(self.CHOICE)
        elif index == self.SELF_AI:
            self._show_page(self.SELF_TR)
        elif index == self.FINISH:
            # go back to the path we came in on
            self._show_page(self.SELF_AI if self._path == "selfhost" else self.PLUS)
        elif index > self.WELCOME:
            self._show_page(index - 1)

    def _on_next(self) -> None:
        index = self.stack.currentIndex()
        if index in (self.WELCOME, self.TOUR1, self.TOUR2):
            self._show_page(index + 1)
        elif index == self.TOUR3:
            self._show_page(self.CHOICE)
        elif index == self.SELF_TR:
            self._save_selfhost_transcription()
            self._show_page(self.SELF_AI)
        elif index == self.SELF_AI:
            self._save_selfhost_ai()
            self._show_page(self.FINISH)
        elif index == self.FINISH:
            self._finish()

    def _on_skip(self) -> None:
        # jump straight to the finish page (nothing is saved beyond what the user
        # already committed via Next); the wizard still marks onboarding_done.
        self._show_page(self.FINISH)

    # ---------- persistence (writes the SAME cfg keys Settings uses) ----------
    @staticmethod
    def _keep(current: str, typed: str) -> str:
        """Never clobber a non-empty existing value with an empty typed one —
        an existing user who just clicks through keeps their settings intact."""
        typed = (typed or "").strip()
        return typed if typed else (current or "")

    def _save_selfhost_transcription(self) -> None:
        self.cfg.account_mode = "selfhost"
        self.cfg.transcription_provider = self.tr_provider.currentData() or "home"
        self.cfg.whisper_url = self._keep(self.cfg.whisper_url, self.tr_url.text())
        self.cfg.online_base_url = self._keep(self.cfg.online_base_url, self.tr_online_base.text())
        self.cfg.online_api_key = self._keep(self.cfg.online_api_key, self.tr_online_key.text())
        self.cfg.deepgram_api_key = self._keep(self.cfg.deepgram_api_key, self.tr_deepgram_key.text())
        self.cfg.save()

    def _save_selfhost_ai(self) -> None:
        self.cfg.account_mode = "selfhost"
        self.cfg.notes_provider = self.ai_provider.currentData() or "anthropic"
        self.cfg.anthropic_api_key = self._keep(self.cfg.anthropic_api_key, self.ai_anthropic_key.text())
        self.cfg.llm_base_url = self._keep(self.cfg.llm_base_url, self.ai_llm_base.text())
        self.cfg.llm_model = self._keep(self.cfg.llm_model, self.ai_llm_model.text())
        self.cfg.llm_api_key = self._keep(self.cfg.llm_api_key, self.ai_llm_key.text())
        self.cfg.local_llm_base_url = self._keep(self.cfg.local_llm_base_url, self.ai_local_base.text())
        self.cfg.local_llm_model = self._keep(self.cfg.local_llm_model, self.ai_local_model.text())
        self.cfg.save()
        if self.shell is not None and hasattr(self.shell, "on_account_changed"):
            self.shell.on_account_changed()

    def _mark_done(self) -> None:
        if self._finished_marked:
            return
        self._finished_marked = True
        self.cfg.onboarding_done = True
        self.cfg.save()

    def _finish(self) -> None:
        self._mark_done()
        self.accept()

    # Mandatory first run: Esc / Alt+F4 / X do nothing until the wizard is
    # finished. The Settings re-run (non-mandatory) stays dismissable, and
    # marks onboarding_done on close so it never nags.
    def reject(self) -> None:  # noqa: N802 (Qt override)
        if self.mandatory and not self._finished_marked:
            return
        self._mark_done()
        super().reject()

    def closeEvent(self, event):  # noqa: N802 (Qt override)
        if self.mandatory and not self._finished_marked:
            event.ignore()
            return
        self._mark_done()
        super().closeEvent(event)
