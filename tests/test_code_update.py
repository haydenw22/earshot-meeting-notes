"""macOS code-only updates (earshot_boot) and unsigned full-app updates.

Covers the signature scheme (RFC 8032 vectors, tamper/malleability/asset-swap
rejection), patch validation (runtime, platform, contents), installing and
activating a code update in a fresh interpreter (imports come from verified
memory, never from the bundle's older copy), tamper-at-rest and failed-start
fallbacks, the updater choosing code vs full updates, and the full-app path
requiring Earshot's signature when the install has no Apple Developer ID.

Run:  QT_QPA_PLATFORM=cocoa python tests/test_code_update.py   (macOS)
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

import earshot_boot as boot  # noqa: E402
from meeting_notes import updater  # noqa: E402

TEST_SEED = bytes(range(32))
TEST_PUB = boot.ed25519_public_key(TEST_SEED)
RUNTIME = "r" * 64
_DL = "https://github.com/haydenw22/earshot-meeting-notes/releases/download/v0.99.0"


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


def sign(name: str, data: bytes, seed: bytes = TEST_SEED) -> str:
    return boot.ed25519_sign(seed, boot.signed_message(name, hashlib.sha256(data).digest())).hex()


def make_patch(version="0.99.0", runtime=RUNTIME, platform="darwin-arm64", extra=None,
               files=None) -> bytes:
    files = files if files is not None else {
        "meeting_notes/__init__.py": f'__version__ = "{version}"\n',
        "meeting_notes/extra.py": "VALUE = 42\n",
        "meeting_notes/sub/__init__.py": "from ..extra import VALUE\nDOUBLE = VALUE * 2\n",
        "meeting_notes/boom.py": "def explode():\n    raise RuntimeError('kaboom from the patch')\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("earshot_patch.json", json.dumps(
            {"format": 1, "version": version, "runtime": runtime, "platform": platform}))
        for name, text in {**files, **(extra or {})}.items():
            zf.writestr(name, text)
    return buf.getvalue()


def test_signatures() -> None:
    print("== Ed25519 + release signature scheme ==")
    sk = bytes.fromhex("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7")
    pk = bytes.fromhex("fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025")
    sig = bytes.fromhex("6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
                        "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a")
    msg = bytes.fromhex("af82")
    check("RFC 8032 test 3: public key", boot.ed25519_public_key(sk) == pk)
    check("RFC 8032 test 3: signature", boot.ed25519_sign(sk, msg) == sig)
    check("RFC 8032 test 3: verifies", boot.ed25519_verify(pk, msg, sig))
    check("flipped message rejected", not boot.ed25519_verify(pk, b"\xaf\x83", sig))
    s_plus_l = int.from_bytes(sig[32:], "little") + boot._L
    check("non-canonical S rejected (malleability)",
          not boot.ed25519_verify(pk, msg, sig[:32] + s_plus_l.to_bytes(32, "little")))

    data = b"PK\x03\x04 some archive"
    good = sign("Earshot-mac.zip", data)
    digest = hashlib.sha256(data).digest()
    check("asset signature verifies", boot.verify_asset("Earshot-mac.zip", digest, good, TEST_PUB))
    check("signature can't be moved to another asset name",
          not boot.verify_asset("Earshot-mac-code.zip", digest, good, TEST_PUB))
    check("signature can't be reused for other bytes",
          not boot.verify_asset("Earshot-mac.zip", hashlib.sha256(b"x").digest(), good, TEST_PUB))
    check("garbage signature text rejected",
          not boot.verify_asset("Earshot-mac.zip", digest, "zz", TEST_PUB))
    check("the real release key is a valid point", boot._decompress(boot.UPDATE_PUBLIC_KEY) is not None)


def test_verify_patch() -> None:
    print("== code update validation ==")
    data = make_patch()
    manifest, files = boot.verify_patch(data, sign(boot.PATCH_ASSET, data), runtime=RUNTIME,
                                        public_key=TEST_PUB)
    check("valid patch accepted", manifest["version"] == "0.99.0"
          and "meeting_notes/extra.py" in files)

    def rejected(blob, why, *, signature=None, runtime=RUNTIME):
        try:
            boot.verify_patch(blob, signature if signature is not None else sign(boot.PATCH_ASSET, blob),
                              runtime=runtime, public_key=TEST_PUB)
            return False
        except ValueError as exc:
            return why in str(exc)

    other_key = sign(boot.PATCH_ASSET, data, seed=b"\x01" * 32)
    check("patch signed by another key rejected", rejected(data, "not signed", signature=other_key))
    tampered = data.replace(b"VALUE = 42", b"VALUE = 43")
    check("byte changed after signing rejected",
          rejected(tampered, "not signed", signature=sign(boot.PATCH_ASSET, data)))
    check("patch for another runtime rejected", rejected(data, "runtime", runtime="x" * 64))
    check("patch for another platform rejected",
          rejected(make_patch(platform="darwin-x86_64"), "platform"))
    check("patch shipping a non-Earshot module rejected",
          rejected(make_patch(extra={"PySide6/__init__.py": "evil = 1\n"}), "unexpected file"))
    check("patch with a path traversal rejected",
          rejected(make_patch(extra={"meeting_notes/../evil.py": "x = 1\n"}), "unexpected file"))
    check("patch shipping a binary rejected",
          rejected(make_patch(extra={"meeting_notes/_native.so": "\0"}), "unexpected file"))


# A fresh interpreter acting as the packaged app's main.py: frozen, darwin, with
# a bundle runtime file, using the test key.
_CHILD = r"""
import json, os, sys, traceback
sys.path.insert(0, sys.argv[1])
import earshot_boot as boot
cfg = json.loads(sys.argv[2])
sys.frozen = True
sys._MEIPASS = cfg["meipass"]
sys.platform = "darwin"
boot.UPDATE_PUBLIC_KEY = bytes.fromhex(cfg["pub"])
active = boot.activate_code_update()
import meeting_notes
out = {"active": active, "version": meeting_notes.__version__, "file": meeting_notes.__file__}
if active:
    from meeting_notes import sub
    out["double"] = sub.DOUBLE
    try:
        import meeting_notes.app  # exists in the bundle, NOT in the patch
        out["fallback"] = True
    except ModuleNotFoundError:
        out["fallback"] = False
    from meeting_notes import boom
    try:
        boom.explode()
    except RuntimeError:
        out["traceback"] = traceback.format_exc()
