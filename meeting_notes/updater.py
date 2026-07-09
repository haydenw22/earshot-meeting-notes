"""In-app auto-updater.

On startup the packaged app asks GitHub for the latest release; if it's newer
than the running version we surface a dialog with the changelog and a
"Download & install" button. Clicking it downloads EarshotSetup.exe and runs it
silently to replace the app in place.

The meeting database is NEVER touched by an update: the installer only writes to
%LOCALAPPDATA%\\Programs\\Earshot (the program files), while recordings and the
database live under %LOCALAPPDATA%\\Earshot (the data dir). Overwriting one
leaves the other alone.

Everything here fails soft — a missing network, a GitHub hiccup or an unexpected
payload just means "no update this launch", never a crash.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass

import httpx

REPO_SLUG = "haydenw22/earshot-meeting-notes"
_RELEASES_URL = f"https://api.github.com/repos/{REPO_SLUG}/releases"
ASSET_NAME = "EarshotSetup.exe"
_UA = "Earshot-Updater"


@dataclass(frozen=True)
class UpdateInfo:
    version: str        # normalised, e.g. "0.32.0"
    notes: str          # markdown changelog covering everything newer than current
    download_url: str   # browser_download_url of EarshotSetup.exe


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

    download_url = ""
    for asset in latest.get("assets", []) or []:
        if isinstance(asset, dict) and asset.get("name") == ASSET_NAME:
            download_url = asset.get("browser_download_url") or ""
            break
    if not download_url:
        return None  # a release with no installer asset is not installable

    newer = [r for r in stable if _is_newer(r.get("tag_name", ""), current_version)]
    blocks = []
    for r in newer:
        cl = _extract_changelog(r.get("body", ""))
        blocks.append(f"## {_norm(r.get('tag_name', ''))}\n\n{cl}" if cl
                      else f"## {_norm(r.get('tag_name', ''))}")
    notes = "\n\n".join(blocks).strip() or "A new version of Earshot is available."
    return UpdateInfo(version=_norm(latest.get("tag_name", "")), notes=notes,
                      download_url=download_url)


def default_download_path() -> str:
    return os.path.join(tempfile.gettempdir(), ASSET_NAME)


def download_installer(url: str, dest_path: str, progress_cb=None, *,
                       timeout: float = 120.0, chunk: int = 262144) -> str:
    """Stream the installer to dest_path, reporting progress_cb(fraction 0..1).
    Writes to a .part file then atomically renames. Raises on failure."""
    tmp = dest_path + ".part"
    with httpx.stream("GET", url, follow_redirects=True, timeout=timeout,
                      headers={"User-Agent": _UA}) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            for data in resp.iter_bytes(chunk):
                f.write(data)
                done += len(data)
                if progress_cb and total:
                    progress_cb(min(1.0, done / total))
    os.replace(tmp, dest_path)
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
