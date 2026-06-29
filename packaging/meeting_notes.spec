# PyInstaller spec — one-folder build of Meeting Notes.
#
# onedir (not onefile): the native binaries bundled here — PyAudioWPatch's
# PortAudio DLL, livekit's FFI library, libsndfile (soundfile) — are loaded by
# path at runtime and onefile's temp-extraction breaks some of them.
#
# Build:  pyinstaller packaging/meeting_notes.spec   (run from the repo root)

import os

from PyInstaller.utils.hooks import collect_all

# Resolve the project root from the spec location so the build works no matter
# what the current working directory is.
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

datas, binaries, hiddenimports = [], [], []
for pkg in ("livekit", "pyaudiowpatch", "soundfile", "soxr"):
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

# Ship the .ico as a data file too, so the running app can load it as the
# (crisp, multi-size) window/taskbar icon — not just the exe's embedded icon.
datas += [(os.path.join(ROOT, "packaging", "earshot.ico"), ".")]

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
    console=False,            # GUI app — no console window
    icon=os.path.join(ROOT, "packaging", "earshot.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Earshot",
)
