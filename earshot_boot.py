"""Code-only updates for the packaged macOS app.

Most Earshot releases change only the app's own Python code. Instead of
replacing the whole Earshot.app (which needs an Apple Developer ID to do
safely, and gives an unsigned app a new identity so macOS asks for the
Microphone and System Audio Recording permissions all over again), a release
also ships `Earshot-mac-code.zip`: just the `meeting_notes` package, signed
with Earshot's update key. The updater stores it in Application Support and
this module, which lives inside the app bundle and never in an update, runs
the newer code at the next launch. The bundle itself is never modified.

A code update only applies to a bundle with an identical *runtime*: the same
Python, third-party packages, native libraries, audio helper and boot code
(fingerprinted at build time by packaging/earshot_mac.spec). When a release
changes any of those, the updater falls back to a full app update instead.

Because the update runs with Earshot's microphone and screen permissions, it
is verified at EVERY launch, not just at download: the Ed25519 signature must
match the public key baked into this (code-signed) bundle, and the verified
bytes are imported straight from memory, so nothing can swap the file between
the check and the import. A code update that fails to reach a healthy startup
three launches running is set aside and the bundled code runs instead.

This module must only use the standard library and must never import
meeting_notes: it decides WHICH meeting_notes gets imported.
"""
from __future__ import annotations

import hashlib
import importlib.abc
import importlib.machinery
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

# Ed25519 public key that release code updates are signed with (the private
# half lives only in the EARSHOT_UPDATE_KEY GitHub Actions secret).
UPDATE_PUBLIC_KEY = bytes.fromhex(
    "f28f8f44ce5a48c38779a344504e1ac50ac06f22e0c0941310f5c1351d5e1b1e"
)

PATCH_ASSET = "Earshot-mac-code.zip"
PATCH_MANIFEST = "earshot_patch.json"
RUNTIME_FILE = "earshot_runtime.json"
PACKAGE = "meeting_notes"
PLATFORM_TAG = "darwin-arm64"
MAX_PATCH_BYTES = 50 * 1024 * 1024
MAX_BOOT_ATTEMPTS = 3
_SIG_DOMAIN = b"earshot-update-v1\0"


# ---------------------------------------------------------------- Ed25519 ----
# RFC 8032 (section 6 reference algorithm). Pure Python: verification takes a
# few milliseconds, and the bundle needs no third-party crypto library.

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, -1, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _point_add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _point_mul(s: int, pt):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _point_add(q, pt)
        pt = _point_add(pt, pt)
        s >>= 1
    return q


def _point_equal(a, b) -> bool:
    return ((a[0] * b[2] - b[0] * a[2]) % _P == 0
            and (a[1] * b[2] - b[1] * a[2]) % _P == 0)


