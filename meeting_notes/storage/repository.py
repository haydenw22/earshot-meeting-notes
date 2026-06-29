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
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def notes(self) -> Optional[dict]:
        if not self.notes_json:
            return None
        try:
            return json.loads(self.notes_json)
        except json.JSONDecodeError:
            return None

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
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
        )


class MeetingRepository:
    # Whitelist of writable columns — guards the dynamic UPDATE below against a
    # caller ever passing an unexpected (or untrusted) field name.
    _WRITABLE = frozenset({
        "title", "date_text", "date_iso", "attendees", "agenda", "template", "bookmarks",
        "transcript", "notes_json", "audio_dir", "headphones_mode", "duration_secs",
        "status", "error", "notion_page_id", "notion_synced_at",
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
            self.conn.close()
        except sqlite3.Error:
            pass

    # --- writes ---
    def create(self, *, date_text: str, date_iso: str, attendees: list[str], agenda: str = "",
               template: str = "") -> Meeting:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO meetings (date_text, date_iso, attendees, agenda, template, status) "
                "VALUES (?, ?, ?, ?, ?, 'New')",
                (date_text, date_iso, json.dumps(attendees), agenda, template),
            )
            self.conn.commit()
            mid = cur.lastrowid
        m = self.get(mid)
        self._index_fts(m)
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
            try:
                self._index_fts(self.get(meeting_id))
            except KeyError:
                pass

    def delete(self, meeting_id: int) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
            self.conn.execute("DELETE FROM meetings_fts WHERE meeting_id = ?", (meeting_id,))
            self.conn.commit()

    # --- full-text search ---
    @staticmethod
    def _searchable(m: "Meeting") -> str:
        parts = [m.title or "", " ".join(m.attendees), m.agenda or "", m.transcript or ""]
        notes = m.notes
        if notes:
            parts.append(notes.get("summary", ""))
            for s in notes.get("sections") or []:
                parts.append(s.get("heading", ""))
                parts.extend(s.get("bullets") or [])
            for a in notes.get("action_items") or []:
                parts.append(a.get("task", ""))
        return "\n".join(p for p in parts if p)

    def _index_fts(self, m: "Meeting") -> None:
        with self._lock:
            self.conn.execute("DELETE FROM meetings_fts WHERE meeting_id = ?", (m.id,))
            self.conn.execute(
                "INSERT INTO meetings_fts (meeting_id, body) VALUES (?, ?)", (m.id, self._searchable(m))
            )
            self.conn.commit()

    def _backfill_fts(self) -> None:
        with self._lock:
            try:
                n = self.conn.execute("SELECT count(*) FROM meetings_fts").fetchone()[0]
                total = self.conn.execute("SELECT count(*) FROM meetings").fetchone()[0]
            except sqlite3.Error:
                return
        if n >= total:
            return
        for m in self.list(limit=100000):
            self._index_fts(m)

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
