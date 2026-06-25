"""The Earshot brand mark: an indigo squircle holding a speech bubble with a
centred soundwave. Theme-independent (the app icon is always the indigo tile).

Rendered to pixmaps for the sidebar tile, the window/taskbar icon, and the
build-time .ico generation.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

BRAND = "#6366F1"


def logo_svg() -> str:
    # Bubble spans x18..78 (centre x=48); the 5 waveform bars span x30..66,
    # also centred on x=48, and are vertically centred on the bubble (y=44).
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96">
<rect x="4" y="4" width="88" height="88" rx="22" fill="{BRAND}"/>
<rect x="18" y="24" width="60" height="40" rx="13" fill="#ffffff"/>
<path d="M32 60 L32 74 L46 60 Z" fill="#ffffff"/>
<g fill="{BRAND}">
<rect x="30" y="38" width="4" height="12" rx="2"/>
<rect x="38" y="33" width="4" height="22" rx="2"/>
<rect x="46" y="30" width="4" height="28" rx="2"/>
<rect x="54" y="35" width="4" height="18" rx="2"/>
<rect x="62" y="38" width="4" height="12" rx="2"/>
</g>
</svg>"""


def _dpr(dpr: Optional[float]) -> float:
    if dpr is not None:
        return float(dpr)
    app = QApplication.instance()
    if app is not None and app.primaryScreen() is not None:
        return float(app.primaryScreen().devicePixelRatio())
    return 1.0


def logo_pixmap(size: int, dpr: Optional[float] = None) -> QPixmap:
    ratio = _dpr(dpr)
    renderer = QSvgRenderer(QByteArray(logo_svg().encode("utf-8")))
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    return pm
