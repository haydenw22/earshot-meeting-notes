"""Render the app's pages to PNGs in both themes (offscreen) so the UI can be
reviewed visually without a display.

Usage:  QT_QPA_PLATFORM=offscreen python tools/screenshots.py [out_dir]
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# NOTE: render with the native platform (real fonts) but invisibly via
# WA_DontShowOnScreen — the offscreen platform renders Latin text as tofu boxes.
# Isolate app data so this never reads/overwrites the real config.json.
import os as _os
import tempfile as _tempfile

_os.environ["LOCALAPPDATA"] = _tempfile.mkdtemp(prefix="earshot_shots_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from meeting_notes.config import Config  # noqa: E402
from meeting_notes.storage import db as dbmod  # noqa: E402
from meeting_notes.storage.repository import MeetingRepository  # noqa: E402
from meeting_notes.ui.shell import Shell  # noqa: E402
from meeting_notes.ui.theme_controller import ThemeController  # noqa: E402

SAMPLES = [
    ("Webinar Strategy, VSL Development, and Resource Optimization", ["Hayden", "Sam", "Priya"], "Done",
     '{"title":"Webinar Strategy, VSL Development, and Resource Optimization",'
     '"summary":"The team aligned on the webinar funnel, agreed to prioritise the VSL rewrite, and '
     'mapped resourcing for the next sprint.","attendees":["Hayden","Sam","Priya"],'
     '"decisions":["Ship the VSL rewrite first","Move the webinar to the 14th"],'
     '"action_items":[{"task":"Draft the new VSL script","owner":"Sam","due":"Friday"},'
     '{"task":"Book the webinar slot","owner":"Priya","due":null}],"topics":["webinar","VSL","resourcing"]}'),
    ("Ad Spend Scaling and Organic Content Strategy Review", ["Hayden", "Marco"], "Done",
     '{"title":"Ad Spend Scaling and Organic Content Strategy Review",'
     '"summary":"Reviewed paid performance and agreed a 20% scale on the winning campaign while '
     'doubling down on organic shorts.","attendees":["Hayden","Marco"],'
     '"decisions":["Scale the top campaign by 20%"],'
     '"action_items":[{"task":"Increase budget on Campaign A","owner":"Marco","due":null}],'
     '"topics":["ads","organic","scaling"]}'),
    ("Quick sync on the Q3 roadmap", ["Hayden"], "Transcribed", None),
]


def build(out_dir: Path, mode: str) -> None:
    cfg = Config()
    cfg.theme_mode = mode
    tmp = tempfile.mkdtemp()
    repo = MeetingRepository(dbmod.connect(Path(tmp) / "shot.db"))
    folder = repo.create_folder("Marketing", "#6366F1")
    first_id = None
    for i, (title, att, status, notes) in enumerate(SAMPLES):
        m = repo.create(date_text="25th June 2026", date_iso="2026-06-25", attendees=att,
                        folder_id=folder.id if i < 2 else None)
        repo.update(m.id, title=title, status=status, duration_secs=1875,
                    transcript="[00:00] Me: Hi everyone, thanks for joining.\n"
                               "[00:04] Them: Great to be here — shall we start with the funnel?\n"
                               "[00:09] Me: Yes. I think the VSL is the bottleneck right now.",
                    notes_json=notes)
        if first_id is None:
            first_id = m.id

    theme = ThemeController(cfg)
    theme.apply()
    shell = Shell(repo, cfg, theme)
    shell.resize(1336, 843)  # the app's default viewport
    app = QApplication.instance()
    shell.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)  # render, don't display
    shell.show()
    app.processEvents()

    def shot(name: str) -> None:
        app.processEvents()
        path = out_dir / f"{name}_{mode}.png"
        shell.grab().save(str(path))
        print(f"  {path}")

    shell.show_home(); shot("home")
    shell.show_record(); shot("record")
    shell.open_meeting(first_id); shot("detail")
    shell.show_project(folder.id); shot("project")
    shell.show_settings("general"); shot("settings")
    shell.show_settings("integrations"); shot("integrations")
    shell.show_settings("account"); shot("account")
    shell.show_settings("plans"); shot("plans")
    shell.show_help(); shot("help")
    repo.close()


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="mn_shots_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    QApplication.instance() or QApplication([])
    for mode in ("light", "dark"):
        build(out_dir, mode)
    print(f"OUT_DIR={out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
