#!/usr/bin/env python3
"""Point the external network's bundle names at the bundles we actually have.

The Pixel 5 external catalog (``catalog_ext_12``) names the two bundles that
own the team-leader billboards and every badge sprite:

* ``npctrainerassets_assets_all_e64405df3996ca7863c3e815c2f6cbca.bundle``
  (4,080,171 bytes, addresses ``arlo_billboard``, ``blanche_billboard``,
  ``candela_billboard``, ``professor_billboard`` …) — the recovered
  ``npcicons_assets_teamleaders_recovered.bundle`` contains exactly those.
* ``badges_playerdynamic_assets_all_87037e48bb637139462ce7e3f3fdb0a4.bundle``
  (4,317,986 bytes, addresses ``Badge_1`` … ``Badge_81``) — the recovered
  ``badges_assets_all_recovered.bundle`` contains exactly those.

Registering both under the external catalog's names is what makes the leader
portraits and the medal grid render: the catalog resolves the address, the
server answers with bytes that really contain the sprite.

Also drops the hash-named Pixel 5 cache entries: they matched no catalog name
and would have bloated the payload by two gigabytes for nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "assets/client-runtime/registry/registry.json"
RECOVERED = ROOT / "apk/built/.recovered_push/recovered"
TARGET_DIR = ROOT / "assets/client-runtime/recovered"
CDN = ROOT / "assets/client-runtime/cdn"

BINDINGS = {
    "npctrainerassets_assets_all_e64405df3996ca7863c3e815c2f6cbca.bundle":
        "npcicons_assets_teamleaders_recovered.bundle",
    "badges_playerdynamic_assets_all_87037e48bb637139462ce7e3f3fdb0a4.bundle":
        "badges_assets_all_recovered.bundle",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".tmp-")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main() -> int:
    document = json.loads(REGISTRY.read_text(encoding="utf8"))
    files = dict(document.get("files") or {})
    before = len(files)
    files = {name: row for name, row in files.items()
             if not str(row.get("file", "")).startswith("builds/pixel5-friend/")}
    print("dropped Pixel 5 cache entries:", before - len(files))

    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    for target, source in BINDINGS.items():
        origin = RECOVERED / source
        if not origin.is_file():
            print("missing recovered bundle: " + str(origin), file=sys.stderr)
            return 1
        staged = TARGET_DIR / source
        if not staged.is_file() or staged.stat().st_size != origin.stat().st_size:
            shutil.copy2(origin, staged)
        row = {"file": staged.relative_to(ROOT).as_posix(),
               "bytes": staged.stat().st_size, "sha256": sha256_file(staged)}
        files[target] = row
        files[source] = row
        print("bound", target, "->", stage_name(row))

    for name in ("catalog_ext_12.json", "catalog_ext_12.hash"):
        path = CDN / name
        if path.is_file():
            files[name] = {"file": path.relative_to(ROOT).as_posix(),
                           "bytes": path.stat().st_size, "sha256": sha256_file(path)}
            print("registered", name, path.stat().st_size)

    document["files"] = dict(sorted(files.items()))
    document["counts"] = {"entries": len(files)}
    write_atomic(REGISTRY, (json.dumps(document, indent=1) + "\n").encode("utf8"))
    print("registry entries:", len(files))
    return 0


def stage_name(row: dict) -> str:
    return row["file"]


if __name__ == "__main__":
    sys.exit(main())
