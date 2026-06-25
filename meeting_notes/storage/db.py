"""SQLite connection + schema. The local DB is the source of truth for every
meeting; notes are written here BEFORE any external sync so nothing is ever lost.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from ..paths import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    title            TEXT,                 -- one-sentence title (post-transcription)
    date_text        TEXT,                 -- human-readable, e.g. '25th June 2026'
    date_iso         TEXT,                 -- '2026-06-25' for sorting
    attendees        TEXT,                 -- JSON array; editable during recording
    agenda           TEXT,                 -- pre-meeting agenda/notes (context for the AI)
    transcript       TEXT,                 -- merged speaker-labelled transcript
    notes_json       TEXT,                 -- structured MeetingNotes as JSON
    audio_dir        TEXT,                 -- folder holding raw + 2-channel audio
    headphones_mode  INTEGER DEFAULT 1,    -- 1 if recorded on headphones (no AEC)
    duration_secs    REAL,
    status           TEXT DEFAULT 'New',   -- New|Recording|Recorded|Transcribing|Summarizing|Done|Error
    error            TEXT,
    notion_page_id   TEXT,                 -- NULL until Notion sync (future)
    notion_synced_at TEXT,
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_meetings_date ON meetings(date_iso);
CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);
"""


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    p = path or db_path()
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


# Columns added after v1 — applied to existing DBs via ALTER TABLE.
_MIGRATIONS = {
    "agenda": "ALTER TABLE meetings ADD COLUMN agenda TEXT",
}


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    existing = {r[1] for r in conn.execute("PRAGMA table_info(meetings)").fetchall()}
    for col, ddl in _MIGRATIONS.items():
        if col not in existing:
            conn.execute(ddl)
    conn.commit()
