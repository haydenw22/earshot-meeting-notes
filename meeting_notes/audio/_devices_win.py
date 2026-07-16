"""Enumerate WASAPI input (microphone) and output-loopback ("system audio")
devices via PyAudioWPatch.

The user picks one microphone (their voice, "me") and one output device whose
WASAPI *loopback* we capture (the other party, "them"). We expose both lists and
helpers to re-resolve a saved device *by name* back to a live PortAudio index.
"""
from __future__ import annotations

from typing import Optional

import pyaudiowpatch as pyaudio

from .devices import AudioDevice


def _wasapi_index(p: "pyaudio.PyAudio") -> Optional[int]:
    try:
        return p.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
    except OSError:
        return None


def list_input_devices() -> list[AudioDevice]:
    """Real microphones / capture devices (excludes loopback endpoints)."""
    p = pyaudio.PyAudio()
    try:
        wasapi = _wasapi_index(p)
        default_in = None
        try:
            default_in = p.get_default_input_device_info()["index"]
        except OSError:
            pass
        out: list[AudioDevice] = []
        seen: set[str] = set()
        for i in range(p.get_device_count()):
            try:
                info = p.get_device_info_by_index(i)
            except OSError:
                continue
            if info.get("maxInputChannels", 0) < 1:
                continue
            if info.get("isLoopbackDevice", False):
                continue
            if wasapi is not None and info.get("hostApi") != wasapi:
                continue
            name = info.get("name", f"Device {i}")
            if name in seen:
                continue
            seen.add(name)
            out.append(
                AudioDevice(
                    index=i,
                    name=name,
                    channels=int(info.get("maxInputChannels", 1)),
                    default_samplerate=int(info.get("defaultSampleRate", 48000)),
                    is_loopback=False,
                    is_default=(i == default_in),
                )
            )
        return out
    finally:
        p.terminate()


def list_loopback_devices() -> list[AudioDevice]:
    """Output devices we can capture via WASAPI loopback ("what they're saying")."""
    p = pyaudio.PyAudio()
    try:
        default_loopback_name = None
        try:
            default_loopback_name = p.get_default_wasapi_loopback()["name"]
        except (OSError, TypeError, LookupError):
            pass
        out: list[AudioDevice] = []
        seen: set[str] = set()
        for info in p.get_loopback_device_info_generator():
            name = info.get("name", "")
            if name in seen:
                continue
            seen.add(name)
            out.append(
                AudioDevice(
                    index=int(info["index"]),
                    name=name,
                    channels=int(info.get("maxInputChannels", 2)),
                    default_samplerate=int(info.get("defaultSampleRate", 48000)),
                    is_loopback=True,
                    is_default=(name == default_loopback_name),
                )
            )
        return out
    finally:
        p.terminate()


def default_input() -> Optional[AudioDevice]:
    devs = list_input_devices()
    for d in devs:
        if d.is_default:
            return d
    return devs[0] if devs else None


def default_loopback() -> Optional[AudioDevice]:
    devs = list_loopback_devices()
    for d in devs:
        if d.is_default:
            return d
    return devs[0] if devs else None
