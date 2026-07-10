"""Ghost-window regression: no widget may be SHOWN while parentless.

setVisible(True)/show() on a widget with no parent promotes it to a top-level
native window — on Windows that flashed a translucent ghost "Earshot" window
(title bar and all) every time the home page refreshed (v0.32.1's teardown fix
missed this build-side variant; Hayden screenshotted the ghost twice).

The guard patches QWidget.setVisible/show for the duration of the page builds
and records any parentless show of an ordinary widget. Dialogs, menus, main
windows and the recording overlay are legitimately top-level and excluded.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_ghost_flash.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QMainWindow,
    QMenu,
    QWidget,
)

from meeting_notes.config import Config  # noqa: E402
from meeting_notes.storage import db as dbmod  # noqa: E402
from meeting_notes.storage.repository import MeetingRepository  # noqa: E402
from meeting_notes.ui.shell import Shell  # noqa: E402
from meeting_notes.ui.theme_controller import ThemeController  # noqa: E402

NOTES = (
    '{"title":"Demo","summary":"A demo meeting.","attendees":["Hayden"],'
    '"action_items":[{"task":"Write docs","owner":"Hayden","done":false,"confirmed":true},'
    '{"task":"Ship it","owner":null,"done":true,"confirmed":true}],'
    '"sections":[{"heading":"Topic","bullets":["A **key** point","Another point"]}]}'
)


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


class GhostGuard:
    """Context manager that records every parentless show of a plain widget."""

    ALLOWED = (QMainWindow, QDialog, QMenu)

    def __init__(self):
        self.violations: list[str] = []

    def __enter__(self):
        self._set_visible = QWidget.setVisible
        self._show = QWidget.show
        guard = self

        def record(widget):
            guard.violations.append(
                f"{type(widget).__name__} (objectName={widget.objectName()!r}) "
                "shown while parentless"
            )

        def set_visible(widget, visible, _orig=self._set_visible):
            if visible and widget.parent() is None and not isinstance(widget, guard.ALLOWED):
                record(widget)
            return _orig(widget, visible)

        def show(widget, _orig=self._show):
            if widget.parent() is None and not isinstance(widget, guard.ALLOWED):
                record(widget)
            return _orig(widget)

        QWidget.setVisible = set_visible
        QWidget.show = show
        return self

    def __exit__(self, *exc):
        QWidget.setVisible = self._set_visible
        QWidget.show = self._show
        return False


def main() -> int:
    app = QApplication.instance() or QApplication([])
    cfg = Config()
    cfg.show_dashboard = True
    tmp = tempfile.mkdtemp()
    repo = MeetingRepository(dbmod.connect(Path(tmp) / "ghost.db"))
    folder = repo.create_folder("Work", "#6366F1")
    for i in range(3):
        m = repo.create(date_text=f"{i+1}st July 2026", date_iso=f"2026-07-0{i+1}",
                        attendees=["Hayden"], folder_id=folder.id if i else None)
        repo.update(m.id, title=f"Meeting {i+1}", status="Done", duration_secs=60,
                    transcript="[00:00] Me: hi", notes_json=NOTES)

    theme = ThemeController(cfg)
    theme.apply()
    shell = Shell(repo, cfg, theme)
    shell.resize(1400, 900)
    shell.show()
    app.processEvents()

    print("== home refresh shows nothing parentless (expanded sections) ==")
    cfg.meetings_collapsed = False
    cfg.dashboard_collapsed = False
    with GhostGuard() as g:
        shell.home.refresh()
        app.processEvents()
    for v in g.violations:
        print("   VIOLATION:", v)
    check("no parentless shows during home refresh", not g.violations)

    print("== repeat navigation home <-> ask <-> settings stays clean ==")
    with GhostGuard() as g:
        for _ in range(3):
            shell.show_home()
            shell.show_ask()
            shell.show_settings() if hasattr(shell, "show_settings") else None
            app.processEvents()
    for v in g.violations:
        print("   VIOLATION:", v)
    check("no parentless shows across navigation", not g.violations)

    print("== meeting detail page build stays clean ==")
    first = repo.list()[0]
    with GhostGuard() as g:
        shell.open_meeting(first.id) if hasattr(shell, "open_meeting") else shell.detail.load(first.id)
        app.processEvents()
    for v in g.violations:
        print("   VIOLATION:", v)
    check("no parentless shows building the detail page", not g.violations)

    QApplication.clipboard().clear()
    print("\nGHOST-FLASH TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
