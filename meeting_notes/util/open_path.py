"""Open a file or folder with the OS default handler, on any platform.

Windows keeps its exact historical behaviour (os.startfile); macOS and Linux
use their standard openers. Callers treat this as fire-and-forget: a missing
target or a platform without an opener fails quietly, matching how
os.startfile call sites behaved before the macOS port.
"""
from __future__ import annotations

import os
import subprocess
import sys


def open_path(path) -> None:
    p = str(path)
    try:
        if sys.platform == "win32":
            os.startfile(p)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["/usr/bin/open", p], close_fds=True)
        else:
            subprocess.Popen(["xdg-open", p], close_fds=True)
    except OSError:
        pass
