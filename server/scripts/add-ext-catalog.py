#!/usr/bin/env python3
"""Serve the external addressables catalog the client keeps asking for.

The device log showed the only failing resource request is
``catalog_ext_12.json`` / ``.hash`` (and its old ``unsupported.bundle``
fallback). Whatever that catalog defines — NPC billboards, badge sprites and
the rest of the external content — can never resolve while the catalog itself
404s.

The Pixel 5 handoff contains the stock external catalog as
``-366816915.json`` (407 bundles, including the badge and team-leader bundles).
Its stock locator is ``AddressablesMainContentCatalog``, the same locator as
the main catalog the client already loads. Addressables rejects a second
catalog with that identity before it can use any of its addresses, so this
script stages the catalog under a distinct locator and a matching derived
hash. It adds every bundle it lists to the allowlist when the bytes exist
locally.
"""
from __future__ import annotations

import argparse
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
SOURCE = FRIEND / "com.unity.addressables/-366816915.json"
SOURCE_HASH = FRIEND / "com.unity.addressables/-366816915.hash"
CDN = ROOT / "assets/client-runtime/cdn"
REGISTRY = ROOT / "assets/client-runtime/registry/registry.json"
INDEX = FRIEND / "index.json"


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


def add_file(files: dict, key: str, path: Path) -> bool:
    if not path.is_file():
        return False
    relative = path.relative_to(ROOT).as_posix()
    row = {"file": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
    if files.get(key) == row:
        return False
    files[key] = row
    return True


def normalized_catalog() -> tuple[bytes, bytes]:
    """Give the external catalog a distinct Addressables identity.

    The stock catalog and ``catalog_926`` both call themselves
    ``AddressablesMainContentCatalog``. A second catalog with the same locator
    is rejected by the runtime, which is why the leader and badge addresses
    never become available. Keep every catalog byte otherwise unchanged.
    """
    document = json.loads(SOURCE.read_text(encoding="utf8"))
    document["m_LocatorId"] = "AddressablesMainContentCatalog_ext12"
    encoded = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf8")
    digest = hashlib.md5(encoded).hexdigest()
    document["m_BuildResultHash"] = digest
    encoded = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf8")
    return encoded, hashlib.md5(encoded).hexdigest().encode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-only", action="store_true",
                        help="Refresh only catalog_ext_12 and its registry rows")
    args = parser.parse_args()
    if not SOURCE.is_file():
        print("missing " + str(SOURCE), file=sys.stderr)
        return 1
    CDN.mkdir(parents=True, exist_ok=True)
    catalog = CDN / "catalog_ext_12.json"
    catalog_hash = CDN / "catalog_ext_12.hash"
    encoded, digest = normalized_catalog()
    write_atomic(catalog, encoded)
    write_atomic(catalog_hash, digest)
    print("staged", catalog.name, catalog.stat().st_size,
          "locator", json.loads(encoded)["m_LocatorId"],
          "hash", catalog_hash.read_text(encoding="utf8").strip())

    document = json.loads(REGISTRY.read_text(encoding="utf8"))
    files = dict(document.get("files") or {})
    add_file(files, "catalog_ext_12.json", catalog)
    add_file(files, "catalog_ext_12.hash", catalog_hash)

    decoded = Catalog(json.loads(catalog.read_text(encoding="utf8")))
    bundles = [i for i in range(len(decoded.entries))
               if decoded.location(i)["type"].endswith(".IAssetBundleResource")]
    added = 0
    if args.catalog_only:
        print("external catalog bundles:", len(bundles), "bundle import skipped")
    else:
        index = json.loads(INDEX.read_text(encoding="utf8")) if INDEX.is_file() else {}
        for i in bundles:
            options = (decoded.options(i) or {}).get("value") or {}
            name = str(options.get("m_BundleName") or "")
            if not name:
                continue
            key = name if name.endswith(".bundle") else name + ".bundle"
            if key in files:
                continue
            row = index.get(key)
            if row:
                if add_file(files, key, ROOT / row["file"]):
                    added += 1
        print("external catalog bundles:", len(bundles), "added from cache:", added)

    document["files"] = dict(sorted(files.items()))
    document["counts"] = {"entries": len(files)}
    write_atomic(REGISTRY, (json.dumps(document, indent=1) + "\n").encode("utf8"))
    print("registry entries:", len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
