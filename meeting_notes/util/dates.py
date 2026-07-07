"""Human-readable date helpers.

The recording screen shows the date as e.g. "25th June 2026" so it reads at a
glance; we also keep an ISO form for sorting and (later) the Notion Date property.
"""
from __future__ import annotations

import datetime as _dt


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def human_date(d: _dt.date | None = None) -> str:
    """e.g. '25th June 2026'."""
    d = d or _dt.date.today()
    return f"{_ordinal(d.day)} {d.strftime('%B')} {d.year}"


def iso_date(d: _dt.date | None = None) -> str:
    """e.g. '2026-06-25'."""
    d = d or _dt.date.today()
    return d.isoformat()


def today_pair() -> tuple[str, str]:
    """(human, iso) for today."""
    d = _dt.date.today()
    return human_date(d), iso_date(d)


def friendly_day(iso: str) -> str:
    """'2026-07-20' (or an ISO timestamp) -> 'July 20'. Anything unparseable is
    returned untouched — used for server-supplied dates like a trial's end."""
    s = (iso or "").strip()
    try:
        d = _dt.date.fromisoformat(s[:10])
    except ValueError:
        return s
    return f"{d.strftime('%B')} {d.day}"
