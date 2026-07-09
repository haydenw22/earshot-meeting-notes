"""Auto-updater: version parsing, GitHub release check (via httpx.MockTransport),
changelog extraction, and the update dialog builds offscreen.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_updater.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from meeting_notes import updater  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


def _release(tag, body="", *, asset=True, draft=False, prerelease=False):
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "body": body,
        "assets": ([{"name": "EarshotSetup.exe",
                     "browser_download_url": f"https://dl.example/{tag}/EarshotSetup.exe"}]
                   if asset else [{"name": "Source code (zip)", "browser_download_url": "x"}]),
    }


# A release body in the exact shape packaging/release_notes.py produces.
BODY_0320 = (
    "**Install:** download `EarshotSetup.exe` below and run it. SmartScreen may warn.\n\n"
    "**Self-host free forever**, or subscribe to Earshot Plus.\n\n"
    "---\n\n"
    "## What's new in 0.32.0\n\n"
    "### Added\n\n"
    "- In-app auto-updates: Earshot checks for a new version on launch.\n\n"
    "---\n\n"
    "Full version history: [CHANGELOG.md](https://github.com/x/blob/main/CHANGELOG.md)"
)

RELEASES = [
    _release("v0.33.0", "unreleased", draft=True),        # draft -> ignored
    _release("v0.33.0-rc1", "rc", prerelease=True),       # prerelease -> ignored
    _release("v0.32.0", BODY_0320),                       # the newest STABLE
    _release("v0.31.1", "### Fixed\n\n- A sign-in crash."),
    _release("v0.31.0", "### Added\n\n- Ask history."),
    _release("v0.25.1", "First public release."),
]


def _transport(releases, *, status=200, raise_exc=None):
    def handler(request):
        if raise_exc is not None:
            raise raise_exc
        return httpx.Response(status, json=releases)
    return httpx.MockTransport(handler)


def main() -> int:
    print("== parse_version ==")
    check("0.32.1 -> (0,32,1)", updater.parse_version("0.32.1") == (0, 32, 1))
    check("v-prefix stripped", updater.parse_version("v1.2") == (1, 2))
    check("empty -> ()", updater.parse_version("") == ())
    check("0.31.0 < 0.31.1", updater.parse_version("0.31.0") < updater.parse_version("0.31.1"))
    check("0.9.0 < 0.10.0 (numeric, not lexical)",
          updater.parse_version("0.9.0") < updater.parse_version("0.10.0"))

    print("== _extract_changelog strips preamble + footer ==")
    cl = updater._extract_changelog(BODY_0320)
    check("keeps the changelog body", "In-app auto-updates" in cl)
    check("drops the install preamble", "Install:" not in cl)
    check("drops the footer", "Full version history" not in cl)

    print("== check_for_update ==")
    info = updater.check_for_update("0.31.0", transport=_transport(RELEASES))
    check("update detected from 0.31.0", info is not None)
    check("latest is 0.32.0 (draft + prerelease ignored)", bool(info) and info.version == "0.32.0")
    check("download url is the 0.32.0 installer asset",
          bool(info) and info.download_url == "https://dl.example/v0.32.0/EarshotSetup.exe")
    check("notes cover the target version", bool(info) and "0.32.0" in info.notes)
    check("notes also cover intermediate 0.31.1", bool(info) and "0.31.1" in info.notes)
    check("notes drop the install preamble", bool(info) and "Install:" not in info.notes)

    check("no update when already on the latest",
          updater.check_for_update("0.32.0", transport=_transport(RELEASES)) is None)
    check("no update when ahead of the latest",
          updater.check_for_update("1.0.0", transport=_transport(RELEASES)) is None)
    check("no update on an HTTP error (fails soft)",
          updater.check_for_update("0.1.0", transport=_transport(RELEASES, status=500)) is None)
    check("no update on a network error (fails soft)",
          updater.check_for_update("0.1.0",
              transport=_transport(RELEASES, raise_exc=httpx.ConnectError("offline"))) is None)
    check("no update when the latest has no installer asset",
          updater.check_for_update("0.1.0", transport=_transport([_release("v9.0.0", asset=False)])) is None)

    print("== is_supported (dev checkout is not frozen) ==")
    check("auto-update off in a dev checkout", updater.is_supported() is False)

    print("== UpdateDialog builds offscreen ==")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from meeting_notes.ui.update_dialog import UpdateDialog

    class _Theme:
        def color(self, _name):
            return "#f43f5e"

    dlg = UpdateDialog(None, _Theme(),
                       updater.UpdateInfo("0.32.0", "## 0.32.0\n\n### Added\n\n- Stuff.",
                                          "https://dl.example/x.exe"))
    check("dialog exposes the Download & install button",
          dlg.install_btn.text() == "Download & install")
    check("dialog exposes a Later button", dlg.later_btn.text() == "Later")
    check("progress bar hidden until download starts", not dlg.progress.isVisible())

    print("\nUPDATER TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
