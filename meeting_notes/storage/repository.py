"""Data access for meetings. Thin, explicit CRUD over the `meetings` table.

A small lock serialises writes so the recording thread (updating attendees live)
and the processing worker can both touch the row without stepping on each other.
"""
from __future__ import annotations

import json
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
        return cls(
            id=d["id"],
            title=d.get("title"),
            date_text=d.get("date_text") or "",
            date_iso=d.get("date_iso") or "",
            attendees=attendees,
            agenda=d.get("agenda") or "",
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
        "title", "date_text", "date_iso", "attendees", "agenda", "transcript", "notes_json",
        "audio_dir", "headphones_mode", "duration_secs", "status", "error",
        "notion_page_id", "notion_synced_at",
    })

    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        self.conn = conn or _db.connect()
        _db.init_db(self.conn)
        # Reentrant: reads/writes share one connection across the UI + worker
        # threads, so every access is serialised through this lock.
        self._lock = threading.RLock()

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    # --- writes ---
    def create(self, *, date_text: str, date_iso: str, attendees: list[str], agenda: str = "") -> Meeting:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO meetings (date_text, date_iso, attendees, agenda, status) "
                "VALUES (?, ?, ?, ?, 'New')",
                (date_text, date_iso, json.dumps(attendees), agenda),
            )
            self.conn.commit()
            mid = cur.lastrowid
        return self.get(mid)

    def update(self, meeting_id: int, **fields: Any) -> None:
        if not fields:
            return
        bad = set(fields) - self._WRITABLE
        if bad:
            raise ValueError(f"not writable: {sorted(bad)}")
        if "attendees" in fields and isinstance(fields["attendees"], (list, tuple)):
            fields["attendees"] = json.dumps(list(fields["attendees"]))
        if "headphones_mode" in fields:
            fields["headphones_mode"] = 1 if fields["headphones_mode"] else 0
        cols = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values())
        vals.append(meeting_id)
        with self._lock:
            self.conn.execute(
                f"UPDATE meetings SET {cols}, updated_at = datetime('now') WHERE id = ?",
                vals,
            )
            self.conn.commit()

    def delete(self, meeting_id: int) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
            self.conn.commit()

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
