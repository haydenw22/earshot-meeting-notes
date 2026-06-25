"""Meeting detail page: title, meta chips, the structured notes and transcript
in tabs, plus re-summarise / re-transcribe / open-folder / delete actions.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..storage.repository import Meeting
from .widgets import Card, status_chip
from .workers import FuncWorker


def _notes_html(m: Meeting, theme) -> str:
    notes = m.notes
    text = theme.color("text")
    muted = theme.color("text_muted")
    primary = theme.color("primary")
    if not notes:
        msg = ("Transcribed — add an Anthropic API key in Settings and click Re-summarise."
               if m.transcript else "No notes yet.")
        return f'<div style="color:{muted}; font-size:14px;">{msg}</div>'
    parts = [f'<div style="color:{text}; font-size:14px; line-height:1.6;">']
    if notes.get("summary"):
        parts.append(f'<p style="color:{text};">{notes["summary"]}</p>')
    if notes.get("decisions"):
        items = "".join(f"<li>{d}</li>" for d in notes["decisions"])
        parts.append(f'<h3 style="color:{text}; margin-top:18px;">Decisions</h3><ul>{items}</ul>')
    if notes.get("action_items"):
        rows = []
        for a in notes["action_items"]:
            tail = []
            if a.get("owner"):
                tail.append(f'<b style="color:{primary};">{a["owner"]}</b>')
            if a.get("due"):
                tail.append(f'due {a["due"]}')
            suffix = f' &middot; {", ".join(tail)}' if tail else ""
            rows.append(f"<li>{a.get('task','')}{suffix}</li>")
        parts.append(f'<h3 style="color:{text}; margin-top:18px;">Action items</h3><ul>{"".join(rows)}</ul>')
    if notes.get("topics"):
        chips = " ".join(f'<span style="color:{muted};">#{t}</span>' for t in notes["topics"])
        parts.append(f'<h3 style="color:{text}; margin-top:18px;">Topics</h3><p>{chips}</p>')
    parts.append("</div>")
    return "".join(parts)


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
        self.notes_view = QTextBrowser()
        self.transcript_view = QTextBrowser()
        self.tabs.addTab(self.notes_view, "Notes")
        self.tabs.addTab(self.transcript_view, "Transcript")
        cl.addWidget(self.tabs)
        root.addWidget(card, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        root.addWidget(self.status_label)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.resummarise_btn = self._action("Re-summarise", self._resummarise)
        self.reprocess_btn = self._action("Re-transcribe", self._reprocess)
        self.folder_btn = self._action("Open audio folder", self._open_folder)
        self.delete_btn = self._action("Delete", self._delete)
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

        self.notes_view.setHtml(_notes_html(m, self.theme))
        self.transcript_view.setPlainText(m.transcript or "No transcript yet.")
        self.resummarise_btn.setEnabled(bool(m.transcript))
        self.reprocess_btn.setEnabled(bool(m.audio_dir))
        self.folder_btn.setEnabled(bool(m.audio_dir))
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

    def apply_theme(self) -> None:
        self.back_btn.setIcon(self.theme.icon("chevron-left", "text_muted", 18))
        self.resummarise_btn.setIcon(self.theme.icon("sparkles", "text_muted", 16))
        self.reprocess_btn.setIcon(self.theme.icon("refresh", "text_muted", 16))
        self.folder_btn.setIcon(self.theme.icon("folder", "text_muted", 16))
        self.delete_btn.setIcon(self.theme.icon("trash", "danger", 16))

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
                attendees=m.attendees, human_date=m.date_text, model=cfg.anthropic_model,
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
            self.repo.delete(self.meeting_id)
            self.shell.notify_data_changed()
            self.shell.show_home()

    def _open_folder(self) -> None:
        if self.meeting_id is None:
            return
        m = self.repo.get(self.meeting_id)
        if m.audio_dir and os.path.isdir(m.audio_dir):
            os.startfile(m.audio_dir)  # noqa: S606
