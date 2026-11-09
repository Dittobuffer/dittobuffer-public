#!/usr/bin/env python3
"""Build the payload split APKs.

AGP's packager is Zip32-only, so a single APK cannot exceed 4 GiB. The base
APK carries the server, bootstrap and client; each payload part becomes its
own signed split APK and the whole set installs as one app with
``adb install-multiple``. AssetManager exposes split assets under the same
``assets/`` path, and Bootstrap also falls back to reading the split APK files
directly.
"""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARTS = REPO / "android/payload-parts"
OUT = REPO / "apk/built"
TOOLS = REPO / ".private/toolchain"
BUILD_TOOLS = TOOLS / "sdk/build-tools/35.0.0"
ANDROID_JAR = TOOLS / "sdk/platforms/android-35/android.jar"
KEYSTORE = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".android/debug.keystore"
PACKAGE = "org.dittobuffer.mobile"
VERSION_CODE = "6"
VERSION_NAME = "1.0.5"

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{package}"
    android:versionCode="{code}"
    android:versionName="{name}"
    split="{split}">
    <application android:hasCode="false"/>
</manifest>
"""


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        print(" ".join(map(str, command)), file=sys.stderr)
        print(result.stdout, result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    return result


def aapt_link(manifest, output):
    run([str(BUILD_TOOLS / "aapt2.exe"), "link", "-o", str(output),
         "--manifest", str(manifest), "-I", str(ANDROID_JAR),
         "--min-sdk-version", "26", "--target-sdk-version", "35"])


def append_asset(apk, name, source):
    with zipfile.ZipFile(apk, "a", allowZip64=True) as archive:
        # ZIP_STORED: the payload is already an uncompressed archive.
        info = zipfile.ZipInfo("assets/" + name)
        info.compress_type = zipfile.ZIP_STORED
        info.file_size = source.stat().st_size
        with source.open("rb") as src, archive.open(info, "w") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)


def main():
    parts = sorted(PARTS.glob("payload-*.zip"))
    if not parts:
        print("no payload parts; run make_payload.py first", file=sys.stderr)
        return 1
    if not ANDROID_JAR.is_file() or not KEYSTORE.is_file():
        print("missing android.jar or debug keystore", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    work = REPO / "android/.splits"
    work.mkdir(exist_ok=True)
    for part in parts:
        # aapt2 only accepts [A-Za-z0-9_.] split names; payload-1 is invalid.
        split = part.stem.replace("-", "")  # payload1
        manifest = work / (split + ".xml")
        manifest.write_text(MANIFEST.format(package=PACKAGE, code=VERSION_CODE,
                                            name=VERSION_NAME, split=split), encoding="utf8")
        unsigned = work / (split + "-unsigned.apk")
        unsigned.unlink(missing_ok=True)
        aapt_link(manifest, unsigned)
        append_asset(unsigned, part.name, part)
        aligned = work / (split + "-aligned.apk")
        aligned.unlink(missing_ok=True)
        run([str(BUILD_TOOLS / "zipalign.exe"), "-f", "4", str(unsigned), str(aligned)])
        final = OUT / (split + ".apk")
        final.unlink(missing_ok=True)
        sign = [str(BUILD_TOOLS / "apksigner.bat"), "sign",
                "--ks", str(KEYSTORE), "--ks-pass", "pass:android",
                "--key-pass", "pass:android", "--ks-key-alias", "androiddebugkey",
                "--out", str(final), str(aligned)]
        run(["cmd", "/c", *sign])
        verify = subprocess.run([str(BUILD_TOOLS / "apksigner.bat"), "verify", str(final)],
                                capture_output=True, text=True, check=False)
        print("%-16s %7.2f GiB  %s" % (final.name, final.stat().st_size / 1073741824,
                                      "verified" if verify.returncode == 0 else "UNVERIFIED"))
        if verify.returncode:
            print(verify.stdout, verify.stderr, file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
