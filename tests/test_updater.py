"""Auto-updater: version parsing, GitHub release check (via httpx.MockTransport),
changelog extraction, download hardening (digest/host/size/type verification,
cancellation) and the update dialog's thread lifecycle.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_updater.py
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from meeting_notes import updater  # noqa: E402

# This file exercises the WINDOWS update flow; pin the platform constants so it
# passes identically on any OS (a no-op on Windows). The darwin flow has its
# own coverage in test_updater_mac.py.
(updater.ASSET_NAME, updater.ASSET_DIGEST_NAME,
 updater._MAGIC, updater._MAGIC_ERR) = updater._platform_assets("win32")


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


_DL_BASE = "https://github.com/haydenw22/earshot-meeting-notes/releases/download"


def _release(tag, body="", *, asset=True, digest=True, draft=False, prerelease=False):
    assets = []
    if asset:
        assets.append({"name": "EarshotSetup.exe",
                       "browser_download_url": f"{_DL_BASE}/{tag}/EarshotSetup.exe"})
        if digest:
            assets.append({"name": "EarshotSetup.exe.sha256",
                           "browser_download_url": f"{_DL_BASE}/{tag}/EarshotSetup.exe.sha256"})
    else:
        assets.append({"name": "Source code (zip)", "browser_download_url": "x"})
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease, "body": body,
            "assets": assets}


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


def _file_transport(content: bytes, *, status=200, headers=None):
    """Serve `content` for any request (the installer-download stand-in)."""
    def handler(request):
        return httpx.Response(status, content=content, headers=headers or {})
    return httpx.MockTransport(handler)


EXE = b"MZ" + b"earshot-installer-payload" * 100
EXE_SHA = hashlib.sha256(EXE).hexdigest()


def test_check_for_update():
    print("== check_for_update ==")
    info = updater.check_for_update("0.31.0", transport=_transport(RELEASES))
    check("update detected from 0.31.0", info is not None)
    check("latest is 0.32.0 (draft + prerelease ignored)", bool(info) and info.version == "0.32.0")
    check("download url is the 0.32.0 installer asset",
          bool(info) and info.download_url == f"{_DL_BASE}/v0.32.0/EarshotSetup.exe")
    check("digest url is the 0.32.0 sha256 asset",
          bool(info) and info.digest_url == f"{_DL_BASE}/v0.32.0/EarshotSetup.exe.sha256")
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
    check("no update when the installer has no sha256 digest asset (unverifiable)",
          updater.check_for_update("0.1.0",
              transport=_transport([_release("v9.0.0", digest=False)])) is None)
    bad = _release("v9.0.0")
    bad["assets"][0]["browser_download_url"] = "https://evil.example/EarshotSetup.exe"
    check("no update when the installer asset URL is off-GitHub",
          updater.check_for_update("0.1.0", transport=_transport([bad])) is None)


def test_fetch_expected_sha256():
    print("== fetch_expected_sha256 ==")
    url = f"{_DL_BASE}/v1/EarshotSetup.exe.sha256"
    got = updater.fetch_expected_sha256(
        url, transport=_file_transport(f"{EXE_SHA}  EarshotSetup.exe\n".encode()))
    check("parses 'HEX  filename' sha256sum format", got == EXE_SHA)
    got = updater.fetch_expected_sha256(url, transport=_file_transport(EXE_SHA.upper().encode()))
    check("bare uppercase hex accepted (normalised)", got == EXE_SHA)
    for label, content in [("HTML error page", b"<html>404</html>"),
                           ("short hex", b"abc123"), ("empty", b"")]:
        try:
            updater.fetch_expected_sha256(url, transport=_file_transport(content))
            ok = False
        except (ValueError, IndexError):
            ok = True
        check(f"rejects {label}", ok)


def test_download_installer():
    print("== download_installer hardening ==")
    url = f"{_DL_BASE}/v1/EarshotSetup.exe"
    tmpdir = tempfile.mkdtemp(prefix="earshot_dl_test_")

    dest = os.path.join(tmpdir, "ok.exe")
    got = updater.download_installer(url, dest, expected_sha256=EXE_SHA,
                                     transport=_file_transport(EXE), chunk=64)
    check("verified download lands at dest", got == dest and Path(dest).read_bytes() == EXE)
    check("no .part file left behind", not os.path.exists(dest + ".part"))

    def expect_raise(label, **kwargs):
        d = os.path.join(tmpdir, label.replace(" ", "_") + ".exe")
        want_cancel = kwargs.pop("_want_cancel", False)
        try:
            updater.download_installer(kwargs.pop("url", url), d, **kwargs)
            ok = False
        except updater.DownloadCancelled:
            ok = want_cancel
        except Exception:
            ok = not want_cancel
        check(label, ok and not os.path.exists(d) and not os.path.exists(d + ".part"))

    expect_raise("sha256 mismatch rejected, nothing kept",
                 expected_sha256="0" * 64, transport=_file_transport(EXE))
    expect_raise("missing digest rejected", expected_sha256="",
                 transport=_file_transport(EXE))
    expect_raise("off-GitHub host rejected", url="https://evil.example/EarshotSetup.exe",
                 expected_sha256=EXE_SHA, transport=_file_transport(EXE))
    expect_raise("plain-http URL rejected",
                 url="http://github.com/x/EarshotSetup.exe",
                 expected_sha256=EXE_SHA, transport=_file_transport(EXE))
    html = b"<html>rate limited</html>"
    expect_raise("non-executable (HTML) payload rejected",
                 expected_sha256=hashlib.sha256(html).hexdigest(),
                 transport=_file_transport(html))
    expect_raise("implausibly large Content-Length rejected",
                 expected_sha256=EXE_SHA,
                 transport=_file_transport(EXE, headers={"Content-Length": str(10 * 1024 ** 3)}))

    # cooperative cancellation between chunks removes the partial file
    state = {"n": 0}
    def cancelled():
        state["n"] += 1
        return state["n"] > 2
    expect_raise("cancellation mid-download raises DownloadCancelled and cleans up",
                 expected_sha256=EXE_SHA, transport=_file_transport(EXE), chunk=16,
                 cancelled=cancelled, _want_cancel=True)

    print("== download path + cleanup ==")
    p = updater.default_download_path()
    q = updater.default_download_path()
    check("each download gets its own fresh private directory",
          os.path.dirname(p) != os.path.dirname(q) and os.path.isdir(os.path.dirname(p)))
    Path(p).write_bytes(b"x")
    updater.cleanup_download(p)
    check("cleanup removes the file and its private dir",
          not os.path.exists(p) and not os.path.isdir(os.path.dirname(p)))
    updater.cleanup_download(q)  # nothing downloaded — must not raise


def test_dialog_lifecycle():
    print("== UpdateDialog builds offscreen + safe close mid-download ==")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from meeting_notes.ui import update_dialog as ud
    from meeting_notes.ui.update_dialog import UpdateDialog

    class _Theme:
        def color(self, _name):
            return "#f43f5e"

    info = updater.UpdateInfo("0.32.0", "## 0.32.0\n\n### Added\n\n- Stuff.",
                              f"{_DL_BASE}/v0.32.0/EarshotSetup.exe",
                              f"{_DL_BASE}/v0.32.0/EarshotSetup.exe.sha256")
    dlg = UpdateDialog(None, _Theme(), info)
    check("dialog exposes the Download & install button",
          dlg.install_btn.text() == "Download & install")
    check("dialog exposes a Later button", dlg.later_btn.text() == "Later")
    check("progress bar hidden until download starts", not dlg.progress.isVisible())

    # Close the dialog while a (stalled) download is running: the app must not
    # crash, and the straggler download must NEVER launch the installer.
    release_download = threading.Event()
    launched = []
    real_download, real_fetch, real_run = (updater.download_installer,
                                           getattr(updater, "fetch_expected_sha256", None),
                                           updater.run_installer)

    def fake_download(url, dest, progress_cb=None, **kwargs):
        cancelled = kwargs.get("cancelled") or (lambda: False)
        while not release_download.wait(0.05):
            if cancelled():
                raise updater.DownloadCancelled()
        Path(dest).write_bytes(b"MZ fake")
        return dest

    updater.download_installer = fake_download
    updater.fetch_expected_sha256 = lambda _url, **_k: EXE_SHA
    updater.run_installer = lambda path: launched.append(path)
    try:
        dlg2 = UpdateDialog(None, _Theme(), info)
        dlg2._start()
        worker = dlg2.worker
        check("download worker started", worker is not None and worker.isRunning())
        t0 = time.monotonic()
        dlg2.reject()                      # user closes mid-download
        rejected_in = time.monotonic() - t0
        release_download.set()             # let a straggler finish, if it ignored the cancel
        if worker is not None:
            worker.wait(5000)
        for _ in range(20):                # drain queued signals to the main thread
            app.processEvents()
            time.sleep(0.01)
        check("worker thread exited after close", worker is None or not worker.isRunning())
        check("closing mid-download never launches the installer", launched == [])
        check("dialog close does not block the UI for seconds", rejected_in < 2.0)
    finally:
        updater.download_installer = real_download
        if real_fetch is not None:
            updater.fetch_expected_sha256 = real_fetch
        updater.run_installer = real_run


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

    test_check_for_update()
    test_fetch_expected_sha256()
    test_download_installer()

    print("== is_supported (dev checkout is not frozen) ==")
    check("auto-update off in a dev checkout", updater.is_supported() is False)

    test_dialog_lifecycle()

    print("\nUPDATER TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
