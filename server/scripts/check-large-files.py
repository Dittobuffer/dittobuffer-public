"""Reject oversized Git blobs while validating existing Git LFS payloads.

Git refuses ordinary Git blobs above 100 MiB. Existing LFS objects are
accepted only when their indexed pointer, filter, length and SHA-256 agree. Run this
before committing (``make check-large-files``) and in CI on every push.

Usage::

    python scripts/check-large-files.py [--limit-mib 100]

Exit codes: ``0`` clean, ``1`` at least one tracked file is too large.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_LIMIT_MIB = 100
LFS_POINTER = re.compile(rb"version https://git-lfs.github.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize ([0-9]+)\n?\Z")


def tracked_files() -> list[Path]:
    """Every path in the index, using NUL separation for non-ASCII names."""

    result = subprocess.run(
        ["git", "ls-files", "-z"],
        capture_output=True,
        check=True,
    )
    return [Path(name) for name in result.stdout.decode("utf-8").split("\0") if name]


def index_entries() -> list[tuple[str, str, Path]]:
    result = subprocess.run(["git", "ls-files", "--stage", "-z"], capture_output=True, check=True)
    entries = []
    for row in result.stdout.decode("utf-8").split("\0"):
        if row:
            metadata, name = row.split("\t", 1)
            mode, oid, stage = metadata.split()
            if stage == "0":
                entries.append((mode, oid, Path(name)))
    return entries


def verified_lfs_payload(path: Path, oid: str, size: int) -> bool:
    # Only a small, canonical pointer already in Git's index can authorize a
    # large checked-out LFS object. A filter attribute alone is insufficient.
    pointer = subprocess.run(["git", "cat-file", "blob", oid], capture_output=True, check=True).stdout
    match = LFS_POINTER.fullmatch(pointer)
    if not match or int(match[2]) != size:
        return False
    attr = subprocess.run(["git", "check-attr", "-z", "filter", "--", path.as_posix()], capture_output=True, check=True).stdout.split(b"\0")
    if len(attr) < 3 or attr[2] != b"lfs":
        return False
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest().encode() == match[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit-mib", type=int, default=DEFAULT_LIMIT_MIB)
    limit = parser.parse_args(argv).limit_mib * 1024 * 1024
    entries = index_entries()
    unique = list(dict.fromkeys(oid for mode, oid, path in entries))
    sizes = {}
    if unique:
        result = subprocess.run(["git", "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"], input=("\n".join(unique) + "\n").encode(), capture_output=True, check=True)
        for line in result.stdout.decode().splitlines():
            oid, kind, size = line.split()
            sizes[oid] = (kind, int(size))
    oversized = []
    lfs_count = 0
    for mode, oid, path in entries:
        kind, indexed_size = sizes[oid]
        # Inspect index bytes even if the working file is missing or smaller.
        if kind == "blob" and indexed_size > limit:
            oversized.append((indexed_size, path, "Git index blob"))
            continue
        if mode not in ("100644", "100755"):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > limit:
            if indexed_size <= 1024 and verified_lfs_payload(path, oid, size):
                lfs_count += 1
            else:
                oversized.append((size, path, "working file without a matching LFS pointer"))
    if not oversized:
        print(f"OK: no ordinary Git blob exceeds {limit // 1024 // 1024} MiB; {lfs_count} large LFS payload(s) verified")
        return 0
    for size, path, reason in sorted(oversized, reverse=True):
        print(f"{size / 1024 / 1024:8.1f} MiB  {path} ({reason})")
    print(f"\n{len(oversized)} file(s) exceed {limit // 1024 // 1024} MiB without valid Git LFS storage. Review the files before publishing; do not change repository ignore rules automatically.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
