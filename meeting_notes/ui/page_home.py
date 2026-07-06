"""Home / library page: a greeting, a hero "record a new meeting" card, filter
chips, the meetings as compact clickable rows, and a right rail (to-do list,
recent notes, AI insights) — or a friendly empty state when there are no
meetings yet.
"""
from __future__ import annotations

import html as _html
import json
import os

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QRadialGradient
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..storage.repository import Meeting
from . import icons
from .widgets import Card, ElideLabel, make_chip, status_chip

# Rail collapses below this viewport width (spec: "responsive — below ~1050px
# hide the right rail").
_RAIL_BREAKPOINT = 1050
_RAIL_WIDTH = 312

# A fixed, deterministic "waveform" pattern for the hero card's decorative
# backdrop — NOT randomised at paint time (that would repaint differently on
# every resize/theme flip). Values are relative bar heights in [0, 1].
_WAVE_PATTERN = [
    0.35, 0.55, 0.28, 0.72, 0.44, 0.61, 0.30, 0.85, 0.50, 0.66,
    0.38, 0.92, 0.58, 0.41, 0.77, 0.33, 0.63, 0.47, 0.81, 0.36,
    0.69, 0.53, 0.25, 0.88, 0.46, 0.60, 0.34, 0.74, 0.42, 0.57,
    0.31, 0.90, 0.49, 0.65, 0.37, 0.79, 0.44, 0.68, 0.29, 0.83,
]


class PaintedBackdrop(QWidget):
    """A decorative gradient panel with a stylised audio-waveform pattern and a
    soft radial glow, used behind the hero "record" CTA and the mini AI-insights
    banner. Deterministic (no randomness at paint time) and repaints itself on
    `apply_theme()` so it stays correct across light/dark and window resizes.

    `variant="hero"` draws the fuller pattern (glow + wide bars); `variant="mini"`
    draws a flatter, smaller-scale version for the AI-insights card.
    """

    def __init__(self, theme, *, variant: str = "hero", parent=None):
        super().__init__(parent)
        self.theme = theme
        self.variant = variant
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def _palette(self) -> tuple[QColor, QColor]:
        """(start, end) gradient colours — dark-indigo->violet in dark mode,
        soft-lavender-on-white in light mode. AI-insights mini variant always
        uses the violet palette (it's a fixed accent banner, not theme-toggled)."""
        dark = self.theme.mode == "dark"
        if self.variant == "mini":
            return (QColor("#5B4FE8") if dark else QColor("#8B7CF6"),
                    QColor("#2E2A78") if dark else QColor("#6C63E0"))
        if dark:
            return QColor("#1E1B4B"), QColor("#4C1D95")
        return QColor("#EEF0FF"), QColor("#E4DEFB")

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = 20.0

        start, end = self._palette()
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, start)
        grad.setColorAt(1.0, end)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        painter.drawRoundedRect(rect, radius, radius)

        # soft radial glow, roughly centred where the mic button sits (hero) or
        # centred on the card (mini)
        glow_cx = rect.width() * (0.5 if self.variant == "mini" else 0.62)
        glow_cy = rect.height() * 0.5
        glow_r = max(rect.width(), rect.height()) * 0.55
        glow = QRadialGradient(glow_cx, glow_cy, glow_r)
        glow_color = QColor(255, 255, 255, 46 if self.variant == "hero" else 30)
        glow.setColorAt(0.0, glow_color)
        glow.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(glow)
        painter.drawRoundedRect(rect, radius, radius)

        self._paint_waveform(painter, rect)
        painter.end()

    def _paint_waveform(self, painter: QPainter, rect: QRectF) -> None:
        """Rows of vertical rounded bars, heights from the fixed pseudo-random
        pattern, fading in opacity toward the left/right edges so the pattern
        reads as decorative texture rather than real data."""
        n = len(_WAVE_PATTERN)
        margin_x = rect.width() * 0.04
        usable_w = rect.width() - 2 * margin_x
        if usable_w <= 0 or n == 0:
            return
        gap = 3.0
        bar_w = max(2.0, usable_w / n - gap)
        max_bar_h = rect.height() * (0.5 if self.variant == "hero" else 0.34)
        base_y = rect.top() + rect.height() * (0.72 if self.variant == "hero" else 0.68)
        mid = (n - 1) / 2.0

        for i, h_frac in enumerate(_WAVE_PATTERN):
            x = rect.left() + margin_x + i * (bar_w + gap)
            bar_h = max(3.0, h_frac * max_bar_h)
            # fade toward both edges (opacity lowest at the ends, full mid-way)
            edge_dist = abs(i - mid) / mid if mid else 0.0
            opacity = max(0.06, 0.30 * (1.0 - edge_dist))
            color = QColor(255, 255, 255)
            color.setAlphaF(opacity)
            painter.setBrush(color)
            bar_rect = QRectF(x, base_y - bar_h, bar_w, bar_h)
            painter.drawRoundedRect(bar_rect, bar_w / 2.0, bar_w / 2.0)