if cfg.get("healthy"):
    boot.mark_healthy()
print(json.dumps(out))
"""


def _bundle(version: str) -> str:
    meipass = tempfile.mkdtemp(prefix="earshot_meipass_")
    Path(meipass, boot.RUNTIME_FILE).write_text(json.dumps({"version": version, "runtime": RUNTIME}))
    return meipass


def _launch(data_dir: str, meipass: str, healthy: bool = False) -> dict:
    env = dict(os.environ, LOCALAPPDATA=data_dir)
    cfg = json.dumps({"meipass": meipass, "pub": TEST_PUB.hex(), "healthy": healthy})
    proc = subprocess.run([sys.executable, "-c", _CHILD, str(ROOT), cfg], env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


class _AsInstalledApp:
    """In-process: look like a packaged mac app (for install_code_update)."""

    def __init__(self, meipass: str, data_dir: str):
        self.meipass, self.data_dir = meipass, data_dir

    def __enter__(self):
        self.saved = (getattr(sys, "frozen", None), getattr(sys, "_MEIPASS", None), sys.platform,
                      boot.UPDATE_PUBLIC_KEY, os.environ.get("LOCALAPPDATA"))
        sys.frozen, sys._MEIPASS, sys.platform = True, self.meipass, "darwin"
        boot.UPDATE_PUBLIC_KEY = TEST_PUB
        os.environ["LOCALAPPDATA"] = self.data_dir
        return self

    def __exit__(self, *exc):
        frozen, meipass, platform, key, data = self.saved
        for attr, value in (("frozen", frozen), ("_MEIPASS", meipass)):
            if value is None:
                if hasattr(sys, attr):
                    delattr(sys, attr)
            else:
                setattr(sys, attr, value)
        sys.platform, boot.UPDATE_PUBLIC_KEY = platform, key
        os.environ["LOCALAPPDATA"] = data
        return False


def _write_patch(data: bytes) -> str:
    path = Path(tempfile.mkdtemp(prefix="EarshotUpdate-")) / boot.PATCH_ASSET
    path.write_bytes(data)
    return str(path)


def test_install_and_activate() -> None:
    print("== install + activate a code update in a fresh interpreter ==")
    data_dir = tempfile.mkdtemp(prefix="earshot_data_")
    meipass = _bundle("0.37.0")
    data = make_patch()
    with _AsInstalledApp(meipass, data_dir):
        version = boot.install_code_update(_write_patch(data), sign(boot.PATCH_ASSET, data))
        check("install returns the new version", version == "0.99.0")
        older = make_patch(version="0.36.9")
        try:
            boot.install_code_update(_write_patch(older), sign(boot.PATCH_ASSET, older))
            check("older code update refused", False)
        except ValueError as exc:
            check("older code update refused", "not newer" in str(exc))

    out = _launch(data_dir, meipass, healthy=True)
    check("next launch runs the code update", out["active"] == "0.99.0" and out["version"] == "0.99.0")
    check("relative imports inside the update work", out.get("double") == 84)
    check("a module missing from the update is NOT taken from the bundle",
          out.get("fallback") is False)
    check("tracebacks show the update's source lines", "raise RuntimeError('kaboom" in out["traceback"])
    check("module paths point into the installed update",
          "app-updates" in out["file"] and "earshot-code-0.99.0.zip" in out["file"])

    updates = Path(data_dir) / "Earshot" / "app-updates"
    for _ in range(4):  # healthy launches never accumulate failed-start strikes
        _launch(data_dir, meipass, healthy=True)
    check("healthy launches keep the update active", _launch(data_dir, meipass)["active"] == "0.99.0")

    print("== tamper at rest falls back to the bundled code ==")
    zpath = updates / "earshot-code-0.99.0.zip"
    original = zpath.read_bytes()
    zpath.write_bytes(original.replace(b"VALUE = 42", b"VALUE = 99"))
    out = _launch(data_dir, meipass)
    check("tampered update is not run", out["active"] == "" and out["version"] != "0.99.0")
    check("the bundled package runs instead", "app-updates" not in out["file"])
    zpath.write_bytes(original)

    print("== an update that never starts cleanly is set aside ==")
    (updates / "boot.json").unlink(missing_ok=True)
    results = [_launch(data_dir, meipass)["active"] for _ in range(boot.MAX_BOOT_ATTEMPTS + 1)]
    check("first launches still try the update",
          results[:boot.MAX_BOOT_ATTEMPTS] == ["0.99.0"] * boot.MAX_BOOT_ATTEMPTS)
    check(f"after {boot.MAX_BOOT_ATTEMPTS} unhealthy starts the bundle runs", results[-1] == "")
    with _AsInstalledApp(meipass, data_dir):
        check("the failed version is remembered", "0.99.0" in boot.rejected_versions())
        try:
            boot.install_code_update(_write_patch(data), sign(boot.PATCH_ASSET, data))
            check("a rejected code update is not reinstalled", False)
        except ValueError as exc:
            check("a rejected code update is not reinstalled", "failed to start" in str(exc))

    print("== a full app update that catches up clears the code update ==")
    out = _launch(data_dir, _bundle("0.99.0"))
    check("bundle at the same version runs its own code", out["active"] == "")
    check("stale code update files are removed",
          not list(updates.glob("earshot-code-*")) and not (updates / "active.json").exists())


def _release_assets(meta: dict, patch: bytes, *, code_sig: str, full_sig: str = "") -> dict:
    return {
        f"{_DL}/Earshot-mac-code.json": json.dumps(meta).encode(),
        f"{_DL}/Earshot-mac-code.zip": patch,
        f"{_DL}/Earshot-mac-code.zip.sig": code_sig.encode(),
        f"{_DL}/Earshot-mac.zip.sig": full_sig.encode(),
    }


def _transport(assets: dict) -> httpx.MockTransport:
    def handler(request):
        body = assets.get(str(request.url))
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})
    return httpx.MockTransport(handler)


def _info(**extra) -> updater.UpdateInfo:
    return updater.UpdateInfo("0.99.0", "notes", f"{_DL}/Earshot-mac.zip",
                              f"{_DL}/Earshot-mac.zip.sha256",
                              signature_url=f"{_DL}/Earshot-mac.zip.sig",
                              code_url=f"{_DL}/Earshot-mac-code.zip",
                              code_signature_url=f"{_DL}/Earshot-mac-code.zip.sig",
                              code_meta_url=f"{_DL}/Earshot-mac-code.json", **extra)


def test_updater_prefers_code_update() -> None:
    print("== updater: code update when it fits, full update otherwise ==")
    data_dir = tempfile.mkdtemp(prefix="earshot_data_")
    meipass = _bundle("0.37.0")
    patch = make_patch()
    meta = {"version": "0.99.0", "runtime": RUNTIME, "sha256": hashlib.sha256(patch).hexdigest()}
    real_download = updater.download_installer

    def offline_download(url, dest, progress_cb=None, **kwargs):  # real verification, mocked wire
        kwargs["transport"] = transport
        return real_download(url, dest, progress_cb, **kwargs)

    transport = _transport(_release_assets(meta, patch, code_sig=sign(boot.PATCH_ASSET, patch)))
    updater.download_installer = offline_download
    try:
        with _AsInstalledApp(meipass, data_dir):
            check("fitting code update is found",
                  updater.code_update_meta(_info(), transport=transport) is not None)
            version = updater.install_code_update(_info(), transport=transport)
            check("code update downloaded, verified and installed", version == "0.99.0")
            check("it is what the next launch will run",
                  json.loads((boot.updates_dir() / "active.json").read_text())["version"] == "0.99.0")

            other = dict(meta, runtime="x" * 64)
            transport = _transport(_release_assets(other, patch, code_sig=sign(boot.PATCH_ASSET, patch)))
            check("runtime changed: no code update (full update instead)",
                  updater.install_code_update(_info(), transport=transport) is None)

            transport = _transport(_release_assets(meta, patch, code_sig=sign(boot.PATCH_ASSET, b"other")))
            try:
                updater.install_code_update(_info(), transport=transport)
                check("badly signed code update refused", False)
            except ValueError:
                check("badly signed code update refused", True)

            no_code = updater.UpdateInfo("0.99.0", "n", f"{_DL}/Earshot-mac.zip", f"{_DL}/x.sha256")
            check("release without code assets: full update",
                  updater.install_code_update(no_code) is None)
    finally:
        updater.download_installer = real_download

    print("== full app archive signature ==")
    archive = b"PK\x03\x04 full app"
    digest = hashlib.sha256(archive).hexdigest()
    saved_key = boot.UPDATE_PUBLIC_KEY
    boot.UPDATE_PUBLIC_KEY = TEST_PUB
    try:
        good = _transport({f"{_DL}/Earshot-mac.zip.sig": sign("Earshot-mac.zip", archive).encode()})
        check("signed archive verifies", updater.verify_release_signature(
            "Earshot-mac.zip", digest, f"{_DL}/Earshot-mac.zip.sig", transport=good))
        moved = _transport({f"{_DL}/Earshot-mac.zip.sig": sign("Earshot-mac-code.zip", archive).encode()})
        try:
            updater.verify_release_signature("Earshot-mac.zip", digest,
                                              f"{_DL}/Earshot-mac.zip.sig", transport=moved)
            check("signature for another asset refused", False)
        except ValueError:
            check("signature for another asset refused", True)
        check("no signature asset reports unsigned",
              updater.verify_release_signature("Earshot-mac.zip", digest, "") is False)
    finally:
        boot.UPDATE_PUBLIC_KEY = saved_key


def _plist_app(path: Path, version: str) -> None:
    (path / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
    with open(path / "Contents" / "Info.plist", "wb") as fh:
        plistlib.dump({"CFBundleIdentifier": "app.tryearshot.earshot",
                       "CFBundleShortVersionString": version}, fh)


def test_unsigned_full_update_needs_earshot_signature() -> None:
    print("== full update on an install without an Apple Developer ID ==")
    root = Path(tempfile.mkdtemp(prefix="earshot_validate_"))
    current, new = root / "Current.app", root / "New.app"
    _plist_app(current, "0.37.0")
    _plist_app(new, "0.38.0")
    old_run = updater.subprocess.run

    def adhoc_run(args, **kwargs):
        if "-d" in args:
            return subprocess.CompletedProcess(
                args, 0, "", "TeamIdentifier=not set\n# designated => cdhash H\"abc\"\n")
        return subprocess.CompletedProcess(args, 0, "", "")

    try:
        updater.subprocess.run = adhoc_run
        try:
            updater._validate_mac_update_app(str(new), str(current), signed=False)
            check("unsigned archive refused", False)
        except ValueError as exc:
            check("unsigned archive refused", "update key" in str(exc))
        updater._validate_mac_update_app(str(new), str(current), signed=True)
        check("archive with Earshot's signature accepted", True)
        _plist_app(new, "0.36.0")
        try:
            updater._validate_mac_update_app(str(new), str(current), signed=True)
            check("signed but older app refused", False)
        except ValueError as exc:
            check("signed but older app refused", "not newer" in str(exc))
    finally:
        updater.subprocess.run = old_run


def test_check_for_update_collects_code_assets() -> None:
    print("== check_for_update picks up the code update assets on macOS ==")
    names = ["Earshot-mac.zip", "Earshot-mac.zip.sha256", "Earshot-mac.zip.sig",
             "Earshot-mac-code.zip", "Earshot-mac-code.zip.sig", "Earshot-mac-code.json"]
    release = {"tag_name": "v0.99.0", "draft": False, "prerelease": False, "body": "### Fixed\n- x",
               "assets": [{"name": n, "browser_download_url": f"{_DL}/{n}"} for n in names]}
    saved = (updater.ASSET_NAME, updater.ASSET_DIGEST_NAME, updater._MAGIC, updater._MAGIC_ERR)
    (updater.ASSET_NAME, updater.ASSET_DIGEST_NAME,
     updater._MAGIC, updater._MAGIC_ERR) = updater._platform_assets("darwin")
    try:
        info = updater.check_for_update(
            "0.37.0", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[release])))
        check("code update urls found", info.code_url.endswith("Earshot-mac-code.zip")
              and info.code_meta_url.endswith(".json") and info.code_signature_url.endswith(".sig"))
        check("full archive signature url found", info.signature_url.endswith("Earshot-mac.zip.sig"))
    finally:
        (updater.ASSET_NAME, updater.ASSET_DIGEST_NAME, updater._MAGIC, updater._MAGIC_ERR) = saved


def test_patch_builder_matches_boot() -> None:
    print("== packaging/mac/make_code_patch.py output passes the boot checks ==")
    sys.path.insert(0, str(ROOT / "packaging" / "mac"))
    import make_code_patch

    dist = Path(tempfile.mkdtemp(prefix="earshot_dist_"))
    version = make_code_patch.re.search(
        r'__version__\s*=\s*"([^"]+)"', (ROOT / "meeting_notes" / "__init__.py").read_text()).group(1)
    res = dist / "Earshot.app" / "Contents" / "Resources"
    res.mkdir(parents=True)
    (res / boot.RUNTIME_FILE).write_text(json.dumps({"version": version, "runtime": RUNTIME}))
    out = make_code_patch.build(dist)
    data = out.read_bytes()
    manifest, files = boot.verify_patch(data, sign(boot.PATCH_ASSET, data), runtime=RUNTIME,
                                        public_key=TEST_PUB)
    meta = json.loads((dist / "Earshot-mac-code.json").read_text())
    check("manifest carries version + runtime", manifest["version"] == version
          and meta["runtime"] == RUNTIME and meta["sha256"] == hashlib.sha256(data).hexdigest())
    check("every package module is included",
          len(files) == len([p for p in (ROOT / "meeting_notes").rglob("*.py")
                             if "__pycache__" not in p.parts]))
    check("build is reproducible", make_code_patch.build(dist).read_bytes() == data)


def main() -> int:
    test_signatures()
    test_verify_patch()
    test_install_and_activate()
    test_updater_prefers_code_update()
    test_unsigned_full_update_needs_earshot_signature()
    test_check_for_update_collects_code_assets()
    test_patch_builder_matches_boot()
    print("\nCODE UPDATE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
