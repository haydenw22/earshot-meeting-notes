"""Render the Earshot brand mark to a multi-resolution Windows .ico for the exe.

Usage:  python tools/make_icon.py
Writes: packaging/earshot.ico
"""
from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QBuffer, QByteArray  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> int:
    QApplication.instance() or QApplication([])
    from PIL import Image

    from meeting_notes.ui import logo

    # render at exact 256px (dpr=1) -> PNG bytes -> PIL
    pm = logo.logo_pixmap(256, dpr=1.0)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    pm.save(buf, "PNG")
    buf.close()
    img = Image.open(BytesIO(bytes(ba))).convert("RGBA")

    out = Path(__file__).resolve().parent.parent / "packaging" / "earshot.ico"
    img.save(str(out), format="ICO", sizes=SIZES)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
