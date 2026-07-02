"""Export a meeting as a single, self-contained HTML file — the local-first
answer to "share link". No server, no account: save the file and send it, and it
opens beautifully in any browser (print-to-PDF friendly too).

Everything user/AI-derived is HTML-escaped; the only markup that survives is the
**bold** → <b> conversion in note bullets.
"""
from __future__ import annotations

import html as _html

from .render import _bold_html

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 40px 16px;
  background: #F5F6FA; color: #1B1C2A;
  font-family: 'Segoe UI Variable Text', 'Segoe UI', system-ui, -apple-system, sans-serif;
  font-size: 15px; line-height: 1.55;
}
.page { max-width: 760px; margin: 0 auto; }
.card {
  background: #fff; border: 1px solid #E8E8F0; border-radius: 16px;
  padding: 32px 36px; margin-bottom: 16px;
  box-shadow: 0 8px 34px rgba(20,22,40,.07);
}
h1 { font-size: 26px; margin: 0 0 6px; letter-spacing: -0.2px; }
h2 { font-size: 16px; margin: 26px 0 8px; }
.meta { color: #646579; font-size: 13px; margin-bottom: 4px; }
.chip {
  display: inline-block; background: #ECEDFE; color: #4A4DD4;
  border-radius: 9px; padding: 2px 10px; font-size: 12px; font-weight: 600;
  margin: 0 6px 6px 0;
}
ul { padding-left: 22px; margin: 6px 0; }
li { margin: 3px 0; }
.actions { list-style: none; padding-left: 2px; }
.actions li { margin: 5px 0; }
.owner { color: #4A4DD4; font-weight: 600; }
.done { color: #9A9BAC; text-decoration: line-through; }
details { margin-top: 8px; }
summary { cursor: pointer; font-weight: 600; color: #4A4DD4; }
pre.transcript {
  white-space: pre-wrap; font-family: inherit; font-size: 13.5px;
  color: #3A3B4E; background: #FBFBFE; border: 1px solid #E8E8F0;
  border-radius: 10px; padding: 14px 16px; margin-top: 10px;
}
.footer { text-align: center; color: #9A9BAC; font-size: 12px; margin-top: 18px; }
@media print { body { background: #fff; padding: 0; } .card { box-shadow: none; border: none; } }
"""


def to_share_html(m, *, include_transcript: bool = False) -> str:
    """A complete standalone HTML document for meeting `m` (a Meeting)."""
    notes = m.notes or {}
    title = notes.get("title") or m.title or "Meeting notes"
    parts: list[str] = []

    parts.append(f"<h1>{_html.escape(title)}</h1>")
    meta_bits = [b for b in (m.date_text, f"{int(m.duration_secs // 60)} min" if m.duration_secs else "") if b]
    if meta_bits:
        parts.append(f'<div class="meta">{_html.escape("  ·  ".join(meta_bits))}</div>')
    attendees = m.attendees or notes.get("attendees") or []
    if attendees:
        chips = "".join(f'<span class="chip">{_html.escape(str(a))}</span>' for a in attendees)
        parts.append(f"<div>{chips}</div>")

    if notes.get("summary"):
        parts.append(f"<p>{_html.escape(notes['summary'])}</p>")

    actions = notes.get("action_items") or []
    if actions:
        parts.append("<h2>Action items</h2><ul class='actions'>")
        for a in actions:
            if not isinstance(a, dict):
                continue
            done = bool(a.get("done"))
            box = "&#9745;" if done else "&#9744;"
            task = _html.escape(a.get("task") or "")
            owner = f' · <span class="owner">{_html.escape(a["owner"])}</span>' if a.get("owner") else ""
            cls = ' class="done"' if done else ""
            sug = "" if a.get("confirmed", True) or done else ' <span class="done">(suggested)</span>'
            parts.append(f"<li>{box} <span{cls}>{task}</span>{owner}{sug}</li>")
        parts.append("</ul>")

    for sec in notes.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        if sec.get("heading"):
            parts.append(f"<h2>{_html.escape(sec['heading'])}</h2>")
        bullets = "".join(f"<li>{_bold_html(str(b))}</li>" for b in (sec.get("bullets") or []))
        if bullets:
            parts.append(f"<ul>{bullets}</ul>")

    if include_transcript and (m.transcript or "").strip():
        parts.append(
            "<details><summary>Full transcript</summary>"
            f"<pre class='transcript'>{_html.escape(m.transcript)}</pre></details>"
        )

    body = "".join(parts)
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{_html.escape(title)}</title><style>{_CSS}</style></head>"
        f"<body><div class='page'><div class='card'>{body}</div>"
        "<div class='footer'>Recorded locally with Earshot — no bots, no cloud.</div>"
        "</div></body></html>"
    )
