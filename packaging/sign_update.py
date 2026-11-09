#!/usr/bin/env python3
"""Sign macOS update assets with Earshot's Ed25519 update key.

Writes <file>.sig (hex) for each file: a signature over the asset's name and
SHA-256 (earshot_boot.signed_message), which the in-app updater checks before
installing anything on an unsigned build and at every launch for code updates.

The private key is the hex 32-byte seed in the EARSHOT_UPDATE_KEY environment
variable (a GitHub Actions secret; Hayden's copy is in the macOS Keychain as
"Earshot update signing key"). Refuses to sign if the key doesn't match the
public key compiled into the app, so a wrong secret can't ship updates that
every install would reject.

Usage: EARSHOT_UPDATE_KEY=... python packaging/sign_update.py dist/Earshot-mac.zip ...
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import earshot_boot  # noqa: E402


def main(paths: list[str]) -> int:
    try:
        seed = bytes.fromhex(os.environ.get("EARSHOT_UPDATE_KEY", "").strip())
    except ValueError:
        seed = b""
    if len(seed) != 32:
        print("EARSHOT_UPDATE_KEY is missing or is not a 32-byte hex seed", file=sys.stderr)
        return 1
    if earshot_boot.ed25519_public_key(seed) != earshot_boot.UPDATE_PUBLIC_KEY:
        print("EARSHOT_UPDATE_KEY does not match earshot_boot.UPDATE_PUBLIC_KEY", file=sys.stderr)
        return 1
    for raw in paths:
        path = Path(raw)
        digest = hashlib.sha256(path.read_bytes()).digest()
        signature = earshot_boot.ed25519_sign(seed, earshot_boot.signed_message(path.name, digest))
        Path(f"{path}.sig").write_text(signature.hex() + "\n", encoding="ascii")
        print(f"signed {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
