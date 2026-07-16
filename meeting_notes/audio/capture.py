"""Platform dispatch for live audio capture.

Windows records with PyAudioWPatch (mic + WASAPI output loopback); macOS
records the mic with sounddevice and system audio via the bundled
earshot-audiotap helper. Both backends expose the same DualStreamRecorder
contract and write the same raw int16 spool files + spool.json sidecar, so
everything downstream (writer, crash salvage, the pipeline) is platform-blind.
"""
from __future__ import annotations

import sys

if sys.platform == "darwin":
    from ._capture_mac import DualStreamRecorder  # noqa: F401
    from ._capture_mac import _MicStream as _Stream  # noqa: F401
else:
    # _Stream and pyaudio are re-exported because tests and tools reach into
    # capture._Stream / capture.pyaudio.paContinue.
    from ._capture_win import DualStreamRecorder, _Stream, pyaudio  # noqa: F401
