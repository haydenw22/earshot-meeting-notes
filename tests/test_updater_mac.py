"""macOS auto-updater branch: platform asset selection, zip payload magic,
release filtering when mac assets are absent, bundle-path discovery, and the
swap script's safety properties. Runs on any OS (the darwin constants are
exercised via monkeypatching); the live swap integration runs on macOS only.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_updater_mac.py
"""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
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


_DL_BASE = "https://github.com/haydenw22/earshot-meeting-notes/releases/download"


def _release(tag, *, win_assets=True, mac_asset=True, mac_digest=True):
    assets = []
    if win_assets:
        assets += [
            {"name": "EarshotSetup.exe",
             "browser_download_url": f"{_DL_BASE}/{tag}/EarshotSetup.exe"},
            {"name": "EarshotSetup.exe.sha256",
             "browser_download_url": f"{_DL_BASE}/{tag}/EarshotSetup.exe.sha256"},
        ]
    if mac_asset:
        assets.append({"name": "Earshot-mac.zip",
                       "browser_download_url": f"{_DL_BASE}/{tag}/Earshot-mac.zip"})
        if mac_digest:
            assets.append({"name": "Earshot-mac.zip.sha256",
                           "browser_download_url": f"{_DL_BASE}/{tag}/Earshot-mac.zip.sha256"})
    return {"tag_name": tag, "draft": False, "prerelease": False,
            "body": "### Added\n\n- stuff\n", "assets": assets}


def _api_transport(releases):
    def handler(request):
        return httpx.Response(200, json=releases)
    return httpx.MockTransport(handler)


class _DarwinAssets:
    """Temporarily swap the module-level asset constants to their darwin values
    (what a packaged mac build computes at import time)."""

    def __enter__(self):
        self.saved = (updater.ASSET_NAME, updater.ASSET_DIGEST_NAME,
                      updater._MAGIC, updater._MAGIC_ERR)
        (updater.ASSET_NAME, updater.ASSET_DIGEST_NAME,
         updater._MAGIC, updater._MAGIC_ERR) = updater._platform_assets("darwin")
        return self

    def __exit__(self, *exc):
        (updater.ASSET_NAME, updater.ASSET_DIGEST_NAME,
         updater._MAGIC, updater._MAGIC_ERR) = self.saved
        return False


def test_platform_assets():
    print("== _platform_assets returns the right constants per platform ==")
    name, digest, magic, err = updater._platform_assets("darwin")
    check("darwin asset is Earshot-mac.zip", name == "Earshot-mac.zip")
    check("darwin digest asset name matches", digest == "Earshot-mac.zip.sha256")
    check("darwin magic is the zip signature", magic == b"PK\x03\x04")
    check("darwin magic error mentions macOS", "macOS" in err)
    name, digest, magic, err = updater._platform_assets("win32")
    check("win32 asset unchanged", name == "EarshotSetup.exe")
    check("win32 digest asset unchanged", digest == "EarshotSetup.exe.sha256")
    check("win32 magic unchanged", magic == b"MZ")


def test_check_for_update_mac_assets():
    print("== check_for_update picks the mac zip + digest under darwin constants ==")
    with _DarwinAssets():
        info = updater.check_for_update(
            "0.30.0", transport=_api_transport([_release("v0.35.0")]))
        check("update found", info is not None)
        check("download url is the mac zip", info.download_url.endswith("/Earshot-mac.zip"))
        check("digest url is the mac digest",
              info.digest_url.endswith("/Earshot-mac.zip.sha256"))

        info = updater.check_for_update(
            "0.30.0",
            transport=_api_transport([_release("v0.35.0", mac_asset=False)]))
        check("windows-only release is not installable on mac", info is None)

        info = updater.check_for_update(
            "0.30.0",
            transport=_api_transport([_release("v0.35.0", mac_digest=False)]))
        check("mac zip without digest is not installable", info is None)


