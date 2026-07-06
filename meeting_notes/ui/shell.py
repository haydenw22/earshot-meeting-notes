"""Application shell: a sidebar (logo, New-recording CTA, search, nav, folders
tree, meetings list, theme toggle) plus a stacked content area. Coordinates
navigation and broadcasts theme changes to every page.
"""
from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QMimeData,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from . import icons, logo
from .folder_dialog import ask_new_folder, ask_rename_folder
from .page_account import AccountPage
from .page_ask import AskPage
from .page_detail import DetailPage
from .page_home import HomePage
from .page_integrations import IntegrationsPage
from .page_record import RecordPage
from .page_settings import SettingsPage
from .widgets import FOLDER_COLORS

_MEETING_MIME = "application/x-earshot-meeting"


class _MeetingList(QListWidget):
    """The unfiled-meetings list — draggable onto a folder in the tree above,
    and a valid drop target for a meeting dragged out of a folder (= unfile).

    Content-sized like _FolderTree: it lives with the projects tree inside ONE
    sidebar scroll column, so it reports its content height instead of
    scrolling internally (twin cramped scrollbars was the failure mode)."""

    meeting_dropped = Signal(int, object)  # meeting_id, folder_id (always None here)

    _MIN_H = 34  # ~one row

    def __init__(self):
        super().__init__()
        self._content_h = self._MIN_H
        pol = self.sizePolicy()
        pol.setVerticalPolicy(QSizePolicy.Policy.Preferred)
        self.setSizePolicy(pol)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def set_content_height(self, h: int) -> None:
        h = max(self._MIN_H, h)
        if h != self._content_h:
            self._content_h = h
            self.setMaximumHeight(h)
            self.updateGeometry()

    def sizeHint(self):  # noqa: N802 (Qt override)
        return QSize(super().sizeHint().width(), self._content_h)

    def minimumSizeHint(self):  # noqa: N802 (Qt override)
        return QSize(super().minimumSizeHint().width(), self._MIN_H)

    def mimeTypes(self) -> list[str]:
        return [_MEETING_MIME]

    def mimeData(self, items):  # noqa: N802 (Qt override)
        mid = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        mime = QMimeData()
        if mid is not None:
            mime.setData(_MEETING_MIME, str(int(mid)).encode("utf-8"))
        return mime

    def dragEnterEvent(self, event):  # noqa: N802 (Qt override)
        if event.mimeData().hasFormat(_MEETING_MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):  # noqa: N802 (Qt override)
        if event.mimeData().hasFormat(_MEETING_MIME):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):  # noqa: N802 (Qt override)
        mime = event.mimeData()
        if not mime.hasFormat(_MEETING_MIME):
            super().dropEvent(event)
            return
        try:
            mid = int(bytes(mime.data(_MEETING_MIME)).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            event.ignore()
            return
        self.meeting_dropped.emit(mid, None)
        event.acceptProposedAction()


class _FolderTree(QTreeWidget):
    """The folders tree — accepts a dropped meeting id (files it), and lets a
    meeting child be dragged back out (unfiles it).

    Sizes itself to its content (mockup: it flows in the sidebar, no boxed
    empty space) but only as a PREFERENCE — when the window is short the
    sidebar column is over-constrained, and demanding the height (fixed/min)
    makes Qt's min-clamp paint the tree over the widgets below it. Preferred
    height + content-capped maximum shrinks gracefully instead (the tree
    scrolls internally)."""

    meeting_dropped = Signal(int, object)  # meeting_id, folder_id (None = unfile)

    _MIN_H = 34  # ~one row: what we shrink to under pressure
    # effectively uncapped: the tree sits in the sidebar's single scroll column
    # now, so overflow scrolls at the COLUMN level instead of inside the tree
    _MAX_CONTENT_H = 100000

    def __init__(self):
        super().__init__()
        self._content_h = self._MIN_H
        pol = self.sizePolicy()
        pol.setVerticalPolicy(QSizePolicy.Policy.Preferred)
        self.setSizePolicy(pol)

    def set_content_height(self, h: int) -> None:
        h = max(self._MIN_H, min(h, self._MAX_CONTENT_H))
        if h != self._content_h:
            self._content_h = h
            self.setMaximumHeight(h)  # never taller than the content
            self.updateGeometry()

    def sizeHint(self):  # noqa: N802 (Qt override)
        return QSize(super().sizeHint().width(), self._content_h)

    def minimumSizeHint(self):  # noqa: N802 (Qt override)
        return QSize(super().minimumSizeHint().width(), self._MIN_H)

    def mimeTypes(self) -> list[str]:
        return [_MEETING_MIME]

    def mimeData(self, items):  # noqa: N802 (Qt override)
        mime = QMimeData()
        if items:
            kind, payload = items[0].data(0, Qt.ItemDataRole.UserRole)
            if kind == "meeting":
                mime.setData(_MEETING_MIME, str(int(payload)).encode("utf-8"))
        return mime

    def dragEnterEvent(self, event):  # noqa: N802 (Qt override)
        if event.mimeData().hasFormat(_MEETING_MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):  # noqa: N802 (Qt override)
        if event.mimeData().hasFormat(_MEETING_MIME):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):  # noqa: N802 (Qt override)
        mime = event.mimeData()
        if not mime.hasFormat(_MEETING_MIME):
            super().dropEvent(event)
            return
        try:
            mid = int(bytes(mime.data(_MEETING_MIME)).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            event.ignore()
            return
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        item = self.itemAt(pos)
        folder_id = None
        if item is not None:
            kind, payload = item.data(0, Qt.ItemDataRole.UserRole)
            if kind == "folder":
                folder_id = payload
            elif kind == "meeting":
                parent = item.parent()
                if parent is not None:
                    pkind, ppayload = parent.data(0, Qt.ItemDataRole.UserRole)
                    if pkind == "folder":
                        folder_id = ppayload
        if folder_id is not None:
            self.meeting_dropped.emit(mid, folder_id)
            event.acceptProposedAction()
        else:
            event.ignore()


class ThemeToggleSlider(QWidget):
    """A ~64x30 pill switch replacing the old text theme button: a sun glyph on
    the left, a moon glyph on the right, and a 24px knob that slides left (light)
    or right (dark). The knob's position is a real Qt property so a
    QPropertyAnimation can tween it smoothly instead of jump-cutting."""

    _WIDTH = 64
    _HEIGHT = 30
    _KNOB = 24
    _MARGIN = 3

    def __init__(self, theme, on_toggle, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._on_toggle = on_toggle
        self._knob_pos = 0.0  # 0.0 = left (light), 1.0 = right (dark)
        self.setFixedSize(self._WIDTH, self._HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Switch light/dark theme")
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._sync_to_mode(animate=False)

    # ---- animated float property (knob position, 0..1) ----
    def _get_knob_pos(self) -> float:
        return self._knob_pos

    def _set_knob_pos(self, value: float) -> None:
        self._knob_pos = value
        self.update()

    knobPos = Property(float, _get_knob_pos, _set_knob_pos)  # noqa: N815 (Qt property naming)

    def _sync_to_mode(self, *, animate: bool = True) -> None:
        target = 1.0 if self.theme.mode == "dark" else 0.0
        if not animate:
            self._anim.stop()
            self._knob_pos = target
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(self._knob_pos)
        self._anim.setEndValue(target)
        self._anim.start()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint() if hasattr(event, "position") else event.pos()
        ):
            self._on_toggle()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        track_rect = QRectF(0, 0, self._WIDTH, self._HEIGHT)
        painter.setPen(QColor(self.theme.color("border_strong")))
        painter.setBrush(QColor(self.theme.color("surface_hover")))
        painter.drawRoundedRect(track_rect, self._HEIGHT / 2, self._HEIGHT / 2)

        # sun (left) + moon (right) glyphs, muted so the knob reads as the focus
        muted = self.theme.color("text_faint")
        sun_pm = icons.pixmap("sun", muted, 14)
        moon_pm = icons.pixmap("moon", muted, 14)
        painter.drawPixmap(int(self._MARGIN + 2), int((self._HEIGHT - 14) / 2), sun_pm)
        painter.drawPixmap(int(self._WIDTH - self._MARGIN - 16), int((self._HEIGHT - 14) / 2), moon_pm)

        # knob: slides between the left rest position and the right rest position
        travel = self._WIDTH - self._KNOB - 2 * self._MARGIN
        knob_x = self._MARGIN + self._knob_pos * travel
        knob_y = (self._HEIGHT - self._KNOB) / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.theme.color("primary")))
        painter.drawEllipse(QRectF(knob_x, knob_y, self._KNOB, self._KNOB))
        painter.end()

    def apply_theme(self) -> None:
        """Called on every theme flip: re-tints via repaint and (re)animates the
        knob to the new side."""
        self._sync_to_mode(animate=True)
        self.update()


