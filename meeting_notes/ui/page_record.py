"""Recording page: auto-filled date, live-editable attendees, device pickers,
level meters and the record/stop control. Hands off to the background pipeline
on stop, then opens the finished meeting.
"""
from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import shutil
import sys
import time

from ..audio import devices as dev
from ..audio.capture import DualStreamRecorder, ensure_capture_permissions
from ..capture.screen import ScreenRecorder, screen_capture_authorized
from ..paths import meeting_dir
from ..util.dates import today_pair
from . import icons
from .overlay import RecordingOverlay
from .widgets import Card, calm_scroll_children
from .workers import FuncWorker

# A channel whose RMS level never crosses this is treated as silent (a muted mic
# or mis-routed system audio reads ~0; even quiet speech/room tone reads higher).
_INPUT_ACTIVE_LEVEL = 0.04
# Don't warn until the recording has had this long to actually produce sound.
_INPUT_GRACE_SECS = 10.0

# Advanced prep-brief picker limits.
_BRIEF_PICKER_RECENT_LIMIT = 25   # how many recent Done meetings the picker offers
_BRIEF_MAX_MEETINGS = 10          # cap on how many meetings feed one brief


def _parse_attendees(text: str) -> list[str]:
    return [a.strip() for a in text.replace(";", ",").split(",") if a.strip()]


