"""Meeting detail page: title, meta chips, the structured notes and transcript
in tabs, plus re-summarise / re-transcribe / open-folder / delete actions.
"""
from __future__ import annotations

import html as _html
import json
import os
import re as _re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..capture import screen as screen_capture
from ..util import stats as _stats

from ..storage.repository import Meeting
from .widgets import Card, clear_layout, make_chip, status_chip
from .workers import FuncWorker


def _md_to_html(text: str) -> str:
    """Escape, then render **bold** as <b> for QLabel rich text."""
    esc = _html.escape(text or "")
    return _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)


class _ClickableImage(QLabel):
    def __init__(self, on_click):
        super().__init__()
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
        super().mouseReleaseEvent(event)


class DetailPage(QWidget):
    def __init__(self, shell, repo, cfg, theme):
        super().__init__()
        self.shell = shell
        self.repo = repo
        self.cfg = cfg
        self.theme = theme
        self.meeting_id: int | None = None
        self.worker: FuncWorker | None = None
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 28, 40, 28)
        root.setSpacing(16)

        self.back_btn = QPushButton("  Back")
        self.back_btn.setProperty("variant", "ghost")
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.setFixedWidth(110)
        self.back_btn.clicked.connect(lambda: self.shell.show_home())
        root.addWidget(self.back_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        self.title = QLabel()
        self.title.setObjectName("H1")
        self.title.setTextFormat(Qt.TextFormat.PlainText)  # AI-generated title → no rich text
        self.title.setWordWrap(True)
        root.addWidget(self.title)

        self.meta_row = QHBoxLayout()
        self.meta_row.setSpacing(8)
        self.meta_row.addStretch(1)
        root.addLayout(self.meta_row)

        # collapsible panel that lists the attendee names (toggled by the chip)
        self.att_panel = QFrame()
        self.att_panel.setVisible(False)
        self.att_panel_lay = QHBoxLayout(self.att_panel)
        self.att_panel_lay.setContentsMargins(0, 2, 0, 6)
        self.att_panel_lay.setSpacing(6)
        root.addWidget(self.att_panel)

        card = Card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(8, 8, 8, 8)
        self.tabs = QTabWidget()
        # Notes tab is a scrollable widget (so action items can be real checkboxes)
        self.notes_scroll = QScrollArea()
        self.notes_scroll.setWidgetResizable(True)
        self.notes_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.notes_host = QWidget()
        self.notes_lay = QVBoxLayout(self.notes_host)
        self.notes_lay.setContentsMargins(20, 16, 20, 16)
        self.notes_lay.setSpacing(8)
        self.notes_lay.addStretch(1)
        self.notes_scroll.setWidget(self.notes_host)

        # Transcript tab — bookmark jump-chips above the text
        transcript_tab = QWidget()
        tt = QVBoxLayout(transcript_tab)
        tt.setContentsMargins(8, 8, 8, 8)
        tt.setSpacing(8)
        self.bookmarks_host = QWidget()
        self.bookmarks_row = QHBoxLayout(self.bookmarks_host)
        self.bookmarks_row.setContentsMargins(4, 0, 4, 0)
        self.bookmarks_row.setSpacing(6)
        self.transcript_view = QTextBrowser()
        tt.addWidget(self.bookmarks_host)
        tt.addWidget(self.transcript_view, 1)

        # Screen tab — captured screenshots (hidden when there are none)
        self.screen_scroll = QScrollArea()
        self.screen_scroll.setWidgetResizable(True)
        self.screen_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.screen_host = QWidget()
        self.screen_grid = QGridLayout(self.screen_host)
        self.screen_grid.setContentsMargins(16, 16, 16, 16)
        self.screen_grid.setSpacing(12)
        self.screen_scroll.setWidget(self.screen_host)

        self.tabs.addTab(self.notes_scroll, "Notes")
        self.tabs.addTab(transcript_tab, "Transcript")
        self._screen_tab_index = self.tabs.addTab(self.screen_scroll, "Screen")
        cl.addWidget(self.tabs)
        root.addWidget(card, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        root.addWidget(self.status_label)

        # AI action runner (the prompt workbench)
        ai_row = QHBoxLayout()
        ai_row.setSpacing(8)
        ai_lbl = QLabel("AI action:")
        ai_lbl.setObjectName("Muted")
        self.action_combo = QComboBox()
        self.run_action_btn = QPushButton("Run")
        self.run_action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_action_btn.clicked.connect(self._run_ai_action)
        ai_row.addWidget(ai_lbl)
        ai_row.addWidget(self.action_combo, 1)
        ai_row.addWidget(self.run_action_btn)
        root.addLayout(ai_row)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.copy_btn = self._action("Copy", None)
        self.copy_btn.setProperty("variant", "primary")
        self.share_btn = self._action("Share…", self._share_html)
        self.todoist_btn = self._action("To Todoist", self._send_todoist)
        self.more_btn = self._action("More", None)
        self.delete_btn = self._action("Delete", self._delete)
        btns.addWidget(self.copy_btn)
        btns.addWidget(self.share_btn)
        btns.addWidget(self.todoist_btn)
        btns.addStretch(1)
        btns.addWidget(self.more_btn)
        btns.addWidget(self.delete_btn)
        root.addLayout(btns)

        self.more_menu = QMenu(self.more_btn)
        self.act_resummarise = self.more_menu.addAction("Re-summarise", self._resummarise)
        self.act_reprocess = self.more_menu.addAction("Re-transcribe", self._reprocess)
        self.act_folder = self.more_menu.addAction("Open audio folder", self._open_folder)
        self.move_menu = self.more_menu.addMenu("Move to project")
        self.more_btn.setMenu(self.more_menu)

        self.copy_menu = QMenu(self.copy_btn)
        self.act_copy_all = self.copy_menu.addAction("Copy all", self._copy_all)
        self.act_copy_actions = self.copy_menu.addAction("Copy action items", self._copy_actions_todo)
        self.act_copy_summary = self.copy_menu.addAction("Copy summary", self._copy_summary_only)
        self.copy_btn.setMenu(self.copy_menu)

    def _action(self, text: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        if slot is not None:
            b.clicked.connect(slot)
        return b

    # ---------- data ----------
    def load(self, meeting_id: int) -> None:
        self.meeting_id = meeting_id
        self.refresh()

    def refresh(self) -> None:
        if self.meeting_id is None:
            return
        try:
            m = self.repo.get(self.meeting_id)
        except KeyError:
            # meeting was deleted; a later theme toggle / worker completion must
            # not crash trying to re-render it
            self.meeting_id = None
            return
        self.title.setText(m.title or "Untitled meeting")

        # rebuild meta chips (keep the trailing stretch item; hide before
        # detaching so the chips can't flash as a ghost top-level window)
        while self.meta_row.count() > 1:
            item = self.meta_row.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        idx = 0
        if m.date_text:
            self.meta_row.insertWidget(idx, self._meta_chip(m.date_text)); idx += 1
        self.meta_row.insertWidget(idx, self._attendees_chip(m.attendees)); idx += 1
        if m.duration_secs:
            self.meta_row.insertWidget(idx, self._meta_chip(f"{int(m.duration_secs // 60)}m {int(m.duration_secs % 60)}s")); idx += 1
        self.meta_row.insertWidget(idx, status_chip(m.status, self.theme))
        self._populate_attendees(m.attendees)

        self._render_notes(m)
        self.transcript_view.setPlainText(m.transcript or "No transcript yet.")
        self._render_screenshots(m)
        self._render_bookmarks(m)
        self._populate_actions()
        self.copy_btn.setEnabled(bool(m.notes))
        self.share_btn.setEnabled(bool(m.notes or m.transcript))
        has_open = any(isinstance(a, dict) and not a.get("done") and a.get("confirmed", True)
                       for a in (m.notes or {}).get("action_items") or [])
        self.todoist_btn.setEnabled(has_open)
        self.todoist_btn.setToolTip(
            "Create a Todoist task for each accepted open action item"
            if has_open else "No accepted open action items to send (keep ✓ suggestions first)"
        )
        self.act_resummarise.setEnabled(bool(m.transcript))
        self.act_reprocess.setEnabled(bool(m.audio_dir))
        self.act_folder.setEnabled(bool(m.audio_dir))
        self._populate_move_menu(m)
        self.run_action_btn.setEnabled(bool(m.transcript or m.notes))
        self.apply_theme()

    # ---------- move to folder ----------
    def _populate_move_menu(self, m) -> None:
        """One aligned icon column: a folder glyph in the folder's colour, and a
        check mark (in that colour) marking where the meeting currently lives —
        no checkbox indicators, which Qt renders awkwardly next to icons."""
        from . import icons

        self.move_menu.clear()

        def add(name, color, text, slot):
            act = self.move_menu.addAction(icons.icon(name, color, 15), text)
            act.triggered.connect(slot)
            return act

        # no separators: rows flow exactly like the parent More menu so the two
        # read as one congruent control
        here = m.folder_id is None
        add("check" if here else "folder", self.theme.color("text_faint"),
            "Uncategorized", lambda _=False: self._move_to_folder(None))

        for f in self.repo.list_folders():
            here = m.folder_id == f.id
            add("check" if here else "folder", f.color, f.name,
                lambda _=False, fid=f.id: self._move_to_folder(fid))

        add("plus", self.theme.color("text_muted"), "New project…", self._move_to_new_folder)

    def _move_to_folder(self, folder_id) -> None:
        if self.meeting_id is None:
            return
        self.repo.update(self.meeting_id, folder_id=folder_id)
        self.refresh()
        self.shell.notify_data_changed()

    def _move_to_new_folder(self) -> None:
        from .folder_dialog import ask_new_folder
        result = ask_new_folder(self, self.theme)
        if result is None:
            return
        name, color = result
        folder = self.repo.create_folder(name, color)
        self._move_to_folder(folder.id)

    def _meta_chip(self, text: str):
        from .widgets import make_chip
        return make_chip(text, fg=self.theme.color("text_muted"), bg=self.theme.color("surface_hover"))

    def _attendees_chip(self, attendees: list[str]):
        """The attendees meta chip — clickable to reveal the names if there are any."""
        n = len(attendees)
        if not n:
            return self._meta_chip("No attendees")
        btn = QPushButton(f"{n} attendee" + ("s" if n != 1 else ""))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setIcon(self.theme.icon("chevron-right", "text_muted", 13))
        muted = self.theme.color("text_muted")
        bg = self.theme.color("surface_hover")
        hover = self.theme.color("border_strong")
        btn.setStyleSheet(
            f"QPushButton{{background:{bg}; color:{muted}; border:none; border-radius:9px;"
            f"padding:3px 10px; font-size:12px; font-weight:600;}}"
            f"QPushButton:hover{{background:{hover};}}"
        )
        btn.clicked.connect(self._toggle_attendees)
        self.att_btn = btn
        return btn

    def _populate_attendees(self, attendees: list[str]) -> None:
        from .widgets import make_chip
        clear_layout(self.att_panel_lay)
        for name in attendees:
            self.att_panel_lay.addWidget(
                make_chip(name, fg=self.theme.color("primary"), bg=self.theme.color("primary_soft"))
            )
        self.att_panel_lay.addStretch(1)
        self.att_panel.setVisible(False)  # collapsed on (re)load

    def _toggle_attendees(self) -> None:
        show = not self.att_panel.isVisible()
        self.att_panel.setVisible(show)
        self.att_btn.setIcon(self.theme.icon("chevron-down" if show else "chevron-right", "text_muted", 13))

    # ---------- notes rendering (agenda, summary, checkbox actions, sections) ----------
    def _render_notes(self, m) -> None:
        clear_layout(self.notes_lay)

        if m.agenda and m.agenda.strip():
            self.notes_lay.addWidget(self._notes_heading("Agenda"))
            ag = QLabel(m.agenda.strip())
            ag.setObjectName("Muted")
            ag.setWordWrap(True)
            self.notes_lay.addWidget(ag)

        notes = m.notes
        if not notes:
            msg = ("Transcribed — add an Anthropic API key in Settings and click Re-summarise."
                   if m.transcript else "No notes yet.")
            lbl = QLabel(msg)
            lbl.setObjectName("Muted")
            lbl.setWordWrap(True)
            self.notes_lay.addWidget(lbl)
            self.notes_lay.addStretch(1)
            return

        if notes.get("summary"):
            # _md_to_html escapes first, so rich text stays injection-safe while
            # **bold** actually renders instead of showing literal asterisks
            s = QLabel(_md_to_html(notes["summary"]))
            s.setWordWrap(True)
            s.setTextFormat(Qt.TextFormat.RichText)
            self.notes_lay.addWidget(s)

        tt = _stats.talk_time(m.transcript or "")
        if tt["has_speakers"]:
            stat = QLabel(f"Talk-time (approx) — you {tt['me_pct']}%  ·  them {tt['them_pct']}%   ·   {tt['total_words']} words")
            stat.setObjectName("Faint")
            self.notes_lay.addWidget(stat)

        actions = notes.get("action_items") or []
        if actions:
            self.notes_lay.addWidget(self._notes_heading("Action items"))
            # legacy items (no 'confirmed' key) count as accepted
            if any(isinstance(a, dict) and not a.get("confirmed", True) and not a.get("done")
                   for a in actions):
                hint = QLabel("Suggested by the AI — keep ✓ the real ones (they join your "
                              "to-do list), edit ✎ to correct, or dismiss ✕.")
                hint.setObjectName("Faint")
                hint.setWordWrap(True)
                self.notes_lay.addWidget(hint)
            for i, a in enumerate(actions):
                self.notes_lay.addWidget(self._action_row(i, a))

        for sec in notes.get("sections") or []:
            heading = sec.get("heading") or ""
            if heading:
                self.notes_lay.addWidget(self._notes_heading(heading))
            for bullet in sec.get("bullets") or []:
                self.notes_lay.addWidget(self._bullet(bullet))

        self.notes_lay.addStretch(1)

    def _notes_heading(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("H3")
        lbl.setContentsMargins(0, 12, 0, 2)
        return lbl

    def _mini_btn(self, text: str, tooltip: str, *, accent: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolTip(tooltip)
        fg = self.theme.color("primary" if accent else "text_faint")
        bg = self.theme.color("primary_soft" if accent else "surface_hover")
        b.setStyleSheet(
            f"QPushButton{{background:{bg}; color:{fg}; border:none; border-radius:8px;"
            f"padding:2px 9px; font-size:12px; font-weight:700;}}"
            f"QPushButton:hover{{background:{self.theme.color('primary_soft')};"
            f"color:{self.theme.color('primary')};}}"
        )
        return b

    def _mini_icon_btn(self, icon_name: str, tooltip: str) -> QPushButton:
        """A compact flat icon-only button for row-level actions (edit, dismiss)."""
        b = QPushButton()
        b.setIcon(self.theme.icon(icon_name, "text_muted", 13))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolTip(tooltip)
        b.setFixedSize(26, 22)
        b.setStyleSheet(
            "QPushButton{background:transparent; border:none; border-radius:8px; padding:0;}"
            f"QPushButton:hover{{background:{self.theme.color('surface_hover')};}}"
        )
        return b

    def _action_label(self, a: dict, *, muted: bool = False) -> QLabel:
        lbl = QLabel()
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        task = _md_to_html(a.get("task") or "")  # escapes, then **bold** → <b>
        if a.get("done"):
            task = f'<span style="color:{self.theme.color("text_faint")}; text-decoration:line-through;">{task}</span>'
        elif muted:
            task = f'<span style="color:{self.theme.color("text_muted")};">{task}</span>'
        owner = a.get("owner")
        suffix = (
            f' &middot; <b style="color:{self.theme.color("primary")};">{_html.escape(owner)}</b>'
            if owner else ""
        )
        lbl.setText(task + suffix)
        return lbl

    def _due_chip_btn(self, idx: int, a: dict) -> QPushButton:
        """A small flat pill showing the due label, coloured by severity.
        Clicking it reopens the date picker on that same item."""
        from ..util.dues import due_label, due_severity

        severity_colors = {
            "overdue": ("danger", "danger_soft"),
            "today": ("warning", "warning_soft"),
            "future": ("text_muted", "surface_hover"),
        }
        sev = due_severity(a.get("due"))
        fg_role, bg_role = severity_colors.get(sev, ("text_muted", "surface_hover"))
        fg, bg = self.theme.color(fg_role), self.theme.color(bg_role)
        btn = QPushButton(due_label(a.get("due")))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip("Change due date")
        btn.setStyleSheet(
            f"QPushButton{{background:{bg}; color:{fg}; border:none; border-radius:9px;"
            f"padding:3px 10px; font-size:12px; font-weight:600;}}"
            f"QPushButton:hover{{background:{self.theme.color('surface_press')};}}"
        )
        btn.clicked.connect(lambda _=False, i=idx: self._pick_row_due(i))
        return btn

    def _pick_row_due(self, idx: int) -> None:
        """Open the date picker for action item `idx` and persist the result."""
        if self.meeting_id is None:
            return
        m = self.repo.get(self.meeting_id)
        actions = (m.notes or {}).get("action_items") or []
        current = actions[idx].get("due") if 0 <= idx < len(actions) else None
        from .widgets import pick_due_date
        iso, accepted = pick_due_date(self, self.theme, current)
        if not accepted:
            return
        self._set_due(idx, iso)

    def _set_due(self, idx: int, iso_or_none: str | None) -> None:
        def fn(actions):
            if 0 <= idx < len(actions) and isinstance(actions[idx], dict):
                actions[idx]["due"] = iso_or_none
        self._mutate_actions(fn)

    def _action_row(self, idx: int, a: dict) -> QWidget:
        if not isinstance(a, dict):
            a = {"task": str(a)}
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 1, 0, 1)
        rl.setSpacing(9)
        # done items render as resolved regardless of confirmation state
        confirmed = bool(a.get("confirmed", True)) or bool(a.get("done"))

        if confirmed:
            cb = QCheckBox()
            cb.setChecked(bool(a.get("done")))
            cb.setCursor(Qt.CursorShape.PointingHandCursor)
            cb.setToolTip("Mark as completed")
            lbl = self._action_label(a)

            def on_toggle(checked: bool, i=idx, item=a, label=lbl) -> None:
                item["done"] = bool(checked)
                self._persist_action(i, checked)
                fresh = self._action_label(item)
                label.setText(fresh.text())
                fresh.deleteLater()

            cb.toggled.connect(on_toggle)
            rl.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)
            rl.addWidget(lbl, 1)
        else:
            from .widgets import make_chip
            chip = make_chip("suggested", fg=self.theme.color("warning"),
                             bg=self.theme.color("warning_soft"))
            rl.addWidget(chip, 0, Qt.AlignmentFlag.AlignTop)
            rl.addWidget(self._action_label(a, muted=True), 1)
            keep = self._mini_btn("✓ Keep", "Accept as a real action item — it joins your to-do list",
                                  accent=True)
            keep.clicked.connect(lambda _=False, i=idx: self._confirm_action(i))
            rl.addWidget(keep, 0, Qt.AlignmentFlag.AlignTop)

        if a.get("due"):
            rl.addWidget(self._due_chip_btn(idx, a), 0, Qt.AlignmentFlag.AlignTop)
        else:
            add_due = self._mini_icon_btn("calendar", "Set due date")
            add_due.clicked.connect(lambda _=False, i=idx: self._pick_row_due(i))
            rl.addWidget(add_due, 0, Qt.AlignmentFlag.AlignTop)

        edit = self._mini_icon_btn("pencil", "Edit this action item")
        edit.clicked.connect(lambda _=False, i=idx, item=dict(a), r=row: self._begin_action_edit(r, i, item))
        rl.addWidget(edit, 0, Qt.AlignmentFlag.AlignTop)
        if not confirmed:
            dismiss = self._mini_icon_btn("x", "Dismiss this suggestion")
            dismiss.clicked.connect(lambda _=False, i=idx: self._dismiss_action(i))
            rl.addWidget(dismiss, 0, Qt.AlignmentFlag.AlignTop)
        return row

    def _begin_action_edit(self, row: QWidget, idx: int, a: dict) -> None:
        """Swap the row content for inline task/owner/due editors."""
        from ..util.dues import due_label

        rl = row.layout()
        clear_layout(rl)
        task_edit = QLineEdit(a.get("task") or "")
        task_edit.setPlaceholderText("What needs doing")
        owner_edit = QLineEdit(a.get("owner") or "")
        owner_edit.setPlaceholderText("Owner (optional)")
        owner_edit.setFixedWidth(150)

        # the pending due value lives in a closure var, changed by the "Due…"
        # mini button, committed alongside task/owner on Save
        due_state = {"due": a.get("due")}
        due_btn = self._mini_btn(due_label(due_state["due"]) or "Due…", "Set due date")

        def pick_due() -> None:
            from .widgets import pick_due_date
            iso, accepted = pick_due_date(self, self.theme, due_state["due"])
            if not accepted:
                return
            due_state["due"] = iso
            due_btn.setText(due_label(iso) or "Due…")

        due_btn.clicked.connect(pick_due)

        save = self._mini_btn("Save", "Save changes", accent=True)
        cancel = self._mini_btn("Cancel", "Discard changes")

        def commit() -> None:
            self._apply_action_edit(idx, task_edit.text(), owner_edit.text(), due_state["due"])

        save.clicked.connect(commit)
        task_edit.returnPressed.connect(commit)
        owner_edit.returnPressed.connect(commit)
        cancel.clicked.connect(self.refresh)
        rl.addWidget(task_edit, 1)
        rl.addWidget(owner_edit)
        rl.addWidget(due_btn)
        rl.addWidget(save)
        rl.addWidget(cancel)
        task_edit.setFocus()

    def _bullet(self, text: str) -> QWidget:
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(2, 0, 0, 0)
        rl.setSpacing(8)
        dot = QLabel("•")
        dot.setObjectName("Muted")
        rl.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        lbl = QLabel(_md_to_html(text))
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        rl.addWidget(lbl, 1)
        return row

    def _persist_action(self, idx: int, checked: bool) -> None:
        if self.meeting_id is None:
            return
        m = self.repo.get(self.meeting_id)
        notes = m.notes or {}
        actions = notes.get("action_items") or []
        if 0 <= idx < len(actions):
            actions[idx]["done"] = bool(checked)
            notes["action_items"] = actions
            self.repo.update(self.meeting_id, notes_json=json.dumps(notes))

    def _mutate_actions(self, fn) -> None:
        """Load → mutate the action_items list → persist → re-render. Used by
        accept/dismiss/edit, all of which change what the dashboard shows."""
        if self.meeting_id is None:
            return
        m = self.repo.get(self.meeting_id)
        notes = m.notes or {}
        actions = notes.get("action_items") or []
        fn(actions)
        notes["action_items"] = actions
        self.repo.update(self.meeting_id, notes_json=json.dumps(notes))
        self.refresh()
        self.shell.notify_data_changed()

    def _confirm_action(self, idx: int) -> None:
        def fn(actions):
            if 0 <= idx < len(actions) and isinstance(actions[idx], dict):
                actions[idx]["confirmed"] = True
        self._mutate_actions(fn)

    def _dismiss_action(self, idx: int) -> None:
        def fn(actions):
            if 0 <= idx < len(actions):
                del actions[idx]
        self._mutate_actions(fn)

    def _apply_action_edit(self, idx: int, task: str, owner: str, due: str | None = None) -> None:
        task = (task or "").strip()
        if not task:  # an emptied task is a dismissal
            self._dismiss_action(idx)
            return

        def fn(actions):
            if 0 <= idx < len(actions) and isinstance(actions[idx], dict):
                actions[idx]["task"] = task
                actions[idx]["owner"] = (owner or "").strip() or None
                actions[idx]["due"] = due
        self._mutate_actions(fn)

    # ---------- bookmarks ----------
    def _render_bookmarks(self, m) -> None:
        clear_layout(self.bookmarks_row)
        bms = m.bookmarks or []
        self.bookmarks_host.setVisible(bool(bms))
        if not bms:
            return
        lbl = QLabel("Bookmarks:")
        lbl.setObjectName("Muted")
        self.bookmarks_row.addWidget(lbl)
        dur_ms = int((m.duration_secs or 0) * 1000) or 1
        for b in bms:
            ms = int(b.get("ms", 0))
            chip = QPushButton(self._fmt_ms(ms))
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setStyleSheet(
                f"QPushButton{{background:{self.theme.color('primary_soft')}; color:{self.theme.color('primary')};"
                f"border:none; border-radius:9px; padding:3px 10px; font-size:12px; font-weight:600;}}"
            )
            chip.clicked.connect(lambda _=False, f=min(1.0, ms / dur_ms): self._jump_fraction(f))
            self.bookmarks_row.addWidget(chip)
        self.bookmarks_row.addStretch(1)

    def _jump_fraction(self, frac: float) -> None:
        self.tabs.setCurrentIndex(1)  # Transcript
        sb = self.transcript_view.verticalScrollBar()
        sb.setValue(int(frac * sb.maximum()))

    # ---------- AI actions (prompt workbench) ----------
    def _populate_actions(self) -> None:
        self.action_combo.clear()
        for a in self.cfg.effective_ai_actions():
            self.action_combo.addItem(a.get("name") or "(unnamed)", a.get("prompt") or "")

    def _run_ai_action(self) -> None:
        if self.meeting_id is None:
            return
        prompt = self.action_combo.currentData()
        if not prompt:
            QMessageBox.warning(self, "No action", "Pick an AI action (manage them in Settings → AI).")
            return
        from ..notes import service as notes_service
        if not notes_service.ready(self.cfg):
            QMessageBox.warning(self, "Notes AI not configured", notes_service.missing_hint(self.cfg))
            return
        name = self.action_combo.currentText()
        repo, cfg, mid = self.repo, self.cfg, self.meeting_id
        self.status_label.setText(f"Running '{name}'…")
        self.run_action_btn.setEnabled(False)

        def job(_p):
            from ..notes import render
            from ..notes import service as notes_service
            m = repo.get(mid)
            notes_text = render.to_plaintext(m.notes) if m.notes else ""
            return notes_service.run_action(
                prompt, cfg, transcript=m.transcript or "", notes_text=notes_text,
                title=m.title or "",
            )

        self.worker = FuncWorker(job)
        self.worker.done.connect(lambda text, n=name: self._on_action_done(text, n))
        self.worker.failed.connect(self._on_action_failed)
        self.worker.start()

    def _on_action_done(self, text: str, name: str) -> None:
        self.status_label.setText("")
        self.run_action_btn.setEnabled(True)
        self._show_result(name, text)

    def _on_action_failed(self, msg: str) -> None:
        self.status_label.setText(f"Error: {msg}")
        self.run_action_btn.setEnabled(True)
        QMessageBox.critical(self, "AI action failed", msg)

    def _show_result(self, title: str, text: str) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumSize(560, 460)
        v = QVBoxLayout(dlg)
        browser = QTextBrowser()
        browser.setReadOnly(True)
        browser.setOpenExternalLinks(False)
        browser.document().setMarkdown(text)
        v.addWidget(browser)
        bar = QHBoxLayout()
        copy = QPushButton("Copy")
        copy.setProperty("variant", "primary")
        copy.setCursor(Qt.CursorShape.PointingHandCursor)

        def do_copy():
            from PySide6.QtCore import QMimeData
            from PySide6.QtWidgets import QApplication
            mime = QMimeData()
            mime.setHtml(browser.document().toHtml())
            mime.setText(browser.document().toPlainText())
            QApplication.clipboard().setMimeData(mime)
            copy.setText("Copied ✓")

        copy.clicked.connect(do_copy)
        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        bar.addWidget(copy)
        bar.addStretch(1)
        bar.addWidget(close)
        v.addLayout(bar)
        dlg.exec()

    # ---------- screenshots ----------
    def _render_screenshots(self, m) -> None:
        self._screen_meeting = m
        clear_layout(self.screen_grid)
        shots = screen_capture.list_screenshots(Path(m.audio_dir)) if m.audio_dir else []
        self.tabs.setTabVisible(self._screen_tab_index, bool(shots))
        if not shots:
            self._cur_cols = 0
            return
        cols = self._screen_cols()
        self._cur_cols = cols
        vw = self.screen_scroll.viewport().width() or 900
        thumb_w = max(180, (vw - 40 - 12 * (cols - 1)) // cols)  # fill the columns
        align = Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        for i, (ms, path) in enumerate(shots):
            self.screen_grid.addWidget(self._thumb(ms, path, thumb_w), i // cols, i % cols, align)
        rows = (len(shots) + cols - 1) // cols
        self.screen_grid.setRowStretch(rows, 1)       # pack to the top (no tall empty cells)
        self.screen_grid.setColumnStretch(cols, 1)    # pack to the left

    def _screen_cols(self) -> int:
        vw = self.screen_scroll.viewport().width() or self.width() or 900
        return 2 if vw < 900 else (3 if vw < 1500 else 4)

    def _thumb(self, ms: int, path: Path, width: int = 300) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        img = _ClickableImage(lambda p=path: os.startfile(str(p)))  # noqa: S606
        pm = QPixmap(str(path))
        if not pm.isNull():
            img.setPixmap(pm.scaledToWidth(int(width), Qt.TransformationMode.SmoothTransformation))
        img.setStyleSheet(f"border:1px solid {self.theme.color('border')}; border-radius:8px;")
        cap = QLabel(self._fmt_ms(ms))
        cap.setObjectName("Faint")
        cap.setAlignment(Qt.AlignmentFlag.AlignLeft)
        v.addWidget(img)
        v.addWidget(cap)
        return w

    @staticmethod
    def _fmt_ms(ms: int) -> str:
        secs = ms // 1000
        return f"{secs // 60:02d}:{secs % 60:02d}"

    def resizeEvent(self, event):
        super().resizeEvent(event)
        m = getattr(self, "_screen_meeting", None)
        if m is not None and getattr(self, "_cur_cols", 0) and self._screen_cols() != self._cur_cols:
            self._render_screenshots(m)

    def apply_theme(self) -> None:
        self.back_btn.setIcon(self.theme.icon("chevron-left", "text_muted", 18))
        self.copy_btn.setIcon(self.theme.icon("copy", "on_primary", 16))
        self.more_btn.setIcon(self.theme.icon("chevron-down", "text_muted", 14))
        self.delete_btn.setIcon(self.theme.icon("trash", "danger", 16))
        self.run_action_btn.setIcon(self.theme.icon("sparkles", "text_muted", 15))
        self.share_btn.setIcon(self.theme.icon("upload", "text_muted", 16))
        self.todoist_btn.setIcon(self.theme.icon("check-square", "text_muted", 16))
        self.act_resummarise.setIcon(self.theme.icon("sparkles", "text_muted", 16))
        self.act_reprocess.setIcon(self.theme.icon("refresh", "text_muted", 16))
        self.act_folder.setIcon(self.theme.icon("folder", "text_muted", 16))

    # ---------- actions ----------
    def _run(self, job, label: str) -> None:
        self.status_label.setText(f"{label}…")
        for a in (self.act_resummarise, self.act_reprocess):
            a.setEnabled(False)
        self.worker = FuncWorker(job)
        self.worker.progress.connect(self.status_label.setText)
        self.worker.done.connect(lambda _r: self._after())
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _after(self) -> None:
        self.status_label.setText("Done.")
        self.refresh()
        self.shell.notify_data_changed()

    def _on_failed(self, msg: str) -> None:
        self.status_label.setText(f"Error: {msg}")
        self.refresh()
        QMessageBox.critical(self, "Failed", msg)

    def _resummarise(self) -> None:
        if self.meeting_id is None:
            return
        from ..notes import service as notes_service
        if not notes_service.ready(self.cfg):
            QMessageBox.warning(self, "Notes AI not configured", notes_service.missing_hint(self.cfg))
            return
        repo, cfg, mid = self.repo, self.cfg, self.meeting_id

        def job(progress):
            from ..notes import service as notes_service
            progress("Writing notes")
            m = repo.get(mid)
            notes = notes_service.generate_notes(
                m.transcript or "", cfg,
                attendees=m.attendees, agenda=m.agenda, human_date=m.date_text,
                extra_instructions=cfg.notes_instructions(m.template),
            )
            repo.update(mid, title=notes.title, notes_json=notes.model_dump_json(),
                        attendees=notes.attendees or m.attendees, status="Done")
            return mid

        self._run(job, "Re-summarising")

    def _reprocess(self) -> None:
        if self.meeting_id is None:
            return
        repo, cfg, mid = self.repo, self.cfg, self.meeting_id

        def job(progress):
            from ..pipeline.processing import process_recording
            process_recording(repo, mid, cfg, progress=progress)
            return mid

        self._run(job, "Re-processing")

    def _delete(self) -> None:
        if self.meeting_id is None:
            return
        if QMessageBox.question(self, "Delete meeting", "Delete this meeting and its record?") \
                == QMessageBox.StandardButton.Yes:
            m = self.repo.get(self.meeting_id)
            if m.audio_dir and os.path.isdir(m.audio_dir) and self._is_recording_folder(m.audio_dir):
                import shutil
                shutil.rmtree(m.audio_dir, ignore_errors=True)  # audio + screenshots
            self.repo.delete(self.meeting_id)
            self.meeting_id = None  # avoid a stale-id KeyError on later refresh()
            self.shell.notify_data_changed()
            self.shell.show_home()

    @staticmethod
    def _is_recording_folder(path: str) -> bool:
        """Guard rmtree/startfile: only ever touch a per-meeting folder that
        actually lives under the recordings root (defends against a tampered or
        mis-set audio_dir turning Delete into arbitrary recursive deletion)."""
        from ..paths import recordings_dir
        try:
            p = Path(path).resolve()
            root = recordings_dir().resolve()
            return root in p.parents and p.name.startswith("meeting_")
        except OSError:
            return False

    def _open_folder(self) -> None:
        if self.meeting_id is None:
            return
        m = self.repo.get(self.meeting_id)
        if m.audio_dir and os.path.isdir(m.audio_dir):
            os.startfile(m.audio_dir)  # noqa: S606

    def _send_todoist(self) -> None:
        """Create a Todoist task for every open action item (deduped by the
        stored task id, so re-sending never duplicates)."""
        if self.meeting_id is None:
            return
        if not (self.cfg.todoist_token or "").strip():
            QMessageBox.information(
                self, "Todoist not connected",
                "Add your Todoist API token in Integrations → Todoist first.\n"
                "(Todoist → Settings → Integrations → Developer → API token.)")
            return
        repo, cfg, mid = self.repo, self.cfg, self.meeting_id
        self.todoist_btn.setEnabled(False)
        self.status_label.setText("Sending open action items to Todoist…")

        def job(_p):
            from ..integrations import todoist
            m = repo.get(mid)
            notes = m.notes or {}
            sent, skipped = todoist.send_open_items(
                cfg.todoist_token, notes, meeting_title=m.title or "Untitled",
                date_text=m.date_text)
            if sent:  # persist the todoist ids so a second click can't duplicate
                repo.update(mid, notes_json=json.dumps(notes))
            return sent, skipped

        def done(result):
            sent, skipped = result
            msg = f"Sent {sent} task(s) to Todoist."
            if skipped:
                msg += f" {skipped} already sent earlier."
            self.status_label.setText(msg)
            self.todoist_btn.setEnabled(True)

        def failed(msg):
            self.status_label.setText(f"Todoist: {msg}")
            self.todoist_btn.setEnabled(True)

        self._todoist_worker = FuncWorker(job)
        self._todoist_worker.done.connect(done)
        self._todoist_worker.failed.connect(failed)
        self._todoist_worker.start()

    def _share_html(self) -> None:
        """Export the meeting as a single self-contained HTML file to send around
        — the local-first version of a share link."""
        if self.meeting_id is None:
            return
        m = self.repo.get(self.meeting_id)
        box = QMessageBox(self)
        box.setWindowTitle("Share meeting")
        box.setText("Export this meeting as a standalone HTML file you can send to anyone.")
        notes_only = box.addButton("Notes only", QMessageBox.ButtonRole.AcceptRole)
        with_tr = box.addButton("Notes + transcript", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        if not (m.transcript or "").strip():
            with_tr.setEnabled(False)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (notes_only, with_tr):
            return
        from PySide6.QtWidgets import QFileDialog

        from ..notes import share
        safe = "".join(c for c in (m.title or "meeting") if c.isalnum() or c in " -_")[:60].strip() or "meeting"
        path, _sel = QFileDialog.getSaveFileName(
            self, "Save shareable file", f"{safe}.html", "Web page (*.html)")
        if not path:
            return
        html_doc = share.to_share_html(m, include_transcript=(clicked is with_tr))
        try:
            Path(path).write_text(html_doc, encoding="utf-8")
        except OSError as e:
            QMessageBox.critical(self, "Could not save", str(e))
            return
        self.status_label.setText(f"Saved {Path(path).name} — opening preview…")
        os.startfile(path)  # noqa: S606

    # ---------- the Copy menu (all / action items / summary) ----------
    def _meeting_with_notes(self):
        if self.meeting_id is None:
            return None
        m = self.repo.get(self.meeting_id)
        return m if m.notes else None

    def _set_clipboard(self, *, text: str, html: str | None = None, message: str = "") -> None:
        from PySide6.QtCore import QMimeData
        from PySide6.QtWidgets import QApplication

        mime = QMimeData()
        mime.setText(text)
        if html is not None:
            mime.setHtml(html)
        QApplication.clipboard().setMimeData(mime)
        if message:
            self.status_label.setText(message)

    def _copy_all(self) -> None:
        """Everything as it looks in Earshot: agenda, summary, action items and
        sections — rich HTML + clean plain text (no markdown symbols)."""
        m = self._meeting_with_notes()
        if m is None:
            return
        from ..notes import render

        agenda = (m.agenda or "").strip()
        self._set_clipboard(
            html=render.to_html(m.notes, date_text=m.date_text, attendees=m.attendees, agenda=agenda),
            text=render.to_plaintext(m.notes, date_text=m.date_text, attendees=m.attendees, agenda=agenda),
            message="Notes copied. Paste into Notion, email, anywhere.",
        )

    def _copy_actions_todo(self) -> None:
        """Action items as plain-text markdown to-dos. Plain text ONLY: with an
        HTML flavour present, Notion would use it instead of converting the
        '- [ ]' markdown into real checkbox blocks."""
        m = self._meeting_with_notes()
        if m is None:
            return
        from ..notes import render

        md = render.todo_markdown(m.notes)
        if not md:
            self.status_label.setText("No action items to copy.")
            return
        self._set_clipboard(text=md,
                            message="Action items copied. Paste into Notion for a to-do list.")

    def _copy_summary_only(self) -> None:
        """Agenda + summary + topic sections, without the action items."""
        m = self._meeting_with_notes()
        if m is None:
            return
        from ..notes import render

        agenda = (m.agenda or "").strip()
        self._set_clipboard(
            html=render.to_html(m.notes, date_text=m.date_text, attendees=m.attendees,
                                agenda=agenda, include_actions=False),
            text=render.to_plaintext(m.notes, date_text=m.date_text, attendees=m.attendees,
                                     agenda=agenda, include_actions=False),
            message="Summary copied. Paste into Notion, email, anywhere.",
        )
