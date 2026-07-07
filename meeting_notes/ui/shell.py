"""Application shell: a sidebar (logo, New-recording CTA, search, nav, folders
tree, meetings list, theme toggle) plus a stacked content area. Coordinates
navigation and broadcasts theme changes to every page.
"""
from __future__ import annotations

from PySide6.QtCore import (
    QEvent,
    QPoint,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import icons, logo
from .folder_dialog import ask_new_folder, ask_rename_folder
from .page_ask import AskPage
from .page_detail import DetailPage
from .page_help import HelpPage
from .page_home import HomePage
from .page_project import ProjectPage
from .page_record import RecordPage
from .page_settings import SettingsPage
from .widgets import FOLDER_COLORS


class AccountCard(QFrame):
    """Full-width flat hover-highlight row: avatar initial, name, 'Local account'
    sub-label, chevron — click navigates to the Account page."""

    def __init__(self, cfg, theme, on_click, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.theme = theme
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("AccountCard")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(10)

        self.avatar = QLabel()
        self.avatar.setFixedSize(32, 32)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.avatar)

        texts = QVBoxLayout()
        texts.setSpacing(0)
        self.name_lbl = QLabel()
        self.name_lbl.setObjectName("H3")
        texts.addWidget(self.name_lbl)
        self.sub_lbl = QLabel("Local account")
        self.sub_lbl.setObjectName("Faint")
        texts.addWidget(self.sub_lbl)
        lay.addLayout(texts, 1)

        self.chevron = QLabel()
        lay.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignVCenter)

        self.refresh()

    def refresh(self) -> None:
        if self.cfg.account_mode == "cloud" and (self.cfg.cloud_email or "").strip():
            # signed into Earshot Plus: show who, and that this is the paid tier
            name = (self.cfg.account_name or "").strip() or self.cfg.cloud_email.split("@")[0]
            self.sub_lbl.setText("Earshot Plus")
        else:
            name = (self.cfg.account_name or "").strip() or "Guest"
            self.sub_lbl.setText("Local account")
        self.name_lbl.setText(name)
        self.avatar.setStyleSheet(
            f"background:{self.theme.color('primary_soft')}; color:{self.theme.color('primary')};"
            f"border-radius:16px; font-size:13px; font-weight:700;"
        )
        self.avatar.setText(name[0].upper())
        self.chevron.setPixmap(icons.pixmap("chevron-right", self.theme.color("text_faint"), 14))
        self.setStyleSheet(
            "#AccountCard{background:transparent; border:none; border-radius:10px;}"
            f"#AccountCard:hover{{background:{self.theme.color('surface_hover')};}}"
        )

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint() if hasattr(event, "position") else event.pos()
        ):
            self._on_click()
        super().mouseReleaseEvent(event)


