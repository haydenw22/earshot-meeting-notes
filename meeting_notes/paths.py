"""Filesystem locations for app data, recordings, config and the database.

Everything user-generated lives under %LOCALAPPDATA%\\Earshot so it survives app
reinstalls and never pollutes the source tree.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

APP_DIR_NAME = "Earshot"
_LEGACY_DIR_NAMES = ("MeetingNotes",)  # pre-rename data dirs to migrate from

# Optional user-chosen folder for recordings + screenshots (set at startup from
# Config.data_dir). The DB + config always stay in the app data dir.
_recordings_override: Optional[Path] = None


def set_recordings_dir(path) -> None:
    global _recordings_override
    _recordings_override = Path(path) if path else None


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
    """The folder recordings are saved into.

    If the user's custom folder is unavailable (USB/NAS drive disconnected),
    fall back to the default app-data folder rather than raising — this runs at
    stop-recording time, where an exception would lose the meeting's audio.
    """
    if _recordings_override is not None:
        try:
            _recordings_override.mkdir(parents=True, exist_ok=True)
            return _recordings_override
        except OSError:
            pass  # fall through to the always-available default
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
