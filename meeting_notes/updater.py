"""In-app auto-updater.

On startup the packaged app asks GitHub for the latest release; if it's newer
than the running version we surface a dialog with the changelog and a
"Download & install" button. On Windows that downloads EarshotSetup.exe and
runs it silently to replace the app in place; on macOS it downloads
Earshot-mac.zip, verifies it, and swaps the .app bundle before relaunching.

Downloads are verified before anything is executed: the release must ship a
SHA-256 digest asset (produced by the release workflow), the download must
stay on official GitHub hosts over HTTPS, look like the right kind of payload
for this platform, stay under a sanity size cap, and hash to exactly the
published digest. A release without a digest asset is treated as not
installable. Each download goes into its own freshly created private temp
directory, never a predictable shared path.

The meeting database is NEVER touched by an update: only the program files
(%LOCALAPPDATA%\\Programs\\Earshot on Windows, the Earshot.app bundle on
macOS) are replaced, while recordings and the database live in the separate
app-data dir. Overwriting one leaves the other alone.

Everything here fails soft — a missing network, a GitHub hiccup or an unexpected
payload just means "no update this launch", never a crash.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass

import httpx

REPO_SLUG = "haydenw22/earshot-meeting-notes"
_RELEASES_URL = f"https://api.github.com/repos/{REPO_SLUG}/releases"


def _platform_assets(platform: str = sys.platform) -> tuple[str, str, bytes, str]:
    """(asset_name, digest_name, payload_magic, magic_error) per platform."""
    if platform == "darwin":
        return ("Earshot-mac.zip", "Earshot-mac.zip.sha256", b"PK\x03\x04",
                "update blocked: response is not a macOS app archive")
    return ("EarshotSetup.exe", "EarshotSetup.exe.sha256", b"MZ",
            "update blocked: response is not a Windows installer")


ASSET_NAME, ASSET_DIGEST_NAME, _MAGIC, _MAGIC_ERR = _platform_assets()
_UA = "Earshot-Updater"
_TMP_PREFIX = "EarshotUpdate-"

# The real installer is ~150 MB; anything wildly beyond that is not our release.
MAX_INSTALLER_BYTES = 500 * 1024 * 1024


class DownloadCancelled(Exception):
    """The user closed the update dialog while the download was in flight."""


@dataclass(frozen=True)
class UpdateInfo:
    version: str        # normalised, e.g. "0.32.0"
    notes: str          # markdown changelog covering everything newer than current
    download_url: str   # browser_download_url of EarshotSetup.exe
    digest_url: str = ""  # browser_download_url of EarshotSetup.exe.sha256


def is_supported() -> bool:
    """Auto-update only applies to packaged builds (a dev checkout has no
    installer to run, and shouldn't be nagged)."""
    return bool(getattr(sys, "frozen", False)) and sys.platform in ("win32", "darwin")


def parse_version(s: str) -> tuple[int, ...]:
    """'v0.32.1' -> (0, 32, 1). Stops at the first non-numeric component; a value
    with no leading number -> ()."""
    s = (s or "").strip().lstrip("vV")
    out: list[int] = []
    for part in s.split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


def _norm(tag: str) -> str:
    return ".".join(str(n) for n in parse_version(tag))


def _is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def _host_allowed(url) -> bool:
    """Only official GitHub hosts over HTTPS may serve an update (github.com for
    the asset permalink, *.githubusercontent.com for the CDN it redirects to)."""
    try:
        u = httpx.URL(url)
    except Exception:
        return False
    if u.scheme != "https":
        return False
    host = (u.host or "").lower()
    return (host == "github.com" or host.endswith(".github.com")
            or host.endswith(".githubusercontent.com"))


def _extract_changelog(body: str) -> str:
    """Pull the human changelog out of a release body, dropping our standard
    install preamble and the 'Full version history' footer. Defensive: if the
    expected markers aren't there, return the body as-is."""
    body = (body or "").strip()
    if not body:
        return ""
    # keep from the first '### ' section heading (Added/Changed/Fixed…)
    idx = body.find("\n### ")
    if body.startswith("### "):
        idx = 0
    elif idx != -1:
        idx += 1  # skip the leading newline
    core = body[idx:] if idx != -1 else body
    foot = core.find("Full version history")
    if foot != -1:
        core = core[:foot].rstrip().rstrip("-").rstrip()
    return core.strip() or body


def check_for_update(current_version: str, *, timeout: float = 8.0,
                     transport=None) -> UpdateInfo | None:
    """Return an UpdateInfo if GitHub has a newer stable release, else None.

    `transport` lets tests inject an httpx.MockTransport. Never raises.
    """
    try:
        kwargs: dict = {
            "timeout": timeout,
            "headers": {"User-Agent": _UA, "Accept": "application/vnd.github+json"},
            "follow_redirects": True,
        }
        if transport is not None:
            kwargs["transport"] = transport
        with httpx.Client(**kwargs) as client:
            resp = client.get(_RELEASES_URL, params={"per_page": 30})
        if resp.status_code != 200:
            return None
        releases = resp.json()
    except Exception:
        return None
    if not isinstance(releases, list):
        return None

    stable = [
        r for r in releases
        if isinstance(r, dict) and not r.get("draft") and not r.get("prerelease")
        and parse_version(r.get("tag_name", ""))
    ]
    stable.sort(key=lambda r: parse_version(r.get("tag_name", "")), reverse=True)
    if not stable:
        return None

    latest = stable[0]
    if not _is_newer(latest.get("tag_name", ""), current_version):
        return None

    download_url = digest_url = ""
    for asset in latest.get("assets", []) or []:
        if not isinstance(asset, dict):
            continue
        if asset.get("name") == ASSET_NAME:
            download_url = asset.get("browser_download_url") or ""
        elif asset.get("name") == ASSET_DIGEST_NAME:
            digest_url = asset.get("browser_download_url") or ""
    # A release without an installer OR without its digest is not installable —
    # we never execute anything we can't verify.
    if not download_url or not digest_url:
        return None
    if not (_host_allowed(download_url) and _host_allowed(digest_url)):
        return None

    newer = [r for r in stable if _is_newer(r.get("tag_name", ""), current_version)]
    blocks = []
    for r in newer:
        cl = _extract_changelog(r.get("body", ""))
        blocks.append(f"## {_norm(r.get('tag_name', ''))}\n\n{cl}" if cl
                      else f"## {_norm(r.get('tag_name', ''))}")
    notes = "\n\n".join(blocks).strip() or "A new version of Earshot is available."
    return UpdateInfo(version=_norm(latest.get("tag_name", "")), notes=notes,
                      download_url=download_url, digest_url=digest_url)


def default_download_path() -> str:
    """A fresh, private per-download directory — never a predictable shared temp
    path that another process could pre-create or swap a file into."""
    return os.path.join(tempfile.mkdtemp(prefix=_TMP_PREFIX), ASSET_NAME)


def cleanup_download(path: str) -> None:
    """Best-effort removal of a (partial) download and its private temp dir."""
    for p in (path + ".part", path):
        try:
            os.unlink(p)
        except OSError:
            pass
    parent = os.path.dirname(path)
    if os.path.basename(parent).startswith(_TMP_PREFIX):
        try:
            os.rmdir(parent)
        except OSError:
            pass


def fetch_expected_sha256(url: str, *, timeout: float = 15.0, transport=None) -> str:
    """Download the release's published digest asset and return the hex digest.
    Raises if the content is not a plausible sha256sum-style line."""
    if not _host_allowed(url):
        raise ValueError("digest URL is not an official GitHub release URL")
    kwargs: dict = {"timeout": timeout, "headers": {"User-Agent": _UA},
                    "follow_redirects": True}
    if transport is not None:
        kwargs["transport"] = transport
    with httpx.Client(**kwargs) as client:
        resp = client.get(url)
    resp.raise_for_status()
    token = (resp.text or "").split()[0].strip().lower()
    if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
        raise ValueError("release digest asset is not a SHA-256 hex digest")
    return token


def download_installer(url: str, dest_path: str, progress_cb=None, *,
                       expected_sha256: str, timeout: float = 120.0,
                       chunk: int = 262144, cancelled=None, transport=None) -> str:
    """Stream the installer to dest_path, reporting progress_cb(fraction 0..1).

    Hard requirements before dest_path ever exists: HTTPS GitHub hosts only
    (including redirects), a sane size, an executable payload, and an exact
    SHA-256 match against the release's published digest. `cancelled` is polled
    between chunks; returning True raises DownloadCancelled. Writes to a .part
    file then atomically renames; any failure removes the partial file. Raises
    on failure.
    """
    expected = (expected_sha256 or "").strip().lower()
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError("no trusted SHA-256 digest for this update; not downloading")
    if not _host_allowed(url):
        raise ValueError("update blocked: not an official GitHub release URL")

    tmp = dest_path + ".part"
    kwargs: dict = {"timeout": timeout, "headers": {"User-Agent": _UA},
                    "follow_redirects": True}
    if transport is not None:
        kwargs["transport"] = transport
    digest = hashlib.sha256()
    try:
        with httpx.Client(**kwargs) as client, client.stream("GET", url) as resp:
            for hop in [*resp.history, resp]:
                if not _host_allowed(hop.url):
                    raise ValueError(f"update blocked: redirected off GitHub ({hop.url.host})")
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or 0)
            if total > MAX_INSTALLER_BYTES:
                raise ValueError("update blocked: download is implausibly large")
            done = 0
            first_bytes = True
            with open(tmp, "wb") as f:
                for data in resp.iter_bytes(chunk):
                    if cancelled is not None and cancelled():
                        raise DownloadCancelled()
                    if first_bytes and data:
                        if not data.startswith(_MAGIC):  # PE / zip payload magic
                            raise ValueError(_MAGIC_ERR)
                        first_bytes = False
                    f.write(data)
                    digest.update(data)
                    done += len(data)
                    if done > MAX_INSTALLER_BYTES:
                        raise ValueError("update blocked: download is implausibly large")
                    if progress_cb and total:
                        progress_cb(min(1.0, done / total))
        if digest.hexdigest() != expected:
            raise ValueError("update failed verification (SHA-256 mismatch); not installing")
        os.replace(tmp, dest_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if progress_cb:
        progress_cb(1.0)
    return dest_path


def run_installer(installer_path: str) -> None:
    """Kick off the platform's install step, detached so it outlives this
    process and can replace it. The caller must quit the app immediately after
    so no files stay locked.

    Windows: run the Inno installer with /SILENT (progress window, no wizard);
    it closes Earshot, installs, then relaunches it. macOS: extract the
    verified zip and hand over to a detached swap script (see
    _install_mac_update)."""
    if sys.platform == "darwin":
        _install_mac_update(installer_path)
        return
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    subprocess.Popen([installer_path, "/SILENT"], close_fds=True,
                     creationflags=creationflags)


# --------------------------------------------------------------- macOS ----

def _running_app_bundle(executable: str | None = None) -> str | None:
    """Path of the .app bundle the running executable lives in, or None when
    not inside one (dev checkout). Walks up from Contents/MacOS/Earshot."""
    path = os.path.abspath(executable or sys.executable)
    while True:
        parent = os.path.dirname(path)
        if parent == path:
            return None
        if path.endswith(".app"):
            return path
        path = parent


def _compose_install_script(pid: int, new_app: str, dest_app: str, workdir: str) -> str:
    """The shell script that performs the swap AFTER the app quits.

    Safety property: the old app is moved aside (not deleted) before the new
    one moves in, and restored if that move fails — a botched update can never
    leave the user with no app. The quarantine strip lets an unsigned update
    relaunch without Gatekeeper re-blocking it; on a notarized build it is a
    harmless no-op."""
    q = lambda s: "'" + str(s).replace("'", "'\\''") + "'"  # noqa: E731
    old = dest_app + ".old"
    return f"""#!/bin/sh
# Earshot update swap script (auto-generated; deletes itself and its workdir)
while /bin/kill -0 {int(pid)} 2>/dev/null; do sleep 0.3; done
rm -rf {q(old)}
mv {q(dest_app)} {q(old)} || exit 1
if mv {q(new_app)} {q(dest_app)}; then
    rm -rf {q(old)}
else
    /usr/bin/ditto {q(new_app)} {q(dest_app)} || {{ rm -rf {q(dest_app)}; mv {q(old)} {q(dest_app)}; exit 1; }}
    rm -rf {q(old)}
fi
/usr/bin/xattr -dr com.apple.quarantine {q(dest_app)} 2>/dev/null || true
/usr/bin/open {q(dest_app)}
rm -rf {q(workdir)}
"""


def _install_mac_update(zip_path: str) -> None:
    """Extract the verified zip and launch the detached swap script. Raises if
    the archive doesn't contain a launchable Earshot.app or we're not running
    from inside a bundle; the caller shows the error and the app keeps running
    on the current version."""
    workdir = tempfile.mkdtemp(prefix=_TMP_PREFIX)
    subprocess.run(["/usr/bin/ditto", "-x", "-k", zip_path, workdir], check=True,
                   capture_output=True)
    new_app = os.path.join(workdir, "Earshot.app")
    main_exe = os.path.join(new_app, "Contents", "MacOS", "Earshot")
    if not (os.path.isfile(main_exe) and os.access(main_exe, os.X_OK)):
        raise ValueError("update blocked: archive does not contain a launchable Earshot.app")
    dest_app = _running_app_bundle()
    if not dest_app:
        raise RuntimeError("not running from an installed Earshot.app; cannot self-update")
    script = os.path.join(workdir, "install.sh")
    with open(script, "w", encoding="utf-8") as f:
        f.write(_compose_install_script(os.getpid(), new_app, dest_app, workdir))
    os.chmod(script, 0o700)
    subprocess.Popen(["/bin/sh", script], close_fds=True, start_new_session=True)