class Shell(QMainWindow):
    def __init__(self, repo, cfg, theme):
        super().__init__()
        self.repo = repo
        self.cfg = cfg
        self.theme = theme
        self.setWindowTitle("Earshot")
        # Wispr-Flow-like default viewport: roomy enough for the two-column
        # Settings screen and side-by-side plan cards.
        self.resize(1336, 843)
        self.setMinimumSize(960, 620)

        self.home = HomePage(self, repo, cfg, theme)
        self.record = RecordPage(self, repo, cfg, theme)
        self.detail = DetailPage(self, repo, cfg, theme)
        self.settings = SettingsPage(self, repo, cfg, theme)
        self.ask = AskPage(self, repo, cfg, theme)
        self.help = HelpPage(self, repo, cfg, theme)
        self.project = ProjectPage(self, repo, cfg, theme)
        self._active_project = None  # ("folder", folder_id|None) while a project is open

        self._build()
        self.theme.changed.connect(self._on_theme_changed)
        self.notify_data_changed()
        self.refresh_plan_state()
        if self.cfg.sidebar_collapsed:
            self._apply_sidebar_collapsed()
        self.show_home()
        # after the window is up, quietly salvage any recording a crash left behind
        QTimer.singleShot(900, self._salvage_interrupted)
        # NB: the first-run setup wizard is launched from app.py BEFORE this
        # window is shown (mandatory, app-not-in-background). run_onboarding()
        # remains for the Settings → "Run setup guide again" path.

        # call auto-detection: offer to record when another app starts using the mic
        from .call_watcher import CallWatcher
        self._call_toast = None
        self.call_watcher = CallWatcher(self)
        self.call_watcher.call_started.connect(self._on_call_started)
        self.call_watcher.call_ended.connect(self._on_call_ended)
        self.call_watcher.start()

    # ---------- embedded-page accessors ----------
    # Account and Integrations live INSIDE Settings now (panes under the
    # ACCOUNT/SETTINGS nav rail) — these keep the old shell attributes working.
    @property
    def account(self):
        return self.settings.account_page

    @property
    def integrations(self):
        return self.settings.integrations_page

    @property
    def plans(self):
        return self.settings.plans_page

    # ---------- construction ----------
    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.sidebar = self._sidebar()
        self.stack = QStackedWidget()
        for page in (self.home, self.record, self.detail, self.settings, self.ask,
                     self.help, self.project):
            self.stack.addWidget(page)

        # floating "expand" affordance shown only while the sidebar is collapsed
        self.expand_btn = QToolButton(self.stack)
        self.expand_btn.setObjectName("SidebarExpand")
        self.expand_btn.setFixedSize(30, 30)
        self.expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.expand_btn.setToolTip("Show sidebar")
        self.expand_btn.clicked.connect(self.toggle_sidebar)
        self.expand_btn.hide()
        self.stack.installEventFilter(self)

        # A splitter makes the sidebar drag-resizable; its order flips for L/R.
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("MainSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)
        self._arrange_splitter()
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        row.addWidget(self.splitter)

    def _arrange_splitter(self) -> None:
        """(Re)order sidebar + content for the configured side and apply width."""
        w = max(180, int(self.cfg.sidebar_width or 258))
        side = "right" if self.cfg.sidebar_side == "right" else "left"
        self.sidebar.setParent(None)
        self.stack.setParent(None)
        if side == "left":
            self.splitter.addWidget(self.sidebar)
            self.splitter.addWidget(self.stack)
            self._sidebar_index = 0
            self.splitter.setStretchFactor(0, 0)
            self.splitter.setStretchFactor(1, 1)
            self.splitter.setSizes([w, max(480, self.width() - w)])
        else:
            self.splitter.addWidget(self.stack)
            self.splitter.addWidget(self.sidebar)
            self._sidebar_index = 1
            self.splitter.setStretchFactor(0, 1)
            self.splitter.setStretchFactor(1, 0)
            self.splitter.setSizes([max(480, self.width() - w), w])
        # border on the inner edge of the sidebar
        self.sidebar.setProperty("side", side)
        self.sidebar.style().unpolish(self.sidebar)
        self.sidebar.style().polish(self.sidebar)
        # re-parenting into the splitter re-shows the sidebar — respect collapse
        if getattr(self.cfg, "sidebar_collapsed", False) and hasattr(self, "expand_btn"):
            self.sidebar.setVisible(False)
            self._position_expand_btn()

    def _on_splitter_moved(self, *_) -> None:
        sizes = self.splitter.sizes()
        if self._sidebar_index < len(sizes):
            self.cfg.sidebar_width = max(180, sizes[self._sidebar_index])
            self.cfg.save()

    def set_sidebar_side(self, side: str) -> None:
        side = "right" if side == "right" else "left"
        if side == self.cfg.sidebar_side:
            return
        self.cfg.sidebar_side = side
        self.cfg.save()
        self._arrange_splitter()

    def _sidebar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Sidebar")
        bar.setMinimumWidth(190)
        bar.setMaximumWidth(640)
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(16, 12, 16, 16)
        lay.setSpacing(12)

        # window chrome row: collapse toggle (left) + light/dark toggle (right)
        chrome = QHBoxLayout()
        chrome.setSpacing(4)
        self.collapse_btn = QToolButton()
        self.collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_btn.setToolTip("Hide sidebar")
        self.collapse_btn.clicked.connect(self.toggle_sidebar)
        chrome.addWidget(self.collapse_btn)
        chrome.addStretch(1)
        self.theme_btn = QToolButton()
        self.theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_btn.setToolTip("Switch light/dark theme")
        self.theme_btn.clicked.connect(self._toggle_theme)
        chrome.addWidget(self.theme_btn)
        lay.addLayout(chrome)

        # logo + title + plan chip (Plus / Trial, when signed in)
        head = QHBoxLayout()
        head.setSpacing(10)
        self.logo = QLabel()
        self.logo.setFixedSize(34, 34)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Earshot")
        title.setObjectName("H2")
        head.addWidget(self.logo)
        head.addWidget(title)
        self.plan_chip = QLabel("")
        self.plan_chip.setVisible(False)
        head.addWidget(self.plan_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        lay.addLayout(head)

        # new recording CTA
        self.new_btn = QPushButton("  New recording")
        self.new_btn.setProperty("variant", "danger")
        self.new_btn.setMinimumHeight(44)
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_btn.clicked.connect(self.show_record)
        lay.addWidget(self.new_btn)
        # live-recording state for the CTA (see set_recording)
        self._rec_pulse = QTimer(self)
        self._rec_pulse.setInterval(600)
        self._rec_pulse.timeout.connect(self._pulse_record_icon)
        self._pulse_on = True

        # import an existing recording
        self.import_btn = QPushButton("  Import file")
        self.import_btn.setProperty("variant", "ghost")
        self.import_btn.setMinimumHeight(38)
        self.import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_btn.clicked.connect(self._import_file)
        lay.addWidget(self.import_btn)

        # search — results open in the main window (the project page in search
        # mode), debounced so typing doesn't re-query per keystroke
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search meetings…")
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._run_search)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        self.search_action = self.search.addAction(
            icons.icon("search", self.theme.color("text_faint"), 16),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        lay.addWidget(self.search)

        # nav: home + ask
        self.home_btn = self._nav_button("Overview", self.show_home)
        lay.addWidget(self.home_btn)
        self.ask_btn = self._nav_button("Ask Earshot", self.show_ask)
        lay.addWidget(self.ask_btn)

        # ---- PROJECTS: a collapsible dropdown of project folders ONLY ----
        # No meeting rows in the sidebar any more — clicking a project opens
        # it in the main window, and unfiled notes live in "Uncategorized".
        mid_host = QWidget()
        mid_lay = QVBoxLayout(mid_host)
        mid_lay.setContentsMargins(0, 0, 0, 0)
        mid_lay.setSpacing(8)

        folders_head = QHBoxLayout()
        folders_head.setSpacing(4)
        folders_sect = QLabel("PROJECTS")
        folders_sect.setObjectName("SectionLabel")
        folders_head.addWidget(folders_sect)
        folders_head.addStretch(1)
        self.new_folder_btn = QToolButton()
        self.new_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_folder_btn.setToolTip("New project")
        self.new_folder_btn.clicked.connect(self._new_folder)
        folders_head.addWidget(self.new_folder_btn)
        self.folders_chevron_btn = QToolButton()
        self.folders_chevron_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folders_chevron_btn.clicked.connect(self._toggle_folders_collapsed)
        folders_head.addWidget(self.folders_chevron_btn)
        mid_lay.addLayout(folders_head)

        self.projects_host = QWidget()
        self.projects_lay = QVBoxLayout(self.projects_host)
        self.projects_lay.setContentsMargins(0, 0, 0, 0)
        self.projects_lay.setSpacing(2)
        mid_lay.addWidget(self.projects_host)
        mid_lay.addStretch(1)

        self.sidebar_scroll = QScrollArea()
        self.sidebar_scroll.setObjectName("SidebarScroll")
        self.sidebar_scroll.setWidgetResizable(True)
        self.sidebar_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar_scroll.setWidget(mid_host)
        lay.addWidget(self.sidebar_scroll, 1)

        # bottom cluster: status/upgrade card (conditional) -> Settings -> Help
        # -> account card. Integrations, the theme pill and the version label
        # moved into Settings — the sidebar stays lean.
        self.status_card = self._status_card()
        self.status_card.setVisible(False)
        lay.addWidget(self.status_card)

        self.settings_btn = self._nav_button("Settings", self.show_settings)
        lay.addWidget(self.settings_btn)
        self.help_btn = self._nav_button("Help", self._open_help_menu)
        self.help_btn.setCheckable(False)
        lay.addWidget(self.help_btn)

        self.account_card = AccountCard(self.cfg, self.theme, self.show_account)
        lay.addWidget(self.account_card)

        self._refresh_sidebar_icons()
        return bar

    def _status_card(self) -> QFrame:
        """The Wispr-style prompt card above the bottom nav: shown while a Plus
        trial is running, a payment failed, or auto-renewal is off. Content is
        rendered from the cached /v1/me snapshot in refresh_plan_state()."""
        card = QFrame()
        card.setObjectName("StatusCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(6)
        self.status_title = QLabel("")
        self.status_title.setObjectName("H3")
        self.status_title.setWordWrap(True)
        cl.addWidget(self.status_title)
        self.status_body = QLabel("")
        self.status_body.setObjectName("Muted")
        self.status_body.setWordWrap(True)
        cl.addWidget(self.status_body)
        self.status_btn = QPushButton("Upgrade")
        self.status_btn.setProperty("variant", "primary")
        self.status_btn.setMinimumHeight(34)
        self.status_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status_btn.clicked.connect(lambda: self.show_settings("plans"))
        cl.addWidget(self.status_btn)
        return card

    def _nav_button(self, text: str, slot) -> QPushButton:
        b = QPushButton("  " + text)
        b.setProperty("variant", "ghost")
        b.setCheckable(True)
        b.setMinimumHeight(40)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(slot)
        return b

    # ---------- navigation ----------
    def _set_active(self, which: str) -> None:
        """Highlight the active sidebar affordance: the three nav buttons plus
        the project rows (which is "project" while a project page is open)."""
        self.home_btn.setChecked(which == "home")
        self.ask_btn.setChecked(which == "ask")
        self.settings_btn.setChecked(which == "settings")
        if which != "project":
            self._active_project = None
        self._sync_project_checks()

    def show_home(self) -> None:
        self.home.refresh()
        self.stack.setCurrentWidget(self.home)
        self._set_active("home")

    def show_ask(self) -> None:
        self.ask.on_shown()
        self.stack.setCurrentWidget(self.ask)
        self._set_active("ask")

    def show_record(self) -> None:
        self.record.on_shown()
        self.stack.setCurrentWidget(self.record)
        self._set_active("record")

    def show_settings(self, section: str | None = None) -> None:
        """Open Settings, optionally straight to a section key (general / audio /
        transcription / ai / integrations / about / account / plans). The nav
        button's clicked(bool) also lands here — only a real key switches pane."""
        if isinstance(section, str) and section:
            self.settings.show_section(section)
        self.stack.setCurrentWidget(self.settings)
        self._set_active("settings")

    def show_integrations(self) -> None:
        self.show_settings("integrations")

    def show_account(self) -> None:
        self.show_settings("account")

    def show_help(self) -> None:
        self.stack.setCurrentWidget(self.help)
        self._set_active("none")

    def show_project(self, folder_id) -> None:
        """Open a project in the main window: an int folder id, or None for the
        Uncategorized project (every meeting not filed anywhere)."""
        self.project.load_folder(folder_id)
        self.stack.setCurrentWidget(self.project)
        self._active_project = ("folder", folder_id)
        self._set_active("project")

    def _open_help_menu(self) -> None:
        """The sidebar Help button: a small menu popping up above the button,
        Wispr-style — the Help Center guide, release notes, and outside links."""
        import webbrowser

        from .page_help import ISSUES_URL, WEBSITE_URL

        menu = QMenu(self)
        c = self.theme.color("text_muted")
        menu.addAction(icons.icon("book-open", c, 16), "Help Center", self.show_help)
        menu.addAction(icons.icon("info", c, 16), "What's new",
                       lambda: self.show_settings("about"))
        menu.addSeparator()

        def _open(url: str) -> None:
            try:
                webbrowser.open(url)
            except Exception:
                pass

        menu.addAction(icons.icon("external-link", c, 16), "Visit tryearshot.app",
                       lambda: _open(WEBSITE_URL))
        menu.addAction(icons.icon("alert-triangle", c, 16), "Report an issue",
                       lambda: _open(ISSUES_URL))
        pos = self.help_btn.mapToGlobal(QPoint(0, 0))
        pos.setY(pos.y() - menu.sizeHint().height() - 6)
        menu.exec(pos)

    def on_account_changed(self) -> None:
        """Called after signing in to / out of Earshot Plus: rebuild the Settings
        sections (Transcription/AI hide in cloud mode) and refresh the sidebar."""
        if hasattr(self.settings, "refresh_tabs"):
            self.settings.refresh_tabs()
        self.refresh_account_card()
        self.refresh_plan_state()

    # ---------- sidebar collapse ----------
    def toggle_sidebar(self) -> None:
        self.cfg.sidebar_collapsed = not self.cfg.sidebar_collapsed
        self.cfg.save()
        self._apply_sidebar_collapsed()

    def _apply_sidebar_collapsed(self) -> None:
        collapsed = bool(self.cfg.sidebar_collapsed)
        self.sidebar.setVisible(not collapsed)
        self.expand_btn.setVisible(collapsed)
        if collapsed:
            self._position_expand_btn()
        else:
            self._arrange_splitter()

    def _position_expand_btn(self) -> None:
        """Keep the floating expand button pinned to the content's top corner on
        the side the sidebar lives on."""
        m = 10
        if self.cfg.sidebar_side == "right":
            x = max(m, self.stack.width() - self.expand_btn.width() - m)
        else:
            x = m
        self.expand_btn.move(x, m)
        self.expand_btn.raise_()

    # ---------- plan chip + status card ----------
    def refresh_plan_state(self) -> None:
        """Render the logo plan chip and the sidebar status card from the cached
        /v1/me snapshot (cfg.extra) — no network calls here."""
        cloud = self.cfg.account_mode == "cloud"
        status = (self.cfg.extra.get("cloud_sub_status") or "").strip()
        end = (self.cfg.extra.get("cloud_period_end") or "").strip()

        if cloud:
            trialing = status in ("trialing", "beta")
            self.plan_chip.setText("Plus Trial" if trialing else "Plus")
            self.plan_chip.setStyleSheet(
                f"background:{self.theme.color('primary_soft')}; color:{self.theme.color('primary')};"
                f"border-radius:8px; padding:2px 8px; font-size:11px; font-weight:700;"
            )
            self.plan_chip.setVisible(True)
        else:
            self.plan_chip.setVisible(False)

        show_card = False
        if cloud and status in ("trialing", "beta"):
            self.status_title.setText("Plus trial active")
            self.status_body.setText(
                f"Your trial ends {end}. Upgrade to keep Plus features." if end
                else "Upgrade to keep Plus features when your trial ends."
            )
            self.status_btn.setText("Upgrade to Plus")
            show_card = True
        elif cloud and status == "past_due":
            self.status_title.setText("Payment needs attention")
            self.status_body.setText("Update billing to keep Earshot Plus running.")
            self.status_btn.setText("Fix billing")
            show_card = True
        elif cloud and status == "canceled":
            self.status_title.setText("Auto-renewal is off")
            self.status_body.setText(
                f"Plus stays active until {end}. Renew to keep it." if end
                else "Renew to keep Earshot Plus."
            )
            self.status_btn.setText("Renew")
            show_card = True
        self.status_card.setVisible(show_card)

    @staticmethod
    def _headless() -> bool:
        """True under the offscreen Qt platform (tests / CI) — where auto-opening
        a modal wizard would block the event loop."""
        import os

        return os.environ.get("QT_QPA_PLATFORM", "") == "offscreen"

    def run_onboarding(self) -> None:
        """Re-run the setup wizard from Settings. Non-mandatory here (the user
        already finished it once) — closable like any dialog. After it closes,
        reflect any account/settings changes it made."""
        from .onboarding import OnboardingDialog
        dlg = OnboardingDialog(self, self.cfg, self.theme, shell=self)
        dlg.exec()
        self.on_account_changed()
        # (the Account pane refreshes itself via refresh_tabs -> show_section)

    def refresh_account_card(self) -> None:
        """Called after the account display name changes (from the Account page)
        so the sidebar card reflects it immediately."""
        if hasattr(self, "account_card"):
            self.account_card.refresh()

    def open_meeting(self, meeting_id: int) -> None:
        self.detail.load(int(meeting_id))
        self.stack.setCurrentWidget(self.detail)
        self._set_active("none")

    # ---------- call auto-detection ----------
    def _on_call_started(self, apps: list) -> None:
        if not self.cfg.call_detect_enabled or self.record.is_busy():
            return
        from .call_watcher import CallToast
        who = apps[0] if apps else "another app"
        self._call_toast = CallToast(
            f"Looks like a call just started in {who}. Record it with Earshot?",
            accept_text="Record now",
            on_accept=self._record_from_prompt,
            on_dismiss=self.call_watcher.snooze_until_idle,
        )
        self._call_toast.show_toast()

    def _record_from_prompt(self) -> None:
        self.show_record()          # loads devices / defaults
        self.record._start()        # start immediately with the saved defaults
        self.raise_()

    def _on_call_ended(self) -> None:
        if not (self.record.recorder and self.record.recorder.running):
            return
        from .call_watcher import CallToast
        self._call_toast = CallToast(
            "The call seems to have ended. Stop recording and process the meeting?",
            accept_text="Stop & process",
            on_accept=self.record._stop,
        )
        self._call_toast.show_toast()

    # ---------- crash salvage ----------
    def _salvage_interrupted(self) -> None:
        """If a previous session died mid-recording, its raw spool files are
        still in the meeting folder — stream them into proper WAVs so the
        meeting is recoverable instead of lost."""
        from pathlib import Path

        from ..audio import writer as wr
        from ..paths import recordings_dir
        from .workers import FuncWorker

        try:
            root = recordings_dir()
            jobs = []
            for m in self.repo.list():
                folder = Path(m.audio_dir) if m.audio_dir else (root / f"meeting_{m.id:06d}")
                if (folder / wr.SIDECAR).exists():
                    jobs.append((m.id, folder))
        except Exception:
            return  # salvage is best-effort — never crash startup over it
        if not jobs:
            return
        repo = self.repo

        def job(progress):
            recovered = []
            for mid, folder in jobs:
                progress(f"Recovering interrupted recording…")
                try:
                    res = wr.salvage_spool(folder)
                except Exception:
                    continue
                if res and res.get("frames"):
                    repo.update(
                        mid, audio_dir=str(folder), duration_secs=res["duration_secs"],
                        status="Recorded",
                        error="Recovered after an interrupted session — open it and Re-transcribe.",
                    )
                    recovered.append(mid)
            return recovered

        self._salvage_worker = FuncWorker(job)
        self._salvage_worker.done.connect(lambda _r: self.notify_data_changed())
        self._salvage_worker.start()

    # ---------- import an existing file ----------
    def _import_file(self) -> None:
        import os
        import shutil

        from PySide6.QtWidgets import QFileDialog

        from .. import paths
        from ..util.dates import today_pair
        from .workers import FuncWorker

        path, _sel = QFileDialog.getOpenFileName(
            self, "Import audio or video file", "",
            "Media files (*.mp3 *.wav *.m4a *.mp4 *.mov *.aac *.flac *.ogg *.opus *.webm *.mkv);;All files (*.*)",
        )
        if not path:
            return

        human, iso = today_pair()
        base = os.path.splitext(os.path.basename(path))[0]
        meeting = self.repo.create(date_text=human, date_iso=iso, attendees=[], agenda="")
        self.repo.update(meeting.id, title=f"Imported — {base}")
        mdir = paths.meeting_dir(meeting.id)
        os.makedirs(mdir, exist_ok=True)
        dest = os.path.join(mdir, "import" + os.path.splitext(path)[1].lower())
        try:
            shutil.copy2(path, dest)
        except OSError as e:
            self.repo.delete(meeting.id)
            QMessageBox.critical(self, "Import failed", f"Could not copy the file: {e}")
            return
        self.repo.update(meeting.id, audio_dir=mdir)
        self.notify_data_changed()
        self.open_meeting(meeting.id)

        repo, cfg, mid = self.repo, self.cfg, meeting.id

        def job(progress):
            from ..pipeline.processing import process_imported_file
            process_imported_file(repo, mid, cfg, dest, progress=progress, summarize=cfg.auto_summary)
            return mid

        self._import_worker = FuncWorker(job)
        self._import_worker.progress.connect(
            lambda msg, m=mid: self.detail.status_label.setText(msg) if self.detail.meeting_id == m else None
        )
        self._import_worker.done.connect(lambda _r, m=mid: self._on_import_done(m))
        self._import_worker.failed.connect(self._on_import_failed)
        self._import_worker.start()

    def _on_import_done(self, mid: int) -> None:
        self.notify_data_changed()
        if self.detail.meeting_id == mid:
            self.detail.refresh()

    def _on_import_failed(self, msg: str) -> None:
        self.notify_data_changed()
        QMessageBox.critical(self, "Import failed", msg)

    # ---------- data + theme ----------
    def notify_data_changed(self) -> None:
        self._rebuild_projects()
        current = self.stack.currentWidget()
        if current is self.home:
            self.home.refresh()
        elif current is self.project:
            self.project.refresh()

    def _rebuild_projects(self) -> None:
        """(Re)build the sidebar PROJECTS rows: one row per project folder plus
        an always-present Uncategorized project for unfiled notes. Rows only —
        the meetings themselves open in the main window."""
        lay = self.projects_lay
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        meetings = self.repo.list()
        counts: dict = {}
        uncat = 0
        for m in meetings:
            if m.folder_id is None:
                uncat += 1
            else:
                counts[m.folder_id] = counts.get(m.folder_id, 0) + 1

        self._project_rows = {}
        for f in self.repo.list_folders():
            lay.addWidget(self._project_row(f.name, counts.get(f.id, 0), f.id, f.color))
        lay.addWidget(self._project_row("Uncategorized", uncat, None, None))

        self._apply_folders_collapsed()
        self._sync_project_checks()

    def _project_row(self, name: str, count: int, folder_id, color: str | None) -> QPushButton:
        btn = QPushButton(f"  {name}  ({count})".replace("&", "&&"))
        btn.setProperty("variant", "ghost")
        btn.setCheckable(True)
        btn.setMinimumHeight(36)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(name)
        icon_color = color if color else self.theme.color("text_faint")
        btn.setIcon(icons.icon("folder", icon_color, 16))
        btn.clicked.connect(lambda _=False, fid=folder_id: self.show_project(fid))
        if folder_id is not None:  # Uncategorized can't be renamed/deleted
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, fid=folder_id, b=btn: self._on_project_context_menu(fid, b.mapToGlobal(pos))
            )
        self._project_rows[folder_id] = btn
        return btn

    def _sync_project_checks(self) -> None:
        active = self._active_project
        for fid, btn in getattr(self, "_project_rows", {}).items():
            btn.setChecked(active == ("folder", fid))

    def _apply_folders_collapsed(self) -> None:
        # header (with [+]) stays visible; only the project rows fold away
        self.projects_host.setVisible(not bool(self.cfg.folders_collapsed))
        self._set_folders_chevron_icon()

    def eventFilter(self, obj, event):
        # keep the floating expand button pinned while the sidebar is collapsed
        if (
            obj is getattr(self, "stack", None)
            and event.type() in (QEvent.Type.Resize, QEvent.Type.Show)
            and getattr(self, "expand_btn", None) is not None
            and self.expand_btn.isVisible()
        ):
            self._position_expand_btn()
        return super().eventFilter(obj, event)

    def _toggle_folders_collapsed(self) -> None:
        self.cfg.folders_collapsed = not self.cfg.folders_collapsed
        self.cfg.save()
        self._apply_folders_collapsed()

    def _set_folders_chevron_icon(self) -> None:
        collapsed = bool(self.cfg.folders_collapsed)
        self.folders_chevron_btn.setIcon(
            icons.icon("chevron-right" if collapsed else "chevron-down", self.theme.color("text_muted"), 14)
        )
        self.folders_chevron_btn.setToolTip("Expand projects" if collapsed else "Collapse projects")

    # ---------- search (results open in the main window) ----------
    def _run_search(self) -> None:
        text = self.search.text().strip()
        if text:
            self.project.load_search(text)
            if self.stack.currentWidget() is not self.project:
                self.stack.setCurrentWidget(self.project)
            self._set_active("none")
        elif self.stack.currentWidget() is self.project and self.project.mode[0] == "search":
            self.show_home()  # cleared the box while looking at results

    # ---------- folders ----------
    def _new_folder(self) -> None:
        result = ask_new_folder(self, self.theme)
        if result is None:
            return
        name, color = result
        self.repo.create_folder(name, color)
        self.notify_data_changed()

    def _on_project_context_menu(self, folder_id: int, global_pos: QPoint) -> None:
        folders = {f.id: f for f in self.repo.list_folders()}
        folder = folders.get(folder_id)
        if folder is None:
            return

        menu = QMenu(self)
        rename_act = menu.addAction("Rename…")
        color_menu = menu.addMenu("Colour")
        color_actions = {}
        for name, hexcolor in FOLDER_COLORS:
            act = color_menu.addAction(self._color_icon(hexcolor), name)
            color_actions[act] = hexcolor
        menu.addSeparator()
        delete_act = menu.addAction("Delete project")

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen is rename_act:
            self._rename_folder(folder_id, folder.name)
        elif chosen is delete_act:
            self._delete_folder(folder_id, folder.name)
            # if the deleted project was open, land on Uncategorized (where its
            # meetings just moved) — the delete may also have been cancelled
            if (self._active_project == ("folder", folder_id)
                    and folder_id not in {f.id for f in self.repo.list_folders()}):
                self.show_project(None)
        elif chosen in color_actions:
            self.repo.update_folder(folder_id, color=color_actions[chosen])
            self.notify_data_changed()

    @staticmethod
    def _color_icon(hexcolor: str) -> QIcon:
        """A small rounded coloured-square QIcon — used for the folder colour
        submenu (each swatch shows its own colour, not the app's neutral icon set)."""
        pm = QPixmap(14, 14)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setBrush(QColor(hexcolor))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, 14, 14, 3, 3)
        painter.end()
        return QIcon(pm)

    def _rename_folder(self, folder_id: int, current_name: str) -> None:
        new_name = ask_rename_folder(self, self.theme, current_name)
        if new_name is None:
            return
        self.repo.update_folder(folder_id, name=new_name)
        self.notify_data_changed()

    def _delete_folder(self, folder_id: int, name: str) -> None:
        if QMessageBox.question(
            self, "Delete project",
            f"Delete “{name}”? Its meetings are kept and move to Uncategorized."
        ) != QMessageBox.StandardButton.Yes:
            return
        self.repo.delete_folder(folder_id)
        self.notify_data_changed()

    def _toggle_theme(self) -> None:
        self.theme.toggle()

    def _on_theme_changed(self, _mode: str) -> None:
        self._refresh_sidebar_icons()
        self._rebuild_projects()
        # settings cascades to its embedded Account/Plans/Integrations panes;
        # the project page rebuilds its rows itself
        for page in (self.home, self.record, self.detail, self.settings, self.ask,
                     self.help, self.project):
            page.apply_theme()
        self.refresh_plan_state()  # chip + status card bake colours into styles
        # rebuild colour-dependent content
        self.home.refresh()
        if self.detail.meeting_id is not None:
            self.detail.refresh()

    def set_recording(self, active: bool) -> None:
        """Reflect a live recording in the sidebar CTA: the label flips to
        'Recording' and the record dot pulses until the recording stops."""
        if active:
            self.new_btn.setText("  Recording")
            self._pulse_on = True
            self._rec_pulse.start()
        else:
            self._rec_pulse.stop()
            self.new_btn.setText("  New recording")
            self.new_btn.setIcon(icons.icon("record", self.theme.color("on_danger"), 16))

    def _pulse_record_icon(self) -> None:
        self._pulse_on = not self._pulse_on
        color = self.theme.color("on_danger") if self._pulse_on else self.theme.color("danger_press")
        self.new_btn.setIcon(icons.icon("record", color, 16))

    def _refresh_sidebar_icons(self) -> None:
        # brand mark (the indigo tile is part of the SVG, so no QSS background)
        self.logo.setPixmap(logo.logo_pixmap(34))
        self.logo.setStyleSheet("")
        if not self._rec_pulse.isActive():
            self.new_btn.setIcon(icons.icon("record", self.theme.color("on_danger"), 16))
        self.import_btn.setIcon(icons.icon("upload", self.theme.color("text_muted"), 16))
        self.home_btn.setIcon(icons.icon("home", self.theme.color("text_muted"), 18))
        self.ask_btn.setIcon(icons.icon("message", self.theme.color("text_muted"), 18))
        self.settings_btn.setIcon(icons.icon("settings", self.theme.color("text_muted"), 18))
        self.help_btn.setIcon(icons.icon("help-circle", self.theme.color("text_muted"), 18))
        self.collapse_btn.setIcon(icons.icon("panel-left", self.theme.color("text_muted"), 17))
        # the theme button previews the mode you'd switch TO
        self.theme_btn.setIcon(icons.icon(
            "sun" if self.theme.mode == "dark" else "moon", self.theme.color("text_muted"), 17))
        if hasattr(self, "expand_btn"):
            self.expand_btn.setIcon(icons.icon("panel-left", self.theme.color("text_muted"), 16))
        self.new_folder_btn.setIcon(icons.icon("plus", self.theme.color("text_muted"), 14))
        self._set_folders_chevron_icon()
        if hasattr(self, "account_card"):
            self.account_card.refresh()
        if hasattr(self, "search_action"):
            self.search_action.setIcon(icons.icon("search", self.theme.color("text_faint"), 16))

    # ---------- close guard ----------
    def closeEvent(self, event):
        from . import workers
        if self.record.is_busy() or workers.active_count():
            if QMessageBox.question(
                self, "Work in progress",
                "A recording or background task (transcription, summary, import…) is still "
                "running and will be interrupted. Quit anyway?"
            ) != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.record._hide_overlay()  # don't leave the floating overlay behind
        # Join background FuncWorkers before the interpreter tears down — exiting
        # with a live QThread aborts the process (0xC0000409) instead of a clean
        # quit. This covers the transcription/summary/import/finalise workers the
        # guard above warns about.
        workers.join_all()
        event.accept()
