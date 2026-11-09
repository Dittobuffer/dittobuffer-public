#!/usr/bin/env python3
"""Bundle the base APK and every payload split into one installable XAPK.

The XAPK is a plain ZIP (Zip64, because the payload is >4 GiB) holding the
base APK, the payload splits, a launcher icon and the standard manifest.json
that APKPure-style installers read. Nothing is re-compressed: APKs are already
compressed, and a stored archive can be verified quickly.
"""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "apk" / "built"
BASE = OUT / "dittobuffer-mobile-base.apk"
PAYLOADS = sorted(OUT.glob("payload[0-9]*.apk"))
ICON = REPO / "android/app/src/main/res/drawable-nodpi/dittobuffer_app_icon.png"
TARGET = OUT / "Dittobuffer-1.0.5.xapk"
PACKAGE = "org.dittobuffer.mobile"
VERSION_CODE = "6"
VERSION_NAME = "1.0.5"
PERMISSIONS = [
    "android.permission.INTERNET",
    "android.permission.ACCESS_NETWORK_STATE",
    "android.permission.FOREGROUND_SERVICE",
    "android.permission.FOREGROUND_SERVICE_DATA_SYNC",
    "android.permission.POST_NOTIFICATIONS",
    "android.permission.WAKE_LOCK",
    "android.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS",
    "android.permission.REQUEST_INSTALL_PACKAGES",
]
README = """Dittobuffer 1.0.5 — self-contained phone server

Install with an XAPK installer that supports split APKs and Zip64, or from a
computer with Android platform-tools:

    adb install-multiple -r base.apk payload*.apk

Open Dittobuffer, keep the screen on for the first unpack (about 5 GiB), then
press Play. The admin console is the Admin tab inside the app.

The payload deliberately excludes personal data: player saves, walked world
data, placed stops and map tiles generated on the device.
"""


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    files = [BASE, *PAYLOADS]
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        print("missing: " + ", ".join(missing), file=sys.stderr)
        return 1
    if not ICON.is_file():
        print("missing icon: " + str(ICON), file=sys.stderr)
        return 1
    splits = [{"file": BASE.name.replace("dittobuffer-mobile-base", "base"), "id": "base"}]
    splits += [{"file": path.name, "id": path.stem} for path in PAYLOADS]
    manifest = {
        "xapk_version": 2,
        "package_name": PACKAGE,
        "name": "Dittobuffer",
        "version_code": VERSION_CODE,
        "version_name": VERSION_NAME,
        "min_sdk_version": "26",
        "target_sdk_version": "35",
        "permissions": PERMISSIONS,
        "split_apks": splits,
    }
    # APKMirror-style readers look for info.json; XAPK readers look for
    # manifest.json. Shipping both costs a few hundred bytes.
    info = {
        "apkm_version": 1,
        "package_name": PACKAGE,
        "version_code": VERSION_CODE,
        "version_name": VERSION_NAME,
        "min_sdk": 26,
        "target_sdk": 35,
        "permissions": PERMISSIONS,
        "split_apks": splits,
    }
    entries = [
        ("manifest.json", json.dumps(manifest, indent=2) + "\n"),
        ("info.json", json.dumps(info, indent=2) + "\n"),
        ("README.txt", README),
    ]
    temporary = TARGET.with_suffix(".xapk.tmp")
    temporary.unlink(missing_ok=True)
    total = 0
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
            for name, text in entries:
                archive.writestr(name, text.encode("utf8"))
            archive.write(ICON, "icon.png")
            for path in files:
                name = BASE.name.replace("dittobuffer-mobile-base", "base") if path == BASE else path.name
                print("packing", name, "%.2f GiB" % (path.stat().st_size / 1073741824), flush=True)
                entry = zipfile.ZipInfo(name)
                entry.compress_type = zipfile.ZIP_STORED
                entry.file_size = path.stat().st_size
                with path.open("rb") as source, archive.open(entry, "w") as target:
                    while chunk := source.read(1 << 22):
                        target.write(chunk)
                total += path.stat().st_size
        temporary.replace(TARGET)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    print("%-40s %7.2f GiB  sha256 %s" % (TARGET.name, TARGET.stat().st_size / 1073741824, sha256(TARGET)))
    print("payload bytes %7.2f GiB in %d files" % (total / 1073741824, len(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