def _recover_x(y: int, sign: int):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, -1, _P)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, -1, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _compress(pt) -> bytes:
    zinv = pow(pt[2], -1, _P)
    x, y = pt[0] * zinv % _P, pt[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _hash_mod_l(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % _L


def _expand_secret(seed: bytes):
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def ed25519_public_key(seed: bytes) -> bytes:
    """Public key for a 32-byte private seed (release tooling and tests)."""
    if len(seed) != 32:
        raise ValueError("an Ed25519 private key is 32 bytes")
    return _compress(_point_mul(_expand_secret(seed)[0], _G))


def ed25519_sign(seed: bytes, msg: bytes) -> bytes:
    """Sign `msg` (release tooling and tests; the app itself only verifies)."""
    a, prefix = _expand_secret(seed)
    public = _compress(_point_mul(a, _G))
    r = _hash_mod_l(prefix + msg)
    big_r = _compress(_point_mul(r, _G))
    s = (r + _hash_mod_l(big_r + public + msg) * a) % _L
    return big_r + int.to_bytes(s, 32, "little")


def ed25519_verify(public: bytes, msg: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    a = _decompress(public)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = _hash_mod_l(signature[:32] + public + msg)
    return _point_equal(_point_mul(s, _G), _point_add(r, _point_mul(h, a)))


def signed_message(asset_name: str, sha256: bytes) -> bytes:
    """What a release signature covers: the asset's name and SHA-256, so a
    signature can't be moved from one release asset onto another."""
    return _SIG_DOMAIN + asset_name.encode("utf-8") + b"\0" + sha256


def verify_asset(asset_name: str, sha256: bytes, signature_hex: str,
                 public_key: bytes | None = None) -> bool:
    """True only for a valid release signature over (asset_name, sha256)."""
    try:
        signature = bytes.fromhex((signature_hex or "").strip())
    except ValueError:
        return False
    return ed25519_verify(public_key or UPDATE_PUBLIC_KEY,
                          signed_message(asset_name, sha256), signature)


# ---------------------------------------------------------------- versions ----

def parse_version(s: str) -> tuple[int, ...]:
    """'v0.37.1' -> (0, 37, 1); stops at the first non-numeric component."""
    out: list[int] = []
    for part in (s or "").strip().lstrip("vV").split("."):
        digits = ""
        for ch in part:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


# ------------------------------------------------------------- locations ----

def updates_dir() -> Path:
    """Where code updates live: <app data>/app-updates (mirrors
    meeting_notes.paths, which this module must not import)."""
    env = os.environ.get("LOCALAPPDATA")
    if env:
        base = Path(env)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home()
    return base / "Earshot" / "app-updates"


def _bundle_runtime_info() -> dict:
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return {}
    try:
        with open(os.path.join(base, RUNTIME_FILE), encoding="utf-8") as fh:
            info = json.load(fh)
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


def bundle_runtime() -> str:
    """Runtime fingerprint of the installed app bundle ('' when unknown)."""
    return str(_bundle_runtime_info().get("runtime") or "")


def bundle_version() -> str:
    """Version the installed app bundle was built as ('' when unknown)."""
    return str(_bundle_runtime_info().get("version") or "")


def supported() -> bool:
    """Code updates apply to packaged macOS builds that know their runtime."""
    return (bool(getattr(sys, "frozen", False)) and sys.platform == "darwin"
            and bool(bundle_runtime()) and bool(bundle_version()))


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _log(msg: str) -> None:
    try:
        d = updates_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "boot.log", "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


# ------------------------------------------------------------ verification ----

def verify_patch(data: bytes, signature_hex: str, *, runtime: str,
                 public_key: bytes | None = None) -> tuple[dict, dict]:
    """Check a code update completely, in memory. Returns (manifest, files)
    where files maps 'meeting_notes/...py' to its bytes. Raises ValueError."""
    if len(data) > MAX_PATCH_BYTES:
        raise ValueError("code update is implausibly large")
    if not verify_asset(PATCH_ASSET, hashlib.sha256(data).digest(), signature_hex, public_key):
        raise ValueError("code update is not signed by Earshot")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("code update is not a valid archive") from exc
    files: dict[str, bytes] = {}
    manifest: dict = {}
    with archive:
        for entry in archive.infolist():
            name = entry.filename
            if entry.is_dir():
                continue
            if name == PATCH_MANIFEST:
                try:
                    manifest = json.loads(archive.read(entry).decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ValueError("code update manifest is unreadable") from exc
                continue
            parts = name.split("/")
            if (parts[0] != PACKAGE or not name.endswith(".py") or name.startswith("/")
                    or any(p in ("", ".", "..") for p in parts)):
                raise ValueError(f"code update contains an unexpected file: {name}")
            files[name] = archive.read(entry)
    if not isinstance(manifest, dict) or manifest.get("format") != 1:
        raise ValueError("code update has no valid manifest")
    if manifest.get("platform") != PLATFORM_TAG:
        raise ValueError("code update is for a different platform")
    if not runtime or manifest.get("runtime") != runtime:
        raise ValueError("code update needs a different app runtime")
    if not parse_version(str(manifest.get("version") or "")):
        raise ValueError("code update has no version")
    if f"{PACKAGE}/__init__.py" not in files:
        raise ValueError("code update is missing its package")
    return manifest, files


# ------------------------------------------------------------------ import ----

class _MemoryLoader(importlib.abc.InspectLoader):
    def __init__(self, source: bytes, origin: str, is_pkg: bool):
        self._source = source
        self._origin = origin
        self._is_pkg = is_pkg

    def is_package(self, fullname):
        return self._is_pkg

    def get_source(self, fullname):
        return self._source.decode("utf-8")

    def get_code(self, fullname):
        return compile(self._source, self._origin, "exec", dont_inherit=True)

    def exec_module(self, module):
        exec(self.get_code(module.__name__), module.__dict__)


class _MemoryFinder(importlib.abc.MetaPathFinder):
    """Serves meeting_notes from verified in-memory sources. A meeting_notes
    module the update doesn't contain is refused rather than falling back to
    the bundle's older copy, so old and new code can never mix."""

    def __init__(self, files: dict[str, bytes], root: str):
        self._root = root
        self._modules: dict[str, tuple[str, bool]] = {}
        for path in files:
            parts = path[:-3].split("/")
            if parts[-1] == "__init__":
                self._modules[".".join(parts[:-1])] = (path, True)
            else:
                self._modules[".".join(parts)] = (path, False)
        self._files = files

    def find_spec(self, fullname, path=None, target=None):
        if fullname != PACKAGE and not fullname.startswith(PACKAGE + "."):
            return None
        entry = self._modules.get(fullname)
        if entry is None:
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        rel, is_pkg = entry
        origin = os.path.join(self._root, *rel.split("/"))
        spec = importlib.machinery.ModuleSpec(
            fullname, _MemoryLoader(self._files[rel], origin, is_pkg),
            origin=origin, is_package=is_pkg)
        if is_pkg:
            spec.submodule_search_locations = [os.path.dirname(origin)]
        spec.has_location = True
        return spec


# -------------------------------------------------------------------- boot ----

_active_version = ""


def active_version() -> str:
    """Version of the code update this process is running ('' = bundled code)."""
    return _active_version


def _clear_installed(d: Path) -> None:
    for p in d.glob("earshot-code-*"):
        try:
            p.unlink()
        except OSError:
            pass
    try:
        (d / "active.json").unlink()
    except OSError:
        pass


def activate_code_update() -> str:
    """Called by main.py before anything imports meeting_notes. Installs the
    newest verified code update, if any, and returns its version ('' when the
    bundled code runs). Never raises: any problem means 'run the bundle'."""
    global _active_version
    try:
        if not supported() or PACKAGE in sys.modules:
            return ""
        d = updates_dir()
        active = _read_json(d / "active.json")
        version = str(active.get("version") or "")
        if not version:
            return ""
        if parse_version(version) <= parse_version(bundle_version()):
            # A full app update has caught up with (or passed) the code update.
            _clear_installed(d)
            return ""
        boot = _read_json(d / "boot.json")
        rejected = [str(v) for v in boot.get("rejected") or []]
        if version in rejected:
            return ""
        attempts = int(boot.get("attempts") or 0) if boot.get("version") == version else 0
        if attempts >= MAX_BOOT_ATTEMPTS:
            _log(f"code update {version} never started cleanly; using the bundled app")
            boot.update(version=version, attempts=0, rejected=rejected + [version])
            _write_json(d / "boot.json", boot)
            return ""
        name = os.path.basename(str(active.get("file") or ""))
        if not name.startswith("earshot-code-"):
            return ""
        data = (d / name).read_bytes()
        signature = (d / (name + ".sig")).read_text(encoding="ascii")
        manifest, files = verify_patch(data, signature, runtime=bundle_runtime())
        if str(manifest.get("version")) != version:
            raise ValueError("code update version does not match its manifest")
        boot.update(version=version, attempts=attempts + 1, rejected=rejected)
        _write_json(d / "boot.json", boot)
        sys.meta_path.insert(0, _MemoryFinder(files, str(d / name)))
        _active_version = version
        return version
    except Exception as exc:  # noqa: BLE001 - booting the bundle is always safe
        _log(f"code update not used: {type(exc).__name__}: {exc}")
        return ""


def mark_healthy() -> None:
    """The app reached a working main window: the active code update is good."""
    if not _active_version:
        return
    try:
        path = updates_dir() / "boot.json"
        boot = _read_json(path)
        if boot.get("version") == _active_version and boot.get("attempts"):
            boot["attempts"] = 0
            _write_json(path, boot)
    except OSError:
        pass


def rejected_versions() -> list[str]:
    return [str(v) for v in _read_json(updates_dir() / "boot.json").get("rejected") or []]


def install_code_update(zip_path: str, signature_hex: str) -> str:
    """Verify a downloaded code update and make it the one the next launch
    runs. Returns its version; raises ValueError if it isn't installable."""
    if not supported():
        raise ValueError("code updates are not available for this installation")
    with open(zip_path, "rb") as fh:
        data = fh.read(MAX_PATCH_BYTES + 1)
    manifest, _files = verify_patch(data, signature_hex, runtime=bundle_runtime())
    version = str(manifest["version"])
    current = active_version() or bundle_version()
    if parse_version(version) <= parse_version(current):
        raise ValueError("code update is not newer than this app")
    if version in rejected_versions():
        raise ValueError("this code update failed to start before")
    d = updates_dir()
    d.mkdir(parents=True, exist_ok=True)
    name = f"earshot-code-{version}.zip"
    for target, payload in ((d / name, data), (d / (name + ".sig"), signature_hex.strip().encode())):
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, target)
    _write_json(d / "active.json", {"version": version, "file": name,
                                    "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    keep = {name, name + ".sig"}
    if _active_version:  # this process is still running from that one
        running = f"earshot-code-{_active_version}.zip"
        keep |= {running, running + ".sig"}
    for p in d.glob("earshot-code-*"):
        if p.name not in keep:
            try:
                p.unlink()
            except OSError:
                pass
    return version
