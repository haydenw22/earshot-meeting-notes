"""Due-date helpers for action items: tolerant ISO parsing, a short human label
("Overdue" / "Today" / "Tomorrow" / "3 Jul"), and a severity bucket used to pick
the due-chip colour (overdue → danger, today → warning, future → muted).
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional


def parse_due(s: Optional[str]) -> Optional[_dt.date]:
    """Tolerant ISO ("YYYY-MM-DD") parse. Never raises — anything unparseable
    (None, empty, junk) returns None."""
    if not s or not isinstance(s, str):
        return None
    try:
        return _dt.date.fromisoformat(s.strip())
    except ValueError:
        return None


def due_label(s: Optional[str], today: Optional[_dt.date] = None) -> str:
    """A short human label for a due date: "Overdue", "Today", "Tomorrow", or
    e.g. "3 Jul" otherwise. Empty string if `s` doesn't parse."""
    d = parse_due(s)
    if d is None:
        return ""
    today = today or _dt.date.today()
    delta = (d - today).days
    if delta < 0:
        return "Overdue"
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    return f"{d.day} {d.strftime('%b')}"  # no leading zero on any platform


def due_severity(s: Optional[str], today: Optional[_dt.date] = None) -> str:
    """One of "overdue" / "today" / "future" / "none" — drives due-chip colour."""
    d = parse_due(s)
    if d is None:
        return "none"
    today = today or _dt.date.today()
    if d < today:
        return "overdue"
    if d == today:
        return "today"
    return "future"
