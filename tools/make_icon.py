"""Render the Earshot brand mark to the app icon files: a multi-resolution
Windows .ico for the exe and a macOS .icns for the .app bundle.

Usage:  python tools/make_icon.py
Writes: packaging/earshot.ico, packaging/earshot.icns
"""
from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QBuffer, QByteArray  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _logo_image(px: int):
    """Render the brand mark at an exact pixel size (dpr=1) into a PIL image."""
    from PIL import Image

    from meeting_notes.ui import logo

    pm = logo.logo_pixmap(px, dpr=1.0)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    pm.save(buf, "PNG")
    buf.close()
    return Image.open(BytesIO(bytes(ba))).convert("RGBA")


def main() -> int:
    QApplication.instance() or QApplication([])
    pkg = Path(__file__).resolve().parent.parent / "packaging"

    ico = pkg / "earshot.ico"
    _logo_image(256).save(str(ico), format="ICO", sizes=SIZES)
    print(f"wrote {ico} ({ico.stat().st_size} bytes)")

    # macOS: Pillow derives the standard icon sizes from a 1024px master.
    icns = pkg / "earshot.icns"
    _logo_image(1024).save(str(icns), format="ICNS")
    print(f"wrote {icns} ({icns.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
