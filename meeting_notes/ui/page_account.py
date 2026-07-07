"""Account page.

Two states, driven by cfg.account_mode:
  - "selfhost" — a local display name, an "Earshot Plus" pitch card (sign in /
    subscribe → the device-link dialog; learn more → tryearshot.app), and a note
    that everything currently lives on this PC.
  - "cloud" — signed in to Earshot Plus: a subscription card with the email,
    plan/status chip, renewal date and a usage meter (fetched from GET /v1/me in
    a worker thread, degrading gracefully to an "offline" label), plus manage
    billing and sign out.
"""
from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .widgets import Card, make_chip

LEARN_MORE_URL = "https://tryearshot.app"

PLUS_PITCH = [
    "Managed transcription — no home server or API keys to set up.",
    "AI meeting notes, action-item suggestions and Ask Earshot, all included.",
    "Your recordings still live on this PC — only audio you transcribe is sent.",
]

# sub_status slug -> (label, foreground-role, background-role)
_STATUS_CHIP = {
    "active": ("Active", "success", "success_soft"),
    "trialing": ("Trial", "primary", "primary_soft"),
    "beta": ("Beta", "primary", "primary_soft"),
    "past_due": ("Past due", "warning", "warning_soft"),
    "canceled": ("Canceled", "danger", "danger_soft"),
    "none": ("No subscription", "text_muted", "surface_hover"),
}