class BriefPickerDialog(QDialog):
    """Advanced-mode prep-brief picker: either all Done meetings in a folder, or
    an explicit hand-picked set (capped at _BRIEF_MAX_MEETINGS)."""

    def __init__(self, parent, repo, theme):
        super().__init__(parent)
        self.repo = repo
        self.theme = theme
        self._checked_order: list[int] = []  # oldest-checked-first, for the eviction rule
        self.setWindowTitle("Prep brief — choose past meetings")
        self.setMinimumWidth(420)
        self.setMinimumHeight(420)

        v = QVBoxLayout(self)
        v.setSpacing(12)

        self.folder_radio = QRadioButton("From a folder")
        self.folder_radio.setChecked(True)
        v.addWidget(self.folder_radio)
        self.folder_combo = QComboBox()
        folders = self.repo.list_folders()
        for f in folders:
            self.folder_combo.addItem(f.name, f.id)
        self.folder_combo.setEnabled(bool(folders))
        if not folders:
            self.folder_radio.setEnabled(False)
        v.addWidget(self.folder_combo)

        self.meetings_radio = QRadioButton("Pick meetings")
        v.addWidget(self.meetings_radio)
        self.meeting_list = QListWidget()
        self.meeting_list.setMinimumHeight(220)
        done = [m for m in self.repo.list(limit=500) if m.status == "Done"]
        done = done[:_BRIEF_PICKER_RECENT_LIMIT]  # repo.list() is already newest-first
        for m in done:
            item = QListWidgetItem(f"{m.title or 'Untitled meeting'}  ·  {m.date_text}")
            item.setData(Qt.ItemDataRole.UserRole, m.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.meeting_list.addItem(item)
        self.meeting_list.itemChanged.connect(self._on_item_changed)
        v.addWidget(self.meeting_list)

        self.limit_hint = QLabel(f"You can pick up to {_BRIEF_MAX_MEETINGS} meetings.")
        self.limit_hint.setObjectName("Faint")
        self.limit_hint.setWordWrap(True)
        v.addWidget(self.limit_hint)

        if folders:
            self.folder_radio.toggled.connect(self._sync_enabled)
        self._sync_enabled()

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        v.addWidget(self.buttons)

    def _sync_enabled(self, *_a) -> None:
        use_folder = self.folder_radio.isChecked()
        self.folder_combo.setEnabled(use_folder and self.folder_combo.count() > 0)
        self.meeting_list.setEnabled(not use_folder)

    def _on_item_changed(self, changed: QListWidgetItem) -> None:
        mid = changed.data(Qt.ItemDataRole.UserRole)
        if changed.checkState() == Qt.CheckState.Checked:
            if mid not in self._checked_order:
                self._checked_order.append(mid)
            if len(self._checked_order) > _BRIEF_MAX_MEETINGS:
                # evict the oldest-checked item rather than blocking the click —
                # the hint label below explains the cap either way
                oldest = self._checked_order.pop(0)
                self._set_check_silently(oldest, Qt.CheckState.Unchecked)
        else:
            if mid in self._checked_order:
                self._checked_order.remove(mid)
        checked_n = len(self._checked_order)
        if checked_n >= _BRIEF_MAX_MEETINGS:
            self.limit_hint.setText(f"Maximum {_BRIEF_MAX_MEETINGS} meetings reached.")
        else:
            self.limit_hint.setText(f"You can pick up to {_BRIEF_MAX_MEETINGS} meetings ({checked_n} selected).")

    def _set_check_silently(self, meeting_id: int, state) -> None:
        self.meeting_list.blockSignals(True)
        for i in range(self.meeting_list.count()):
            item = self.meeting_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == meeting_id:
                item.setCheckState(state)
                break
        self.meeting_list.blockSignals(False)

    def payload(self):
        """('folder', folder_id) or ('meetings', [id, ...]) — feed to
        RecordPage.resolve_brief_meetings() together with repo.list()."""
        if self.folder_radio.isChecked():
            return ("folder", self.folder_combo.currentData())
        return ("meetings", list(self._checked_order))


class RecordPage(QWidget):
    def __init__(self, shell, repo, cfg, theme):
        super().__init__()
        self.shell = shell
        self.repo = repo
        self.cfg = cfg
        self.theme = theme
        self.recorder: DualStreamRecorder | None = None
        self.meeting_id: int | None = None
        self.worker: FuncWorker | None = None
        self._screen_rec: ScreenRecorder | None = None
        self._record_t0 = 0.0
        self._bookmarks: list[dict] = []
        self._mic_seen = False   # has the mic channel ever produced real audio?
        self._them_seen = False  # has the system-audio channel?
        self.overlay: RecordingOverlay | None = None
        self._human_date, self._iso_date = today_pair()
        self._build()
        # scrolling the page must never change a combo it passes over
        calm_scroll_children(self)

    # ---------- layout ----------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        host = QWidget()
        outer.addWidget(scroll)
        scroll.setWidget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(40, 32, 40, 32)
        root.setSpacing(18)

        self.heading = QLabel("New recording")
        self.heading.setObjectName("H1")
        sub = QLabel("Capture your mic and the other side on separate channels.")
        sub.setObjectName("Muted")
        root.addWidget(self.heading)
        root.addWidget(sub)

        # session card: date + attendees
        sess = Card()
        sl = QVBoxLayout(sess)
        sl.setContentsMargins(22, 20, 22, 20)
        sl.setSpacing(14)
        self.date_label = QLabel(self._human_date)
        self.date_label.setObjectName("H2")
        date_row = QHBoxLayout()
        self.date_icon = QLabel()
        date_row.addWidget(self.date_icon)
        date_row.addWidget(self.date_label)
        date_row.addStretch(1)
        sl.addLayout(date_row)
        att_lbl = QLabel("Attendees")
        att_lbl.setObjectName("H3")
        sl.addWidget(att_lbl)
        self.attendees = QLineEdit()
        self.attendees.setPlaceholderText("Add names, comma-separated — you can keep editing during the call")
        self.attendees.editingFinished.connect(self._persist_attendees)
        sl.addWidget(self.attendees)
        # notes template (hidden unless the user has created templates)
        self.template_box = QWidget()
        tbox = QVBoxLayout(self.template_box)
        tbox.setContentsMargins(0, 0, 0, 0)
        tbox.setSpacing(6)
        tpl_lbl = QLabel("Notes template")
        tpl_lbl.setObjectName("H3")
        self.template_combo = QComboBox()
        tbox.addWidget(tpl_lbl)
        tbox.addWidget(self.template_combo)
        sl.addWidget(self.template_box)
        # folder — always visible (default remains no project)
        folder_lbl = QLabel("Project")
        folder_lbl.setObjectName("H3")
        sl.addWidget(folder_lbl)
        self.folder_combo = QComboBox()
        self.folder_combo.currentIndexChanged.connect(self._on_folder_combo_changed)
        sl.addWidget(self.folder_combo)
        root.addWidget(sess)

        # agenda card — pre-meeting notes that stay on screen while recording and
        # are fed to the AI as context.
        ag = Card()
        agl = QVBoxLayout(ag)
        agl.setContentsMargins(22, 18, 22, 20)
        agl.setSpacing(10)
        ag_head = QHBoxLayout()
        ag_lbl = QLabel("Agenda / notes")
        ag_lbl.setObjectName("H3")
        ag_head.addWidget(ag_lbl)
        ag_head.addStretch(1)
        self.brief_btn = QPushButton("  Prep brief from past meetings")
        self.brief_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.brief_btn.setToolTip(
            "AI writes a short brief from related past meetings — what was decided last time "
            "and what's still open — using the attendees/template above to find them."
        )
        self.brief_btn.clicked.connect(self._generate_brief)
        ag_head.addWidget(self.brief_btn)
        agl.addLayout(ag_head)
        self.agenda = QPlainTextEdit()
        self.agenda.setPlaceholderText(
            "Optional — talking points or an agenda to keep on screen during the call. "
            "Also gives the AI summary more context."
        )
        self.agenda.setMinimumHeight(120)
        agl.addWidget(self.agenda)
        root.addWidget(ag)

        # audio sources card
        src = Card()
        srl = QVBoxLayout(src)
        srl.setContentsMargins(22, 20, 22, 20)
        srl.setSpacing(12)
        srl.addWidget(self._field_label("Your microphone"))
        self.mic_combo = QComboBox()
        srl.addWidget(self.mic_combo)
        srl.addWidget(self._field_label("Their audio (system output to capture)"))
        self.them_combo = QComboBox()
        srl.addWidget(self.them_combo)
        self.headphones = QCheckBox("I'm on headphones (skip echo cancellation)")
        self.headphones.setChecked(self.cfg.headphones_mode)
        srl.addSpacing(4)
        srl.addWidget(self.headphones)
        self.capture_screen = QCheckBox("Capture my screen during the meeting (screenshots for context)")
        self.capture_screen.setChecked(self.cfg.capture_screen)
        self.capture_screen.toggled.connect(self._toggle_screen_capture)
        srl.addWidget(self.capture_screen)
        root.addWidget(src)

        # live meters card
        self.live = Card()
        ll = QVBoxLayout(self.live)
        ll.setContentsMargins(22, 18, 22, 18)
        ll.setSpacing(10)
        self.mic_meter = self._meter("you")
        self.them_meter = self._meter("them")
        ll.addLayout(self._meter_row("You", self.mic_meter))
        ll.addLayout(self._meter_row("Them", self.them_meter))
        # amber warning shown when a channel stays completely silent
        self.input_warning = QFrame()
        self.input_warning.setObjectName("WarnBanner")
        iw = QHBoxLayout(self.input_warning)
        iw.setContentsMargins(12, 9, 12, 9)
        iw.setSpacing(9)
        self.input_warning_icon = QLabel()
        self.input_warning_text = QLabel("")
        self.input_warning_text.setObjectName("WarnText")
        self.input_warning_text.setWordWrap(True)
        iw.addWidget(self.input_warning_icon, 0, Qt.AlignmentFlag.AlignTop)
        iw.addWidget(self.input_warning_text, 1)
        self.input_warning.setVisible(False)
        ll.addWidget(self.input_warning)
        self.timer_label = QLabel("00:00")
        self.timer_label.setObjectName("H2")
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ll.addWidget(self.timer_label)
        self._bookmark_sequence = QKeySequence(QKeySequence.StandardKey.Bold)
        native_bookmark = self._bookmark_sequence.toString(QKeySequence.SequenceFormat.NativeText)
        self.bookmark_btn = QPushButton(f"  Bookmark this moment  ({native_bookmark})")
        self.bookmark_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bookmark_btn.clicked.connect(self._add_bookmark)
        ll.addWidget(self.bookmark_btn)
        self.bookmark_count = QLabel("")
        self.bookmark_count.setObjectName("Faint")
        self.bookmark_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ll.addWidget(self.bookmark_count)
        self.live.setVisible(False)
        root.addWidget(self.live)

        # record control
        self.record_btn = QPushButton("Start recording")
        self.record_btn.setProperty("variant", "danger")
        self.record_btn.setMinimumHeight(52)
        self.record_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.record_btn.clicked.connect(self._toggle)
        root.addWidget(self.record_btn)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        root.addStretch(1)

        self.poll = QTimer(self)
        self.poll.setInterval(100)
        self.poll.timeout.connect(self._on_poll)
        self._bm_shortcut = QShortcut(self._bookmark_sequence, self)
        self._bm_shortcut.activated.connect(self._add_bookmark)
        self.apply_theme()

    # ---------- recording overlay ----------
    def _show_overlay(self) -> None:
        if not self.cfg.overlay_enabled:
            return
        if self.overlay is None:
            self.overlay = RecordingOverlay(self.cfg)
        self.overlay.set_opacity(self.cfg.overlay_opacity)
        self.overlay.update_time(0)
        self.overlay.update_levels(0.0, 0.0)
        self.overlay.show_overlay()

    def _hide_overlay(self) -> None:
        if self.overlay is not None:
            self.overlay.close_overlay()
            self.overlay = None

    # ---------- pre-meeting brief (meeting-series memory) ----------
    def _related_past_meetings(self, limit: int = 3) -> list:
        """Latest completed meetings sharing an attendee or the selected template;
        falls back to the most recent completed meetings."""
        wanted = {a.lower() for a in _parse_attendees(self.attendees.text())}
        tpl = (self.template_combo.currentData() or "") if self.template_box.isVisible() else ""
        done = [m for m in self.repo.list() if m.status == "Done" and m.notes]
        related = [
            m for m in done
            if (wanted and wanted & {a.lower() for a in m.attendees})
            or (tpl and m.template == tpl)
        ]
        return (related or done)[:limit]

    @staticmethod
    def resolve_brief_meetings(meetings: list, mode_payload) -> list:
        """Pure helper: turn a BriefPickerDialog payload into the list of past
        meetings to brief from. `meetings` is any iterable of Meeting (e.g.
        repo.list()) — this does not touch the repo or the DB.

        mode_payload:
          ("folder", folder_id) -> all Done meetings in that folder, newest
              first (as `meetings` is already ordered), capped at
              _BRIEF_MAX_MEETINGS.
          ("meetings", [id, ...]) -> exactly those meetings (Done or not —
              the picker only ever offers Done ones), in the given order,
              capped at _BRIEF_MAX_MEETINGS.
        """
        if not mode_payload:
            return []
        kind, value = mode_payload
        if kind == "folder":
            if value is None:
                return []
            done_in_folder = [m for m in meetings if m.status == "Done" and m.folder_id == value]
            return done_in_folder[:_BRIEF_MAX_MEETINGS]
        if kind == "meetings":
            by_id = {m.id: m for m in meetings}
            picked = [by_id[i] for i in (value or []) if i in by_id]
            return picked[:_BRIEF_MAX_MEETINGS]
        return []

    def _generate_brief(self) -> None:
        from ..notes import service as notes_service
        if not notes_service.ready(self.cfg):
            QMessageBox.warning(self, "Notes AI not configured", notes_service.missing_hint(self.cfg))
            return
        if self.cfg.brief_mode == "advanced":
            dlg = BriefPickerDialog(self, self.repo, self.theme)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            past = self.resolve_brief_meetings(self.repo.list(limit=500), dlg.payload())
        else:
            past = self._related_past_meetings()
        if not past:
            self.status_label.setText("No completed meetings yet to brief from.")
            return
        self._run_brief(past)

    def _run_brief(self, past: list) -> None:
        blocks = []
        for m in past:
            notes = m.notes or {}
            open_items = [
                f"- {a.get('task')}" + (f" (owner: {a.get('owner')})" if a.get("owner") else "")
                for a in (notes.get("action_items") or [])
                if isinstance(a, dict) and not a.get("done")
            ]
            blocks.append(
                f"### {m.title or 'Untitled'} ({m.date_text})\n"
                f"Summary: {notes.get('summary', '')}\n"
                + ("Open action items:\n" + "\n".join(open_items) if open_items else "No open action items.")
            )
        instruction = (
            "Write a short PRE-MEETING BRIEF for my upcoming meeting, based on these past "
            "meetings. Structure: 'Last time' (2-3 bullet recap of key decisions), 'Still open' "
            "(the unfinished action items with owners), 'Suggested talking points' (2-3 bullets). "
            "Under 180 words, plain text, no preamble."
        )
        cfg = self.cfg
        context = "\n\n".join(blocks)

        self.brief_btn.setEnabled(False)
        self.brief_btn.setText("  Writing brief…")
        self.status_label.setText("Reading your past meetings…")

        def job(_p):
            from ..notes import service as notes_service
            return notes_service.run_action(instruction, cfg, notes_text=context, title="Pre-meeting brief")

        def done(text):
            self.brief_btn.setEnabled(True)
            self.brief_btn.setText("  Prep brief from past meetings")
            existing = self.agenda.toPlainText().strip()
            self.agenda.setPlainText(text + ("\n\n" + existing if existing else ""))
            self.status_label.setText(f"Brief added from {len(past)} past meeting(s) — edit freely.")

        def failed(msg):
            self.brief_btn.setEnabled(True)
            self.brief_btn.setText("  Prep brief from past meetings")
            self.status_label.setText(f"Brief failed: {msg}")

        self._brief_worker = FuncWorker(job)
        self._brief_worker.done.connect(done)
        self._brief_worker.failed.connect(failed)
        self._brief_worker.start()

    def _add_bookmark(self) -> None:
        if not (self.recorder and self.recorder.running):
            return
        ms = int(self.recorder.elapsed * 1000)
        self._bookmarks.append({"ms": ms, "label": ""})
        secs = ms // 1000
        self.bookmark_count.setText(f"{len(self._bookmarks)} bookmark(s) · last at {secs // 60:02d}:{secs % 60:02d}")
        if self.meeting_id is not None:
            self.repo.update(self.meeting_id, bookmarks=self._bookmarks)

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("H3")
        return lbl

    def _meter(self, kind: str) -> QProgressBar:
        m = QProgressBar()
        m.setRange(0, 100)
        m.setTextVisible(False)
        m.setProperty("meter", kind)
        return m

    def _meter_row(self, name: str, meter: QProgressBar) -> QHBoxLayout:
        row = QHBoxLayout()
        lbl = QLabel(name)
        lbl.setObjectName("Muted")
        lbl.setFixedWidth(46)
        row.addWidget(lbl)
        row.addWidget(meter)
        return row

    def apply_theme(self) -> None:
        self.date_icon.setPixmap(icons.pixmap("calendar", self.theme.color("text_muted"), 18))
        is_rec = bool(self.recorder and self.recorder.running)
        name = "stop" if is_rec else "record"
        self.record_btn.setIcon(self.theme.icon(name, "on_danger", 18))
        self.bookmark_btn.setIcon(self.theme.icon("bookmark", "text", 16))
        self.input_warning_icon.setPixmap(icons.pixmap("alert-triangle", self.theme.color("warning"), 18))
        self.input_warning.setStyleSheet(
            f"#WarnBanner{{background:{self.theme.color('warning_soft')};"
            f"border:1px solid {self.theme.color('warning')}; border-radius:10px;}}"
            f"#WarnText{{color:{self.theme.color('warning')}; font-weight:600; background:transparent;}}"
        )

    # ---------- shown ----------
    def on_shown(self) -> None:
        self._human_date, self._iso_date = today_pair()
        self.date_label.setText(self._human_date)
        if not (self.recorder and self.recorder.running):
            self._load_devices()
            self.status_label.setText("")
            templates = self.cfg.templates or []
            self.template_box.setVisible(bool(templates))
            if templates:
                self.template_combo.clear()
                self.template_combo.addItem("General (default)", "")
                for t in templates:
                    self.template_combo.addItem(t.get("name") or "(unnamed)", t.get("name") or "")
            self._populate_folder_combo()

    def _populate_folder_combo(self, *, select_folder_id=None) -> None:
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo.addItem("Uncategorized", None)
        for f in self.repo.list_folders():
            self.folder_combo.addItem(icons.icon("folder", f.color, 16), f.name, f.id)
        self.folder_combo.addItem("＋ New project…", "__new__")
        if select_folder_id is not None:
            idx = self.folder_combo.findData(select_folder_id)
            if idx >= 0:
                self.folder_combo.setCurrentIndex(idx)
        self.folder_combo.blockSignals(False)

    def _on_folder_combo_changed(self, index: int) -> None:
        if self.folder_combo.itemData(index) != "__new__":
            return
        from .folder_dialog import ask_new_folder
        result = ask_new_folder(self, self.theme)
        if result is None:
            self._populate_folder_combo()  # revert to "No folder"
            return
        name, color = result
        folder = self.repo.create_folder(name, color)
        self._populate_folder_combo(select_folder_id=folder.id)

    def _load_devices(self) -> None:
        try:
            mics = dev.list_input_devices()
            loops = dev.list_loopback_devices()
        except Exception as e:
            QMessageBox.critical(self, "Audio error", f"Could not list audio devices:\n{e}")
            mics, loops = [], []
        self.mic_combo.clear()
        for d in mics:
            self.mic_combo.addItem(d.name + ("  (default)" if d.is_default else ""), d)
        self.them_combo.clear()
        for d in loops:
            self.them_combo.addItem(d.name + ("  (default)" if d.is_default else ""), d)
        self._select(self.mic_combo, self.cfg.mic_device_name, mics)
        self._select(self.them_combo, self.cfg.loopback_device_name, loops)
        ok = bool(mics and loops)
        self.record_btn.setEnabled(ok)
        if not ok:
            where = "macOS" if sys.platform == "darwin" else "Windows"
            self.status_label.setText(
                f"No microphone or system audio source found. Check {where} sound settings.")

    @staticmethod
    def _select(combo: QComboBox, saved, devs) -> None:
        target = saved or next((d.name for d in devs if d.is_default), None)
        if not target:
            return
        for i in range(combo.count()):
            d = combo.itemData(i)
            if d is not None and d.name == target:
                combo.setCurrentIndex(i)
                return

    # ---------- record lifecycle ----------
    def _toggle(self) -> None:
        if self.recorder and self.recorder.running:
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        mic = self.mic_combo.currentData()
        them = self.them_combo.currentData()
        if mic is None or them is None:
            QMessageBox.warning(self, "Pick devices", "Choose both a microphone and a system-audio device.")
            return
        attendees = _parse_attendees(self.attendees.text())
        agenda = self.agenda.toPlainText().strip()
        template = (self.template_combo.currentData() or "") if self.template_box.isVisible() else ""
        folder_data = self.folder_combo.currentData()
        folder_id = folder_data if folder_data != "__new__" else None
        self._bookmarks = []
        self.bookmark_count.setText("")
        self._mic_seen = False
        self._them_seen = False
        self._write_error_reported = False
        self.input_warning.setVisible(False)
        self.apply_theme()  # reset the banner to its amber (input-warning) style
        try:
            # On macOS this explicitly resolves the microphone prompt before
            # the system-audio tap can begin writing. Other platforms no-op.
            ensure_capture_permissions(mic.index)
        except Exception as e:
            QMessageBox.critical(self, "Recording permission needed", str(e))
            return
        if self.capture_screen.isChecked() and not screen_capture_authorized(request=True):
            self._disable_screen_capture_for_permission()
        meeting = self.repo.create(
            date_text=self._human_date, date_iso=self._iso_date, attendees=attendees, agenda=agenda,
            template=template, folder_id=folder_id,
        )
        self.meeting_id = meeting.id
        self.repo.update(self.meeting_id, status="Recording", headphones_mode=self.headphones.isChecked())
        self.cfg.mic_device_name = mic.name
        self.cfg.loopback_device_name = them.name
        self.cfg.headphones_mode = self.headphones.isChecked()
        self.cfg.save()
        try:
            self.recorder = DualStreamRecorder(
                mic_index=mic.index, mic_channels=mic.channels, mic_rate=mic.default_samplerate,
                loop_index=them.index, loop_channels=them.channels, loop_rate=them.default_samplerate,
                spool_dir=meeting_dir(self.meeting_id),  # in-folder spool → crash-salvageable
            )
            self.recorder.start()
            self._record_t0 = self.recorder.started_at
        except Exception as e:
            QMessageBox.critical(self, "Could not start", f"Failed to open audio streams:\n{e}")
            failed_id = self.meeting_id
            failed_dir = meeting_dir(failed_id)
            self.repo.delete(failed_id)
            shutil.rmtree(failed_dir, ignore_errors=True)
            self.meeting_id = None
            self.recorder = None
            self.shell.notify_data_changed()
            return
        self.record_btn.setText("Stop recording")
        self.apply_theme()
        self.mic_combo.setEnabled(False)
        self.them_combo.setEnabled(False)
        self.headphones.setEnabled(False)
        self.live.setVisible(True)
        self.status_label.setText("Recording… attendees stay editable.")
        if hasattr(self.shell, "set_recording"):
            self.shell.set_recording(True)
        self.shell.notify_data_changed()
        self.poll.start()
        self._show_overlay()
        self.cfg.capture_screen = self.capture_screen.isChecked()
        self.cfg.save()
        self._maybe_start_screen()

    def _maybe_start_screen(self) -> None:
        if not self.capture_screen.isChecked() or self.meeting_id is None:
            return
        if self._screen_rec and self._screen_rec.running:
            return
        out = meeting_dir(self.meeting_id) / "screenshots"
        self._screen_rec = ScreenRecorder(out, start_monotonic=self._record_t0, monitor=self.cfg.screen_monitor)
        self._screen_rec.start()

    def _stop_screen(self) -> None:
        if self._screen_rec:
            self._screen_rec.stop()
            self._screen_rec = None

    def _toggle_screen_capture(self, checked: bool) -> None:
        if checked and not screen_capture_authorized(request=True):
            self._disable_screen_capture_for_permission()
            return
        self.cfg.capture_screen = checked
        self.cfg.save()
        if self.recorder and self.recorder.running:
            if checked:
                self._maybe_start_screen()
            else:
                self._stop_screen()

    def _disable_screen_capture_for_permission(self) -> None:
        with QSignalBlocker(self.capture_screen):
            self.capture_screen.setChecked(False)
        self.cfg.capture_screen = False
        self.cfg.save()
        QMessageBox.warning(
            self,
            "Screen Recording permission needed",
            "Earshot cannot capture screenshots yet. Allow Earshot under System Settings > "
            "Privacy & Security > Screen & System Audio Recording, then restart Earshot. "
            "Audio recording can continue without screenshots.",
        )

    def _on_poll(self) -> None:
        if not self.recorder:
            return
        mic_level, them_level = self.recorder.mic_level, self.recorder.them_level
        self.mic_meter.setValue(int(mic_level * 100))
        self.them_meter.setValue(int(them_level * 100))
        secs = int(self.recorder.elapsed)
        self.timer_label.setText(f"{secs // 60:02d}:{secs % 60:02d}")
        if mic_level > _INPUT_ACTIVE_LEVEL:
            self._mic_seen = True
        if them_level > _INPUT_ACTIVE_LEVEL:
            self._them_seen = True
        write_error = self.recorder.write_error
        screen_error = self._screen_rec.error if self._screen_rec else None
        if not write_error and getattr(self, "_banner_danger", False):
            # A transiently-latched capture problem healed (see _capture_mac's
            # recovery logic): drop the red styling so the banner returns to
            # its amber input-warning look, or hides entirely.
            self._banner_danger = False
            self.apply_theme()
        if write_error:
            self._show_write_error(write_error)
        elif screen_error:
            self.input_warning_text.setText(
                screen_error + " Check Screen Recording permission in System Settings."
            )
            self.input_warning.setVisible(True)
        else:
            self._update_input_warning(self.recorder.elapsed)
        if self.overlay is not None:
            self.overlay.update_levels(mic_level, them_level)
            self.overlay.update_time(self.recorder.elapsed)

    def _update_input_warning(self, elapsed: float) -> None:
        """Show an amber banner if a channel has produced no audio at all after the
        grace period. Clears the moment sound is detected (so normal pauses don't trip it)."""
        capture_warning = getattr(self.recorder, "capture_warning", None)
        if capture_warning:
            self.input_warning_text.setText(capture_warning)
            self.input_warning.setVisible(True)
            return
        if elapsed < _INPUT_GRACE_SECS or (self._mic_seen and self._them_seen):
            self.input_warning.setVisible(False)
            return
        if not self._mic_seen and not self._them_seen:
            msg = ("No audio detected yet — we're not hearing your microphone or the other side. "
                   "Check your mic and that the meeting audio is playing through the selected device.")
        elif not self._mic_seen:
            msg = "No sound from your microphone yet — check it isn't muted and the right input is selected above."
        else:
            msg = ("No sound from the other side yet — make sure the meeting audio is playing through the "
                   "device selected under “Their audio”.")
        self.input_warning_text.setText(msg)
        self.input_warning.setVisible(True)

    def _show_write_error(self, write_error: str) -> None:
        """Danger banner: a spool file stopped accepting writes (disk full,
        recordings folder disconnected, permissions) while the level meters
        keep moving off the in-memory buffer. Without this the user only finds
        out when the recording comes up short. Shown while the recorder reports
        the problem; a transient hiccup that provably heals (macOS tap
        recovery) clears it, anything persistent stays until the recording
        stops. The first failure is stamped on the meeting row either way."""
        self._banner_danger = True
        self.input_warning_icon.setPixmap(icons.pixmap("alert-triangle", self.theme.color("danger"), 18))
        self.input_warning.setStyleSheet(
            f"#WarnBanner{{background:{self.theme.color('danger_soft')};"
            f"border:1px solid {self.theme.color('danger')}; border-radius:10px;}}"
            f"#WarnText{{color:{self.theme.color('danger')}; font-weight:600; background:transparent;}}"
        )
        self.input_warning_text.setText(
            "Recording problem: Earshot can no longer save audio for "
            f"{write_error.split(' (')[0]}. Audio up to this point is kept, but new audio "
            "is being lost. Check free disk space and that the recordings folder is still "
            "available, then stop and restart the recording."
        )
        self.input_warning.setVisible(True)
        if not self._write_error_reported and self.meeting_id is not None:
            self._write_error_reported = True
            self.repo.update(self.meeting_id,
                             error=f"Recording problem: audio stopped saving for {write_error}")

    def _persist_attendees(self) -> None:
        if self.meeting_id is not None:
            self.repo.update(self.meeting_id, attendees=_parse_attendees(self.attendees.text()))

    def _stop(self) -> None:
        # Idle guard: a stale "call ended — Stop & process?" toast (which lives
        # ~30s) can fire this after the user already stopped. Without the guard
        # meeting_dir(None) raises and the record button is left disabled on
        # "Processing…" forever.
        if self.recorder is None or self.meeting_id is None:
            return
        self.poll.stop()
        self._stop_screen()
        self._hide_overlay()
        if hasattr(self.shell, "set_recording"):
            self.shell.set_recording(False)
        self.input_warning.setVisible(False)
        self.record_btn.setEnabled(False)
        self.record_btn.setText("Processing…")
        self._persist_attendees()
        if self.meeting_id is not None:
            self.repo.update(self.meeting_id, agenda=self.agenda.toPlainText().strip())
        recorder = self.recorder
        self.recorder = None
        mid = self.meeting_id
        cfg, repo = self.cfg, self.repo
        audio_dir = meeting_dir(mid)
        from ..audio import writer as wr
        from ..pipeline.processing import process_recording

        def job(progress):
            write_error = recorder.write_error  # capture before the streams close
            progress("Finalising audio")
            spool = recorder.stop()
            # Streams the spools to WAV block-by-block; the raw spools (the only
            # copy of the meeting) are deleted only after the WAVs verify.
            wr.finalize_recording(spool, audio_dir)
            repo.update(mid, audio_dir=str(audio_dir), duration_secs=spool.duration_secs, status="Recorded")
            if cfg.auto_transcribe:
                process_recording(repo, mid, cfg, progress=progress, summarize=cfg.auto_summary)
            else:
                progress("Saved — open the meeting to transcribe it when you're ready")
            if write_error:
                # the pipeline clears `error` when transcription starts; re-stamp
                # so the incomplete-audio warning survives on the meeting row
                repo.update(mid, error=("Recording problem: audio stopped saving for "
                                        f"{write_error}. The saved audio may be incomplete."))
            return mid

        self.worker = FuncWorker(job)
        self.worker.progress.connect(self.status_label.setText)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()
        self.shell.notify_data_changed()

    def _reset_controls(self) -> None:
        self.record_btn.setEnabled(True)
        self.record_btn.setText("Start recording")
        self.mic_combo.setEnabled(True)
        self.them_combo.setEnabled(True)
        self.headphones.setEnabled(True)
        self.input_warning.setVisible(False)
        self._hide_overlay()
        self.live.setVisible(False)
        self.apply_theme()

    def _on_done(self, mid) -> None:
        self.status_label.setText("Done.")
        self.meeting_id = None  # editing the next session must not rewrite this one
        self._reset_controls()
        self.attendees.clear()
        self.agenda.clear()  # fresh form for the next recording
        self.bookmark_count.setText("")
        self.shell.notify_data_changed()
        self.shell.open_meeting(int(mid))

    def _on_failed(self, msg: str) -> None:
        self.status_label.setText(f"Error: {msg}")
        self.meeting_id = None
        self._reset_controls()
        self.shell.notify_data_changed()
        QMessageBox.critical(self, "Processing failed", msg)

    def is_busy(self) -> bool:
        return bool((self.recorder and self.recorder.running) or (self.worker and self.worker.isRunning()))