def test_download_magic_mac():
    print("== download enforces the zip magic under darwin constants ==")
    zip_payload = b"PK\x03\x04" + b"fake zip content " * 100
    pe_payload = b"MZ" + b"fake exe content " * 100

    def transport_for(payload):
        def handler(request):
            return httpx.Response(200, content=payload,
                                  headers={"Content-Length": str(len(payload))})
        return httpx.MockTransport(handler)

    url = f"{_DL_BASE}/v0.35.0/Earshot-mac.zip"
    with _DarwinAssets():
        dest = os.path.join(tempfile.mkdtemp(prefix="EarshotUpdate-"), "Earshot-mac.zip")
        got = updater.download_installer(
            url, dest, expected_sha256=hashlib.sha256(zip_payload).hexdigest(),
            transport=transport_for(zip_payload))
        check("zip payload with matching digest downloads", os.path.exists(got))

        dest2 = os.path.join(tempfile.mkdtemp(prefix="EarshotUpdate-"), "Earshot-mac.zip")
        try:
            updater.download_installer(
                url, dest2, expected_sha256=hashlib.sha256(pe_payload).hexdigest(),
                transport=transport_for(pe_payload))
            check("PE payload rejected on mac", False)
        except ValueError as e:
            check("PE payload rejected on mac", "macOS app archive" in str(e))
        check("no partial file left behind", not os.path.exists(dest2 + ".part"))

        dest3 = os.path.join(tempfile.mkdtemp(prefix="EarshotUpdate-"), "Earshot-mac.zip")
        try:
            updater.download_installer(
                url, dest3, expected_sha256="0" * 64,
                transport=transport_for(zip_payload))
            check("sha mismatch rejected", False)
        except ValueError as e:
            check("sha mismatch rejected", "verification" in str(e))


def test_running_app_bundle():
    print("== _running_app_bundle walks up to the enclosing .app ==")
    got = updater._running_app_bundle("/Applications/Earshot.app/Contents/MacOS/Earshot")
    check("finds the bundle root", got == "/Applications/Earshot.app")
    got = updater._running_app_bundle("/Users/x/Apps/Earshot.app/Contents/MacOS/python3")
    check("finds nested bundle roots too", got == "/Users/x/Apps/Earshot.app")
    check("plain paths return None",
          updater._running_app_bundle("/usr/local/bin/python3") is None)


def test_compose_install_script():
    print("== swap script: wait loop, rollback, quarantine strip, relaunch ==")
    script = updater._compose_install_script(
        4242, "/tmp/work/Earshot.app", "/Applications/Earshot.app", "/tmp/work")
    check("waits for the app to exit", "/bin/kill -0 4242" in script)
    check("moves the old app aside first",
          "mv '/Applications/Earshot.app' '/Applications/Earshot.app.old'" in script)
    check("restores the old app if the swap fails",
          "mv '/Applications/Earshot.app.old' '/Applications/Earshot.app'" in script)
    check("strips quarantine so an unsigned update can relaunch",
          "xattr -dr com.apple.quarantine" in script)
    check("relaunches the installed app", "/usr/bin/open '/Applications/Earshot.app'" in script)
    check("cleans up its workdir", "rm -rf '/tmp/work'" in script)
    hostile = updater._compose_install_script(1, "/tmp/o'brien/Earshot.app",
                                              "/Applications/Earshot.app", "/tmp/o'brien")
    check("paths with quotes are shell-quoted", "'\\''" in hostile)


def _fake_app(path: Path, marker: str) -> None:
    exe = path / "Contents" / "MacOS"
    exe.mkdir(parents=True)
    main = exe / "Earshot"
    main.write_text("#!/bin/sh\nexit 0\n")
    main.chmod(main.stat().st_mode | stat.S_IEXEC)
    (path / "Contents" / "marker.txt").write_text(marker)


def test_mac_swap_integration():
    """darwin only: run the real swap script against two fake .app trees."""
    if sys.platform != "darwin":
        print("== swap integration skipped (not macOS) ==")
        return
    print("== swap integration: the script really replaces the bundle ==")
    root = Path(tempfile.mkdtemp(prefix="earshot_swap_"))
    dest = root / "Installed" / "Earshot.app"
    dest.parent.mkdir()
    _fake_app(dest, "OLD")
    work = root / "work"
    work.mkdir()
    new_app = work / "Earshot.app"
    _fake_app(new_app, "NEW")

    script = updater._compose_install_script(os.getpid(), str(new_app), str(dest), str(work))
    # Neuter the relaunch and the wait loop for the test: pid -> a dead pid,
    # open -> true. Everything else runs for real.
    script = script.replace(f"/bin/kill -0 {os.getpid()}", "/bin/kill -0 999999999")
    script = script.replace("/usr/bin/open", "/usr/bin/true")
    sh = work / "install.sh"
    sh.write_text(script)
    sh.chmod(0o700)
    subprocess.run(["/bin/sh", str(sh)], check=True, timeout=30)

    check("new app is in place",
          (dest / "Contents" / "marker.txt").read_text() == "NEW")
    check("no .old bundle left behind", not (dest.parent / "Earshot.app.old").exists())
    check("workdir cleaned up", not work.exists())


def main() -> int:
    test_platform_assets()
    test_check_for_update_mac_assets()
    test_download_magic_mac()
    test_running_app_bundle()
    test_compose_install_script()
    test_mac_swap_integration()
    print("\nMAC UPDATER TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
