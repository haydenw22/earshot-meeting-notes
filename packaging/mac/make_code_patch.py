#!/usr/bin/env python3
"""Build Earshot-mac-code.zip: the code-only macOS update (see earshot_boot.py).

Run after PyInstaller has built dist/Earshot.app from the same tree. Packs the
meeting_notes package sources plus a manifest naming the version and the
bundle's runtime fingerprint, reproducibly (sorted entries, fixed timestamps),
and writes Earshot-mac-code.json next to it so the updater can tell whether a
code update fits an installed app before downloading it.

Usage: python packaging/mac/make_code_patch.py [dist_dir]
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_EPOCH = (1980, 1, 1, 0, 0, 0)


def _bundle_runtime(app: Path) -> dict:
    for rel in ("Contents/Resources/earshot_runtime.json", "Contents/Frameworks/earshot_runtime.json"):
        path = app / rel
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise SystemExit(f"{app} has no earshot_runtime.json; build it with packaging/earshot_mac.spec")


def _entry(name: str, data: bytes, archive: zipfile.ZipFile) -> None:
    info = zipfile.ZipInfo(name, date_time=_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def build(dist: Path) -> Path:
    version = re.search(r'__version__\s*=\s*"([^"]+)"',
                        (ROOT / "meeting_notes" / "__init__.py").read_text(encoding="utf-8")).group(1)
    runtime = _bundle_runtime(dist / "Earshot.app")
    if runtime.get("version") != version:
        raise SystemExit(f"dist/Earshot.app is {runtime.get('version')}, sources are {version}")
    manifest = {"format": 1, "version": version, "runtime": runtime["runtime"],
                "platform": "darwin-arm64"}
    out = dist / "Earshot-mac-code.zip"
    sources = sorted(p for p in (ROOT / "meeting_notes").rglob("*.py")
                     if "__pycache__" not in p.parts)
    with zipfile.ZipFile(out, "w") as archive:
        _entry("earshot_patch.json", json.dumps(manifest, indent=2).encode("utf-8"), archive)
        for path in sources:
            _entry(path.relative_to(ROOT).as_posix(), path.read_bytes(), archive)
    data = out.read_bytes()
    meta = dict(manifest, sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    (dist / "Earshot-mac-code.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"built {out.name}: {len(sources)} modules, {len(data) // 1024} KB, runtime "
          f"{manifest['runtime'][:12]}")
    return out


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist")
