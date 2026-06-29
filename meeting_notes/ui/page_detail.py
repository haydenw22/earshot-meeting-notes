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
    QMessageBox,
    QPlainTextEdit,
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
from .widgets import Card, make_chip, status_chip
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
        self.copy_btn = self._action("Copy summary", self._copy_summary)
        self.copy_btn.setProperty("variant", "primary")
        self.resummarise_btn = self._action("Re-summarise", self._resummarise)
        self.reprocess_btn = self._action("Re-transcribe", self._reprocess)
        self.folder_btn = self._action("Open audio folder", self._open_folder)
        self.delete_btn = self._action("Delete", self._delete)
        btns.addWidget(self.copy_btn)
        btns.addWidget(self.resummarise_btn)
        btns.addWidget(self.reprocess_btn)
        btns.addWidget(self.folder_btn)
        btns.addStretch(1)
        btns.addWidget(self.delete_btn)
        root.addLayout(btns)

    def _action(self, text: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(slot)
        return b

    # ---------- data ----------
    def load(self, meeting_id: int) -> None:
        self.meeting_id = meeting_id
        self.refresh()

    def refresh(self) -> None:
        if self.meeting_id is None:
            return
        m = self.repo.get(self.meeting_id)
        self.title.setText(m.title or "Untitled meeting")

        # rebuild meta chips
        while self.meta_row.count() > 1:
            item = self.meta_row.takeAt(0)
            w = item.widget()
            if w:
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
        self.resummarise_btn.setEnabled(bool(m.transcript))
        self.reprocess_btn.setEnabled(bool(m.audio_dir))
        self.folder_btn.setEnabled(bool(m.audio_dir))
        self.run_action_btn.setEnabled(bool(m.transcript or m.notes))
        self.apply_theme()

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
        while self.att_panel_lay.count():
            item = self.att_panel_lay.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
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
        while self.notes_lay.count():
            item = self.notes_lay.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

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
            s = QLabel(notes["summary"])
            s.setWordWrap(True)
            self.notes_lay.addWidget(s)

        tt = _stats.talk_time(m.transcript or "")
        if tt["has_speakers"]:
            stat = QLabel(f"Talk-time (approx) — you {tt['me_pct']}%  ·  them {tt['them_pct']}%   ·   {tt['total_words']} words")
            stat.setObjectName("Faint")
            self.notes_lay.addWidget(stat)

        actions = notes.get("action_items") or []
        if actions:
            self.notes_lay.addWidget(self._notes_heading("Action items"))
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

    def _action_row(self, idx: int, a: dict) -> QWidget:
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 1, 0, 1)
        rl.setSpacing(9)
        cb = QCheckBox()
        cb.setChecked(bool(a.get("done")))
        cb.setCursor(Qt.CursorShape.PointingHandCursor)
        lbl = QLabel()
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        owner = a.get("owner")

        def render(done: bool) -> None:
            task = _html.escape(a.get("task") or "")
            if done:
                task = f'<span style="color:{self.theme.color("text_faint")}; text-decoration:line-through;">{task}</span>'
            suffix = (
                f' &middot; <b style="color:{self.theme.color("primary")};">{_html.escape(owner)}</b>'
                if owner else ""
            )
            lbl.setText(task + suffix)

        render(bool(a.get("done")))

        def on_toggle(checked: bool) -> None:
            render(checked)
            self._persist_action(idx, checked)

        cb.toggled.connect(on_toggle)
        rl.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)
        rl.addWidget(lbl, 1)
        return row

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

    # ---------- bookmarks ----------
    def _render_bookmarks(self, m) -> None:
        while self.bookmarks_row.count():
            item = self.bookmarks_row.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
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
        if not self.cfg.resolved_anthropic_key():
            QMessageBox.warning(self, "No API key", "Add an Anthropic API key in Settings → AI first.")
            return
        name = self.action_combo.currentText()
        repo, cfg, mid = self.repo, self.cfg, self.meeting_id
        self.status_label.setText(f"Running '{name}'…")
        self.run_action_btn.setEnabled(False)

        def job(_p):
            from ..notes import actions, render
            m = repo.get(mid)
            notes_text = render.to_plaintext(m.notes) if m.notes else ""
            return actions.run_action(
                prompt, transcript=m.transcript or "", notes_text=notes_text,
                title=m.title or "", api_key=cfg.resolved_anthropic_key(), model=cfg.anthropic_model,
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
        edit = QPlainTextEdit(text)
        edit.setReadOnly(True)
        v.addWidget(edit)
        bar = QHBoxLayout()
        copy = QPushButton("Copy")
        copy.setProperty("variant", "primary")
        copy.setCursor(Qt.CursorShape.PointingHandCursor)

        def do_copy():
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
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
        while self.screen_grid.count():
            item = self.screen_grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
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
        self.resummarise_btn.setIcon(self.theme.icon("sparkles", "text_muted", 16))
        self.reprocess_btn.setIcon(self.theme.icon("refresh", "text_muted", 16))
        self.folder_btn.setIcon(self.theme.icon("folder", "text_muted", 16))
        self.delete_btn.setIcon(self.theme.icon("trash", "danger", 16))
        self.run_action_btn.setIcon(self.theme.icon("sparkles", "text_muted", 15))

    # ---------- actions ----------
    def _run(self, job, label: str) -> None:
        self.status_label.setText(f"{label}…")
        for b in (self.resummarise_btn, self.reprocess_btn):
            b.setEnabled(False)
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
        if not self.cfg.resolved_anthropic_key():
            QMessageBox.warning(self, "No API key", "Add an Anthropic API key in Settings first.")
            return
        repo, cfg, mid = self.repo, self.cfg, self.meeting_id

        def job(progress):
            from ..notes import anthropic_client
            progress("Writing notes")
            m = repo.get(mid)
            notes = anthropic_client.generate_notes(
                m.transcript or "", api_key=cfg.resolved_anthropic_key(),
                attendees=m.attendees, agenda=m.agenda, human_date=m.date_text, model=cfg.anthropic_model,
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
            if m.audio_dir and os.path.isdir(m.audio_dir):
                import shutil
                shutil.rmtree(m.audio_dir, ignore_errors=True)  # audio + screenshots
            self.repo.delete(self.meeting_id)
            self.shell.notify_data_changed()
            self.shell.show_home()

    def _open_folder(self) -> None:
        if self.meeting_id is None:
            return
        m = self.repo.get(self.meeting_id)
        if m.audio_dir and os.path.isdir(m.audio_dir):
            os.startfile(m.audio_dir)  # noqa: S606

    def _copy_summary(self) -> None:
        """Put the notes on the clipboard as rich HTML + clean plain text so they
        paste cleanly into Notion / email / anywhere (no markdown symbols)."""
        if self.meeting_id is None:
            return
        m = self.repo.get(self.meeting_id)
        if not m.notes:
            return
        from PySide6.QtCore import QMimeData
        from PySide6.QtWidgets import QApplication

        from ..notes import render

        mime = QMimeData()
        mime.setHtml(render.to_html(m.notes, date_text=m.date_text, attendees=m.attendees))
        mime.setText(render.to_plaintext(m.notes, date_text=m.date_text, attendees=m.attendees))
        QApplication.clipboard().setMimeData(mime)
        self.status_label.setText("Summary copied — paste into Notion, email, anywhere.")
