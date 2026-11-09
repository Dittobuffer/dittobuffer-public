#!/usr/bin/env python3
"""Pack every game asset into stored zip parts bundled in the APK.

The payload is the complete universal asset set: all Pokémon and avatar
bundles, every recovered/model directory the manifests point at, the OSM map
extract (so vector tiles render everywhere the extract covers), the client
runtime registry and the client's own Unity cache. Player data (the SQLite
database and the walked world snapshot with its stops) is not shipped.

Parts stay below 2 GB each so no single zip entry needs a 64-bit size field.
"""

import os
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ASSETS = REPO / "android/app/src/main/assets"
# Outside src/main/assets: the parts become split APKs, not base assets.
PARTS = REPO / "android/payload-parts"
LIMIT = 1_800_000_000

# Whole trees. `builds` is required: 1,749 of the 2,739 manifest bundles
# resolve to builds/m107..m113 directories, not to assets/pokemon.
INCLUDE_DIRS = [
    "assets/pokemon",
    "assets/avatar",
    "assets/world",
    "assets/static",
    "assets/game-master",
    "assets/client-runtime",
    "assets/client-cache",
    "builds",
    "data/static",
    "data/maps",
    "config",
]
# The standalone client is bundled as its own APK by build.sh; its build tree
# is a 1.2 GB duplicate of the payload. The generated tile cache is rebuilt at
# runtime.
EXCLUDE_DIRS = {
    "builds/kit-standalone",
    "data/maps/tile-cache",
    # A friend's device-cache export used only to analyse which bundles the
    # external catalog names; it is not served and would add ~2 GiB.
    "builds/pixel5-friend",
}
# Personal / machine-specific data: never shipped.
EXCLUDE_FILES = ("data/family-development.sqlite3",)
SKIP_NAMES = {"__pycache__", ".DS_Store", "Thumbs.db", ".pytest_cache"}


def excluded(rel: str) -> bool:
    if rel in EXCLUDE_FILES:
        return True
    return any(rel == item or rel.startswith(item + "/") for item in EXCLUDE_DIRS)


def iter_files():
    for folder in INCLUDE_DIRS:
        base = REPO / folder
        if not base.is_dir():
            print("warning: missing dir " + folder, file=sys.stderr)
            continue
        for root, dirs, files in os.walk(base):
            rel_root = Path(root).relative_to(REPO).as_posix()
            dirs[:] = [d for d in dirs if d not in SKIP_NAMES and not excluded(rel_root + "/" + d)]
            for name in files:
                if name in SKIP_NAMES or name.endswith((".pyc", ".pyo", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm")):
                    continue
                path = Path(root) / name
                rel = path.relative_to(REPO).as_posix()
                if excluded(rel):
                    continue
                yield path, rel


def main() -> int:
    PARTS.mkdir(parents=True, exist_ok=True)
    for old in list(ASSETS.glob("payload*.zip")) + list(PARTS.glob("payload-*.zip")):
        old.unlink()
    parts = []
    count = 0
    total = 0
    part = None
    part_bytes = 0

    def open_part():
        nonlocal part, part_bytes
        number = len(parts) + 1
        path = PARTS / f"payload-{number}.zip"
        part = zipfile.ZipFile(path, "w", zipfile.ZIP_STORED, allowZip64=True)
        parts.append(path)
        part_bytes = 0

    open_part()
    for path, rel in iter_files():
        size = path.stat().st_size
        if part_bytes and part_bytes + size > LIMIT:
            part.close()
            open_part()
        part.write(path, rel)
        part_bytes += size
        count += 1
        total += size
    part.close()

    print("%d files, %.1f GiB in %d parts" % (count, total / 1073741824, len(parts)))
    for path in parts:
        print("  %-40s %8.2f GiB" % (path.name, path.stat().st_size / 1073741824))
    return 0


if __name__ == "__main__":
    sys.exit(main())
