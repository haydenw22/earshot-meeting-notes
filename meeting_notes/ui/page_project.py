"""Project page: one project's meeting notes in the main window.

The sidebar lists only project folders now — clicking one lands here. Three
modes share the page:
  - a real project (folder_id)          — name, colour icon, manage menu
  - Uncategorized (folder_id None)      — every meeting not filed in a project
  - search results (sidebar search box) — full-text matches across all meetings

Rows are the same MeetingRow cards as Home (open / move / delete kebab), so
meetings look and behave identically everywhere.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .page_home import MeetingRow
from .widgets import FOLDER_COLORS, clear_layout


class ProjectPage(QWidget):
    def __init__(self, shell, repo, cfg, theme):
        super().__init__()
        self.shell = shell
        self.repo = repo
        self.cfg = cfg
        self.theme = theme
        # ("folder", folder_id|None) — None is Uncategorized — or ("search", query)
        self._mode: tuple[str, object] = ("folder", None)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 32, 40, 24)
        root.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(14)
        self.icon_tile = QLabel()
        self.icon_tile.setFixedSize(44, 44)
        self.icon_tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.icon_tile)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.h1 = QLabel("")
        self.h1.setObjectName("H1")
        titles.addWidget(self.h1)
        self.count = QLabel("")
        self.count.setObjectName("Muted")
        titles.addWidget(self.count)
        header.addLayout(titles)
        header.addStretch(1)

        self.manage_btn = QPushButton("⋮")
        self.manage_btn.setProperty("variant", "ghost")
        self.manage_btn.setProperty("iconbtn", True)
        self.manage_btn.setFixedSize(34, 34)
        self.manage_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.manage_btn.setStyleSheet("QPushButton{font-size:17px; font-weight:700;}")
        self.manage_btn.setToolTip("Manage project")
        self.manage_btn.clicked.connect(self._open_manage_menu)
        header.addWidget(self.manage_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        self.list_lay = QVBoxLayout(host)
        self.list_lay.setContentsMargins(2, 2, 2, 2)
        self.list_lay.setSpacing(12)
        self.scroll.setWidget(host)
        root.addWidget(self.scroll, 1)

    # ---------- entry points ----------
    def load_folder(self, folder_id) -> None:
        """Show one project (an int id) or Uncategorized (None)."""
        self._mode = ("folder", folder_id)
        self.refresh()

    def load_search(self, query: str) -> None:
        self._mode = ("search", (query or "").strip())
        self.refresh()

    @property
    def mode(self) -> tuple[str, object]:
        return self._mode

    # ---------- render ----------
    def refresh(self) -> None:
        clear_layout(self.list_lay)

        meetings = self.repo.list()
        folders_by_id = {f.id: f for f in self.repo.list_folders()}
        kind, val = self._mode

        if kind == "search":
            query = str(val)
            match_ids = set(self.repo.search(query)) if query else set()
            low = query.lower()
            shown = [m for m in meetings
                     if m.id in match_ids or (low and low in (m.title or "").lower())]
            self.h1.setText("Search")
            n = len(shown)
            self.count.setText(f'{n} result{"s" if n != 1 else ""} for "{query}"')
            self._set_tile("search", self.theme.color("primary"))
            self.manage_btn.setVisible(False)
            empty_text = "No meetings match that search."
        elif val is None:
            shown = [m for m in meetings if m.folder_id is None]
            self.h1.setText("Uncategorized")
            n = len(shown)
            self.count.setText(f'{n} meeting{"s" if n != 1 else ""} not filed in a project')
            self._set_tile("folder", self.theme.color("text_faint"))
            self.manage_btn.setVisible(False)
            empty_text = "Nothing here — every meeting is filed in a project."
        else:
            folder = folders_by_id.get(val)
            if folder is None:  # deleted while shown — render an empty shell
                self.h1.setText("Project")
                self.count.setText("This project no longer exists.")
                self._set_tile("folder", self.theme.color("text_faint"))
                self.manage_btn.setVisible(False)
                self.list_lay.addStretch(1)
                return
            shown = [m for m in meetings if m.folder_id == val]
            self.h1.setText(folder.name)
            n = len(shown)
            self.count.setText(f'{n} meeting{"s" if n != 1 else ""}')
            self._set_tile("folder", folder.color)
            self.manage_btn.setVisible(True)
            empty_text = "No meetings in this project yet. File one from a meeting's ⋮ menu, or pick this project when you start a recording."
            # inside a project every row would repeat the same folder chip —
            # drop it (MeetingRow only chips folders it can look up)
            folders_by_id = {fid: f for fid, f in folders_by_id.items() if fid != val}

        for m in shown:
            self.list_lay.addWidget(MeetingRow(m, self.theme, self.repo, self.shell, folders_by_id))
        if not shown:
            empty = QLabel(empty_text)
            empty.setObjectName("Muted")
            empty.setWordWrap(True)
            empty.setContentsMargins(6, 10, 6, 0)
            self.list_lay.addWidget(empty)
        self.list_lay.addStretch(1)

    def _set_tile(self, icon_name: str, color: str) -> None:
        self.icon_tile.setStyleSheet(
            f"background:{self.theme.color('surface_hover')}; border-radius:12px;"
        )
        self.icon_tile.setPixmap(icons.pixmap(icon_name, color, 20))

    # ---------- manage menu (real projects only) ----------
    def _open_manage_menu(self) -> None:
        kind, val = self._mode
        if kind != "folder" or val is None:
            return
        folders = {f.id: f for f in self.repo.list_folders()}
        folder = folders.get(val)
        if folder is None:
            return

        menu = QMenu(self)
        rename_act = menu.addAction(icons.icon("pencil", self.theme.color("text_muted"), 15), "Rename…")
        color_menu = menu.addMenu("Colour")
        color_actions = {}
        for name, hexcolor in FOLDER_COLORS:
            act = color_menu.addAction(self.shell._color_icon(hexcolor), name)
            color_actions[act] = hexcolor
        menu.addSeparator()
        delete_act = menu.addAction(icons.icon("trash", self.theme.color("danger"), 15), "Delete project")

        chosen = menu.exec(self.manage_btn.mapToGlobal(QPoint(0, self.manage_btn.height())))
        if chosen is None:
            return
        if chosen is rename_act:
            self.shell._rename_folder(folder.id, folder.name)
        elif chosen is delete_act:
            self.shell._delete_folder(folder.id, folder.name)
            # deletion unfiles its meetings; if the user confirmed, this project
            # is gone — land on Uncategorized where those meetings now live
            if folder.id not in {f.id for f in self.repo.list_folders()}:
                self.shell.show_project(None)
        elif chosen in color_actions:
            self.repo.update_folder(folder.id, color=color_actions[chosen])
            self.shell.notify_data_changed()
        self.refresh()

    def apply_theme(self) -> None:
        self.refresh()
