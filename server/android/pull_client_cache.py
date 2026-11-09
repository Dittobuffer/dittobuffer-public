#!/usr/bin/env python3
"""Copy the real on-device client cache into the repository.

Two things are produced:

* ``builds/m151-client-runtime-v3/`` - the exact 212 files the server's
  ``client_resources`` registry whitelists, pulled from the client's
  UnityCache and verified against the registry's SHA256/size.
* ``assets/client-cache/`` - the complete UnityCache and addressables tree,
  preserved verbatim so a fresh install has every byte the client had cached.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = "org.librenlp.pokemongo.dev"
DEVICE = "/sdcard/Android/data/" + PKG + "/files"
REGISTRY = REPO / "assets/client-runtime/registry/registry.json"
TARGET = REPO / "builds/m151-client-runtime-v3"
CACHE = REPO / "assets/client-cache"


def adb(*args, timeout=900):
    env = dict(os.environ, MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
    return subprocess.run(["adb", *args], capture_output=True, text=True,
                          timeout=timeout, env=env, check=False)


def pull(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = adb("pull", source, str(destination))
    return result.returncode == 0 and destination.is_file()


def main():
    registry = json.loads(REGISTRY.read_text(encoding="utf8"))
    TARGET.mkdir(parents=True, exist_ok=True)
    ok = bad = 0
    for name, entry in registry["files"].items():
        destination = TARGET / name
        expected = entry["bytes"]
        if destination.is_file() and destination.stat().st_size == expected:
            ok += 1
            continue
        source = entry.get("source")
        if not source:
            # The catalog pair lives beside the client's addressables cache.
            source = f"{DEVICE}/com.unity.addressables/{name}"
        if not pull(source, destination):
            bad += 1
            print("MISSING", name, file=sys.stderr)
            continue
        data = destination.read_bytes()
        if len(data) != expected or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            bad += 1
            destination.unlink(missing_ok=True)
            print("HASH MISMATCH", name, file=sys.stderr)
        else:
            ok += 1
    print(f"registry files: {ok} verified, {bad} failed")

    # Whole cache, byte for byte. Re-pull only when the tree is absent.
    if not (CACHE / "UnityCache" / "Shared").is_dir():
        CACHE.mkdir(parents=True, exist_ok=True)
        print("pulling UnityCache …")
        adb("pull", f"{DEVICE}/UnityCache", str(CACHE / "UnityCache"))
        print("pulling addressables …")
        adb("pull", f"{DEVICE}/com.unity.addressables", str(CACHE / "addressables"))
    files = sum(1 for _ in CACHE.rglob("*") if _.is_file()) if CACHE.is_dir() else 0
    size = sum(_.stat().st_size for _ in CACHE.rglob("*") if _.is_file()) if CACHE.is_dir() else 0
    # The Android bootstrap enables client_resources only when this marker is
    # present, so a partial pull can never crash server startup.
    marker = TARGET / ".verified"
    if bad == 0 and ok == len(registry["files"]):
        marker.write_text(str(ok), encoding="utf8")
    else:
        marker.unlink(missing_ok=True)
        print("registry incomplete; client_resources stays disabled", file=sys.stderr)
    print(f"client cache: {files} files, {size / 1048576:.1f} MiB")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
