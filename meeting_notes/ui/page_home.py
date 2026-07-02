"""Home / library page: a greeting, a New-recording CTA, and the meetings as
clickable cards (or a friendly empty state when there are none yet).
"""
from __future__ import annotations

import html as _html
import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..storage.repository import Meeting
from . import icons
from .widgets import Card, add_shadow, status_chip


class MeetingCard(Card):
    def __init__(self, meeting: Meeting, theme, on_click):
        super().__init__(shadow=True)
        self._on_click = on_click
        self._mid = meeting.id
        self.setProperty("clickable", True)  # QSS: accent border on hover
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(14)

        icon = QLabel()
        icon.setPixmap(icons.pixmap("file", theme.color("primary"), 22))
        icon.setFixedWidth(28)
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(icon)

        mid = QVBoxLayout()
        mid.setSpacing(4)
        title = QLabel(meeting.title or "Untitled meeting")
        title.setObjectName("H3")
        title.setTextFormat(Qt.TextFormat.PlainText)  # AI-generated title → no rich text
        title.setWordWrap(True)
        mid.addWidget(title)
        bits = [meeting.date_text or meeting.date_iso]
        if meeting.attendees:
            bits.append(", ".join(meeting.attendees[:4]) + ("…" if len(meeting.attendees) > 4 else ""))
        if meeting.duration_secs:
            bits.append(f"{int(meeting.duration_secs // 60)}m")
        meta = QLabel("   ·   ".join(b for b in bits if b))
        meta.setObjectName("Faint")
        meta.setWordWrap(True)
        mid.addWidget(meta)
        lay.addLayout(mid, 1)

        lay.addWidget(status_chip(meeting.status, theme), alignment=Qt.AlignmentFlag.AlignVCenter)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self._on_click(self._mid)
        super().mouseReleaseEvent(event)


class HomePage(QWidget):
    def __init__(self, shell, repo, cfg, theme):
        super().__init__()
        self.shell = shell
        self.repo = repo
        self.cfg = cfg
        self.theme = theme
        # folder filter for the card list: None = all, an int = that folder,
        # "unfiled" = meetings with no folder
        self._folder_filter = None
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
        self.new_btn.setProperty("variant", "danger")
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_btn.setMinimumHeight(42)
        self.new_btn.clicked.connect(lambda: self.shell.show_record())
        header.addWidget(self.new_btn, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_host = QWidget()
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(2, 2, 2, 2)
        self.list_lay.setSpacing(12)
        self.scroll.setWidget(self.list_host)
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
        cta.setProperty("variant", "danger")
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
        # clear list — detach synchronously (deleteLater alone leaves orphan
        # widgets parented to the container until the event loop runs).
        while self.list_lay.count():
            item = self.list_lay.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        meetings = self.repo.list()
        folders = self.repo.list_folders()
        # an emptied/deleted folder can no longer be the active filter
        if isinstance(self._folder_filter, int) and self._folder_filter not in {f.id for f in folders}:
            self._folder_filter = None
        shown = self._filtered_meetings(meetings)
        if self._folder_filter is None:
            self.count.setText(f"{len(meetings)} recorded" if meetings else "Nothing recorded yet")
        else:
            label = self._filter_label(folders)
            self.count.setText(f"{len(shown)} in {label}" if shown else f"Nothing recorded in {label} yet")
        has = bool(meetings)
        self.scroll.setVisible(has)
        self.empty.setVisible(not has)
        if has:
            if self.cfg.show_dashboard:
                pending = self._gather_pending(meetings)
                if pending:
                    self.list_lay.addWidget(self._dashboard_card(pending))
            if folders:
                self.list_lay.addWidget(self._chip_row(folders, meetings))
            for m in shown:
                self.list_lay.addWidget(MeetingCard(m, self.theme, self.shell.open_meeting))
            self.list_lay.addStretch(1)
        self.apply_theme()

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
            f"QPushButton{{background:{bg}; color:{fg}; border:none; border-radius:9px;"
            f"padding:5px 12px; font-size:12px; font-weight:600;}}"
            f"QPushButton:hover{{background:{self.theme.color('primary_soft')}; color:{self.theme.color('primary')};}}"
        )
        btn.clicked.connect(lambda _=False, v=value: self._set_folder_filter(v))
        return btn

    def _set_folder_filter(self, value) -> None:
        self._folder_filter = value
        self.refresh()

    # ---------- pending action-items dashboard ----------
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
                                "owner": a.get("owner"), "title": m.title or "Untitled"})
            if len(out) >= limit:
                break
        return out[:limit]

    def _dashboard_card(self, pending: list[dict]) -> QWidget:
        self._dash_pending = pending
        card = Card(shadow=True)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("check-square", self.theme.color("primary"), 18))
        t = QLabel(f"To do — {len(pending)} pending action item" + ("s" if len(pending) != 1 else ""))
        t.setObjectName("H3")
        head.addWidget(ic)
        head.addWidget(t)
        head.addStretch(1)
        mark_btn = QPushButton("Mark all done")
        mark_btn.setProperty("variant", "ghost")
        mark_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mark_btn.clicked.connect(self._mark_all_done)
        clear_btn = QPushButton("Clear all")
        clear_btn.setProperty("variant", "ghost")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._confirm_clear_all)
        chevron_btn = QPushButton()
        chevron_btn.setProperty("variant", "ghost")
        chevron_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        chevron_btn.setFixedSize(28, 28)
        chevron_btn.setToolTip("Collapse" if not self.cfg.dashboard_collapsed else "Expand")
        chevron_btn.clicked.connect(self._toggle_dashboard)
        self._dash_chevron_btn = chevron_btn
        head.addWidget(mark_btn)
        head.addWidget(clear_btn)
        head.addWidget(chevron_btn)
        lay.addLayout(head)

        items_host = QWidget()
        items_lay = QVBoxLayout(items_host)
        items_lay.setContentsMargins(0, 0, 0, 0)
        items_lay.setSpacing(8)
        for p in pending:
            items_lay.addWidget(self._pending_row(p))
        items_host.setVisible(not self.cfg.dashboard_collapsed)
        self._dash_items_host = items_host
        lay.addWidget(items_host)

        self._set_dashboard_chevron_icon()
        return card

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
        open_btn = QPushButton("Open")
        open_btn.setProperty("variant", "ghost")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(lambda _=False, mid=p["meeting_id"]: self.shell.open_meeting(mid))
        rl.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)
        rl.addWidget(lbl, 1)
        rl.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignTop)
        return row

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

    def apply_theme(self) -> None:
        self.new_btn.setIcon(self.theme.icon("record", "on_danger", 16))
        self.empty_icon.setPixmap(icons.pixmap("mic", self.theme.color("text_faint"), 56))
