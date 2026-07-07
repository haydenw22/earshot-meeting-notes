"""Plans & Billing pane (shown inside Settings, under ACCOUNT).

A status banner (trial countdown / renewal / payment trouble, plus the usage
meter when signed in) above two plan cards: Free (self-host) and Earshot Plus.
Live account data comes from GET /v1/me in a worker thread and is cached in
cfg.extra (cloud_sub_status / cloud_period_end / cloud_billing_url) so the
sidebar can show trial prompts without its own network calls.
"""
from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .widgets import add_shadow, make_chip

FREE_FEATURES = [
    "Record unlimited meetings, stored on this PC",
    "Bring your own transcription: home server, Groq, Mistral, OpenAI or Deepgram",
    "Bring your own AI: Claude, any OpenAI-compatible cloud, or fully local",
    "Projects, action items, prep briefs and Ask Earshot",
    "Todoist and webhook integrations",
]

PLUS_FEATURES = [
    "Everything in Free",
    "Managed transcription, no server or API keys to set up",
    "AI notes, action items and Ask Earshot included",
    "Monthly transcription allowance with a live usage meter",
    "Your recordings still live on this PC",
]


class PlansPage(QWidget):
    def __init__(self, shell, repo, cfg, theme):
        super().__init__()
        self.shell = shell
        self.repo = repo
        self.cfg = cfg
        self.theme = theme
        self._me_worker = None
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 28, 40, 28)
        root.setSpacing(16)

        head = QLabel("Plans & Billing")
        head.setObjectName("H1")
        root.addWidget(head)
        sub = QLabel("Self-host for free, forever. Or let Earshot Plus handle the setup for you.")
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

    # ---------- (re)build ----------
    def _rebuild_cards(self) -> None:
        lay = self._host_lay
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        # per-mode widgets from the previous build are deleted C++ objects now
        for attr in ("usage_bar", "usage_lbl", "banner_title", "banner_sub",
                     "upgrade_btn", "billing_btn"):
            if hasattr(self, attr):
                delattr(self, attr)
        lay.addWidget(self._banner_card())
        lay.addWidget(self._plans_row())
        lay.addStretch(1)
        if self._cloud:
            self._fetch_me()

    def refresh(self) -> None:
        """Rebuild for the current account mode (sign-in/out, page shown)."""
        self._rebuild_cards()

    def apply_theme(self) -> None:
        self._rebuild_cards()

    @property
    def _cloud(self) -> bool:
        return self.cfg.account_mode == "cloud"

    # ---------- status banner ----------
    def _banner_copy(self) -> tuple[str, str]:
        if not self._cloud:
            return ("You're on the Free plan",
                    "Self-hosted with your own keys, free forever. Earshot Plus adds managed "
                    "transcription and AI for $9 a month.")
        from ..util.dates import friendly_day

        status = (self.cfg.extra.get("cloud_sub_status") or "").strip()
        end = friendly_day(self.cfg.extra.get("cloud_period_end") or "")
        if status in ("trialing", "beta"):
            when = f" It ends {end}." if end else ""
            return ("Your Earshot Plus trial is active",
                    f"Enjoy full access while it lasts.{when} Upgrade to keep managed "
                    "transcription and AI without interruption.")
        if status == "past_due":
            return ("Your payment needs attention",
                    "The last payment didn't go through. Update your billing details to keep "
                    "Earshot Plus running.")
        if status == "canceled":
            until = f" Plus stays active until {end}." if end else ""
            return ("Your subscription is ending",
                    f"Auto-renewal is off.{until} Renew any time to keep managed transcription "
                    "and AI.")
        renews = f" Renews {end}." if end else ""
        return ("Earshot Plus is active", f"Thanks for supporting Earshot.{renews}")

    def _banner_card(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName("PlanBanner")
        bl = QVBoxLayout(banner)
        bl.setContentsMargins(22, 18, 22, 20)
        bl.setSpacing(8)

        title, sub = self._banner_copy()
        self.banner_title = QLabel(title)
        self.banner_title.setObjectName("H3")
        bl.addWidget(self.banner_title)
        self.banner_sub = QLabel(sub)
        self.banner_sub.setObjectName("Muted")
        self.banner_sub.setWordWrap(True)
        bl.addWidget(self.banner_sub)

        if self._cloud:
            self.usage_lbl = QLabel("Loading usage…")
            self.usage_lbl.setObjectName("Faint")
            bl.addWidget(self.usage_lbl)
            self.usage_bar = QProgressBar()
            self.usage_bar.setTextVisible(False)
            self.usage_bar.setRange(0, 100)
            self.usage_bar.setValue(0)
            bl.addWidget(self.usage_bar)
        return banner

    # ---------- plan cards ----------
    def _plans_row(self) -> QWidget:
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(16)
        rl.addWidget(self._plan_card(
            kicker="SELF-HOST", name="Free", price="$0",
            price_note="free forever",
            features=FREE_FEATURES,
            current=not self._cloud,
        ), 1)
        rl.addWidget(self._plan_card(
            kicker="MANAGED", name="Earshot Plus", price="$9",
            price_note="per month",
            features=PLUS_FEATURES,
            current=self._cloud,
        ), 1)
        return row

    def _plan_card(self, *, kicker: str, name: str, price: str, price_note: str,
                   features: list[str], current: bool) -> QFrame:
        card = QFrame()
        card.setObjectName("PlanCard")
        card.setProperty("current", "true" if current else "false")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        add_shadow(card)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(22, 18, 22, 20)
        cl.setSpacing(12)

        top = QHBoxLayout()
        k = QLabel(kicker)
        k.setObjectName("PlanKicker")
        top.addWidget(k)
        top.addStretch(1)
        if current:
            top.addWidget(make_chip("Current plan", fg=self.theme.color("primary"),
                                    bg=self.theme.color("primary_soft")))
        cl.addLayout(top)

        n = QLabel(name)
        n.setObjectName("H2")
        cl.addWidget(n)

        price_row = QHBoxLayout()
        price_row.setSpacing(6)
        p = QLabel(price)
        p.setObjectName("PlanPrice")
        price_row.addWidget(p)
        pn = QLabel(price_note)
        pn.setObjectName("Muted")
        price_row.addWidget(pn, 0, Qt.AlignmentFlag.AlignBottom)
        price_row.addStretch(1)
        cl.addLayout(price_row)

        for line in features:
            frow = QHBoxLayout()
            frow.setSpacing(8)
            dot = QLabel()
            dot.setPixmap(icons.pixmap("check", self.theme.color("success"), 15))
            frow.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
            lbl = QLabel(line)
            lbl.setObjectName("Muted")
            lbl.setWordWrap(True)
            frow.addWidget(lbl, 1)
            cl.addLayout(frow)
        cl.addStretch(1)

        # action button: upgrade on the Plus card (selfhost), manage billing when
        # subscribed; the Free card explains how to switch back instead
        if name == "Earshot Plus":
            if self._cloud:
                self.billing_btn = QPushButton("Manage billing")
                self.billing_btn.setProperty("variant", "primary")
                self.billing_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                self.billing_btn.clicked.connect(self._manage_billing)
                cl.addWidget(self.billing_btn)
            else:
                self.upgrade_btn = QPushButton("  Upgrade to Plus")
                self.upgrade_btn.setProperty("variant", "primary")
                self.upgrade_btn.setIcon(icons.icon("cloud", self.theme.color("on_primary"), 15))
                self.upgrade_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                self.upgrade_btn.clicked.connect(self._open_link_dialog)
                cl.addWidget(self.upgrade_btn)
        elif self._cloud:
            hint = QLabel("Switch back any time by signing out on the Account page.")
            hint.setObjectName("Faint")
            hint.setWordWrap(True)
            cl.addWidget(hint)
        return card

    # ---------- actions ----------
    def _open_link_dialog(self) -> None:
        from .cloud_link import CloudLinkDialog
        dlg = CloudLinkDialog(self, self.cfg, self.theme, on_linked=self._on_linked)
        dlg.exec()

    def _on_linked(self, _data: dict) -> None:
        if hasattr(self.shell, "on_account_changed"):
            self.shell.on_account_changed()
        self.refresh()

    def _manage_billing(self) -> None:
        url = (self.cfg.extra.get("cloud_billing_url") or "").strip() \
            or self.cfg.cloud_api_base.rstrip("/") + "/account"
        try:
            webbrowser.open(url)
        except Exception:
            pass

    # ---------- live account data ----------
    def _fetch_me(self) -> None:
        """GET /v1/me off the GUI thread; cache the snapshot for the sidebar and
        refresh the banner + meter. Degrades to an offline label on failure."""
        from .workers import FuncWorker
        base, token = self.cfg.cloud_api_base, self.cfg.cloud_token
        if not token:  # cloud mode without a token (tests / odd states): no fetch
            self._on_me_failed("not signed in")
            return

        def job(_progress):
            from ..transcription import earshot_client
            return earshot_client.get_me(base, token)

        self._me_worker = FuncWorker(job)
        self._me_worker.done.connect(self._on_me)
        self._me_worker.failed.connect(self._on_me_failed)
        self._me_worker.start()

    def _on_me(self, data: dict) -> None:
        if not hasattr(self, "usage_bar"):  # page rebuilt while the fetch was in flight
            return
        if not isinstance(data, dict):
            self._on_me_failed("offline")
            return
        # cache for the sidebar status card / plan chip (no network there)
        self.cfg.extra["cloud_sub_status"] = data.get("sub_status") or ""
        self.cfg.extra["cloud_period_end"] = data.get("period_end") or ""
        if data.get("billing_url"):
            self.cfg.extra["cloud_billing_url"] = data.get("billing_url")
        self.cfg.save()

        title, sub = self._banner_copy()
        self.banner_title.setText(title)
        self.banner_sub.setText(sub)
        usage = data.get("usage") or {}
        used = float(usage.get("transcribe_seconds") or 0.0)
        cap = float(usage.get("cap_seconds") or 0.0)
        if cap > 0:
            pct = max(0, min(100, int(round(100 * used / cap))))
            self.usage_bar.setValue(pct)
            self.usage_lbl.setText(
                f"Transcription this month: {self._fmt_hours(used)} of {self._fmt_hours(cap)} ({pct}%)"
            )
        else:
            self.usage_bar.setValue(0)
            self.usage_lbl.setText(f"Transcription this month: {self._fmt_hours(used)}")
        if hasattr(self.shell, "refresh_plan_state"):
            self.shell.refresh_plan_state()

    def _on_me_failed(self, _msg: str) -> None:
        if not hasattr(self, "usage_lbl"):
            return
        self.usage_lbl.setText("Usage unavailable, you appear to be offline.")
        self.usage_bar.setValue(0)

    @staticmethod
    def _fmt_hours(seconds: float) -> str:
        hours = seconds / 3600.0
        if hours >= 1:
            return f"{hours:.1f} h"
        return f"{int(round(seconds / 60))} min"
