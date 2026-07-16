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

import os
import re

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