class AccountPage(QWidget):
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

        head = QLabel("Account")
        head.setObjectName("H1")
        root.addWidget(head)

        outer_w = QWidget()
        outer = QVBoxLayout(outer_w)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        host = QWidget()
        self._host_lay = QVBoxLayout(host)
        self._host_lay.setContentsMargins(2, 16, 2, 2)
        self._host_lay.setSpacing(16)
        scroll.setWidget(host)
        outer.addWidget(scroll)
        root.addWidget(outer_w, 1)

        self._rebuild_cards()
        self.apply_theme()

    def _rebuild_cards(self) -> None:
        lay = self._host_lay
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        # drop references to per-mode widgets from the previous build — they're
        # now deleted C++ objects, so touching them (e.g. from apply_theme) would
        # raise. Whichever card is rebuilt below re-sets the ones it owns.
        for attr in ("subscribe_btn", "learn_btn", "billing_btn", "signout_btn",
                     "usage_bar", "usage_lbl", "renewal_lbl", "status_chip_holder"):
            if hasattr(self, attr):
                delattr(self, attr)
        lay.addWidget(self._profile_card())
        if self.cfg.account_mode == "cloud":
            lay.addWidget(self._subscription_card())
        else:
            lay.addWidget(self._plus_card())
        lay.addWidget(self._data_card())
        lay.addStretch(1)

    def refresh(self) -> None:
        """Rebuild the cards for the current account mode (called on sign-in /
        sign-out and whenever the account page is shown)."""
        self._rebuild_cards()
        self.apply_theme()

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

    # ---------- Profile ----------
    def _profile_card(self) -> Card:
        card, cl = self._card("Profile")

        row = QHBoxLayout()
        row.setSpacing(14)
        self.avatar = QLabel()
        self.avatar.setFixedSize(56, 56)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.avatar)

        form = QVBoxLayout()
        form.setSpacing(4)
        name_lbl = QLabel("Display name")
        name_lbl.setObjectName("Muted")
        form.addWidget(name_lbl)
        self.name_edit = QLineEdit(self.cfg.account_name)
        self.name_edit.setPlaceholderText("Guest")
        self.name_edit.editingFinished.connect(self._on_name_changed)
        form.addWidget(self.name_edit)
        row.addLayout(form, 1)
        cl.addLayout(row)

        helper = QLabel("Used for the account card and future sharing features.")
        helper.setObjectName("Faint")
        helper.setWordWrap(True)
        cl.addWidget(helper)

        self._update_avatar()
        return card

    def _on_name_changed(self) -> None:
        self.cfg.account_name = self.name_edit.text().strip()
        self.cfg.save()
        self._update_avatar()
        self.shell.refresh_account_card()

    def _update_avatar(self) -> None:
        name = (self.cfg.account_name or "").strip() or "Guest"
        initial = name[0].upper()
        self.avatar.setStyleSheet(
            f"background:{self.theme.color('primary_soft')}; color:{self.theme.color('primary')};"
            f"border-radius:28px; font-size:22px; font-weight:700;"
        )
        self.avatar.setText(initial)

    # ---------- Earshot Plus pitch (selfhost mode) ----------
    def _plus_card(self) -> Card:
        card, cl = self._card(
            "Earshot Plus",
            "Managed transcription and AI, from $9/mo — no keys, no home server. Everything you "
            "record still lives on this PC.",
        )
        for line in PLUS_PITCH:
            row = QHBoxLayout()
            row.setSpacing(8)
            dot = QLabel()
            dot.setPixmap(icons.pixmap("check", self.theme.color("primary"), 15))
            row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
            lbl = QLabel(line)
            lbl.setObjectName("Muted")
            lbl.setWordWrap(True)
            row.addWidget(lbl, 1)
            cl.addLayout(row)

        btn_row = QHBoxLayout()
        self.subscribe_btn = QPushButton("  Sign in / Subscribe")
        self.subscribe_btn.setProperty("variant", "primary")
        self.subscribe_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.subscribe_btn.clicked.connect(self._open_link_dialog)
        btn_row.addWidget(self.subscribe_btn)
        self.learn_btn = QPushButton("Learn more")
        self.learn_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.learn_btn.clicked.connect(lambda: self._open_url(LEARN_MORE_URL))
        btn_row.addWidget(self.learn_btn)
        btn_row.addStretch(1)
        cl.addLayout(btn_row)
        return card

    def _open_link_dialog(self) -> None:
        from .cloud_link import CloudLinkDialog
        dlg = CloudLinkDialog(self, self.cfg, self.theme, on_linked=self._on_linked)
        dlg.exec()

    def _on_linked(self, _data: dict) -> None:
        # cfg already updated by the dialog; refresh this page + the settings tabs
        # + the sidebar card so the new signed-in state shows everywhere.
        if hasattr(self.shell, "on_account_changed"):
            self.shell.on_account_changed()
        self.refresh()

    # ---------- Subscription (cloud mode) ----------
    def _subscription_card(self) -> Card:
        card, cl = self._card("Earshot Plus")

        row = QHBoxLayout()
        row.setSpacing(10)
        email = QLabel(self.cfg.cloud_email or "Signed in")
        email.setObjectName("H3")
        row.addWidget(email)
        self.status_chip_holder = QWidget()
        chip_lay = QHBoxLayout(self.status_chip_holder)
        chip_lay.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.status_chip_holder)
        row.addStretch(1)
        cl.addLayout(row)

        self.renewal_lbl = QLabel("")
        self.renewal_lbl.setObjectName("Muted")
        cl.addWidget(self.renewal_lbl)

        self.usage_lbl = QLabel("Loading usage…")
        self.usage_lbl.setObjectName("Faint")
        cl.addWidget(self.usage_lbl)
        self.usage_bar = QProgressBar()
        self.usage_bar.setTextVisible(False)
        self.usage_bar.setRange(0, 100)
        self.usage_bar.setValue(0)
        cl.addWidget(self.usage_bar)

        btn_row = QHBoxLayout()
        self.billing_btn = QPushButton("Manage billing")
        self.billing_btn.setProperty("variant", "primary")
        self.billing_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.billing_btn.clicked.connect(self._manage_billing)
        btn_row.addWidget(self.billing_btn)
        self.signout_btn = QPushButton("Sign out")
        self.signout_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.signout_btn.clicked.connect(self._sign_out)
        btn_row.addWidget(self.signout_btn)
        btn_row.addStretch(1)
        cl.addLayout(btn_row)

        self._billing_url = self.cfg.cloud_api_base.rstrip("/") + "/account"
        self._set_status_chip(self.cfg.extra.get("cloud_sub_status", "") or "")
        self._fetch_me()
        return card

    def _set_status_chip(self, sub_status: str) -> None:
        # clear any existing chip
        lay = self.status_chip_holder.layout()
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        label, fg, bg = _STATUS_CHIP.get(sub_status, ("", "text_muted", "surface_hover"))
        if label:
            lay.addWidget(make_chip(label, fg=self.theme.color(fg), bg=self.theme.color(bg)))

    def _fetch_me(self) -> None:
        """Fetch GET /v1/me off the GUI thread; update the meter/renewal on done,
        or show an offline label on failure (never crash / hang)."""
        from .workers import FuncWorker
        base, token = self.cfg.cloud_api_base, self.cfg.cloud_token

        def job(_progress):
            from ..transcription import earshot_client
            return earshot_client.get_me(base, token)

        self._me_worker = FuncWorker(job)
        self._me_worker.done.connect(self._on_me)
        self._me_worker.failed.connect(self._on_me_failed)
        self._me_worker.start()

    def _on_me(self, data: dict) -> None:
        # The fetch can settle AFTER the page rebuilt into self-host mode (e.g.
        # the user signed out during the ~10s connect window) — the usage
        # widgets no longer exist. Bail rather than AttributeError in the slot.
        if not hasattr(self, "usage_bar"):
            return
        if not isinstance(data, dict):
            self._on_me_failed("offline")
            return
        sub_status = data.get("sub_status") or ""
        self._set_status_chip(sub_status)
        period_end = data.get("period_end") or ""
        # copy matches the subscription state: a trial ENDS, a cancelled sub RUNS
        # OUT, an active sub RENEWS — and the button says what to do about it
        if sub_status in ("trialing", "beta"):
            self.renewal_lbl.setText(f"Trial ends {period_end}" if period_end else "Trial active")
            self.billing_btn.setText("Upgrade now")
        elif sub_status in ("canceled", "past_due"):
            self.renewal_lbl.setText(
                f"Plus stays active until {period_end}" if period_end else "Auto-renewal is off"
            )
            self.billing_btn.setText("Renew subscription")
        elif period_end:
            self.renewal_lbl.setText(f"Renews {period_end}")
        else:
            self.renewal_lbl.setText("")
        billing_url = data.get("billing_url")
        if billing_url:
            self._billing_url = billing_url
        # cache the snapshot so the sidebar trial card / plan chip can render
        # without their own network calls
        self.cfg.extra["cloud_sub_status"] = sub_status
        self.cfg.extra["cloud_period_end"] = period_end
        if billing_url:
            self.cfg.extra["cloud_billing_url"] = billing_url
        self.cfg.save()
        if hasattr(self.shell, "refresh_plan_state"):
            self.shell.refresh_plan_state()
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

    def _on_me_failed(self, _msg: str) -> None:
        if not hasattr(self, "usage_lbl"):  # page rebuilt while the fetch was in flight
            return
        self.usage_lbl.setText("Usage unavailable — offline.")
        self.usage_bar.setValue(0)

    @staticmethod
    def _fmt_hours(seconds: float) -> str:
        hours = seconds / 3600.0
        if hours >= 1:
            return f"{hours:.1f} h"
        return f"{int(round(seconds / 60))} min"

    def _manage_billing(self) -> None:
        self._open_url(self._billing_url)

    def _sign_out(self) -> None:
        # Sign out LOCALLY and instantly — the server-side revoke is best-effort
        # and runs off the GUI thread, so an unreachable server can't freeze the
        # app (revoke has an 8s connect timeout). Capture the token first.
        base, token = self.cfg.cloud_api_base, self.cfg.cloud_token
        self.cfg.cloud_token = ""
        self.cfg.cloud_email = ""
        self.cfg.account_mode = "selfhost"
        self.cfg.save()
        if token:
            from .workers import FuncWorker

            def job(_progress):
                from ..transcription import earshot_client
                earshot_client.revoke(base, token)  # never raises
                return None

            self._revoke_worker = FuncWorker(job)
            self._revoke_worker.start()
        if hasattr(self.shell, "on_account_changed"):
            self.shell.on_account_changed()
        self.refresh()

    # ---------- Your data ----------
    def _data_card(self) -> Card:
        card, cl = self._card("Your data")
        line = QLabel("Everything — recordings, transcripts, notes and settings — is stored locally on this PC.")
        line.setObjectName("Muted")
        line.setWordWrap(True)
        cl.addWidget(line)
        open_btn = QPushButton("Open storage folder")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(self._open_storage_folder)
        cl.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    def _open_storage_folder(self) -> None:
        import os

        from ..paths import recordings_dir
        folder = str(recordings_dir())
        if os.path.isdir(folder):
            os.startfile(folder)  # noqa: S606

    def _open_url(self, url: str) -> None:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def apply_theme(self) -> None:
        self._update_avatar()
        if hasattr(self, "subscribe_btn"):
            self.subscribe_btn.setIcon(icons.icon("cloud", self.theme.color("on_primary"), 15))
