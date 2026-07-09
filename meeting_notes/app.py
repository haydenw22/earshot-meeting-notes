"""Application bootstrap: theme, config, repository and the main shell window."""
from __future__ import annotations

import os
import sys

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from .config import Config
from .storage.repository import MeetingRepository
from .ui import logo
from .ui.shell import Shell
from .ui.theme_controller import ThemeController


def _app_icon() -> QIcon:
    """Prefer the multi-resolution .ico (crisp at small taskbar sizes); fall back
    to the rendered SVG mark when it isn't on disk."""
    candidates = []
    base = getattr(sys, "_MEIPASS", None)  # PyInstaller bundle dir
    if base:
        candidates.append(os.path.join(base, "earshot.ico"))
    candidates.append(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packaging", "earshot.ico")
    )
    for path in candidates:
        if os.path.exists(path):
            return QIcon(path)
    return QIcon(logo.logo_pixmap(256))


def _setup_replay_flag() -> str:
    """Path of the one-shot 'replay the setup guide' flag file, next to the exe
    (frozen) or the repo root (dev). A deploy can drop this file to make the
    NEXT launch run the first-run wizard once — with all user data untouched.
    The install dir is used (not the data dir) because deploy tooling reliably
    writes there."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "run_setup_once.flag")


def _consume_replay_flag() -> bool:
    """True (once) if the replay flag exists; deletes it so it can't loop."""
    flag = _setup_replay_flag()
    if not os.path.exists(flag):
        return False
    try:
        os.remove(flag)
    except OSError:
        return False  # never risk a wizard-every-launch loop from a stuck flag
    return True


def _set_windows_app_id() -> None:
    """Give Earshot its own taskbar identity so Windows uses our icon for the
    taskbar button (and pins/groups it correctly) rather than a generic one."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Whittle.Earshot")
    except Exception:
        pass


def run() -> int:
    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("Earshot")
    app.setOrganizationName("Whittle")
    app.setFont(QFont("Segoe UI", 10))

    icon = _app_icon()
    app.setWindowIcon(icon)

    cfg = Config.load()
    from .paths import set_recordings_dir
    set_recordings_dir(cfg.data_dir)  # honour a custom recordings folder
    repo = MeetingRepository()
    repo.recover_interrupted()  # unstick meetings a previous crash left mid-flight

    theme = ThemeController(cfg)
    theme.apply()

    window = Shell(repo, cfg, theme)
    window.setWindowIcon(icon)

    # First run: the setup wizard is MANDATORY and runs before the main window
    # exists on screen — no app in the background, no way to dismiss it without
    # finishing. (Skipped headless: tests/CI have no user to walk through it.)
    # A deploy-dropped run_setup_once.flag replays it exactly once.
    if not Shell._headless() and (_consume_replay_flag() or not cfg.onboarding_done):
        from .ui.onboarding import OnboardingDialog

        wizard = OnboardingDialog(None, cfg, theme, shell=window, mandatory=True)
        wizard.setWindowIcon(icon)
        wizard.exec()
        window.on_account_changed()

    window.show()

    # Packaged Windows builds check GitHub for a newer release in the background;
    # if one is found the user gets an "Update available" dialog with the
    # changelog and a one-click download+install. Kept on the window so the
    # worker thread isn't garbage-collected mid-check. No-op for dev checkouts.
    from .ui.update_dialog import schedule_update_check
    window._update_worker = schedule_update_check(window, theme)

    return app.exec()
