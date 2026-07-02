"""Push open action items into Todoist (REST API v2, single personal token).

Task-manager sync is where action items stop dying in notes: each open item
becomes a Todoist task titled with the task text, with the meeting + owner in
the description. Items remember their created task id so re-sending a meeting
never duplicates them.
"""
from __future__ import annotations

import httpx

_API = "https://api.todoist.com/rest/v2"


class TodoistError(RuntimeError):
    pass


def ping(token: str, timeout: float = 8.0) -> bool:
    if not (token or "").strip():
        return False
    try:
        r = httpx.get(f"{_API}/projects", headers={"Authorization": f"Bearer {token}"},
                      timeout=timeout)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def create_task(token: str, content: str, description: str = "", timeout: float = 15.0) -> str:
    """Create one task; returns its Todoist id."""
    try:
        r = httpx.post(
            f"{_API}/tasks",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": content, "description": description},
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        raise TodoistError(f"could not reach Todoist: {e}") from e
    if r.status_code in (401, 403):
        raise TodoistError("Todoist rejected the token — check Settings → General.")
    if r.status_code == 429:
        raise TodoistError("Todoist rate-limited the request; try again in a minute.")
    if r.status_code not in (200, 204):
        raise TodoistError(f"Todoist returned {r.status_code}: {r.text[:200]}")
    try:
        return str(r.json().get("id") or "")
    except ValueError:
        return ""


def send_open_items(token: str, notes: dict, *, meeting_title: str, date_text: str) -> tuple[int, int]:
    """Create a task for every OPEN action item that hasn't been sent before.
    Mutates the items in `notes` (adds todoist_id). Returns (sent, skipped)."""
    if not (token or "").strip():
        raise TodoistError("No Todoist token configured (Settings → General).")
    sent = skipped = 0
    for a in notes.get("action_items") or []:
        if not isinstance(a, dict) or a.get("done"):
            continue
        if not a.get("confirmed", True):
            continue  # AI suggestions aren't real tasks until the user keeps them
        if a.get("todoist_id"):
            skipped += 1
            continue
        task = (a.get("task") or "").strip()
        if not task:
            continue
        owner = f"Owner: {a['owner']}\n" if a.get("owner") else ""
        desc = f"{owner}From meeting: {meeting_title} ({date_text}) — via Earshot"
        a["todoist_id"] = create_task(token, task, desc)
        sent += 1
    return sent, skipped