class AccountCard(QFrame):
    """Full-width flat hover-highlight row: avatar initial, name, 'Local account'
    sub-label, chevron — click navigates to the Account page."""

    def __init__(self, cfg, theme, on_click, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.theme = theme
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("AccountCard")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(10)

        self.avatar = QLabel()
        self.avatar.setFixedSize(32, 32)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.avatar)

        texts = QVBoxLayout()
        texts.setSpacing(0)
        self.name_lbl = QLabel()
        self.name_lbl.setObjectName("H3")
        texts.addWidget(self.name_lbl)
        self.sub_lbl = QLabel("Local account")
        self.sub_lbl.setObjectName("Faint")
        texts.addWidget(self.sub_lbl)
        lay.addLayout(texts, 1)

        self.chevron = QLabel()
        lay.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignVCenter)

        self.refresh()

    def refresh(self) -> None:
        if self.cfg.account_mode == "cloud" and (self.cfg.cloud_email or "").strip():
            # signed into Earshot Plus: show who, and that this is the paid tier
            name = (self.cfg.account_name or "").strip() or self.cfg.cloud_email.split("@")[0]
            self.sub_lbl.setText("Earshot Plus")
        else:
            name = (self.cfg.account_name or "").strip() or "Guest"
            self.sub_lbl.setText("Local account")
        self.name_lbl.setText(name)
        self.avatar.setStyleSheet(
            f"background:{self.theme.color('primary_soft')}; color:{self.theme.color('primary')};"
            f"border-radius:16px; font-size:13px; font-weight:700;"
        )
        self.avatar.setText(name[0].upper())
        self.chevron.setPixmap(icons.pixmap("chevron-right", self.theme.color("text_faint"), 14))
        self.setStyleSheet(
            "#AccountCard{background:transparent; border:none; border-radius:10px;}"
            f"#AccountCard:hover{{background:{self.theme.color('surface_hover')};}}"
        )

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint() if hasattr(event, "position") else event.pos()
        ):
            self._on_click()
        super().mouseReleaseEvent(event)


