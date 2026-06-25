"""Filesystem locations for app data, recordings, config and the database.

Everything user-generated lives under %LOCALAPPDATA%\\Earshot so it survives app
reinstalls and never pollutes the source tree.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

APP_DIR_NAME = "Earshot"
_LEGACY_DIR_NAMES = ("MeetingNotes",)  # pre-rename data dirs to migrate from


def app_data_dir() -> Path:
    """Root directory for all persistent app data (migrates a legacy folder once)."""
    base = Path(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"))
    d = base / APP_DIR_NAME
    if not d.exists():
        for legacy in _LEGACY_DIR_NAMES:
            old = base / legacy
            if not old.exists():
                continue
            try:
                old.rename(d)  # fast path: same volume, keeps meetings + DB
                break
            except OSError:
                # rename failed (locked / cross-volume) — copy so data is never
                # lost; the old folder stays as a backup rather than orphaned.
                try:
                    shutil.copytree(old, d, dirs_exist_ok=True)
                    break
                except OSError:
                    pass
    d.mkdir(parents=True, exist_ok=True)
    return d


def recordings_dir() -> Path:
    d = app_data_dir() / "recordings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def meeting_dir(meeting_id: int) -> Path:
    """Per-meeting folder holding its raw + processed audio."""
    d = recordings_dir() / f"meeting_{meeting_id:06d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return app_data_dir() / "meetings.db"


def config_path() -> Path:
    return app_data_dir() / "config.json"
