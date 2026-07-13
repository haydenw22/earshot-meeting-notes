"""In-app auto-updater.

On startup the packaged app asks GitHub for the latest release; if it's newer
than the running version we surface a dialog with the changelog and a
"Download & install" button. Clicking it downloads EarshotSetup.exe and runs it
silently to replace the app in place.

Downloads are verified before anything is executed: the release must ship a
SHA-256 digest asset (EarshotSetup.exe.sha256, produced by the release
workflow), the download must stay on official GitHub hosts over HTTPS, look
like a Windows executable, stay under a sanity size cap, and hash to exactly
the published digest. A release without a digest asset is treated as not
installable. Each download goes into its own freshly created private temp
directory, never a predictable shared path.

The meeting database is NEVER touched by an update: the installer only writes to
%LOCALAPPDATA%\\Programs\\Earshot (the program files), while recordings and the
database live under %LOCALAPPDATA%\\Earshot (the data dir). Overwriting one
leaves the other alone.

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
ASSET_NAME = "EarshotSetup.exe"
ASSET_DIGEST_NAME = ASSET_NAME + ".sha256"
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
    """Auto-update only applies to the packaged Windows build (a dev checkout has
    no installer to run, and shouldn't be nagged)."""
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


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
                        if not data.startswith(b"MZ"):  # PE executable magic
                            raise ValueError("update blocked: response is not a Windows installer")
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
    """Launch the downloaded installer silently and DETACHED so it outlives this
    process and can replace it. /SILENT shows a progress window but no wizard;
    the installer closes Earshot, installs, then relaunches it. The caller must
    quit the app immediately after so no files stay locked."""
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    subprocess.Popen([installer_path, "/SILENT"], close_fds=True,
                     creationflags=creationflags)