class _ClickableCard(Card):
    """A Card that reports plain left-clicks (not drags) via a callback —
    used for the hero card, the AI-insights card and each compact meeting row,
    which are all "the whole card is a button" affordances."""

    def __init__(self, on_click, *, shadow: bool = True):
        super().__init__(shadow=shadow)
        self._on_click = on_click
        self.setProperty("clickable", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())
                and self._on_click is not None):
            self._on_click()
        super().mouseReleaseEvent(event)


class MeetingRow(_ClickableCard):
    """A compact single-line meeting row: icon tile, title + meta, optional
    folder chip, status chip, and a kebab menu (Open / Move to project / Delete)."""

    def __init__(self, meeting: Meeting, theme, repo, shell, folders_by_id: dict):
        super().__init__(lambda: shell.open_meeting(meeting.id), shadow=False)
        self.meeting = meeting
        self.theme = theme
        self.repo = repo
        self.shell = shell
        self.setFixedHeight(64)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 10, 10)
        lay.setSpacing(12)

        tile = QLabel()
        tile.setFixedSize(36, 36)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setStyleSheet(
            f"background:{theme.color('primary_soft')}; border-radius:9px;"
        )
        icon_lbl = QLabel(tile)
        icon_lbl.setPixmap(icons.pixmap("file", theme.color("primary"), 17))
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setGeometry(0, 0, 36, 36)
        lay.addWidget(tile)

        mid = QVBoxLayout()
        mid.setSpacing(2)
        # ElideLabel: a plain QLabel's minimum width is its full text, which on
        # narrow windows (vertical monitor) pushes the page past the right edge
        title = ElideLabel(meeting.title or "Untitled meeting")
        title.setObjectName("H3")
        title.setTextFormat(Qt.TextFormat.PlainText)  # AI-generated title -> no rich text
        title.setWordWrap(False)
        mid.addWidget(title)
        bits = [meeting.date_text or meeting.date_iso]
        if meeting.attendees:
            bits.append(", ".join(meeting.attendees[:3]) + ("…" if len(meeting.attendees) > 3 else ""))
        if meeting.duration_secs:
            bits.append(f"{int(meeting.duration_secs // 60)}m")
        meta = ElideLabel("   ·   ".join(b for b in bits if b))
        meta.setObjectName("Faint")
        meta.setWordWrap(False)
        mid.addWidget(meta)
        lay.addLayout(mid, 1)

        folder = folders_by_id.get(meeting.folder_id) if meeting.folder_id else None
        if folder is not None:
            lay.addWidget(self._folder_chip(folder), 0, Qt.AlignmentFlag.AlignVCenter)

        lay.addWidget(status_chip(meeting.status, theme), 0, Qt.AlignmentFlag.AlignVCenter)

        self.kebab_btn = QPushButton("⋮")
        self.kebab_btn.setProperty("variant", "ghost")
        self.kebab_btn.setProperty("iconbtn", True)
        self.kebab_btn.setFixedSize(30, 30)
        self.kebab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.kebab_btn.setStyleSheet("QPushButton{font-size:16px; font-weight:700;}")
        self.kebab_btn.clicked.connect(self._open_kebab_menu)
        lay.addWidget(self.kebab_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def _folder_chip(self, folder) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(8, 3, 10, 3)
        lay.setSpacing(4)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("folder", folder.color, 12))
        lay.addWidget(ic)
        lbl = QLabel(folder.name)
        lbl.setStyleSheet(f"color:{self.theme.color('text_muted')}; font-size:12px; font-weight:600;")
        lay.addWidget(lbl)
        w.setStyleSheet(f"background:{self.theme.color('surface_hover')}; border-radius:9px;")
        return w

    # ---------- kebab menu: Open / Move to project / Delete ----------
    def _open_kebab_menu(self) -> None:
        menu = QMenu(self)
        open_act = menu.addAction("Open")
        open_act.triggered.connect(lambda: self.shell.open_meeting(self.meeting.id))

        move_menu = menu.addMenu("Move to project")
        self._populate_move_menu(move_menu)

        menu.addSeparator()
        delete_act = menu.addAction(icons.icon("trash", self.theme.color("danger"), 15), "Delete")
        delete_act.triggered.connect(self._delete)

        menu.exec(self.kebab_btn.mapToGlobal(QPoint(0, self.kebab_btn.height())))

    def _populate_move_menu(self, menu: QMenu) -> None:
        """Same icon-column pattern as DetailPage._populate_move_menu: a check
        icon (in the folder's colour) marks the meeting's current location,
        plain folder glyphs elsewhere — no checkable indicators next to icons."""
        m = self.meeting

        def add(name, color, text, slot):
            act = menu.addAction(icons.icon(name, color, 15), text)
            act.triggered.connect(slot)
            return act

        here = m.folder_id is None
        add("check" if here else "folder", self.theme.color("text_faint"),
            "No project", lambda _=False: self._move_to_folder(None))

        for f in self.repo.list_folders():
            here = m.folder_id == f.id
            add("check" if here else "folder", f.color, f.name,
                lambda _=False, fid=f.id: self._move_to_folder(fid))

        add("plus", self.theme.color("text_muted"), "New project…", self._move_to_new_folder)

    def _move_to_folder(self, folder_id) -> None:
        self.repo.update(self.meeting.id, folder_id=folder_id)
        self.shell.notify_data_changed()  # refreshes home (this row's own list) too

    def _move_to_new_folder(self) -> None:
        from .folder_dialog import ask_new_folder
        result = ask_new_folder(self, self.theme)
        if result is None:
            return
        name, color = result
        folder = self.repo.create_folder(name, color)
        self._move_to_folder(folder.id)

    def _delete(self) -> None:
        if QMessageBox.question(
            self, "Delete meeting", "Delete this meeting and its record?"
        ) != QMessageBox.StandardButton.Yes:
            return
        m = self.repo.get(self.meeting.id)
        if m.audio_dir and os.path.isdir(m.audio_dir) and self._is_recording_folder(m.audio_dir):
            import shutil
            shutil.rmtree(m.audio_dir, ignore_errors=True)  # audio + screenshots
        self.repo.delete(self.meeting.id)
        self.shell.notify_data_changed()  # refreshes home (this row's own list) too

    @staticmethod
    def _is_recording_folder(path: str) -> bool:
        """Guard rmtree: only ever touch a per-meeting folder that actually
        lives under the recordings root (same guard as DetailPage._delete —
        defends against a tampered/mis-set audio_dir turning Delete into
        arbitrary recursive deletion)."""
        from pathlib import Path

        from ..paths import recordings_dir
        try:
            p = Path(path).resolve()
            root = recordings_dir().resolve()
            return root in p.parents and p.name.startswith("meeting_")
        except OSError:
            return False