class Shell(QMainWindow):
    def __init__(self, repo, cfg, theme):
        super().__init__()
        self.repo = repo
        self.cfg = cfg
        self.theme = theme
        self.setWindowTitle("Earshot")
        self.resize(1100, 720)
        self.setMinimumSize(880, 600)

        self.home = HomePage(self, repo, cfg, theme)
        self.record = RecordPage(self, repo, cfg, theme)
        self.detail = DetailPage(self, repo, cfg, theme)
        self.settings = SettingsPage(self, repo, cfg, theme)
        self.ask = AskPage(self, repo, cfg, theme)
        self.integrations = IntegrationsPage(self, repo, cfg, theme)
        self.account = AccountPage(self, repo, cfg, theme)

        self._build()
        self.theme.changed.connect(self._on_theme_changed)
        self.notify_data_changed()
        self.show_home()
        # after the window is up, quietly salvage any recording a crash left behind
        QTimer.singleShot(900, self._salvage_interrupted)
        # NB: the first-run setup wizard is launched from app.py BEFORE this
        # window is shown (mandatory, app-not-in-background). run_onboarding()
        # remains for the Settings → "Run setup guide again" path.

        # call auto-detection: offer to record when another app starts using the mic
        from .call_watcher import CallWatcher
        self._call_toast = None
        self.call_watcher = CallWatcher(self)
        self.call_watcher.call_started.connect(self._on_call_started)
        self.call_watcher.call_ended.connect(self._on_call_ended)
        self.call_watcher.start()

    # ---------- construction ----------
    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.sidebar = self._sidebar()
        self.stack = QStackedWidget()
        for page in (self.home, self.record, self.detail, self.settings, self.ask,
                     self.integrations, self.account):
            self.stack.addWidget(page)

        # A splitter makes the sidebar drag-resizable; its order flips for L/R.
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("MainSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)
        self._arrange_splitter()
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        row.addWidget(self.splitter)

    def _arrange_splitter(self) -> None:
        """(Re)order sidebar + content for the configured side and apply width."""
        w = max(180, int(self.cfg.sidebar_width or 258))
        side = "right" if self.cfg.sidebar_side == "right" else "left"
        self.sidebar.setParent(None)
        self.stack.setParent(None)
        if side == "left":
            self.splitter.addWidget(self.sidebar)
            self.splitter.addWidget(self.stack)
            self._sidebar_index = 0
            self.splitter.setStretchFactor(0, 0)
            self.splitter.setStretchFactor(1, 1)
            self.splitter.setSizes([w, max(480, self.width() - w)])
        else:
            self.splitter.addWidget(self.stack)
            self.splitter.addWidget(self.sidebar)
            self._sidebar_index = 1
            self.splitter.setStretchFactor(0, 1)
            self.splitter.setStretchFactor(1, 0)
            self.splitter.setSizes([max(480, self.width() - w), w])
        # border on the inner edge of the sidebar
        self.sidebar.setProperty("side", side)
        self.sidebar.style().unpolish(self.sidebar)
        self.sidebar.style().polish(self.sidebar)

    def _on_splitter_moved(self, *_) -> None:
        sizes = self.splitter.sizes()
        if self._sidebar_index < len(sizes):
            self.cfg.sidebar_width = max(180, sizes[self._sidebar_index])
            self.cfg.save()

    def set_sidebar_side(self, side: str) -> None:
        side = "right" if side == "right" else "left"
        if side == self.cfg.sidebar_side:
            return
        self.cfg.sidebar_side = side
        self.cfg.save()
        self._arrange_splitter()

    def _sidebar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Sidebar")
        bar.setMinimumWidth(190)
        bar.setMaximumWidth(640)
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(16, 18, 16, 16)
        lay.setSpacing(12)

        # logo + title
        head = QHBoxLayout()
        head.setSpacing(10)
        self.logo = QLabel()
        self.logo.setFixedSize(34, 34)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Earshot")
        title.setObjectName("H2")
        head.addWidget(self.logo)
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)

        # new recording CTA
        self.new_btn = QPushButton("  New recording")
        self.new_btn.setProperty("variant", "danger")
        self.new_btn.setMinimumHeight(44)
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_btn.clicked.connect(self.show_record)
        lay.addWidget(self.new_btn)
        # live-recording state for the CTA (see set_recording)
        self._rec_pulse = QTimer(self)
        self._rec_pulse.setInterval(600)
        self._rec_pulse.timeout.connect(self._pulse_record_icon)
        self._pulse_on = True

        # import an existing recording
        self.import_btn = QPushButton("  Import file")
        self.import_btn.setProperty("variant", "ghost")
        self.import_btn.setMinimumHeight(38)
        self.import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_btn.clicked.connect(self._import_file)
        lay.addWidget(self.import_btn)

        # search
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search meetings…")
        self.search.textChanged.connect(self._filter_list)
        self.search_action = self.search.addAction(
            icons.icon("search", self.theme.color("text_faint"), 16),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        lay.addWidget(self.search)

        # nav: home + ask
        self.home_btn = self._nav_button("Overview", self.show_home)
        lay.addWidget(self.home_btn)
        self.ask_btn = self._nav_button("Ask Earshot", self.show_ask)
        lay.addWidget(self.ask_btn)

        # ---- middle column: PROJECTS + MEETING NOTES in ONE scroll area ----
        # Both sections size to their content and the column scrolls as a
        # whole — two widgets fighting for height gave cramped twin
        # scrollbars on short windows. The host must report its content
        # height as its MINIMUM: QScrollArea(widgetResizable) compresses the
        # widget to minimumSizeHint before it ever scrolls, which would
        # min-clamp-overlap the sections all over again.
        class _MidHost(QWidget):
            def minimumSizeHint(inner):  # noqa: N805 (Qt override, local class)
                return QSize(0, inner.sizeHint().height())

        mid_host = _MidHost()
        mid_lay = QVBoxLayout(mid_host)
        mid_lay.setContentsMargins(0, 0, 0, 0)
        mid_lay.setSpacing(8)

        # ---- PROJECTS section (internal name/keys/DB stay folder_*) ----
        folders_head = QHBoxLayout()
        folders_head.setSpacing(4)
        folders_sect = QLabel("PROJECTS")
        folders_sect.setObjectName("SectionLabel")
        folders_head.addWidget(folders_sect)
        folders_head.addStretch(1)
        self.new_folder_btn = QToolButton()
        self.new_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_folder_btn.setToolTip("New project")
        self.new_folder_btn.clicked.connect(self._new_folder)
        folders_head.addWidget(self.new_folder_btn)
        self.folders_chevron_btn = QToolButton()
        self.folders_chevron_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folders_chevron_btn.clicked.connect(self._toggle_folders_collapsed)
        folders_head.addWidget(self.folders_chevron_btn)
        mid_lay.addLayout(folders_head)

        self.folder_tree = _FolderTree()
        self.folder_tree.setObjectName("SidebarTree")
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.setIndentation(14)
        self.folder_tree.setRootIsDecorated(True)
        self.folder_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.folder_tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # rows are IDENTICAL to the MEETING NOTES list below: one line, elided,
        # same pill height — filed and unfiled meetings must read the same
        self.folder_tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.folder_tree.setWordWrap(False)
        self.folder_tree.setUniformRowHeights(True)
        self.folder_tree.itemClicked.connect(self._on_tree_click)
        self.folder_tree.itemExpanded.connect(lambda _i: self._queue_fit_folder_tree())
        self.folder_tree.itemCollapsed.connect(lambda _i: self._queue_fit_folder_tree())
        self.folder_tree.installEventFilter(self)
        self.folder_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.folder_tree.customContextMenuRequested.connect(self._on_folder_context_menu)
        # drag & drop: a meeting dragged out of a folder lands back on the list
        self.folder_tree.setDragEnabled(True)
        self.folder_tree.setAcceptDrops(True)
        self.folder_tree.setDropIndicatorShown(True)
        self.folder_tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.folder_tree.setDragDropMode(QTreeWidget.DragDropMode.DragDrop)
        self.folder_tree.meeting_dropped.connect(self._on_meeting_dropped_on_folder)
        mid_lay.addWidget(self.folder_tree)

        sect = QLabel("MEETING NOTES")
        sect.setObjectName("SectionLabel")
        mid_lay.addWidget(sect)

        self.meeting_list = _MeetingList()
        self.meeting_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.meeting_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.meeting_list.itemClicked.connect(self._on_list_click)
        # drag & drop: a meeting dragged here from a folder becomes unfiled
        self.meeting_list.setDragEnabled(True)
        self.meeting_list.setAcceptDrops(True)
        self.meeting_list.setDropIndicatorShown(True)
        self.meeting_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.meeting_list.setDragDropMode(QListWidget.DragDropMode.DragDrop)
        self.meeting_list.meeting_dropped.connect(self._on_meeting_dropped_on_folder)
        mid_lay.addWidget(self.meeting_list)
        mid_lay.addStretch(1)

        self.sidebar_scroll = QScrollArea()
        self.sidebar_scroll.setObjectName("SidebarScroll")
        self.sidebar_scroll.setWidgetResizable(True)
        self.sidebar_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar_scroll.setWidget(mid_host)
        lay.addWidget(self.sidebar_scroll, 1)

        # bottom cluster (mockup order): Integrations -> Settings -> theme
        # toggle -> account card -> version label
        self.integrations_btn = self._nav_button("Integrations", self.show_integrations)
        lay.addWidget(self.integrations_btn)
        self.settings_btn = self._nav_button("Settings", self.show_settings)
        lay.addWidget(self.settings_btn)

        theme_row = QHBoxLayout()
        theme_row.addStretch(1)
        self.theme_toggle = ThemeToggleSlider(self.theme, self._toggle_theme)
        theme_row.addWidget(self.theme_toggle)
        theme_row.addStretch(1)
        lay.addLayout(theme_row)

        self.account_card = AccountCard(self.cfg, self.theme, self.show_account)
        lay.addWidget(self.account_card)

        ver = QLabel(f"v{__version__}")
        ver.setObjectName("Faint")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(ver)

        self._refresh_sidebar_icons()
        return bar

    def _nav_button(self, text: str, slot) -> QPushButton:
        b = QPushButton("  " + text)
        b.setProperty("variant", "ghost")
        b.setCheckable(True)
        b.setMinimumHeight(40)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(slot)
        return b

    # ---------- navigation ----------
    def _set_active(self, which: str) -> None:
        self.home_btn.setChecked(which == "home")
        self.ask_btn.setChecked(which == "ask")
        self.settings_btn.setChecked(which == "settings")
        self.integrations_btn.setChecked(which == "integrations")

    def _clear_selections(self) -> None:
        """Selection hygiene: only one of {list, tree} should ever show a
        highlighted row at a time."""
        if hasattr(self, "meeting_list"):
            self.meeting_list.clearSelection()
        if hasattr(self, "folder_tree"):
            self.folder_tree.clearSelection()

    def show_home(self) -> None:
        self.home.refresh()
        self.stack.setCurrentWidget(self.home)
        self._set_active("home")
        self._clear_selections()

    def show_ask(self) -> None:
        self.ask.on_shown()
        self.stack.setCurrentWidget(self.ask)
        self._set_active("ask")
        self._clear_selections()

    def show_record(self) -> None:
        self.record.on_shown()
        self.stack.setCurrentWidget(self.record)
        self._set_active("record")
        self._clear_selections()

    def show_settings(self) -> None:
        self.settings.apply_theme()
        self.stack.setCurrentWidget(self.settings)
        self._set_active("settings")
        self._clear_selections()

    def show_integrations(self) -> None:
        self.integrations.apply_theme()
        self.stack.setCurrentWidget(self.integrations)
        self._set_active("integrations")
        self._clear_selections()

    def show_account(self) -> None:
        self.account.refresh()
        self.stack.setCurrentWidget(self.account)
        self._set_active("none")
        self._clear_selections()

    def on_account_changed(self) -> None:
        """Called after signing in to / out of Earshot Plus: rebuild the Settings
        tabs (Transcription/AI hide in cloud mode) and refresh the sidebar card."""
        if hasattr(self.settings, "refresh_tabs"):
            self.settings.refresh_tabs()
        self.refresh_account_card()

    @staticmethod
    def _headless() -> bool:
        """True under the offscreen Qt platform (tests / CI) — where auto-opening
        a modal wizard would block the event loop."""
        import os

        return os.environ.get("QT_QPA_PLATFORM", "") == "offscreen"

    def run_onboarding(self) -> None:
        """Re-run the setup wizard from Settings. Non-mandatory here (the user
        already finished it once) — closable like any dialog. After it closes,
        reflect any account/settings changes it made."""
        from .onboarding import OnboardingDialog
        dlg = OnboardingDialog(self, self.cfg, self.theme, shell=self)
        dlg.exec()
        self.on_account_changed()
        if self.stack.currentWidget() is self.account:
            self.account.refresh()

    def refresh_account_card(self) -> None:
        """Called after the account display name changes (from the Account page)
        so the sidebar card reflects it immediately."""
        if hasattr(self, "account_card"):
            self.account_card.refresh()

    def open_meeting(self, meeting_id: int, *, _from_tree: bool = False) -> None:
        self.detail.load(int(meeting_id))
        self.stack.setCurrentWidget(self.detail)
        self._set_active("none")
        # whichever widget triggered the open keeps its own selection; clear the other
        if _from_tree:
            if hasattr(self, "meeting_list"):
                self.meeting_list.clearSelection()
        else:
            if hasattr(self, "folder_tree"):
                self.folder_tree.clearSelection()

    # ---------- call auto-detection ----------
    def _on_call_started(self, apps: list) -> None:
        if not self.cfg.call_detect_enabled or self.record.is_busy():
            return
        from .call_watcher import CallToast
        who = apps[0] if apps else "another app"
        self._call_toast = CallToast(
            f"Looks like a call just started in {who}. Record it with Earshot?",
            accept_text="Record now",
            on_accept=self._record_from_prompt,
            on_dismiss=self.call_watcher.snooze_until_idle,
        )
        self._call_toast.show_toast()

    def _record_from_prompt(self) -> None:
        self.show_record()          # loads devices / defaults
        self.record._start()        # start immediately with the saved defaults
        self.raise_()

    def _on_call_ended(self) -> None:
        if not (self.record.recorder and self.record.recorder.running):
            return
        from .call_watcher import CallToast
        self._call_toast = CallToast(
            "The call seems to have ended. Stop recording and process the meeting?",
            accept_text="Stop & process",
            on_accept=self.record._stop,
        )
        self._call_toast.show_toast()

    # ---------- crash salvage ----------
    def _salvage_interrupted(self) -> None:
        """If a previous session died mid-recording, its raw spool files are
        still in the meeting folder — stream them into proper WAVs so the
        meeting is recoverable instead of lost."""
        from pathlib import Path

        from ..audio import writer as wr
        from ..paths import recordings_dir
        from .workers import FuncWorker

        try:
            root = recordings_dir()
            jobs = []
            for m in self.repo.list():
                folder = Path(m.audio_dir) if m.audio_dir else (root / f"meeting_{m.id:06d}")
                if (folder / wr.SIDECAR).exists():
                    jobs.append((m.id, folder))
        except Exception:
            return  # salvage is best-effort — never crash startup over it
        if not jobs:
            return
        repo = self.repo

        def job(progress):
            recovered = []
            for mid, folder in jobs:
                progress(f"Recovering interrupted recording…")
                try:
                    res = wr.salvage_spool(folder)
                except Exception:
                    continue
                if res and res.get("frames"):
                    repo.update(
                        mid, audio_dir=str(folder), duration_secs=res["duration_secs"],
                        status="Recorded",
                        error="Recovered after an interrupted session — open it and Re-transcribe.",
                    )
                    recovered.append(mid)
            return recovered

        self._salvage_worker = FuncWorker(job)
        self._salvage_worker.done.connect(lambda _r: self.notify_data_changed())
        self._salvage_worker.start()

    # ---------- import an existing file ----------
    def _import_file(self) -> None:
        import os
        import shutil

        from PySide6.QtWidgets import QFileDialog

        from .. import paths
        from ..util.dates import today_pair
        from .workers import FuncWorker

        path, _sel = QFileDialog.getOpenFileName(
            self, "Import audio or video file", "",
            "Media files (*.mp3 *.wav *.m4a *.mp4 *.mov *.aac *.flac *.ogg *.opus *.webm *.mkv);;All files (*.*)",
        )
        if not path:
            return

        human, iso = today_pair()
        base = os.path.splitext(os.path.basename(path))[0]
        meeting = self.repo.create(date_text=human, date_iso=iso, attendees=[], agenda="")
        self.repo.update(meeting.id, title=f"Imported — {base}")
        mdir = paths.meeting_dir(meeting.id)
        os.makedirs(mdir, exist_ok=True)
        dest = os.path.join(mdir, "import" + os.path.splitext(path)[1].lower())
        try:
            shutil.copy2(path, dest)
        except OSError as e:
            self.repo.delete(meeting.id)
            QMessageBox.critical(self, "Import failed", f"Could not copy the file: {e}")
            return
        self.repo.update(meeting.id, audio_dir=mdir)
        self.notify_data_changed()
        self.open_meeting(meeting.id)

        repo, cfg, mid = self.repo, self.cfg, meeting.id

        def job(progress):
            from ..pipeline.processing import process_imported_file
            process_imported_file(repo, mid, cfg, dest, progress=progress, summarize=cfg.auto_summary)
            return mid

        self._import_worker = FuncWorker(job)
        self._import_worker.progress.connect(
            lambda msg, m=mid: self.detail.status_label.setText(msg) if self.detail.meeting_id == m else None
        )
        self._import_worker.done.connect(lambda _r, m=mid: self._on_import_done(m))
        self._import_worker.failed.connect(self._on_import_failed)
        self._import_worker.start()

    def _on_import_done(self, mid: int) -> None:
        self.notify_data_changed()
        if self.detail.meeting_id == mid:
            self.detail.refresh()

    def _on_import_failed(self, msg: str) -> None:
        self.notify_data_changed()
        QMessageBox.critical(self, "Import failed", msg)

    # ---------- data + theme ----------
    def notify_data_changed(self) -> None:
        self._rebuild_list()
        if self.stack.currentWidget() is self.home:
            self.home.refresh()

    def _rebuild_list(self) -> None:
        # remember which folders were expanded so a rebuild (e.g. after a plain
        # rename elsewhere) doesn't visually collapse everything
        expanded_ids = self._expanded_folder_ids()

        all_meetings = self.repo.list()
        folders = self.repo.list_folders()
        by_folder: dict[int, list] = {}
        unfiled = []
        for m in all_meetings:
            if m.folder_id is not None:
                by_folder.setdefault(m.folder_id, []).append(m)
            else:
                unfiled.append(m)

        self.folder_tree.clear()
        for f in folders:
            count = len(by_folder.get(f.id, []))
            folder_item = QTreeWidgetItem([f"{f.name} ({count})"])
            folder_item.setIcon(0, icons.icon("folder", f.color, 16))
            folder_item.setData(0, Qt.ItemDataRole.UserRole, ("folder", f.id))
            self.folder_tree.addTopLevelItem(folder_item)
            for m in by_folder.get(f.id, []):
                child = QTreeWidgetItem([m.title or "Untitled meeting"])
                child.setIcon(0, icons.icon("file", self.theme.color("text_muted"), 16))
                child.setData(0, Qt.ItemDataRole.UserRole, ("meeting", m.id))
                folder_item.addChild(child)
            folder_item.setExpanded(f.id in expanded_ids if expanded_ids is not None else True)

        self.meeting_list.clear()
        for m in unfiled:
            item = QListWidgetItem(m.title or "Untitled meeting")
            item.setData(Qt.ItemDataRole.UserRole, m.id)
            item.setIcon(icons.icon("file", self.theme.color("text_muted"), 16))
            self.meeting_list.addItem(item)

        self._apply_folders_collapsed()
        self._filter_list(self.search.text())

    def _expanded_folder_ids(self) -> set | None:
        """The set of currently-expanded folder ids, or None on first build
        (no items yet) so callers can default new/first-seen folders to expanded."""
        if self.folder_tree.topLevelItemCount() == 0:
            return None
        out = set()
        for i in range(self.folder_tree.topLevelItemCount()):
            item = self.folder_tree.topLevelItem(i)
            if item.isExpanded():
                _kind, fid = item.data(0, Qt.ItemDataRole.UserRole)
                out.add(fid)
        return out

    def _apply_folders_collapsed(self) -> None:
        # the tree itself is hidden when collapsed OR simply empty of folders
        # (spec: header stays visible so [+] is discoverable, only the tree hides)
        collapsed = bool(self.cfg.folders_collapsed)
        has_folders = self.folder_tree.topLevelItemCount() > 0
        self.folder_tree.setVisible(has_folders and not collapsed)
        self._set_folders_chevron_icon()
        self._queue_fit_folder_tree()

    def _queue_fit_folder_tree(self) -> None:
        # after the pending relayout, so wrapped-row heights are final
        QTimer.singleShot(0, self._fit_folder_tree_height)

    def _fit_folder_tree_height(self) -> None:
        """Measure the tree's content and feed it to the tree's preferred
        height (see _FolderTree: preference, not a demand)."""
        t = self.folder_tree
        t.doItemsLayout()  # re-query wrap heights at the current width
        h = 0
        for i in range(t.topLevelItemCount()):
            top = t.topLevelItem(i)
            h += t.visualItemRect(top).height()
            if top.isExpanded():
                for j in range(top.childCount()):
                    h += t.visualItemRect(top.child(j)).height()
        t.set_content_height(h + 8)

    def eventFilter(self, obj, event):
        # sidebar splitter drags change the wrap width -> row heights change
        if obj is self.folder_tree and event.type() == QEvent.Type.Resize:
            self._queue_fit_folder_tree()
        return super().eventFilter(obj, event)

    def _toggle_folders_collapsed(self) -> None:
        self.cfg.folders_collapsed = not self.cfg.folders_collapsed
        self.cfg.save()
        self._apply_folders_collapsed()

    def _set_folders_chevron_icon(self) -> None:
        collapsed = bool(self.cfg.folders_collapsed)
        self.folders_chevron_btn.setIcon(
            icons.icon("chevron-right" if collapsed else "chevron-down", self.theme.color("text_muted"), 14)
        )
        self.folders_chevron_btn.setToolTip("Expand projects" if collapsed else "Collapse projects")

    def _filter_list(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            for i in range(self.meeting_list.count()):
                self.meeting_list.item(i).setHidden(False)
            for i in range(self.folder_tree.topLevelItemCount()):
                folder_item = self.folder_tree.topLevelItem(i)
                folder_item.setHidden(False)
                for j in range(folder_item.childCount()):
                    folder_item.child(j).setHidden(False)
            self._queue_fit_sidebar()
            return
        # full-text match across transcript + notes + attendees + agenda, plus
        # a plain title substring so partial words still narrow the list live.
        match_ids = set(self.repo.search(text))
        low = text.lower()
        for i in range(self.meeting_list.count()):
            item = self.meeting_list.item(i)
            mid = item.data(Qt.ItemDataRole.UserRole)
            visible = (mid in match_ids) or (low in item.text().lower())
            item.setHidden(not visible)
        for i in range(self.folder_tree.topLevelItemCount()):
            folder_item = self.folder_tree.topLevelItem(i)
            any_visible = False
            for j in range(folder_item.childCount()):
                child = folder_item.child(j)
                mid = child.data(0, Qt.ItemDataRole.UserRole)[1]
                visible = (mid in match_ids) or (low in child.text(0).lower())
                child.setHidden(not visible)
                any_visible = any_visible or visible
            folder_item.setHidden(not any_visible)
        self._queue_fit_sidebar()

    def _queue_fit_sidebar(self) -> None:
        """Re-fit BOTH content-sized sidebar sections after the pending relayout."""
        self._queue_fit_folder_tree()
        QTimer.singleShot(0, self._fit_meeting_list_height)

    def _fit_meeting_list_height(self) -> None:
        """Size the unfiled list to its visible content — it scrolls at the
        sidebar-column level, never internally."""
        lst = self.meeting_list
        lst.doItemsLayout()
        h = 0
        for i in range(lst.count()):
            item = lst.item(i)
            if not item.isHidden():
                h += lst.visualItemRect(item).height()
        lst.set_content_height(h + 6)

    def _on_list_click(self, item: QListWidgetItem) -> None:
        mid = item.data(Qt.ItemDataRole.UserRole)
        if mid is not None:
            self.open_meeting(int(mid))

    def _on_tree_click(self, item: QTreeWidgetItem, _col: int) -> None:
        kind, payload = item.data(0, Qt.ItemDataRole.UserRole)
        if kind == "meeting":
            self.open_meeting(int(payload), _from_tree=True)
        # a folder row just expands/collapses (Qt's default behaviour) — nothing else to do

    # ---------- folders ----------
    def _new_folder(self) -> None:
        result = ask_new_folder(self, self.theme)
        if result is None:
            return
        name, color = result
        self.repo.create_folder(name, color)
        self.notify_data_changed()

    def _on_folder_context_menu(self, pos: QPoint) -> None:
        item = self.folder_tree.itemAt(pos)
        if item is None:
            return
        kind, payload = item.data(0, Qt.ItemDataRole.UserRole)
        if kind != "folder":
            return
        folder_id = payload
        folders = {f.id: f for f in self.repo.list_folders()}
        folder = folders.get(folder_id)
        if folder is None:
            return

        menu = QMenu(self)
        rename_act = menu.addAction("Rename…")
        color_menu = menu.addMenu("Colour")
        color_actions = {}
        for name, hexcolor in FOLDER_COLORS:
            act = color_menu.addAction(self._color_icon(hexcolor), name)
            color_actions[act] = hexcolor
        menu.addSeparator()
        delete_act = menu.addAction("Delete project")

        chosen = menu.exec(self.folder_tree.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is rename_act:
            self._rename_folder(folder_id, folder.name)
        elif chosen is delete_act:
            self._delete_folder(folder_id, folder.name)
        elif chosen in color_actions:
            self.repo.update_folder(folder_id, color=color_actions[chosen])
            self.notify_data_changed()

    @staticmethod
    def _color_icon(hexcolor: str) -> QIcon:
        """A small rounded coloured-square QIcon — used for the folder colour
        submenu (each swatch shows its own colour, not the app's neutral icon set)."""
        pm = QPixmap(14, 14)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setBrush(QColor(hexcolor))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, 14, 14, 3, 3)
        painter.end()
        return QIcon(pm)

    def _rename_folder(self, folder_id: int, current_name: str) -> None:
        new_name = ask_rename_folder(self, self.theme, current_name)
        if new_name is None:
            return
        self.repo.update_folder(folder_id, name=new_name)
        self.notify_data_changed()

    def _delete_folder(self, folder_id: int, name: str) -> None:
        if QMessageBox.question(
            self, "Delete project",
            f"Delete “{name}”? Its meetings are kept and become unfiled."
        ) != QMessageBox.StandardButton.Yes:
            return
        self.repo.delete_folder(folder_id)
        self.notify_data_changed()

    def _on_meeting_dropped_on_folder(self, meeting_id: int, folder_id) -> None:
        self.repo.update(int(meeting_id), folder_id=folder_id)
        self.notify_data_changed()

    def _toggle_theme(self) -> None:
        self.theme.toggle()

    def _on_theme_changed(self, _mode: str) -> None:
        self._refresh_sidebar_icons()
        self._rebuild_list()
        for page in (self.home, self.record, self.detail, self.settings, self.ask,
                     self.integrations, self.account):
            page.apply_theme()
        # rebuild colour-dependent content
        self.home.refresh()
        if self.detail.meeting_id is not None:
            self.detail.refresh()

    def set_recording(self, active: bool) -> None:
        """Reflect a live recording in the sidebar CTA: the label flips to
        'Recording' and the record dot pulses until the recording stops."""
        if active:
            self.new_btn.setText("  Recording")
            self._pulse_on = True
            self._rec_pulse.start()
        else:
            self._rec_pulse.stop()
            self.new_btn.setText("  New recording")
            self.new_btn.setIcon(icons.icon("record", self.theme.color("on_danger"), 16))

    def _pulse_record_icon(self) -> None:
        self._pulse_on = not self._pulse_on
        color = self.theme.color("on_danger") if self._pulse_on else self.theme.color("danger_press")
        self.new_btn.setIcon(icons.icon("record", color, 16))

    def _refresh_sidebar_icons(self) -> None:
        # brand mark (the indigo tile is part of the SVG, so no QSS background)
        self.logo.setPixmap(logo.logo_pixmap(34))
        self.logo.setStyleSheet("")
        if not self._rec_pulse.isActive():
            self.new_btn.setIcon(icons.icon("record", self.theme.color("on_danger"), 16))
        self.import_btn.setIcon(icons.icon("upload", self.theme.color("text_muted"), 16))
        self.home_btn.setIcon(icons.icon("home", self.theme.color("text_muted"), 18))
        self.ask_btn.setIcon(icons.icon("message", self.theme.color("text_muted"), 18))
        self.integrations_btn.setIcon(icons.icon("zap", self.theme.color("text_muted"), 18))
        self.settings_btn.setIcon(icons.icon("settings", self.theme.color("text_muted"), 18))
        self.new_folder_btn.setIcon(icons.icon("plus", self.theme.color("text_muted"), 14))
        self._set_folders_chevron_icon()
        if hasattr(self, "theme_toggle"):
            self.theme_toggle.apply_theme()
        if hasattr(self, "account_card"):
            self.account_card.refresh()
        if hasattr(self, "search_action"):
            self.search_action.setIcon(icons.icon("search", self.theme.color("text_faint"), 16))

    # ---------- close guard ----------
    def closeEvent(self, event):
        from . import workers
        if self.record.is_busy() or workers.active_count():
            if QMessageBox.question(
                self, "Work in progress",
                "A recording or background task (transcription, summary, import…) is still "
                "running and will be interrupted. Quit anyway?"
            ) != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.record._hide_overlay()  # don't leave the floating overlay behind
        event.accept()
