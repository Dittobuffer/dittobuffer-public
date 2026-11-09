"""Verify reviewed local inputs; only --apply copies missing authorized files.

No network or device access. Existing files are never replaced. The default
checks the small compatibility group; optional groups restore independently
obtained models, avatar resources, client runtime resources, or Explorer data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = "docs/local-resources.example.json"
COMPATIBILITY_PATHS = {
    "src/dittobuffer/gameplay/" + name for name in (
        "avatar-catalog.json", "battle-moves.json", "mega-rules.json",
        "pokemon-reference.json", "trainer-reference.json", "raid-items.json",
    )
} | {
    "src/dittobuffer/world/world-item-rules.json",
    "data/static/gmts.binpb.gz", "data/static/settings.binpb.gz",
}


def project_path(root: Path, relative: str) -> Path:
    """Reject both Windows and POSIX escapes, including existing symlinks."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("A nonempty forward-slash relative path is required")
    rel = PurePosixPath(relative)
    if rel.is_absolute() or PureWindowsPath(relative).drive or ".." in rel.parts or ":" in relative:
        raise ValueError("Resource path must stay within its selected root")
    value = (root / relative).resolve()
    if value == root or not value.is_relative_to(root):
        raise ValueError("Resource path leaves its selected root")
    return value


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_records(manifest: Path, groups: set[str] | None) -> list[dict]:
    document = json.loads(manifest.read_text(encoding="utf8"))
    if document.get("format") != 1 or not isinstance(document.get("files"), list):
        raise ValueError("Expected a version 1 local resource restoration manifest")
    records, seen = [], set()
    available = {row.get("group") for row in document["files"] if isinstance(row, dict)}
    if groups and not groups.issubset(available):
        raise ValueError("Unknown resource group: " + ", ".join(sorted(groups - available)))
    for row in document["files"]:
        if not isinstance(row, dict) or not isinstance(row.get("group"), str):
            raise ValueError("Every resource needs an explicit group")
        path = row.get("path")
        if not isinstance(path, str) or (
            path not in COMPATIBILITY_PATHS and not path.startswith("assets/pokemon/local/")
        ):
            raise ValueError("Restoration is restricted to compatibility files or ignored local resources")
        # Validate inactive groups too: malformed or ambiguous metadata must not
        # become accepted merely because a caller selected another group.
        project_path(Path("/").resolve(), path)
        source = row.get("source_path", path)
        project_path(Path("/").resolve(), source)
        if path.casefold() in seen:
            raise ValueError("Duplicate destination in resource manifest: " + path)
        seen.add(path.casefold())
        size, sha = row.get("bytes"), row.get("sha256")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("Resource length must be a nonnegative integer")
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("Resource SHA256 must be exactly 64 lowercase hexadecimal characters")
        if groups is None or row["group"] in groups:
            records.append(row)
    return records


def verified_file(path: Path, row: dict) -> bool:
    return path.is_file() and path.stat().st_size == row["bytes"] and digest(path) == row["sha256"]


def plan_restore(root: Path, records: list[dict], source: Path | None):
    """Validate the entire selected group before creating any directories."""
    planned, verified, failures = [], [], []
    for row in records:
        destination = project_path(root, row["path"])
        if destination.exists():
            if verified_file(destination, row):
                verified.append(row["path"])
            else:
                failures.append(row["path"] + ": existing file differs; preserved")
            continue
        if source is None:
            failures.append(row["path"] + ": missing authorized local input")
            continue
        incoming = project_path(source, row.get("source_path", row["path"]))
        if not verified_file(incoming, row):
            failures.append(row["path"] + ": authorized source missing or differs from reviewed pin")
            continue
        planned.append((incoming, destination, row))
    return planned, verified, failures


def restore_missing(root: Path, incoming: Path, destination: Path, row: dict):
    """Publish a fully reverified copy exclusively, without replacing any path."""
    destination = project_path(root, row["path"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = project_path(root, row["path"])
    temporary = destination.with_name("." + destination.name + ".restore-" + uuid.uuid4().hex)
    try:
        count, sha = 0, hashlib.sha256()
        with incoming.open("rb") as source, temporary.open("xb") as target:
            while block := source.read(1024 * 1024):
                target.write(block)
                count += len(block)
                sha.update(block)
        if count != row["bytes"] or sha.hexdigest() != row["sha256"]:
            raise ValueError("Authorized source changed during copy: " + row["path"])
        # Hard-link publication is exclusive on Windows and POSIX. A racing
        # existing file causes failure; os.replace/rename must not be used.
        destination = project_path(root, row["path"])
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)  # Only this invocation's new temporary file.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--source", type=Path, help="Root of your independently authorized source tree")
    parser.add_argument("--group", action="append", help="Repeat to select groups; default: compatibility")
    parser.add_argument("--all", action="store_true", help="Check all groups, including optional artwork/runtime")
    parser.add_argument("--apply", action="store_true", help="Copy only missing files after every selected input verifies")
    parser.add_argument("--verbose", action="store_true", help="Print every verified/planned resource path")
    args = parser.parse_args(argv)
    if args.apply and args.source is None:
        parser.error("--apply requires --source with your authorized inputs")
    if args.all and args.group:
        parser.error("Choose --all or --group, not both")
    groups = None if args.all else set(args.group or ["compatibility"])
    root = ROOT.resolve()
    source = args.source.resolve() if args.source else None
    if source is not None and not source.is_dir():
        parser.error("--source must be an existing authorized directory")
    try:
        records = load_records(project_path(root, args.manifest), groups)
        planned, verified, failures = plan_restore(root, records, source)
        if args.verbose:
            for path in verified:
                print("VERIFIED", path)
        if failures:
            for failure in failures[:20]:
                print(failure)
            if len(failures) > 20:
                print(f"... {len(failures) - 20} additional failures; see the selected manifest")
            print(json.dumps(dict(verified=len(verified), missing_or_changed=len(failures), written=0)))
            return 1
        for incoming, destination, row in planned:
            if args.apply:
                restore_missing(root, incoming, destination, row)
            if args.verbose:
                print("RESTORED" if args.apply else "WOULD_RESTORE", row["path"])
        print(json.dumps(dict(groups=sorted(groups) if groups else "all", verified=len(verified),
                              restored=len(planned) if args.apply else 0,
                              would_restore=0 if args.apply else len(planned), bytes=sum(row["bytes"] for row in records))))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print("Resource verification failed:", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
