"""Recording page: auto-filled date, live-editable attendees, device pickers,
level meters and the record/stop control. Hands off to the background pipeline
on stop, then opens the finished meeting.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import time

from ..audio import devices as dev
from ..audio.capture import DualStreamRecorder
from ..capture.screen import ScreenRecorder
from ..paths import meeting_dir
from ..util.dates import today_pair
from . import icons
from .overlay import RecordingOverlay
from .widgets import Card
from .workers import FuncWorker

# A channel whose RMS level never crosses this is treated as silent (a muted mic
# or mis-routed system audio reads ~0; even quiet speech/room tone reads higher).
_INPUT_ACTIVE_LEVEL = 0.04
# Don't warn until the recording has had this long to actually produce sound.
_INPUT_GRACE_SECS = 10.0


def _parse_attendees(text: str) -> list[str]:
    return [a.strip() for a in text.replace(";", ",").split(",") if a.strip()]


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
        root.addWidget(sess)

        # agenda card — pre-meeting notes that stay on screen while recording and
        # are fed to the AI as context.
        ag = Card()
        agl = QVBoxLayout(ag)
        agl.setContentsMargins(22, 18, 22, 20)
        agl.setSpacing(10)
        ag_lbl = QLabel("Agenda / notes")
        ag_lbl.setObjectName("H3")
        agl.addWidget(ag_lbl)
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
        self.bookmark_btn = QPushButton("  Bookmark this moment  (Ctrl+B)")
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
        self._bm_shortcut = QShortcut(QKeySequence("Ctrl+B"), self)
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
            self.status_label.setText("No microphone or loopback device found — check Windows sound settings.")

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
        self._bookmarks = []
        self.bookmark_count.setText("")
        self._mic_seen = False
        self._them_seen = False
        self.input_warning.setVisible(False)
        meeting = self.repo.create(
            date_text=self._human_date, date_iso=self._iso_date, attendees=attendees, agenda=agenda,
            template=template,
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
            )
            self._record_t0 = time.monotonic()
            self.recorder.start()
        except Exception as e:
            QMessageBox.critical(self, "Could not start", f"Failed to open audio streams:\n{e}")
            self.repo.update(self.meeting_id, status="Error", error=str(e))
            return
        self.record_btn.setText("Stop recording")
        self.apply_theme()
        self.mic_combo.setEnabled(False)
        self.them_combo.setEnabled(False)
        self.headphones.setEnabled(False)
        self.live.setVisible(True)
        self.status_label.setText("Recording… attendees stay editable.")
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
        self.cfg.capture_screen = checked
        self.cfg.save()
        if self.recorder and self.recorder.running:
            if checked:
                self._maybe_start_screen()
            else:
                self._stop_screen()

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
        self._update_input_warning(self.recorder.elapsed)
        if self.overlay is not None:
            self.overlay.update_levels(mic_level, them_level)
            self.overlay.update_time(self.recorder.elapsed)

    def _update_input_warning(self, elapsed: float) -> None:
        """Show an amber banner if a channel has produced no audio at all after the
        grace period. Clears the moment sound is detected (so normal pauses don't trip it)."""
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

    def _persist_attendees(self) -> None:
        if self.meeting_id is not None:
            self.repo.update(self.meeting_id, attendees=_parse_attendees(self.attendees.text()))

    def _stop(self) -> None:
        self.poll.stop()
        self._stop_screen()
        self._hide_overlay()
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
            progress("Finalising audio")
            result = recorder.stop()
            wr.save_recording(result.me_48k, result.them_48k, audio_dir)
            repo.update(mid, audio_dir=str(audio_dir), duration_secs=result.duration_secs, status="Recorded")
            if cfg.auto_transcribe:
                process_recording(repo, mid, cfg, progress=progress, summarize=cfg.auto_summary)
            else:
                progress("Saved — open the meeting to transcribe it when you're ready")
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
        self._reset_controls()
        self.attendees.clear()
        self.agenda.clear()  # fresh form for the next recording
        self.bookmark_count.setText("")
        self.shell.notify_data_changed()
        self.shell.open_meeting(int(mid))

    def _on_failed(self, msg: str) -> None:
        self.status_label.setText(f"Error: {msg}")
        self._reset_controls()
        self.shell.notify_data_changed()
        QMessageBox.critical(self, "Processing failed", msg)

    def is_busy(self) -> bool:
        return bool((self.recorder and self.recorder.running) or (self.worker and self.worker.isRunning()))
