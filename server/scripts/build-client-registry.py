#!/usr/bin/env python3
"""Serve the complete stock addressables set the client actually asks for.

The bundled client resolves bundles through the *stock* 0.429 catalog, whose
``AssetBundleRequestOptions.m_Hash`` is the Unity cache version. The Pixel 5
export is the matching cache: ``UnityCache/Shared/<url-hash>/<cache-version>/``.
Joining the two by ``m_Hash`` turns every catalog bundle into a real file that
the server is allowed to serve, instead of the 222-entry partial allowlist that
left medals, team leaders and half the Pokemon UI blank.

The resulting registry keys are the file names in the catalog's internal IDs,
which is exactly what the client requests; ``cache_version`` also accepts the
hash-named spelling Unity falls back to.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dittobuffer.core.asset_catalog import Catalog  # noqa: E402

FRIEND = ROOT / "builds/pixel5-friend"
CATALOG_DIR = FRIEND / "com.unity.addressables"
INDEX = FRIEND / "index.json"
REGISTRY = ROOT / "assets/client-runtime/registry/registry.json"
SERVED_CATALOG = "catalog_926.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
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


def basename(internal: str) -> str:
    return str(internal).replace("\\", "/").rsplit("/", 1)[-1]


def main() -> int:
    previous = json.loads(REGISTRY.read_text(encoding="utf8")).get("files") or {}
    index = json.loads(INDEX.read_text(encoding="utf8"))
    by_version = {}
    for name, row in index.items():
        by_version.setdefault(row["cache"].lower(), []).append((name, row))

    files = {}
    # 1. The addressables catalogs themselves, byte for byte.
    for path in sorted(CATALOG_DIR.iterdir()):
        if path.is_file():
            files[path.name] = {"file": path.relative_to(ROOT).as_posix(),
                                "bytes": path.stat().st_size,
                                "sha256": sha256_file(path)}

    # 2. Every cached bundle under the name Unity stored it as.
    for name, row in index.items():
        files[name] = {"file": row["file"], "bytes": row["bytes"],
                       "sha256": sha256_file(ROOT / row["file"]),
                       "cache_version": row["key"]}

    # 3. Join the stock catalog to the cache by cache version.
    document = json.loads((CATALOG_DIR / SERVED_CATALOG).read_text(encoding="utf8"))
    catalog = Catalog(document)
    linked = missing = size_bad = 0
    missing_names = []
    for number in range(len(catalog.entries)):
        location = catalog.location(number)
        if not location["type"].endswith(".IAssetBundleResource"):
            continue
        options = (catalog.options(number) or {}).get("value") or {}
        version = str(options.get("m_Hash") or "").lower()
        name = basename(location["internal_id"])
        rows = by_version.get(version)
        if not rows:
            missing += 1
            if len(missing_names) < 20:
                missing_names.append(name)
            continue
        expected = options.get("m_BundleSize")
        chosen = next((row for _, row in rows if expected in (None, row["bytes"])), rows[0][1])
        if expected is not None and chosen["bytes"] != expected:
            size_bad += 1
        files[name] = {"file": chosen["file"], "bytes": chosen["bytes"],
                       "sha256": sha256_file(ROOT / chosen["file"]),
                       "cache_version": version}
        linked += 1

    # 4. Keep anything the old allowlist served that the new set does not cover.
    kept = 0
    for name, row in previous.items():
        if name in files:
            continue
        path = ROOT / row["file"]
        if path.is_file() and path.stat().st_size == row["bytes"]:
            files[name] = row
            kept += 1

    total_bytes = sum(row["bytes"] for row in files.values())
    print("catalog bundles linked to cache:", linked)
    print("catalog bundles without a cached version:", missing)
    if missing_names:
        print("   e.g.", missing_names[:8])
    print("size disagreements (served anyway):", size_bad)
    print("carried over from previous allowlist:", kept)
    print("registry entries:", len(files), "total %.2f GiB" % (total_bytes / 1073741824))

    payload = {"files": dict(sorted(files.items())),
               "catalog": (CATALOG_DIR / SERVED_CATALOG).relative_to(ROOT).as_posix(),
               "counts": {"entries": len(files), "catalog_bundles": linked + missing,
                          "covered": linked}}
    write_atomic(REGISTRY, (json.dumps(payload, indent=1) + "\n").encode("utf8"))
    print("wrote", REGISTRY.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
