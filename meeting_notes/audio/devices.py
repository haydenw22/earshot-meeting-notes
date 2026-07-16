"""Audio device model + platform dispatch for device enumeration.

The user picks one microphone (their voice, "me") and one system-audio source
(the other party, "them"). Selections are persisted by NAME and re-resolved to
a live device each session (indices reshuffle across replugs). Windows
enumerates WASAPI devices via PyAudioWPatch, including the output-loopback
endpoints; macOS enumerates PortAudio input devices via sounddevice and
exposes a single synthetic "System audio" entry backed by the bundled
earshot-audiotap helper.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional


@dataclass
class AudioDevice:
    index: int
    name: str
    channels: int
    default_samplerate: int
    is_loopback: bool
    is_default: bool = False

    def short_name(self) -> str:
        # Loopback names come through as "Speakers (...) [Loopback]"; keep it readable.
        return self.name


def resolve_by_name(name: Optional[str], devices: list[AudioDevice]) -> Optional[AudioDevice]:
    """Re-bind a saved device name to a current device (indices change across sessions)."""
    if not name:
        return None
    for d in devices:
        if d.name == name:
            return d
    return None


# Platform backends sit below the shared model so their `from .devices import
# AudioDevice` resolves against the already-initialised half of this module.
if sys.platform == "darwin":
    from ._devices_mac import (  # noqa: E402,F401
        default_input,
        default_loopback,
        list_input_devices,
        list_loopback_devices,
    )
else:
    from ._devices_win import (  # noqa: E402,F401
        default_input,
        default_loopback,
        list_input_devices,
        list_loopback_devices,
    )
