"""Data access for meetings. Thin, explicit CRUD over the `meetings` table.

A small lock serialises writes so the recording thread (updating attendees live)
and the processing worker can both touch the row without stepping on each other.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from . import db as _db


@dataclass
class Folder:
    id: Optional[int] = None
    name: str = ""
    color: str = "#6366F1"


@dataclass
class Chat:
    """A saved Ask Earshot conversation. `messages` is a list of
    {role: 'you'|'answer'|'error', text: str, citations?: list, scope?: str}."""
    id: Optional[int] = None
    title: str = "New chat"
    messages: list = field(default_factory=list)
    updated_at: Optional[str] = None


@dataclass
class Meeting:
    id: Optional[int] = None
    title: Optional[str] = None
    date_text: str = ""
    date_iso: str = ""
    attendees: list[str] = field(default_factory=list)
    agenda: str = ""
    template: str = ""
    bookmarks: list = field(default_factory=list)   # [{ms, label}]
    transcript: Optional[str] = None
    notes_json: Optional[str] = None
    audio_dir: Optional[str] = None
    headphones_mode: bool = True
    duration_secs: Optional[float] = None
    status: str = "New"
    error: Optional[str] = None
    notion_page_id: Optional[str] = None
    notion_synced_at: Optional[str] = None
    folder_id: Optional[int] = None
    share_url: Optional[str] = None   # public Earshot Plus share link, if published
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def notes(self) -> Optional[dict]:
        if not self.notes_json:
            return None
        try:
            parsed = json.loads(self.notes_json)
        except json.JSONDecodeError:
            return None
        # a non-dict here (hand-edited DB, wrong-shape model output) must read as
        # "no notes", not crash every consumer
        return parsed if isinstance(parsed, dict) else None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Meeting":
        d = dict(row)
        try:
            attendees = json.loads(d.get("attendees") or "[]")
        except json.JSONDecodeError:
            attendees = []
        try:
            bookmarks = json.loads(d.get("bookmarks") or "[]")
        except (json.JSONDecodeError, TypeError):
            bookmarks = []
        return cls(
            id=d["id"],
            title=d.get("title"),
            date_text=d.get("date_text") or "",
            date_iso=d.get("date_iso") or "",
            attendees=attendees,
            agenda=d.get("agenda") or "",
            template=d.get("template") or "",
            bookmarks=bookmarks,
            transcript=d.get("transcript"),
            notes_json=d.get("notes_json"),
            audio_dir=d.get("audio_dir"),
            headphones_mode=bool(d.get("headphones_mode", 1)),
            duration_secs=d.get("duration_secs"),
            status=d.get("status") or "New",
            error=d.get("error"),
            notion_page_id=d.get("notion_page_id"),
            notion_synced_at=d.get("notion_synced_at"),
            folder_id=d.get("folder_id"),
            share_url=d.get("share_url"),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
        )


class MeetingRepository:
    # Whitelist of writable columns — guards the dynamic UPDATE below against a
    # caller ever passing an unexpected (or untrusted) field name.
    _WRITABLE = frozenset({
        "title", "date_text", "date_iso", "attendees", "agenda", "template", "bookmarks",
        "transcript", "notes_json", "audio_dir", "headphones_mode", "duration_secs",
        "status", "error", "notion_page_id", "notion_synced_at", "folder_id", "share_url",
    })

    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        self.conn = conn or _db.connect()
        _db.init_db(self.conn)
        # Reentrant: reads/writes share one connection across the UI + worker
        # threads, so every access is serialised through this lock.
        self._lock = threading.RLock()
        self._backfill_fts()

    def close(self) -> None:
        try:
            with self._lock:  # never yank the connection from under a worker mid-write
                self.conn.close()
        except sqlite3.Error:
            pass

    # --- writes ---
    def create(self, *, date_text: str, date_iso: str, attendees: list[str], agenda: str = "",
               template: str = "", folder_id: Optional[int] = None) -> Meeting:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO meetings (date_text, date_iso, attendees, agenda, template, folder_id, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'New')",
                (date_text, date_iso, json.dumps(attendees), agenda, template, folder_id),
            )
            self.conn.commit()
            mid = cur.lastrowid
            m = self.get(mid)
            self._index_fts(m)  # same lock scope: no window for a racing delete
        return m

    def update(self, meeting_id: int, **fields: Any) -> None:
        if not fields:
            return
        bad = set(fields) - self._WRITABLE
        if bad:
            raise ValueError(f"not writable: {sorted(bad)}")
        if "attendees" in fields and isinstance(fields["attendees"], (list, tuple)):
            fields["attendees"] = json.dumps(list(fields["attendees"]))
        if "bookmarks" in fields and isinstance(fields["bookmarks"], (list, tuple)):
            fields["bookmarks"] = json.dumps(list(fields["bookmarks"]))
        if "headphones_mode" in fields:
            fields["headphones_mode"] = 1 if fields["headphones_mode"] else 0
        reindex = bool({"title", "transcript", "notes_json", "attendees", "agenda"} & set(fields))
        cols = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values())
        vals.append(meeting_id)
        with self._lock:
            self.conn.execute(
                f"UPDATE meetings SET {cols}, updated_at = datetime('now') WHERE id = ?",
                vals,
            )
            self.conn.commit()
            if reindex:
                # inside the lock: a concurrent delete() can't interleave and
                # leave an orphaned FTS row behind
                try:
                    self._index_fts(self.get(meeting_id))
                except KeyError:
                    pass

    def delete(self, meeting_id: int) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
            self.conn.execute("DELETE FROM meetings_fts WHERE meeting_id = ?", (meeting_id,))
            self.conn.commit()

    # --- folders ---
    def create_folder(self, name: str, color: str) -> Folder:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO folders (name, color) VALUES (?, ?)", (name, color),
            )
            self.conn.commit()
            fid = cur.lastrowid
            row = self.conn.execute("SELECT * FROM folders WHERE id = ?", (fid,)).fetchone()
        return Folder(id=row["id"], name=row["name"], color=row["color"])

    def list_folders(self) -> list[Folder]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM folders ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [Folder(id=r["id"], name=r["name"], color=r["color"]) for r in rows]

    def update_folder(self, folder_id: int, *, name: Optional[str] = None,
                       color: Optional[str] = None) -> None:
        fields: dict[str, Any] = {}
        if name is not None:
            fields["name"] = name
        if color is not None:
            fields["color"] = color
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values())
        vals.append(folder_id)
        with self._lock:
            self.conn.execute(f"UPDATE folders SET {cols} WHERE id = ?", vals)
            self.conn.commit()

    def delete_folder(self, folder_id: int) -> None:
        with self._lock:
            # unfile first: meetings are kept, they just lose their folder
            self.conn.execute(
                "UPDATE meetings SET folder_id = NULL, updated_at = datetime('now') WHERE folder_id = ?",
                (folder_id,),
            )
            self.conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
            self.conn.commit()

    # ---------- Ask Earshot chat history ----------
    def create_chat(self, title: str, messages: list) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO ask_chats (title, messages_json) VALUES (?, ?)",
                (title or "New chat", json.dumps(messages or [])),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def update_chat(self, chat_id: int, *, title: Optional[str] = None,
                    messages: Optional[list] = None) -> None:
        sets, vals = ["updated_at = datetime('now')"], []
        if title is not None:
            sets.append("title = ?")
            vals.append(title)
        if messages is not None:
            sets.append("messages_json = ?")
            vals.append(json.dumps(messages))
        vals.append(chat_id)
        with self._lock:
            self.conn.execute(f"UPDATE ask_chats SET {', '.join(sets)} WHERE id = ?", vals)
            self.conn.commit()

    def list_chats(self, limit: int = 50) -> list[Chat]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, title, updated_at FROM ask_chats ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Chat(id=r["id"], title=r["title"], updated_at=r["updated_at"]) for r in rows]

    def get_chat(self, chat_id: int) -> Optional[Chat]:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM ask_chats WHERE id = ?", (chat_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            messages = json.loads(row["messages_json"] or "[]")
        except json.JSONDecodeError:
            messages = []
        return Chat(id=row["id"], title=row["title"],
                    messages=messages if isinstance(messages, list) else [],
                    updated_at=row["updated_at"])

    def delete_chat(self, chat_id: int) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM ask_chats WHERE id = ?", (chat_id,))
            self.conn.commit()

    def recover_interrupted(self) -> int:
        """Flip meetings left mid-flight by a crash / force-quit out of their
        in-progress status so they don't sit stuck forever. Any saved audio stays
        on disk, so the user can Re-transcribe. Returns the number reset."""
        with self._lock:
            cur = self.conn.execute(
                "UPDATE meetings SET status = 'Error', "
                "error = COALESCE(NULLIF(error, ''), 'Interrupted — Earshot closed before this "
                "finished. Open it and Re-transcribe to resume.'), "
                "updated_at = datetime('now') "
                "WHERE status IN ('Recording', 'Transcribing', 'Summarizing')"
            )
            self.conn.commit()
            return cur.rowcount

    # --- full-text search ---
    @staticmethod
    def _searchable(m: "Meeting") -> str:
        # Defensive on shape: a hand-edited DB or wrong-shape model output must
        # degrade to a partial index, never crash (this runs at startup).
        parts = [m.title or "", " ".join(str(a) for a in m.attendees), m.agenda or "", m.transcript or ""]
        notes = m.notes
        if notes:
            parts.append(str(notes.get("summary") or ""))
            sections = notes.get("sections")
            for s in sections if isinstance(sections, list) else []:
                if not isinstance(s, dict):
                    continue
                parts.append(str(s.get("heading") or ""))
                bullets = s.get("bullets")
                parts.extend(str(b) for b in (bullets if isinstance(bullets, list) else []))
            actions = notes.get("action_items")
            for a in actions if isinstance(actions, list) else []:
                if isinstance(a, dict):
                    parts.append(str(a.get("task") or ""))
        return "\n".join(p for p in parts if p)

    def _index_fts(self, m: "Meeting") -> None:
        with self._lock:
            # skip if the row vanished (racing delete) — never resurrect a ghost
            row = self.conn.execute("SELECT 1 FROM meetings WHERE id = ?", (m.id,)).fetchone()
            self.conn.execute("DELETE FROM meetings_fts WHERE meeting_id = ?", (m.id,))
            if row is not None:
                self.conn.execute(
                    "INSERT INTO meetings_fts (meeting_id, body) VALUES (?, ?)", (m.id, self._searchable(m))
                )
            self.conn.commit()

    def _backfill_fts(self) -> None:
        """Index only the meetings missing from FTS. One bad row must not block
        startup, and an already-indexed library must cost ~nothing."""
        with self._lock:
            try:
                missing = [r[0] for r in self.conn.execute(
                    "SELECT id FROM meetings WHERE id NOT IN "
                    "(SELECT meeting_id FROM meetings_fts)"
                ).fetchall()]
            except sqlite3.Error:
                return
        for mid in missing:
            try:
                self._index_fts(self.get(mid))
            except (KeyError, sqlite3.Error, ValueError, TypeError):
                continue

    def search(self, query: str, limit: int = 300) -> list[int]:
        """Meeting ids whose content matches the query, ranked by relevance."""
        terms = [t for t in re.findall(r"\w+", (query or "")) if t]
        if not terms:
            return []
        match = " ".join(f'"{t}"*' for t in terms)
        with self._lock:
            try:
                rows = self.conn.execute(
                    "SELECT meeting_id FROM meetings_fts WHERE meetings_fts MATCH ? "
                    "ORDER BY rank LIMIT ?",
                    (match, limit),
                ).fetchall()
            except sqlite3.Error:
                return []
        return [r[0] for r in rows]

    # --- reads (locked: the worker and UI thread share one connection) ---
    def get(self, meeting_id: int) -> Meeting:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM meetings WHERE id = ?", (meeting_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"meeting {meeting_id} not found")
        return Meeting.from_row(row)

    def list(self, limit: int = 500) -> list[Meeting]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM meetings ORDER BY COALESCE(date_iso, created_at) DESC, id DESC "
                "LIMIT ?",
                (limit,),
            ).fetchall()
        return [Meeting.from_row(r) for r in rows]
