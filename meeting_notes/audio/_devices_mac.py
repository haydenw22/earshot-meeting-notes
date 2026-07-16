"""Enumerate audio devices on macOS.

Microphones come from PortAudio via sounddevice. System audio ("them") is not
an input device on macOS: it is captured by the bundled earshot-audiotap
helper (a Core Audio process tap, macOS 14.4+), so the loopback list is a
single synthetic entry whose fixed format matches the helper's output.
"""
from __future__ import annotations

from typing import Optional

from ._capture_mac import TAP_CHANNELS, TAP_RATE
from .devices import AudioDevice

SYSTEM_AUDIO_NAME = "System audio"
SYSTEM_AUDIO_INDEX = -1  # sentinel: "use the tap helper", not a PortAudio index


def list_input_devices() -> list[AudioDevice]:
    """Real microphones / capture devices."""
    import sounddevice as sd

    try:
        infos = sd.query_devices()
    except Exception:
        return []
    try:
        default_in = sd.default.device[0]
    except Exception:
        default_in = None
    out: list[AudioDevice] = []
    seen: set[str] = set()
    for i, info in enumerate(infos):
        if int(info.get("max_input_channels", 0) or 0) < 1:
            continue
        name = info.get("name") or f"Device {i}"
        if name in seen:
            continue
        seen.add(name)
        out.append(
            AudioDevice(
                index=i,
                name=name,
                channels=int(info.get("max_input_channels", 1) or 1),
                default_samplerate=int(info.get("default_samplerate", TAP_RATE) or TAP_RATE),
                is_loopback=False,
                is_default=(i == default_in),
            )
        )
    return out


def list_loopback_devices() -> list[AudioDevice]:
    """System audio ("what they're saying"), captured by the tap helper."""
    return [
        AudioDevice(
            index=SYSTEM_AUDIO_INDEX,
            name=SYSTEM_AUDIO_NAME,
            channels=TAP_CHANNELS,
            default_samplerate=TAP_RATE,
            is_loopback=True,
            is_default=True,
        )
    ]


def default_input() -> Optional[AudioDevice]:
    devs = list_input_devices()
    for d in devs:
        if d.is_default:
            return d
    return devs[0] if devs else None


def default_loopback() -> Optional[AudioDevice]:
    devs = list_loopback_devices()
    return devs[0] if devs else None
