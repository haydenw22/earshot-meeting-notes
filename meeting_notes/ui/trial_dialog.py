"""The Earshot Plus upgrade prompt for the card-less free trial.

Three moments, one dialog (cloud_plan.prompt_kind decides which):
  ending  under 48 hours left: subscribe now and nothing changes
  cap     the trial's transcription allowance is used up
  ended   the trial is over

It sells with the person's own numbers (meetings, hours, action items counted
from the local library since the trial began), says plainly what stops and
what they keep, offers monthly or yearly, and takes the risk out (cancel
anytime, 14-day refund). "Keep Earshot Plus" opens the account page in the
browser, which opens the Paddle checkout straight away.
"""
from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import cloud_plan
from . import icons
from .logo import logo_pixmap


class _PlanOption(QFrame):
    """One selectable plan card (monthly / yearly)."""

    clicked = Signal()

    def __init__(self, title: str, price: str, per: str, note: str, badge: str = ""):
        super().__init__()
        self.setObjectName("TrialPlan")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(6)
        t = QLabel(title)
        t.setObjectName("TrialPlanTitle")
        top.addWidget(t)
        top.addStretch(1)
        if badge:
            b = QLabel(badge)
            b.setObjectName("TrialBadge")
            top.addWidget(b)
        lay.addLayout(top)
        row = QHBoxLayout()
        row.setSpacing(4)
        p = QLabel(price)
        p.setObjectName("TrialPlanPrice")
        row.addWidget(p)
        u = QLabel(per)
        u.setObjectName("Muted")
        row.addWidget(u, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        lay.addLayout(row)
        n = QLabel(note)
        n.setObjectName("Faint")
        lay.addWidget(n)
        self.set_selected(False)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, event):  # noqa: N802 (Qt override)
        self.clicked.emit()
        super().mouseReleaseEvent(event)


