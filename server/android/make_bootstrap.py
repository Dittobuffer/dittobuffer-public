#!/usr/bin/env python3
"""Pack the server source, config and static data into the APK bootstrap zip."""

import os
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "android/app/src/main/assets/server-bootstrap.zip"

INCLUDE_DIRS = ["src", "protocol", "config"]
INCLUDE_FILES = [
    "data/static/gmts.binpb.gz",
    "data/static/max-reference.json",
    "data/static/pvp-reference.json",
]
SKIP = {"__pycache__", ".pytest_cache", ".mypy_cache"}


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    total = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for folder in INCLUDE_DIRS:
            base = REPO / folder
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d not in SKIP]
                for name in files:
                    if name.endswith((".pyc", ".pyo")):
                        continue
                    path = Path(root) / name
                    arc = path.relative_to(REPO).as_posix()
                    z.write(path, arc)
                    count += 1
                    total += path.stat().st_size
        for item in INCLUDE_FILES:
            path = REPO / item
            if path.is_file():
                z.write(path, item)
                count += 1
                total += path.stat().st_size
    print("%s: %d files, %.1f MiB uncompressed, %.1f MiB zip" % (
        OUT, count, total / 1048576, OUT.stat().st_size / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())
