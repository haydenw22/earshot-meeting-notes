# PyInstaller spec — macOS .app bundle of Earshot (Apple Silicon, macOS 14.4+).
#
# The Windows build lives in meeting_notes.spec and is untouched by this file;
# the two differ structurally (no PyAudioWPatch here, sounddevice + the
# earshot-audiotap helper instead, plus the BUNDLE step), so they are separate
# specs rather than one file full of platform conditionals.
#
# Build:  pyinstaller packaging/earshot_mac.spec   (run from the repo root)
# Needs:  packaging/mac/bin/earshot-audiotap       (sh packaging/mac/audiotap/build.sh)
#
# Signing: set MACOS_CODESIGN_IDENTITY to a Developer ID Application identity
# to sign during the build (hardened runtime + entitlements); leave it unset
# for an ad-hoc signed local/CI build that runs with Gatekeeper's Open Anyway.

import hashlib
import json
import os
import re
import sys

import PyInstaller
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

VERSION = re.search(
    r'__version__\s*=\s*"([^"]+)"',
    open(os.path.join(ROOT, "meeting_notes", "__init__.py"), encoding="utf-8").read(),
).group(1)

datas, binaries, hiddenimports = [], [], []
for pkg in ("livekit", "sounddevice", "soundfile", "soxr"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += ["scipy.signal", "scipy.signal._sigtools", "PySide6.QtSvg", "mss", "PIL", "PIL.Image"]

# First-party modules pulled in lazily (imported inside functions), plus httpx
# (used by the webhook). Listed explicitly so the build never misses them.
hiddenimports += [
    "httpx",
    "meeting_notes.changelog",
    "meeting_notes.integrations.webhook",
    "meeting_notes.notes.actions",
    "meeting_notes.pipeline.processing",
    "meeting_notes.util.stats",
    "meeting_notes.ui.named_list",
]

# Icons: .icns for the bundle/dock, .ico kept for parity with in-app loaders.
datas += [
    (os.path.join(ROOT, "packaging", "earshot.icns"), "."),
    (os.path.join(ROOT, "packaging", "earshot.ico"), "."),
]

# The system-audio capture helper rides along inside the bundle; the app finds
# it via sys._MEIPASS (see meeting_notes/audio/_capture_mac.py).
binaries += [(os.path.join(ROOT, "packaging", "mac", "bin", "earshot-audiotap"), ".")]

_identity = os.environ.get("MACOS_CODESIGN_IDENTITY") or None
_entitlements = os.path.join(ROOT, "packaging", "mac", "entitlements.plist") if _identity else None

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    noarchive=False,
)


# Runtime fingerprint for code-only updates (see earshot_boot.py): a digest of
# everything in the bundle EXCEPT meeting_notes. Earshot-mac-code.zip only
# installs onto a bundle with the identical fingerprint, so a change to the
# Python minor version, any third-party package or native library, the audio
# helper, the boot code or this spec makes the updater ship a full app instead.
# The Python installation's own files, and files the build itself regenerates,
# are fingerprinted by name only: a different 3.12.x patch release runs the
# same pure-Python code.
def _file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _runtime_fingerprint():
    python_home = os.path.realpath(sys.base_prefix) + os.sep
    build_dir = os.path.realpath(workpath) + os.sep
    helper = os.path.join(ROOT, "packaging", "mac", "bin", "earshot-audiotap")
    helper_src = os.path.join(ROOT, "packaging", "mac", "audiotap")

    def content(path):
        if not path or not os.path.isfile(path):
            return ""
        if path == helper:  # swiftc output is not byte-reproducible: use its source
            return "|".join(_file_digest(os.path.join(helper_src, f))
                            for f in ("main.swift", "build.sh", "entitlements.plist"))
        real = os.path.realpath(path)
        if real.startswith(python_home) and "site-packages" not in real:
            return ""
        if real.startswith(build_dir):  # regenerated every build (base_library.zip)
            return ""
        return _file_digest(path)

    lines = [f"python {sys.version_info[0]}.{sys.version_info[1]}",
             f"pyinstaller {PyInstaller.__version__}"]
    for name, path, kind in sorted(a.pure):
        if name == "meeting_notes" or name.startswith("meeting_notes."):
            continue
        lines.append(f"{kind} {name} {content(path)}")
    for dest, path, kind in sorted(a.binaries + a.datas):
        lines.append(f"{kind} {dest} {content(path)}")
    for path in (os.path.join(ROOT, "main.py"), os.path.join(ROOT, "earshot_boot.py"), SPEC):
        lines.append(f"file {os.path.basename(path)} {_file_digest(path)}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest(), lines


_runtime, _runtime_lines = _runtime_fingerprint()
_runtime_json = os.path.join(workpath, "earshot_runtime.json")
os.makedirs(workpath, exist_ok=True)
with open(_runtime_json, "w", encoding="utf-8") as _fh:
    json.dump({"version": VERSION, "runtime": _runtime}, _fh, indent=2)
# What went into the fingerprint, for diffing two builds when a release
# unexpectedly needs a full app update.
with open(os.path.join(workpath, "earshot_runtime_inputs.txt"), "w", encoding="utf-8") as _fh:
    _fh.write("\n".join(_runtime_lines) + "\n")
a.datas += [("earshot_runtime.json", _runtime_json, "DATA")]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Earshot",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    target_arch="arm64",
    codesign_identity=_identity,
    entitlements_file=_entitlements,
    icon=os.path.join(ROOT, "packaging", "earshot.icns"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Earshot",
)

app = BUNDLE(
    coll,
    name="Earshot.app",
    icon=os.path.join(ROOT, "packaging", "earshot.icns"),
    bundle_identifier="app.tryearshot.earshot",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "Earshot",
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "14.4",
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription":
            "Earshot records your microphone during meetings you choose to record.",
        "NSAudioCaptureUsageDescription":
            "Earshot records system audio so the other side of your meeting "
            "is captured in your notes.",
    },
)