class HomePage(QWidget):
    def __init__(self, shell, repo, cfg, theme):
        super().__init__()
        self.shell = shell
        self.repo = repo
        self.cfg = cfg
        self.theme = theme
        # folder filter for the row list: None = all, an int = that folder,
        # "unfiled" = meetings with no folder
        self._folder_filter = None
        self._todo_show_all = False
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 32, 40, 24)
        root.setSpacing(18)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.h1 = QLabel("Your meetings")
        self.h1.setObjectName("H1")
        self.count = QLabel("")
        self.count.setObjectName("Muted")
        titles.addWidget(self.h1)
        titles.addWidget(self.count)
        header.addLayout(titles)
        header.addStretch(1)
        self.new_btn = QPushButton("  New recording")
        self.new_btn.setProperty("variant", "primary")
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_btn.setMinimumHeight(42)
        self.new_btn.clicked.connect(lambda: self.shell.show_record())
        header.addWidget(self.new_btn, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # host: two columns side by side — left (stretch, the list) + right
        # rail (fixed width) — both scroll together with the page. On narrow
        # windows (e.g. a vertical monitor) the same layout flips vertical and
        # the rail stacks under the list instead of disappearing.
        self.columns_host = QWidget()
        self.columns = QHBoxLayout(self.columns_host)
        self.columns.setContentsMargins(2, 2, 2, 2)
        self.columns.setSpacing(20)

        self.list_host = QWidget()
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(12)
        self.columns.addWidget(self.list_host, 1)

        self.rail = QWidget()
        self.rail.setFixedWidth(_RAIL_WIDTH)
        self.rail_lay = QVBoxLayout(self.rail)
        self.rail_lay.setContentsMargins(0, 0, 0, 0)
        self.rail_lay.setSpacing(16)
        self.columns.addWidget(self.rail, 0)
        self._columns_tail = None  # stretch item appended only in stacked mode

        self.scroll.setWidget(self.columns_host)
        root.addWidget(self.scroll, 1)

        self.empty = self._empty_state()
        root.addWidget(self.empty)
        root.setStretchFactor(self.empty, 1)

    def _empty_state(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addStretch(1)
        self.empty_icon = QLabel()
        self.empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.empty_icon)
        t = QLabel("No meetings yet")
        t.setObjectName("H2")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        s = QLabel("Capture your mic and the other side on separate channels — notes write themselves.")
        s.setObjectName("Muted")
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(s)
        cta = QPushButton("  Start your first recording")
        cta.setProperty("variant", "primary")
        cta.setCursor(Qt.CursorShape.PointingHandCursor)
        cta.setMinimumHeight(44)
        cta.clicked.connect(lambda: self.shell.show_record())
        self.empty_cta = cta
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cta)
        row.addStretch(1)
        lay.addSpacing(6)
        lay.addLayout(row)
        lay.addStretch(2)
        return w

    def refresh(self) -> None:
        # clear list + rail — detach synchronously (deleteLater alone leaves
        # orphan widgets parented to the container until the event loop runs).
        for lay in (self.list_lay, self.rail_lay):
            while lay.count():
                item = lay.takeAt(0)
                w = item.widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()

        meetings = self.repo.list()
        folders = self.repo.list_folders()
        folders_by_id = {f.id: f for f in folders}
        # an emptied/deleted folder can no longer be the active filter
        if isinstance(self._folder_filter, int) and self._folder_filter not in folders_by_id:
            self._folder_filter = None
        shown = self._filtered_meetings(meetings)
        if self._folder_filter is None:
            self.count.setText(f"{len(meetings)} recorded" if meetings else "Nothing recorded yet")
        else:
            label = self._filter_label(folders)
            self.count.setText(f"{len(shown)} in {label}" if shown else f"Nothing recorded in {label} yet")

        # hero card is always visible (even with 0 meetings)
        self.list_lay.addWidget(self._hero_card())

        has = bool(meetings)
        self.empty.setVisible(not has)
        if has:
            # collapsible "Meetings" section: hiding the list gives the To do
            # card the room (esp. stacked narrow layout — no scrolling needed)
            self.list_lay.addWidget(self._meetings_header(len(shown)))
            body = QWidget()
            body_lay = QVBoxLayout(body)
            body_lay.setContentsMargins(0, 0, 0, 0)
            body_lay.setSpacing(12)
            if folders:
                body_lay.addWidget(self._chip_row(folders, meetings))
            for m in shown:
                body_lay.addWidget(MeetingRow(m, self.theme, self.repo, self.shell, folders_by_id))
            body.setVisible(not self.cfg.meetings_collapsed)
            self._meetings_host = body
            self.list_lay.addWidget(body)
            self.list_lay.addStretch(1)

        # right rail
        if self.cfg.show_dashboard:
            pending = self._gather_pending(meetings)
            todo_card = self._todo_card(pending, meetings)
            if todo_card is not None:
                self.rail_lay.addWidget(todo_card)
        self.rail_lay.addWidget(self._ai_insights_card())
        self.rail_lay.addStretch(1)

        self._apply_rail_visibility()
        self.apply_theme()

    # ---------- hero card ----------
    def _hero_card(self) -> QWidget:
        card = _ClickableCard(lambda: self.shell.show_record(), shadow=True)
        card.setMinimumHeight(210)
        card.setMaximumHeight(230)
        outer = QVBoxLayout(card)
        outer.setContentsMargins(0, 0, 0, 0)

        backdrop = PaintedBackdrop(self.theme, variant="hero")
        outer.addWidget(backdrop)
        self._hero_backdrop = backdrop

        content = QHBoxLayout(backdrop)
        content.setContentsMargins(32, 0, 32, 0)
        content.setSpacing(24)

        texts = QVBoxLayout()
        texts.setSpacing(6)
        texts.addStretch(1)
        title = QLabel("Record a new meeting")
        title.setObjectName("H2")
        title.setStyleSheet("color:#FFFFFF;" if self.theme.mode == "dark" else f"color:{self.theme.color('text')};")
        texts.addWidget(title)
        subtitle = QLabel("Capture, transcribe and summarize with AI")
        subtitle.setStyleSheet(
            "color:rgba(255,255,255,0.75);" if self.theme.mode == "dark"
            else f"color:{self.theme.color('text_muted')};"
        )
        texts.addWidget(subtitle)
        texts.addStretch(1)
        content.addLayout(texts, 1)

        mic_btn = QPushButton()
        mic_btn.setFixedSize(72, 72)
        mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mic_btn.setIcon(icons.icon("mic", "#FFFFFF", 30))
        mic_btn.setIconSize(QSize(30, 30))
        primary = self.theme.color("primary")
        primary_hover = self.theme.color("primary_hover")
        mic_btn.setStyleSheet(
            f"QPushButton{{background:{primary}; border:none; border-radius:36px;}}"
            f"QPushButton:hover{{background:{primary_hover};}}"
        )
        mic_btn.clicked.connect(lambda: self.shell.show_record())
        content.addWidget(mic_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._hero_card_ref = card
        return card

    # ---------- folder filter chips ----------
    def _filtered_meetings(self, meetings: list) -> list:
        if self._folder_filter is None:
            return meetings
        if self._folder_filter == "unfiled":
            return [m for m in meetings if m.folder_id is None]
        return [m for m in meetings if m.folder_id == self._folder_filter]

    def _filter_label(self, folders: list) -> str:
        if self._folder_filter == "unfiled":
            return "Unfiled"
        for f in folders:
            if f.id == self._folder_filter:
                return f.name
        return ""

    def _chip_row(self, folders: list, meetings: list) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self._filter_chip("All", len(meetings), None))
        for f in folders:
            count = sum(1 for m in meetings if m.folder_id == f.id)
            lay.addWidget(self._filter_chip(f.name, count, f.id, icon_color=f.color))
        unfiled_count = sum(1 for m in meetings if m.folder_id is None)
        lay.addWidget(self._filter_chip("Unfiled", unfiled_count, "unfiled"))
        lay.addStretch(1)
        return row

    def _filter_chip(self, name: str, count: int, value, *, icon_color: str | None = None) -> QPushButton:
        active = self._folder_filter == value
        btn = QPushButton(f"  {name} ({count})" if icon_color else f"{name} ({count})")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if icon_color:
            btn.setIcon(icons.icon("folder", icon_color, 14))
        fg = self.theme.color("primary") if active else self.theme.color("text_muted")
        bg = self.theme.color("primary_soft") if active else self.theme.color("surface_hover")
        btn.setStyleSheet(
            f"QPushButton{{background:{bg}; color:{fg}; border:none; border-radius:10px;"
            f"padding:6px 14px; font-size:12px; font-weight:600;}}"
            f"QPushButton:hover{{background:{self.theme.color('primary_soft')}; color:{self.theme.color('primary')};}}"
        )
        btn.clicked.connect(lambda _=False, v=value: self._set_folder_filter(v))
        return btn

    def _set_folder_filter(self, value) -> None:
        self._folder_filter = value
        self.refresh()

    # ---------- pending action-items (to-do) ----------
    def _gather_pending(self, meetings, limit: int = 40) -> list[dict]:
        out: list[dict] = []
        for m in meetings:
            notes = m.notes
            if not notes:
                continue
            for i, a in enumerate(notes.get("action_items") or []):
                if not isinstance(a, dict):
                    continue
                # only ACCEPTED items are real to-dos; AI suggestions wait on the
                # meeting page until kept (legacy items without the key count as accepted)
                if not a.get("done") and a.get("confirmed", True):
                    out.append({"meeting_id": m.id, "idx": i, "task": a.get("task") or "",
                                "owner": a.get("owner"), "due": a.get("due"),
                                "title": m.title or "Untitled"})
            if len(out) >= limit:
                break
        return out[:limit]

    @staticmethod
    def _completion_pct(meetings) -> int | None:
        """Percentage of DONE action items across all meetings' *confirmed*
        items (accepted to-dos only — matches what the dashboard itself
        counts as real). None when there are no confirmed items at all."""
        done = 0
        total = 0
        for m in meetings:
            notes = m.notes
            if not notes:
                continue
            for a in notes.get("action_items") or []:
                if not isinstance(a, dict):
                    continue
                if not a.get("confirmed", True):
                    continue
                total += 1
                if a.get("done"):
                    done += 1
        if total == 0:
            return None
        return round(100 * done / total)

    def _todo_card(self, pending: list[dict], meetings: list) -> QWidget | None:
        self._dash_pending = pending
        pct = self._completion_pct(meetings)
        if not pending and pct is None:
            return None  # nothing to show at all (0 items -> hide badge AND card body is moot)

        card = Card(shadow=True)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("check-square", self.theme.color("primary"), 18))
        head.addWidget(ic)
        t = QLabel("To do")
        t.setObjectName("H3")
        head.addWidget(t)
        head.addStretch(1)
        if pct is not None:
            badge = make_chip(f"{pct}%", fg=self.theme.color("success"), bg=self.theme.color("success_soft"))
            head.addWidget(badge)
        kebab = QPushButton("⋮")
        kebab.setProperty("variant", "ghost")
        kebab.setProperty("iconbtn", True)
        kebab.setFixedSize(28, 28)
        kebab.setCursor(Qt.CursorShape.PointingHandCursor)
        kebab.setStyleSheet("QPushButton{font-size:16px; font-weight:700;}")
        kebab.clicked.connect(self._open_todo_kebab_menu)
        self._todo_kebab_btn = kebab
        head.addWidget(kebab)
        chevron_btn = QPushButton()
        chevron_btn.setProperty("variant", "ghost")
        chevron_btn.setProperty("iconbtn", True)
        chevron_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        chevron_btn.setFixedSize(28, 28)
        chevron_btn.setToolTip("Collapse" if not self.cfg.dashboard_collapsed else "Expand")
        chevron_btn.clicked.connect(self._toggle_dashboard)
        self._dash_chevron_btn = chevron_btn
        head.addWidget(chevron_btn)
        lay.addLayout(head)

        n = len(pending)
        count_lbl = QLabel(f"{n} pending action item" + ("s" if n != 1 else ""))
        count_lbl.setObjectName("Muted")
        lay.addWidget(count_lbl)

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 6, 0, 0)
        body_lay.setSpacing(8)

        items_host = QWidget()
        items_lay = QVBoxLayout(items_host)
        items_lay.setContentsMargins(0, 0, 0, 0)
        items_lay.setSpacing(8)
        shown_items = pending if self._todo_show_all else pending[:6]
        for p in shown_items:
            items_lay.addWidget(self._pending_row(p))
        body_lay.addWidget(items_host)

        if len(pending) > 6:
            footer = QPushButton("View all to-dos →" if not self._todo_show_all else "Show fewer ←")
            footer.setProperty("variant", "ghost")
            footer.setCursor(Qt.CursorShape.PointingHandCursor)
            footer.setStyleSheet(f"QPushButton{{color:{self.theme.color('primary')}; text-align:center;}}")
            footer.clicked.connect(self._toggle_todo_show_all)
            body_lay.addWidget(footer)

        body.setVisible(not self.cfg.dashboard_collapsed)
        self._dash_items_host = body
        lay.addWidget(body)

        self._set_dashboard_chevron_icon()
        return card

    def _toggle_todo_show_all(self) -> None:
        self._todo_show_all = not self._todo_show_all
        self.refresh()

    def _open_todo_kebab_menu(self) -> None:
        menu = QMenu(self)
        mark_act = menu.addAction("Mark all done")
        mark_act.triggered.connect(self._mark_all_done)
        clear_act = menu.addAction("Clear all")
        clear_act.triggered.connect(self._confirm_clear_all)
        menu.exec(self._todo_kebab_btn.mapToGlobal(QPoint(0, self._todo_kebab_btn.height())))

    def _pending_row(self, p: dict) -> QWidget:
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 1, 0, 1)
        rl.setSpacing(9)
        cb = QCheckBox()
        cb.setCursor(Qt.CursorShape.PointingHandCursor)
        cb.toggled.connect(lambda checked, mid=p["meeting_id"], idx=p["idx"]: self._mark_done(mid, idx, checked))
        lbl = QLabel()
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        owner = f' &middot; <b style="color:{self.theme.color("primary")};">{_html.escape(p["owner"])}</b>' if p["owner"] else ""
        src = f' <span style="color:{self.theme.color("text_faint")};">— {_html.escape(p["title"])}</span>'
        from .page_detail import _md_to_html  # escapes, then **bold** → <b>
        lbl.setText(_md_to_html(p["task"]) + owner + src)
        rl.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)
        rl.addWidget(lbl, 1)
        rl.addWidget(self._due_chip_btn(p), 0, Qt.AlignmentFlag.AlignTop)
        return row

    def _due_chip_btn(self, p: dict) -> QPushButton:
        """Same clickable due affordance as the detail page: a coloured pill
        when a due date is set, or a subtle "+ date" mini button when absent."""
        from ..util.dues import due_label, due_severity

        mid, idx = p["meeting_id"], p["idx"]
        if p.get("due"):
            severity_colors = {
                "overdue": ("danger", "danger_soft"),
                "today": ("warning", "warning_soft"),
                "future": ("text_muted", "surface_hover"),
            }
            sev = due_severity(p["due"])
            fg_role, bg_role = severity_colors.get(sev, ("text_muted", "surface_hover"))
            fg, bg = self.theme.color(fg_role), self.theme.color(bg_role)
            btn = QPushButton(due_label(p["due"]))
            btn.setToolTip("Change due date")
        else:
            fg, bg = self.theme.color("text_faint"), self.theme.color("surface_hover")
            btn = QPushButton("+ date")
            btn.setToolTip("Set due date")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton{{background:{bg}; color:{fg}; border:none; border-radius:9px;"
            f"padding:3px 10px; font-size:12px; font-weight:600;}}"
            f"QPushButton:hover{{background:{self.theme.color('surface_press')};}}"
        )
        btn.clicked.connect(lambda _=False, mid=mid, idx=idx, cur=p.get("due"): self._pick_pending_due(mid, idx, cur))
        return btn

    def _pick_pending_due(self, meeting_id: int, idx: int, current: str | None) -> None:
        from .widgets import pick_due_date
        iso, accepted = pick_due_date(self, self.theme, current)
        if not accepted:
            return
        self._set_due(meeting_id, idx, iso)

    def _mark_done(self, meeting_id: int, idx: int, checked: bool) -> None:
        if not checked:
            return
        m = self.repo.get(meeting_id)
        notes = m.notes or {}
        actions = notes.get("action_items") or []
        if 0 <= idx < len(actions):
            actions[idx]["done"] = True
            notes["action_items"] = actions
            self.repo.update(meeting_id, notes_json=json.dumps(notes))
        QTimer.singleShot(0, self.refresh)  # rebuild after the signal settles

    def _set_due(self, meeting_id: int, idx: int, iso_or_none: str | None) -> None:
        m = self.repo.get(meeting_id)
        notes = m.notes or {}
        actions = notes.get("action_items") or []
        if 0 <= idx < len(actions) and isinstance(actions[idx], dict):
            actions[idx]["due"] = iso_or_none
            notes["action_items"] = actions
            self.repo.update(meeting_id, notes_json=json.dumps(notes))
        QTimer.singleShot(0, self.refresh)

    # ---------- collapsible "Meetings" section ----------
    def _meetings_header(self, n: int) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("file", self.theme.color("primary"), 16))
        lay.addWidget(ic)
        t = QLabel("Meetings")
        t.setObjectName("H3")
        lay.addWidget(t)
        cnt = QLabel(f"({n})")
        cnt.setObjectName("Faint")
        lay.addWidget(cnt)
        lay.addStretch(1)
        chevron = QPushButton()
        chevron.setProperty("variant", "ghost")
        chevron.setProperty("iconbtn", True)
        chevron.setCursor(Qt.CursorShape.PointingHandCursor)
        chevron.setFixedSize(28, 28)
        chevron.clicked.connect(self._toggle_meetings)
        self._meetings_chevron_btn = chevron
        lay.addWidget(chevron)
        self._set_meetings_chevron_icon()
        return w

    def _toggle_meetings(self) -> None:
        self.cfg.meetings_collapsed = not self.cfg.meetings_collapsed
        self.cfg.save()
        host = getattr(self, "_meetings_host", None)
        if host is not None:
            host.setVisible(not self.cfg.meetings_collapsed)
        self._set_meetings_chevron_icon()

    def _set_meetings_chevron_icon(self) -> None:
        btn = getattr(self, "_meetings_chevron_btn", None)
        if btn is None:
            return
        collapsed = self.cfg.meetings_collapsed
        btn.setIcon(self.theme.icon("chevron-right" if collapsed else "chevron-down", "text_muted", 16))
        btn.setToolTip("Expand meetings" if collapsed else "Collapse meetings")

    def _toggle_dashboard(self) -> None:
        self.cfg.dashboard_collapsed = not self.cfg.dashboard_collapsed
        self.cfg.save()
        host = getattr(self, "_dash_items_host", None)
        if host is not None:
            host.setVisible(not self.cfg.dashboard_collapsed)
        self._set_dashboard_chevron_icon()

    def _set_dashboard_chevron_icon(self) -> None:
        btn = getattr(self, "_dash_chevron_btn", None)
        if btn is None:
            return
        collapsed = self.cfg.dashboard_collapsed
        btn.setIcon(self.theme.icon("chevron-right" if collapsed else "chevron-down", "text_muted", 16))
        btn.setToolTip("Expand" if collapsed else "Collapse")

    def _mark_all_done(self) -> None:
        """Mark every currently-listed pending item done — grouped per meeting
        (one repo.update each) so a meeting with several open items only writes once."""
        by_meeting: dict[int, list[int]] = {}
        for p in getattr(self, "_dash_pending", []):
            by_meeting.setdefault(p["meeting_id"], []).append(p["idx"])
        for mid, idxs in by_meeting.items():
            m = self.repo.get(mid)
            notes = m.notes or {}
            actions = notes.get("action_items") or []
            for idx in idxs:
                if 0 <= idx < len(actions):
                    actions[idx]["done"] = True
            notes["action_items"] = actions
            self.repo.update(mid, notes_json=json.dumps(notes))
        QTimer.singleShot(0, self.refresh)

    def _confirm_clear_all(self) -> None:
        n = len(getattr(self, "_dash_pending", []))
        if QMessageBox.question(
            self, "Clear all",
            f"Delete all {n} pending action items from their meetings?",
        ) == QMessageBox.StandardButton.Yes:
            self._clear_all_pending()

    def _clear_all_pending(self) -> None:
        """Delete every currently-listed pending item from its meeting's notes —
        grouped per meeting (one repo.update each), deleting by descending index
        within a meeting so earlier deletions don't shift later indices."""
        by_meeting: dict[int, list[int]] = {}
        for p in getattr(self, "_dash_pending", []):
            by_meeting.setdefault(p["meeting_id"], []).append(p["idx"])
        for mid, idxs in by_meeting.items():
            m = self.repo.get(mid)
            notes = m.notes or {}
            actions = notes.get("action_items") or []
            for idx in sorted(idxs, reverse=True):
                if 0 <= idx < len(actions):
                    del actions[idx]
            notes["action_items"] = actions
            self.repo.update(mid, notes_json=json.dumps(notes))
        QTimer.singleShot(0, self.refresh)

    # ---------- "AI insights" card ----------
    def _ai_insights_card(self) -> QWidget:
        card = _ClickableCard(lambda: self.shell.show_ask(), shadow=True)
        card.setMinimumHeight(120)
        outer = QVBoxLayout(card)
        outer.setContentsMargins(0, 0, 0, 0)

        backdrop = PaintedBackdrop(self.theme, variant="mini")
        outer.addWidget(backdrop)

        content = QVBoxLayout(backdrop)
        content.setContentsMargins(18, 16, 18, 16)
        content.setSpacing(6)
        content.addStretch(1)

        head = QHBoxLayout()
        head.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("sparkles", "#FFFFFF", 16))
        head.addWidget(ic)
        title = QLabel("AI insights")
        title.setStyleSheet("color:#FFFFFF; font-size:15px; font-weight:700;")
        head.addWidget(title)
        head.addStretch(1)
        content.addLayout(head)

        sub = QLabel("Catch key decisions, action items and themes across all meetings.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:rgba(255,255,255,0.82); font-size:12px;")
        content.addWidget(sub)
        content.addStretch(1)

        return card

    # ---------- responsive rail ----------
    def _apply_rail_visibility(self) -> None:
        """Reflow, never hide: narrow windows (vertical monitors) stack the
        rail full-width UNDER the meeting list — the to-dos must stay reachable."""
        narrow = self.width() < _RAIL_BREAKPOINT
        if narrow:
            self.columns.setDirection(QBoxLayout.Direction.TopToBottom)
            self.rail.setMinimumWidth(0)
            self.rail.setMaximumWidth(16777215)
            # stacked: the list takes its natural height and any leftover page
            # space falls BELOW the rail — otherwise a collapsed meeting list
            # leaves a huge gap between its header and the To do card
            self.columns.setStretch(0, 0)
            if self._columns_tail is None:
                self.columns.addStretch(1)
                self._columns_tail = self.columns.itemAt(self.columns.count() - 1)
        else:
            self.columns.setDirection(QBoxLayout.Direction.LeftToRight)
            self.rail.setFixedWidth(_RAIL_WIDTH)
            self.columns.setStretch(0, 1)
            if self._columns_tail is not None:
                self.columns.removeItem(self._columns_tail)
                self._columns_tail = None
        self.rail.setVisible(True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_rail_visibility()

    def apply_theme(self) -> None:
        self.new_btn.setIcon(self.theme.icon("record", "on_primary", 16))
        self.empty_icon.setPixmap(icons.pixmap("mic", self.theme.color("text_faint"), 56))
        backdrop = getattr(self, "_hero_backdrop", None)
        if backdrop is not None:
            backdrop.update()
