"""Tests for Phase S2 — colour-coded FOLDERS: repository CRUD, the sidebar
folders tree (a real Shell, since that's where the interesting wiring lives),
the record-page folder picker, the home-page filter chips, the Ask-page scope
filter, and the detail-page "Move to folder" submenu.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_folders.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Isolate ALL app data to a throwaway dir so the test can never read or overwrite
# the real %LOCALAPPDATA%\Earshot\config.json (which would wipe the user's key/URL).
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from meeting_notes.config import Config  # noqa: E402
from meeting_notes.storage import db as dbmod  # noqa: E402
from meeting_notes.storage.repository import MeetingRepository  # noqa: E402
from meeting_notes.ui.page_ask import AskPage  # noqa: E402
from meeting_notes.ui.page_detail import DetailPage  # noqa: E402
from meeting_notes.ui.page_home import HomePage  # noqa: E402
from meeting_notes.ui.page_record import RecordPage  # noqa: E402
from meeting_notes.ui.shell import Shell  # noqa: E402
from meeting_notes.ui.theme_controller import ThemeController  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


def new_repo() -> MeetingRepository:
    return MeetingRepository(dbmod.connect(Path(tempfile.mkdtemp()) / "f.db"))


class _Shell:
    def show_home(self):
        pass

    def show_record(self):
        pass

    def open_meeting(self, _mid):
        pass

    def notify_data_changed(self):
        self.notified = True


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841
    theme = ThemeController(Config())
    theme.apply()

    # ---------------------------------------------------------------
    print("== repository: folder CRUD ==")
    repo = new_repo()
    f1 = repo.create_folder("Acme Corp", "#EF4444")
    f2 = repo.create_folder("beta team", "#3B82F6")
    check("create_folder returns a Folder with id/name/color",
          f1.id is not None and f1.name == "Acme Corp" and f1.color == "#EF4444")

    listed = repo.list_folders()
    check("list_folders returns both, ordered by name (case-insensitive)",
          [f.name for f in listed] == ["Acme Corp", "beta team"])

    repo.update_folder(f1.id, name="Acme Corporation", color="#22C55E")
    reloaded = {f.id: f for f in repo.list_folders()}
    check("update_folder updates name", reloaded[f1.id].name == "Acme Corporation")
    check("update_folder updates color", reloaded[f1.id].color == "#22C55E")

    print("== repository: create(folder_id=...) round-trips ==")
    m1 = repo.create(date_text="d", date_iso="2026-07-02", attendees=[], folder_id=f1.id)
    check("meeting created with folder_id", repo.get(m1.id).folder_id == f1.id)

    m2 = repo.create(date_text="d", date_iso="2026-07-02", attendees=[])
    check("meeting created with no folder_id defaults to None", repo.get(m2.id).folder_id is None)

    print("== repository: update(folder_id=None) unfiles ==")
    repo.update(m1.id, folder_id=None)
    check("update to folder_id=None unfiles the meeting", repo.get(m1.id).folder_id is None)
    repo.update(m1.id, folder_id=f2.id)
    check("update to a folder_id files the meeting", repo.get(m1.id).folder_id == f2.id)

    print("== repository: delete_folder unfiles its meetings, then deletes the folder ==")
    m3 = repo.create(date_text="d", date_iso="2026-07-02", attendees=[], folder_id=f2.id)
    repo.delete_folder(f2.id)
    check("meeting 1 unfiled after its folder is deleted", repo.get(m1.id).folder_id is None)
    check("meeting 3 unfiled after its folder is deleted", repo.get(m3.id).folder_id is None)
    check("folder no longer listed", f2.id not in {f.id for f in repo.list_folders()})
    check("meetings themselves are NOT deleted", repo.get(m1.id) is not None and repo.get(m3.id) is not None)
    repo.close()

    # ---------------------------------------------------------------
    print("== shell: sidebar project rows with 1 folder / 1 filed / 1 uncategorized ==")
    shell_repo = new_repo()
    folder = shell_repo.create_folder("Client X", "#6366F1")
    filed = shell_repo.create(date_text="d", date_iso="2026-07-02", attendees=[], folder_id=folder.id)
    shell_repo.update(filed.id, title="Filed meeting")
    unfiled = shell_repo.create(date_text="d", date_iso="2026-07-02", attendees=[])
    shell_repo.update(unfiled.id, title="Loose meeting")

    shell = Shell(shell_repo, Config(), theme)
    app.processEvents()

    rows = shell._project_rows
    check("one row per folder + the Uncategorized row", set(rows.keys()) == {folder.id, None})
    check("folder row shows name + count", "Client X" in rows[folder.id].text() and "(1)" in rows[folder.id].text())
    check("Uncategorized row shows its count", "Uncategorized" in rows[None].text() and "(1)" in rows[None].text())
    check("no meeting rows live in the sidebar any more",
          not hasattr(shell, "meeting_list") and not hasattr(shell, "folder_tree"))

    print("== shell: clicking a project opens it in the main window ==")
    shell.show_project(folder.id)
    app.processEvents()
    check("project page is the current widget", shell.stack.currentWidget() is shell.project)
    check("project page shows the folder name", shell.project.h1.text() == "Client X")
    check("its row is highlighted", shell._project_rows[folder.id].isChecked())
    check("other rows are not", not shell._project_rows[None].isChecked())

    shell.show_project(None)
    app.processEvents()
    check("Uncategorized opens as a project", shell.project.h1.text() == "Uncategorized")
    check("Uncategorized row is highlighted", shell._project_rows[None].isChecked())

    print("== shell: counts + open project refresh on notify_data_changed ==")
    shell_repo.update(unfiled.id, folder_id=folder.id)
    shell.notify_data_changed()
    app.processEvents()
    check("folder count updates", "(2)" in shell._project_rows[folder.id].text())
    check("Uncategorized count updates", "(0)" in shell._project_rows[None].text())
    shell_repo.update(unfiled.id, folder_id=None)
    shell.notify_data_changed()
    app.processEvents()
    check("moving back updates counts again", "(1)" in shell._project_rows[None].text())

    print("== shell: search opens results in the main window; clearing returns home ==")
    shell.search.setText("Filed")
    shell._run_search()
    app.processEvents()
    check("search shows the project page in search mode",
          shell.stack.currentWidget() is shell.project and shell.project.mode[0] == "search")
    check("search results count the matching meeting", 'for "Filed"' in shell.project.count.text())
    shell.search.setText("")
    shell._run_search()
    app.processEvents()
    check("clearing the box returns to home", shell.stack.currentWidget() is shell.home)

    print("== shell: folders_collapsed persists + hides the project rows ==")
    cfg2 = Config()
    check("folders_collapsed defaults False", cfg2.folders_collapsed is False)
    shell._toggle_folders_collapsed()
    check("project rows hidden after collapsing", shell.projects_host.isHidden())
    check("cfg.folders_collapsed now True", shell.cfg.folders_collapsed is True)
    shell._toggle_folders_collapsed()
    check("project rows visible again after expanding", not shell.projects_host.isHidden())

    print("== shell: new-folder round-trips through the repo into the sidebar ==")
    before = len(shell_repo.list_folders())
    shell_repo.create_folder("Second Folder", "#F59E0B")
    shell.notify_data_changed()
    app.processEvents()
    check("a directly-created folder shows up after notify_data_changed",
          len(shell_repo.list_folders()) == before + 1
          and len(shell._project_rows) == before + 2)  # + the Uncategorized row

    shell_repo.close()

    # ---------------------------------------------------------------
    print("== record page: folder_combo lists 'Uncategorized' + created folders ==")
    rec_repo = new_repo()
    rf = rec_repo.create_folder("Sales", "#22C55E")
    record = RecordPage(_Shell(), rec_repo, Config(), theme)
    record.on_shown()
    app.processEvents()
    combo_texts = [record.folder_combo.itemText(i) for i in range(record.folder_combo.count())]
    check("folder_combo starts with 'Uncategorized'", combo_texts[0] == "Uncategorized")
    check("folder_combo lists the created folder", "Sales" in combo_texts)
    check("folder_combo ends with the 'New project' entry", combo_texts[-1].endswith("New project…"))

    print("== record page: starting a recording with a folder selected stores folder_id ==")
    idx = record.folder_combo.findData(rf.id)
    check("the Sales folder is selectable in the combo", idx >= 0)
    record.folder_combo.setCurrentIndex(idx)
    # _start() opens real audio devices, which aren't available offscreen/headless;
    # exercise just the folder_id resolution logic that _start() feeds into repo.create().
    folder_data = record.folder_combo.currentData()
    resolved_folder_id = folder_data if folder_data != "__new__" else None
    check("resolved folder id matches the selected folder", resolved_folder_id == rf.id)
    created = rec_repo.create(date_text="d", date_iso="2026-07-02", attendees=[], folder_id=resolved_folder_id)
    check("meeting created this way carries the folder_id", rec_repo.get(created.id).folder_id == rf.id)

    # data=None ("Uncategorized") must resolve to folder_id=None, and the sentinel
    # "__new__" (if somehow left selected) must never leak into repo.create()
    record.folder_combo.setCurrentIndex(0)
    folder_data0 = record.folder_combo.currentData()
    check("'Uncategorized' resolves to folder_id=None", (folder_data0 if folder_data0 != "__new__" else None) is None)
    rec_repo.close()

    # ---------------------------------------------------------------
    print("== home page: _folder_filter yields correct filtered card counts ==")
    home_repo = new_repo()
    hf = home_repo.create_folder("Acme", "#EF4444")
    ha = home_repo.create(date_text="d", date_iso="2026-07-02", attendees=[], folder_id=hf.id)
    home_repo.update(ha.id, title="A")
    hb = home_repo.create(date_text="d", date_iso="2026-07-01", attendees=[], folder_id=hf.id)
    home_repo.update(hb.id, title="B")
    hc = home_repo.create(date_text="d", date_iso="2026-06-30", attendees=[])
    home_repo.update(hc.id, title="C")

    home = HomePage(_Shell(), home_repo, Config(), theme)
    home.refresh()
    all_meetings = home_repo.list()
    check("no filter -> all 3 meetings", len(home._filtered_meetings(all_meetings)) == 3)

    home._set_folder_filter(hf.id)
    check("folder filter -> only the 2 filed meetings",
          {m.title for m in home._filtered_meetings(home_repo.list())} == {"A", "B"})
    check("count label reflects the filtered count", home.count.text() == "2 in Acme")

    home._set_folder_filter("unfiled")
    check("unfiled filter -> only the 1 unfiled meeting",
          [m.title for m in home._filtered_meetings(home_repo.list())] == ["C"])
    check("count label reflects uncategorized", home.count.text() == "1 in Uncategorized")

    home._set_folder_filter(None)
    check("back to All -> 3 again", len(home._filtered_meetings(home_repo.list())) == 3)
    home_repo.close()

    # ---------------------------------------------------------------
    print("== ask page: scope-filter helper covers all/folder/unfiled/single ==")
    ask_repo = new_repo()
    af = ask_repo.create_folder("Legal", "#A855F7")
    aa = ask_repo.create(date_text="d", date_iso="2026-07-02", attendees=[], folder_id=af.id)
    ask_repo.update(aa.id, title="Filed one")
    ab = ask_repo.create(date_text="d", date_iso="2026-07-01", attendees=[])
    ask_repo.update(ab.id, title="Unfiled one")

    meetings = ask_repo.list()
    check("scope None -> all meetings", {m.id for m in AskPage.scoped_meetings(meetings, None)} == {aa.id, ab.id})
    check("scope ('folder', id) -> only that folder's meetings",
          [m.id for m in AskPage.scoped_meetings(meetings, ("folder", af.id))] == [aa.id])
    check("scope 'unfiled' -> only unfiled meetings",
          [m.id for m in AskPage.scoped_meetings(meetings, "unfiled")] == [ab.id])
    check("scope ('meeting', id) -> just that one meeting",
          [m.id for m in AskPage.scoped_meetings(meetings, ("meeting", ab.id))] == [ab.id])

    ask_page = AskPage(_Shell(), ask_repo, Config(), theme)
    ask_page.on_shown()
    app.processEvents()
    scope_texts = [ask_page.scope_combo.itemText(i) for i in range(ask_page.scope_combo.count())]
    check("scope combo starts with 'All meetings'", scope_texts[0] == "All meetings")
    check("scope combo lists the folder", "Legal" in scope_texts)
    check("scope combo lists 'Uncategorized'", "Uncategorized" in scope_texts)
    check("scope combo lists recent meetings", "Filed one" in scope_texts and "Unfiled one" in scope_texts)
    ask_repo.close()

    # ---------------------------------------------------------------
    print("== detail page: move_menu lists 'Uncategorized' + folders; triggering moves the meeting ==")
    det_repo = new_repo()
    df = det_repo.create_folder("Ops", "#14B8A6")
    dm = det_repo.create(date_text="d", date_iso="2026-07-02", attendees=[])
    det_repo.update(dm.id, title="Detail test meeting", status="Done")

    detail_shell = _Shell()
    page = DetailPage(detail_shell, det_repo, Config(), theme)
    page.load(dm.id)
    move_texts = [a.text() for a in page.move_menu.actions()]
    check("move_menu contains 'Uncategorized'", "Uncategorized" in move_texts)
    check("move_menu contains the Ops folder", "Ops" in move_texts)
    check("move_menu contains 'New project…'", "New project…" in move_texts)

    # current location is marked with a check ICON (checkable indicators render
    # awkwardly next to icons in QMenu, so the design uses icons only)
    check("no move_menu row is checkable", all(not a.isCheckable() for a in page.move_menu.actions()))
    check("every row carries an icon (aligned column)",
          all(not a.icon().isNull() for a in page.move_menu.actions() if a.text()))

    page._move_to_folder(df.id)
    check("triggering the folder action updates folder_id", det_repo.get(dm.id).folder_id == df.id)
    check("shell was notified so the sidebar refreshes", getattr(detail_shell, "notified", False))

    page._move_to_folder(None)
    check("moving back to 'Uncategorized' unfiles the meeting", det_repo.get(dm.id).folder_id is None)
    det_repo.close()

    print("\nFOLDERS TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
