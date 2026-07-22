#!/usr/bin/env python3
"""Compose the GitHub Release body for a given Earshot version.

Pulls the matching section out of CHANGELOG.md and wraps it with install
instructions and a self-host / Earshot Plus note. Used by
.github/workflows/release.yml to fill the release body.

Standalone (no Qt / package imports) so it runs fast on the CI runner.

Usage: python packaging/release_notes.py 0.31.0
"""
from __future__ import annotations

import pathlib
import re
import sys

REPO_URL = "https://github.com/haydenw22/earshot-meeting-notes"

PREAMBLE = (
    "**Install on Windows:** download `EarshotSetup.exe` below and run it (Windows "
    "10/11, 64-bit). SmartScreen may warn because the installer is new and unsigned: "
    "click **More info**, then **Run anyway**.\n\n"
    "**Install on macOS** (Apple Silicon, macOS 14.4 or newer): download `Earshot.dmg` "
    "below, open it and drag Earshot to Applications. The app is not yet Apple-signed, "
    "so the FIRST launch shows \"Apple cannot check it for malicious software\" with no "
    "way to proceed. This is expected, and needed only once:\n"
    "1. Click **OK** on that dialog.\n"
    "2. Open **System Settings**, then **Privacy & Security**, and scroll to the bottom.\n"
    "3. Next to the note that Earshot was blocked, click **Open Anyway**, then confirm "
    "**Open**.\n"
    "Every later launch is normal. Signed and notarized builds are coming, which removes "
    "this step entirely.\n\n"
    "**Self-host free forever** with your own AI keys (the setup guide walks you "
    "through it), or subscribe to **Earshot Plus** at https://tryearshot.app for "
    "managed transcription and AI with zero setup."
)


def changelog_section(version: str) -> str:
    """Return the body of the ## [version] ... section of CHANGELOG.md."""
    version = version.lstrip("v")
    path = pathlib.Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        m = re.match(r"^## \[([0-9][^\]]*)\]", line)
        if m:
            if capturing:  # reached the next version's section
                break
            capturing = m.group(1) == version
            continue
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: release_notes.py <version>")
    version = sys.argv[1].lstrip("v")
    parts = [PREAMBLE]
    body = changelog_section(version)
    if body:
        parts.append(f"## What's new in {version}\n\n{body}")
    parts.append(f"Full version history: [CHANGELOG.md]({REPO_URL}/blob/main/CHANGELOG.md)")
    print("\n\n---\n\n".join(parts))


if __name__ == "__main__":
    main()
