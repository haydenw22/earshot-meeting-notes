"""Which apps are using the microphone right now?

Reads the same source Windows uses for the taskbar mic indicator: the
CapabilityAccessManager consent store. Every app that has ever accessed the mic
has a key; `LastUsedTimeStop == 0` means it is capturing RIGHT NOW. This covers
Zoom, Teams, Discord and browser-tab calls (Meet shows up as the browser exe) —
no per-app integration, no extra dependencies, no audio probing.
"""
from __future__ import annotations

import os
import sys

_CONSENT_ROOT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"

# Ourselves (and our dev interpreter) must never count as "a call".
_SELF_NAMES = {"earshot.exe", "python.exe", "pythonw.exe"}

# Friendlier labels for the usual suspects (fallback: the exe name).
_FRIENDLY = {
    "zoom.exe": "Zoom",
    "cpthost.exe": "Zoom",
    "ms-teams.exe": "Microsoft Teams",
    "teams.exe": "Microsoft Teams",
    "chrome.exe": "Chrome (browser call)",
    "msedge.exe": "Edge (browser call)",
    "firefox.exe": "Firefox (browser call)",
    "brave.exe": "Brave (browser call)",
    "discord.exe": "Discord",
    "slack.exe": "Slack",
    "webexmta.exe": "Webex",
    "atmgr.exe": "Webex",
}

# Only these ever trigger the record prompt. Games, voice assistants, dictation
# tools etc. all use the mic too — prompting for them is noise (a real user hit
# this with a game's voice chat).
_MEETING_EXES = {
    "zoom.exe", "cpthost.exe",           # Zoom (+ its in-meeting helper)
    "ms-teams.exe", "teams.exe",         # Microsoft Teams (new + classic)
    "webexmta.exe", "atmgr.exe",         # Webex
    "slack.exe",                         # Slack huddles
}
# Browsers MIGHT be a Google Meet tab — or a game, a voice note, anything.
# They're allowed to trigger, but only after sustained use (see CallWatcher).
_BROWSER_EXES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
    "opera.exe", "vivaldi.exe", "arc.exe",
}


def classify(exe: str) -> str:
    """'meeting' (Zoom/Teams/Webex/Slack) | 'browser' (possible Meet tab) |
    'other' (never prompt-worthy: games, dictation, assistants…)."""
    exe = (exe or "").lower()
    if exe in _MEETING_EXES:
        return "meeting"
    if exe in _BROWSER_EXES:
        return "browser"
    return "other"


def friendly_name(key_name: str) -> str:
    """'C:#Users#x#AppData#...#Zoom.exe' (NonPackaged key) → 'Zoom'."""
    exe = key_name.rsplit("#", 1)[-1].strip().lower()
    if not exe.endswith(".exe"):
        # packaged (Store) apps use their package family name
        return key_name.split("_", 1)[0] or key_name
    return _FRIENDLY.get(exe, exe[:-4].capitalize())


def _exe_of(key_name: str) -> str:
    return key_name.rsplit("#", 1)[-1].strip().lower()


def apps_using_microphone_classified() -> list[tuple[str, str]]:
    """(friendly_name, category) of apps currently capturing the mic, excluding
    Earshot. category ∈ meeting|browser|other. [] on error / non-Windows."""
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []
    self_exe = os.path.basename(sys.executable).lower()
    active: list[tuple[str, str]] = []

    def scan(root_path: str, packaged: bool) -> None:
        try:
            root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, root_path)
        except OSError:
            return
        with root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if not packaged and sub == "NonPackaged":
                    continue
                try:
                    with winreg.OpenKey(root, sub) as k:
                        stop, _ = winreg.QueryValueEx(k, "LastUsedTimeStop")
                except OSError:
                    continue
                if stop != 0:
                    continue
                if not packaged:
                    exe = _exe_of(sub)
                    if exe in _SELF_NAMES or exe == self_exe:
                        continue
                    active.append((friendly_name(sub), classify(exe)))
                else:
                    # packaged (Store) apps: no exe name; treat as 'other' unless
                    # it's the Store Teams package
                    cat = "meeting" if "teams" in sub.lower() else "other"
                    active.append((friendly_name(sub), cat))

    scan(_CONSENT_ROOT + r"\NonPackaged", packaged=False)
    scan(_CONSENT_ROOT, packaged=True)
    # dedupe by name, keep order
    seen: set[str] = set()
    out = []
    for name, cat in active:
        if name not in seen:
            seen.add(name)
            out.append((name, cat))
    return out


def apps_using_microphone() -> list[str]:
    """Friendly names of apps currently capturing the mic (excluding Earshot)."""
    return [name for name, _cat in apps_using_microphone_classified()]
