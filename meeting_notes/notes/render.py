"""Render structured meeting notes to clean HTML + plain text for the clipboard.

The HTML pastes as formatted rich text into Notion, Gmail, Outlook, etc. — proper
headings, bold and bullet points with NO markdown symbols. The plain-text version
(for plain editors) uses Unicode bullets (•) and check marks (☑/☐), also free of
markdown. Both are placed on the clipboard so the target app picks the richest it
supports.
"""
from __future__ import annotations

import html as _html
import re as _re
from typing import Optional


def _bold_html(text: str) -> str:
    """Escape, then turn **bold** into <b> (drops the asterisks)."""
    esc = _html.escape(text or "")
    return _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)


def _strip_md(text: str) -> str:
    """Drop **bold** markers, keep the words (for plain text)."""
    return _re.sub(r"\*\*(.+?)\*\*", r"\1", text or "")


def _meta_line(date_text: str, attendees: Optional[list[str]]) -> str:
    bits = [b for b in [date_text, ", ".join(attendees or [])] if b]
    return "  ·  ".join(bits)


def to_html(notes: dict, *, title: str = "", date_text: str = "", attendees: Optional[list[str]] = None) -> str:
    title = notes.get("title") or title or "Meeting notes"
    parts = [f"<h2>{_html.escape(title)}</h2>"]
    meta = _meta_line(date_text, attendees or notes.get("attendees"))
    if meta:
        parts.append(f'<p style="color:#666;">{_html.escape(meta)}</p>')
    if notes.get("summary"):
        parts.append(f"<p>{_html.escape(notes['summary'])}</p>")

    actions = notes.get("action_items") or []
    if actions:
        parts.append("<h3>Action items</h3>")
        rows = []
        for a in actions:
            box = "&#9745;" if a.get("done") else "&#9744;"  # ☑ / ☐
            task = _html.escape(a.get("task") or "")
            owner = f" — <b>{_html.escape(a['owner'])}</b>" if a.get("owner") else ""
            sug = ('' if a.get("confirmed", True) or a.get("done")
                   else ' <i style="color:#999;">(suggested)</i>')
            rows.append(f'<li style="list-style:none;">{box} {task}{owner}{sug}</li>')
        parts.append(f'<ul style="padding-left:0;">{"".join(rows)}</ul>')

    for sec in notes.get("sections") or []:
        if sec.get("heading"):
            parts.append(f"<h3>{_html.escape(sec['heading'])}</h3>")
        bullets = "".join(f"<li>{_bold_html(b)}</li>" for b in (sec.get("bullets") or []))
        if bullets:
            parts.append(f"<ul>{bullets}</ul>")

    return f"<div>{''.join(parts)}</div>"


def to_plaintext(notes: dict, *, title: str = "", date_text: str = "", attendees: Optional[list[str]] = None) -> str:
    title = notes.get("title") or title or "Meeting notes"
    lines = [title]
    meta = _meta_line(date_text, attendees or notes.get("attendees"))
    if meta:
        lines.append(meta)
    if notes.get("summary"):
        lines += ["", notes["summary"]]

    actions = notes.get("action_items") or []
    if actions:
        lines += ["", "Action items"]
        for a in actions:
            box = "☑" if a.get("done") else "☐"  # ☑ / ☐
            owner = f" — {a['owner']}" if a.get("owner") else ""
            sug = "" if a.get("confirmed", True) or a.get("done") else "  (suggested)"
            lines.append(f"  {box} {_strip_md(a.get('task') or '')}{owner}{sug}")

    for sec in notes.get("sections") or []:
        if sec.get("heading"):
            lines += ["", sec["heading"]]
        for b in sec.get("bullets") or []:
            lines.append(f"  • {_strip_md(b)}")  # • bullet

    return "\n".join(lines)
