"""Which apps are using the microphone right now?

Windows: reads the same source Windows uses for the taskbar mic indicator, the
CapabilityAccessManager consent store. Every app that has ever accessed the mic
has a key; `LastUsedTimeStop == 0` means it is capturing RIGHT NOW. This covers
Zoom, Teams, Discord and browser-tab calls (Meet shows up as the browser exe) —
no per-app integration, no extra dependencies, no audio probing.

macOS: there is no supported per-app equivalent an ordinary app may read, so
this is explicitly approximate. Core Audio checks every input device (not just
the default) for active capture, then the process list identifies dedicated
meeting apps that could be responsible. Browsers are deliberately excluded on
macOS because the OS cannot distinguish Meet from a voice note or unrelated
mic use; the Settings UI discloses this limitation.
"""
from __future__ import annotations

import os
import subprocess
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


# macOS process names (basename of the executable) worth prompting for.
_MAC_MEETING_PROCS = {
    "zoom.us": "Zoom",
    "MSTeams": "Microsoft Teams",
    "Teams": "Microsoft Teams",
    "Webex": "Webex",
    "Meeting Center": "Webex",
    "Slack": "Slack",
    "FaceTime": "FaceTime",
}
_MAC_BROWSER_PROCS = {
    "Google Chrome": "Chrome (browser call)",
    "Safari": "Safari (browser call)",
    "firefox": "Firefox (browser call)",
    "Microsoft Edge": "Edge (browser call)",
    "Brave Browser": "Brave (browser call)",
    "Arc": "Arc (browser call)",
    "Opera": "Opera (browser call)",
    "Vivaldi": "Vivaldi (browser call)",
}


def _mac_mic_in_use() -> bool:
    """True when any Core Audio input device is capturing in any process."""
    import ctypes
    import ctypes.util

    lib = ctypes.util.find_library("CoreAudio")
    if not lib:
        return False
    ca = ctypes.CDLL(lib)

    class _Address(ctypes.Structure):
        _fields_ = [("mSelector", ctypes.c_uint32),
                    ("mScope", ctypes.c_uint32),
                    ("mElement", ctypes.c_uint32)]

    system_object = 1  # kAudioObjectSystemObject
    scope_global = int.from_bytes(b"glob", "big")
    scope_input = int.from_bytes(b"inpt", "big")
    addr = _Address(int.from_bytes(b"dev#", "big"), scope_global, 0)  # all devices
    size = ctypes.c_uint32(0)
    if ca.AudioObjectGetPropertyDataSize(system_object, ctypes.byref(addr), 0, None,
                                         ctypes.byref(size)) != 0:
        return False
    count = size.value // ctypes.sizeof(ctypes.c_uint32)
    if not count:
        return False
    devices = (ctypes.c_uint32 * count)()
    if ca.AudioObjectGetPropertyData(system_object, ctypes.byref(addr), 0, None,
                                     ctypes.byref(size), devices) != 0:
        return False
    streams_selector = int.from_bytes(b"stm#", "big")
    running_selector = int.from_bytes(b"gone", "big")
    for device in devices:
        # Devices without input streams are outputs and cannot be microphones.
        streams = _Address(streams_selector, scope_input, 0)
        stream_size = ctypes.c_uint32(0)
        if ca.AudioObjectGetPropertyDataSize(device, ctypes.byref(streams), 0, None,
                                             ctypes.byref(stream_size)) != 0:
            continue
        if stream_size.value == 0:
            continue
        running_addr = _Address(running_selector, scope_global, 0)
        running = ctypes.c_uint32(0)
        running_size = ctypes.c_uint32(ctypes.sizeof(running))
        if ca.AudioObjectGetPropertyData(device, ctypes.byref(running_addr), 0, None,
                                         ctypes.byref(running_size), ctypes.byref(running)) == 0:
            if running.value != 0:
                return True
    return False


def _mac_apps_classified() -> list[tuple[str, str]]:
    """Approximation: any input busy + a dedicated meeting app running.

    Browsers are intentionally not returned: public macOS APIs cannot identify
    which process owns the input, so a browser entry would be especially prone
    to false prompts.
    """
    try:
        if not _mac_mic_in_use():
            return []
        procs = subprocess.run(["/bin/ps", "-axo", "comm="], capture_output=True,
                               text=True, timeout=5).stdout.splitlines()
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in procs:
        base = os.path.basename(line.strip())
        if base in _MAC_MEETING_PROCS:
            label, cat = _MAC_MEETING_PROCS[base], "meeting"
        else:
            continue
        if label not in seen:
            seen.add(label)
            out.append((label, cat))
    return out


def apps_using_microphone_classified() -> list[tuple[str, str]]:
    """(friendly_name, category) of apps currently capturing the mic, excluding
    Earshot. category ∈ meeting|browser|other. [] on error / unsupported OS."""
    if sys.platform == "darwin":
        return _mac_apps_classified()
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