class TrialDialog(QDialog):
    """`kind` is 'ending' | 'cap' | 'ended'. `on_subscribed` runs when the person
    says they've finished checkout (the shell re-checks the plan)."""

    def __init__(self, parent, cfg, theme, repo, kind: str, *, on_subscribed=None):
        super().__init__(parent)
        self.cfg = cfg
        self.theme = theme
        self.repo = repo
        self.kind = kind if kind in ("ending", "cap", "ended") else "ended"
        self.on_subscribed = on_subscribed
        self.trial = cloud_plan.trial(cfg)
        self.stats = cloud_plan.trial_stats(repo, self.trial.started if self.trial else None)
        self.monthly, self.annual = cloud_plan.prices(cfg)
        self._annual_selected = False
        self.setWindowTitle("Earshot Plus")
        self.setModal(True)
        # A fixed width lets the wrapped labels report their real height, so
        # the dialog fits its content instead of padding it.
        self.setFixedWidth(600)
        self.setStyleSheet(self._qss())
        self._build()

    def showEvent(self, event):  # noqa: N802 (Qt override)
        super().showEvent(event)
        h = self.heightForWidth(self.width())
        if h > 0:
            self.setFixedHeight(h)  # exactly as tall as the content at this width

    # ---------- copy ----------
    def _headline(self) -> tuple[str, str, str]:
        """(kicker, headline, subline)."""
        n = self.stats.meetings
        if self.kind == "ending":
            when = cloud_plan.ends_phrase(self.trial) if self.trial else "soon"
            return ("FREE TRIAL",
                    f"Your free trial ends {when}",
                    "Subscribe now and nothing changes. Every meeting keeps turning into "
                    "notes and action items on its own, with no gap.")
        if self.kind == "cap":
            return ("TRIAL ALLOWANCE USED",
                    "You've used all your trial hours",
                    "That's a lot of meetings. Subscribe and Plus includes 40 hours of "
                    "transcription every month.")
        if n:
            sub = (f"Earshot wrote up your last {n} meeting{'s' if n != 1 else ''}. "
                   "Keep it going for your next one.")
        else:
            sub = "Keep Earshot Plus and every meeting turns into notes and action items on its own."
        return ("FREE TRIAL ENDED", "Your free trial has ended", sub)

    # ---------- layout ----------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._hero(), 0)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(28, 22, 28, 18)
        bl.setSpacing(16)
        if self.stats.meetings:
            bl.addWidget(self._section_label(
                "YOUR TRIAL SO FAR" if self.kind == "ending" else "WHAT EARSHOT DID IN YOUR TRIAL"))
            bl.addLayout(self._stats_row())
        else:
            bl.addWidget(self._section_label("WHAT PLUS DOES FOR YOU"))
            bl.addLayout(self._benefits())
        if self.kind in ("ended", "cap"):
            bl.addLayout(self._changes())
        bl.addLayout(self._plans())
        bl.addWidget(self._cta())
        trust = QLabel("Cancel anytime  ·  14-day money-back guarantee  ·  Secure checkout by Paddle")
        trust.setObjectName("Faint")
        trust.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(trust)
        self.waiting = QLabel("")
        self.waiting.setObjectName("Muted")
        self.waiting.setWordWrap(True)
        self.waiting.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.waiting.setVisible(False)
        bl.addWidget(self.waiting)
        bl.addLayout(self._footer())
        root.addWidget(body, 1)

    def _hero(self) -> QFrame:
        hero = QFrame()
        hero.setObjectName("TrialHero")
        # only as tall as its text: wrapped labels must not hand it spare height
        hero.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        hl = QVBoxLayout(hero)
        hl.setContentsMargins(28, 24, 28, 24)
        hl.setSpacing(8)
        kicker, headline, sub = self._headline()
        top = QHBoxLayout()
        top.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(30))
        top.addWidget(logo)
        k = QLabel(kicker)
        k.setObjectName("TrialKicker")
        top.addWidget(k, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        hl.addLayout(top)
        h = QLabel(headline)
        h.setObjectName("TrialHeadline")
        h.setWordWrap(True)
        hl.addWidget(h)
        s = QLabel(sub)
        s.setObjectName("TrialSub")
        s.setWordWrap(True)
        hl.addWidget(s)
        self.headline_lbl = h
        return hero

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("PlanKicker")
        return lbl

    def _stats_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        st = self.stats
        tiles = [
            (str(st.meetings), "meeting" + ("s" if st.meetings != 1 else "") + " recorded"),
            (st.hours_text, "hours captured" if st.seconds >= 3600 else "of meetings captured"),
            (str(st.action_items), "action item" + ("s" if st.action_items != 1 else "") + " caught"),
        ]
        for big, small in tiles:
            tile = QFrame()
            tile.setObjectName("TrialStat")
            tl = QVBoxLayout(tile)
            tl.setContentsMargins(14, 12, 14, 12)
            tl.setSpacing(0)
            b = QLabel(big)
            b.setObjectName("TrialStatNum")
            tl.addWidget(b)
            s = QLabel(small)
            s.setObjectName("Muted")
            s.setWordWrap(True)
            tl.addWidget(s)
            row.addWidget(tile, 1)
        return row

    def _benefits(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(8)
        for icon, text in (
            ("mic", "Records any call with no bot joining, in Zoom, Teams, Meet or anything else"),
            ("sparkles", "A summary, decisions and action items minutes after every meeting"),
            ("message", "Ask Earshot anything across all your meetings, with quotes as proof"),
        ):
            col.addLayout(self._line(icon, self.theme.color("primary"), text))
        return col

    def _changes(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(8)
        col.addLayout(self._line("check", self.theme.color("success"),
                                 "Your recordings, transcripts and notes stay on this computer. Nothing is deleted."))
        if self.kind == "cap":
            col.addLayout(self._line("x", self.theme.color("danger"),
                                     "New meetings won't be transcribed until you subscribe."))
        else:
            col.addLayout(self._line("x", self.theme.color("danger"),
                                     "New meetings won't get transcripts, notes or action items."))
            col.addLayout(self._line("x", self.theme.color("danger"),
                                     "Ask Earshot, prep briefs, sync and share links pause."))
        return col

    def _line(self, icon: str, color: str, text: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        dot = QLabel()
        dot.setPixmap(icons.pixmap(icon, color, 16))
        row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        row.addWidget(lbl, 1)
        return row

    def _plans(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        per_day = round(self.monthly * 12 / 365 * 100)
        self.opt_month = _PlanOption("Monthly", f"${self.monthly}", "/ month",
                                     f"About {per_day} cents a day")
        self.opt_month.clicked.connect(lambda: self._select(False))
        row.addWidget(self.opt_month, 1)
        self.opt_year = None
        if self.annual:
            saving = self.monthly * 12 - self.annual
            per_month = self.annual / 12
            self.opt_year = _PlanOption("Yearly", f"${self.annual}", "/ year",
                                        f"${per_month:.2f} a month, billed yearly",
                                        badge=f"Save ${saving}" if saving > 0 else "")
            self.opt_year.clicked.connect(lambda: self._select(True))
            row.addWidget(self.opt_year, 1)
        self._select(False)
        return row

    def _select(self, annual: bool) -> None:
        self._annual_selected = bool(annual and self.opt_year is not None)
        self.opt_month.set_selected(not self._annual_selected)
        if self.opt_year is not None:
            self.opt_year.set_selected(self._annual_selected)
        if hasattr(self, "cta"):
            self.cta.setText(self._cta_text())

    def _cta_text(self) -> str:
        price = (f"${self.annual}/year" if self._annual_selected else f"${self.monthly}/month")
        verb = "Subscribe now" if self.kind == "ending" else "Keep Earshot Plus"
        return f"{verb}  ·  {price}"

    def _cta(self) -> QPushButton:
        self.cta = QPushButton(self._cta_text())
        self.cta.setObjectName("TrialCta")
        self.cta.setProperty("variant", "primary")
        self.cta.setMinimumHeight(48)
        self.cta.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cta.setDefault(True)
        self.cta.clicked.connect(self._checkout)
        return self.cta

    def _footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.selfhost_btn = QPushButton("Use my own AI keys instead (free)")
        self.selfhost_btn.setProperty("variant", "ghost")
        self.selfhost_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.selfhost_btn.clicked.connect(self._self_host)
        row.addWidget(self.selfhost_btn)
        row.addStretch(1)
        self.later_btn = QPushButton("Remind me tomorrow" if self.kind == "ending" else "Not now")
        self.later_btn.setProperty("variant", "ghost")
        self.later_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.later_btn.clicked.connect(self.reject)
        row.addWidget(self.later_btn)
        return row

    # ---------- actions ----------
    def _checkout(self) -> None:
        if self.cta.property("done") == "true":
            # second click: "I've subscribed" -> let the shell re-check the plan
            if callable(self.on_subscribed):
                self.on_subscribed()
            self.accept()
            return
        try:
            webbrowser.open(cloud_plan.checkout_url(self.cfg, annual=self._annual_selected))
        except Exception:
            pass
        self.waiting.setText("Finish checking out in your browser. Earshot Plus picks up again "
                             "as soon as you've subscribed.")
        self.waiting.setVisible(True)
        self.cta.setText("I've subscribed")
        self.cta.setProperty("done", "true")

    def _self_host(self) -> None:
        try:
            webbrowser.open(cloud_plan.SELF_HOST_GUIDE)
        except Exception:
            pass
        self.reject()

    # ---------- style ----------
    def _qss(self) -> str:
        c = self.theme.color
        return f"""
#TrialHero {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #5B5BF0, stop:1 #9A57EA);
}}
#TrialKicker {{
    color: #FFFFFF; background: rgba(255,255,255,0.18); border-radius: 9px;
    padding: 3px 10px; font-size: 11px; font-weight: 700; letter-spacing: 1px;
}}
#TrialHeadline {{ color: #FFFFFF; font-size: 25px; font-weight: 700; }}
#TrialSub {{ color: rgba(255,255,255,0.88); font-size: 14px; }}
#TrialStat {{
    background: {c('surface_alt')}; border: 1px solid {c('border')}; border-radius: 12px;
}}
#TrialStatNum {{ color: {c('primary')}; font-size: 26px; font-weight: 700; }}
#TrialPlan {{
    background: {c('surface')}; border: 1px solid {c('border_strong')}; border-radius: 12px;
}}
#TrialPlan[selected="true"] {{ background: {c('primary_soft')}; border: 2px solid {c('primary')}; }}
#TrialPlanTitle {{ font-weight: 600; color: {c('text')}; }}
#TrialPlanPrice {{ font-size: 22px; font-weight: 700; color: {c('text')}; }}
#TrialBadge {{
    color: {c('success')}; background: {c('success_soft')}; border-radius: 8px;
    padding: 2px 8px; font-size: 11px; font-weight: 700;
}}
#TrialCta {{ font-size: 15px; font-weight: 600; border-radius: 12px; }}
"""
