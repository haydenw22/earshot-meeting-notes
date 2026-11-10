"""Earshot Plus plan state for the UI: the cached /v1/me snapshot, the free
trial's timeline, when to show the upgrade prompt, and what the trial did for
this person (counted from the local library, never the server).

No Qt here, so the decisions are unit-testable. The cache lives in cfg.extra
so the sidebar and dialogs render without their own network calls.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Optional

WEBSITE = "https://tryearshot.app"
SELF_HOST_GUIDE = WEBSITE + "/self-hosting-earshot"
PRICE_MONTHLY = 9
PRICE_ANNUAL = 90

# How long before the trial ends the "ending soon" prompt starts, and how
# often any prompt may come back after "Not now".
ENDING_SOON = _dt.timedelta(hours=48)
REMIND_EVERY = _dt.timedelta(hours=20)


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _parse(ts) -> Optional[_dt.datetime]:
    if not ts:
        return None
    try:
        d = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def cache_me(cfg, data: dict) -> None:
    """Store a /v1/me snapshot in cfg.extra (caller saves)."""
    cfg.extra["cloud_sub_status"] = data.get("sub_status") or ""
    cfg.extra["cloud_period_end"] = data.get("period_end") or ""
    if data.get("billing_url"):
        cfg.extra["cloud_billing_url"] = data.get("billing_url")
    trial = data.get("trial")
    cfg.extra["cloud_trial"] = trial if isinstance(trial, dict) else None
    for key in ("checkout_url", "checkout_annual_url", "price_monthly", "price_annual"):
        cfg.extra[f"cloud_{key}"] = data.get(key)


@dataclass
class Trial:
    state: str                      # "active" | "ended"
    started: Optional[_dt.datetime]
    ends: Optional[_dt.datetime]
    cap_reached: bool

    def left(self, now: Optional[_dt.datetime] = None) -> _dt.timedelta:
        if self.ends is None:
            return _dt.timedelta(0)
        return max(_dt.timedelta(0), self.ends - (now or _now()))

    @property
    def over(self) -> bool:
        return self.state == "ended"


def trial(cfg) -> Optional[Trial]:
    """The free trial from the cached snapshot, or None (paid, card trial, beta,
    self-host, or never fetched)."""
    if cfg.account_mode != "cloud":
        return None
    t = cfg.extra.get("cloud_trial")
    if not isinstance(t, dict):
        return None
    return Trial(
        state="ended" if t.get("state") == "ended" else "active",
        started=_parse(t.get("started_at")),
        ends=_parse(t.get("ends_at")),
        cap_reached=bool(t.get("cap_reached")),
    )


def ends_phrase(t: Trial, now: Optional[_dt.datetime] = None) -> str:
    """'today' / 'tomorrow' / 'in 3 days', by the person's local calendar."""
    if t.ends is None:
        return "soon"
    now = now or _now()
    days = (t.ends.astimezone().date() - now.astimezone().date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days} days"


def prompt_kind(cfg, now: Optional[_dt.datetime] = None) -> Optional[str]:
    """Which upgrade prompt fits right now: 'ended' (the trial is over),
    'cap' (its transcription allowance is used up), 'ending' (under 48 hours
    left) or None."""
    t = trial(cfg)
    if t is None:
        return None
    if t.over:
        return "ended"
    if t.cap_reached:
        return "cap"
    if t.ends is not None and t.left(now) <= ENDING_SOON:
        return "ending"
    return None


def should_prompt(cfg, kind: str, now: Optional[_dt.datetime] = None) -> bool:
    """At most once per REMIND_EVERY per kind, so "Not now" is respected."""
    shown = cfg.extra.get("trial_prompt_shown") or {}
    last = _parse(shown.get(kind)) if isinstance(shown, dict) else None
    return last is None or (now or _now()) - last >= REMIND_EVERY


def mark_prompted(cfg, kind: str, now: Optional[_dt.datetime] = None) -> None:
    shown = cfg.extra.get("trial_prompt_shown")
    shown = dict(shown) if isinstance(shown, dict) else {}
    shown[kind] = (now or _now()).isoformat()
    cfg.extra["trial_prompt_shown"] = shown


def checkout_url(cfg, annual: bool = False) -> str:
    key = "cloud_checkout_annual_url" if annual else "cloud_checkout_url"
    url = (cfg.extra.get(key) or "").strip()
    return url or f"{WEBSITE}/account?checkout={'annual' if annual else 'monthly'}"


def prices(cfg) -> tuple[int, Optional[int]]:
    """(monthly, annual-or-None) in USD, from the server when it said."""
    monthly = cfg.extra.get("cloud_price_monthly") or PRICE_MONTHLY
    # never fetched (or an older server): assume the yearly plan exists
    annual = cfg.extra["cloud_price_annual"] if "cloud_price_annual" in cfg.extra else PRICE_ANNUAL
    return int(monthly), (int(annual) if annual else None)


@dataclass
class TrialStats:
    meetings: int = 0
    seconds: float = 0.0
    action_items: int = 0
    with_notes: int = 0

    @property
    def hours_text(self) -> str:
        hours = self.seconds / 3600.0
        if hours >= 1:
            return f"{hours:.1f}".rstrip("0").rstrip(".")
        return f"{max(1, round(self.seconds / 60))} min" if self.seconds else "0"


def trial_stats(repo, since: Optional[_dt.datetime]) -> TrialStats:
    """What Earshot did for this person since the trial began: meetings
    recorded, time captured, action items found, meetings with notes."""
    out = TrialStats()
    for m in repo.list(limit=2000):
        created = _parse(m.created_at)
        if since is not None and (created is None or created < since):
            continue
        out.meetings += 1
        out.seconds += float(m.duration_secs or 0.0)
        notes = m.notes or {}
        items = notes.get("action_items")
        if isinstance(items, list):
            # kept and still-suggested items both count (dismissed ones are gone)
            out.action_items += sum(1 for i in items if isinstance(i, dict))
        if notes:
            out.with_notes += 1
    return out
