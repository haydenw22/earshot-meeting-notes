"""Delete a meeting and its recording folder, honestly.

The old flow removed files with ignore_errors=True and then deleted the
database row no matter what: a locked or undeletable file silently left
sensitive audio on disk while the app reported success and forgot where the
files were. Deletion now verifies the folder is actually gone BEFORE the row is
removed; on failure the meeting stays in the library with the folder and error
reported, so the user can fix the cause and retry (or open the folder).

One shared implementation for the Home row menu and the Detail page, so the two
paths can't drift apart again.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeleteResult:
    ok: bool
    error: str = ""   # one-line reason files could not be removed
    folder: str = ""  # the folder that still holds files, when not ok


def is_recording_folder(path: str, meeting_id: int | None = None) -> bool:
    """Guard rmtree: only ever touch a per-meeting folder (defends against a
    tampered or mis-set audio_dir turning Delete into arbitrary recursive
    deletion). That is one under the current recordings root, or this
    meeting's own meeting_NNNNNN folder wherever an earlier recordings
    location put it: after the folder is changed in Settings, older meetings
    still live in the old one."""
    from ..paths import recordings_dir
    try:
        raw = Path(path)
        p = raw.resolve()
        if (meeting_id is not None and p.name == f"meeting_{meeting_id:06d}"
                and not raw.is_symlink()):
            return True
        root = recordings_dir().resolve()
        return root in p.parents and p.name.startswith("meeting_")
    except OSError:
        return False


def delete_meeting(repo, meeting_id: int) -> DeleteResult:
    """Remove the meeting's recording folder (audio + screenshots + spools) and
    then its database row. The row is only removed once the files are verifiably
    gone; a failed file deletion keeps the meeting so nothing is orphaned."""
    m = repo.get(meeting_id)
    folder = m.audio_dir or ""
    if m.status == "Recording":
        # Its audio is still being written; deleting the row now would leave
        # the finished recording in a folder no meeting points to.
        return DeleteResult(False, error="it is still being recorded. Stop the recording first.",
                            folder=folder)
    if folder and os.path.isdir(folder) and is_recording_folder(folder, meeting_id):
        err = ""
        try:
            shutil.rmtree(folder)
        except OSError as e:
            err = str(e)
        if os.path.exists(folder):  # verify: rmtree can partially succeed
            return DeleteResult(False, error=err or "some files could not be removed",
                                folder=folder)
    repo.delete(meeting_id)
    return DeleteResult(True)
