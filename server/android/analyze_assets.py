#!/usr/bin/env python3
"""Report how each asset-manifest entry resolves on disk."""
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def resolve(sha, entry, route):
    rel = entry["file"].replace("\\", "/")
    candidates = [rel,
                  f"assets/pokemon/bundles/{sha}.bundle",
                  f"assets/pokemon/bundles/{sha}",
                  f"builds/m113-recovered-content/{sha}.bundle",
                  f"builds/m113-recovered-content/{sha}"]
    if route == "/assets/avatar":
        prefix = "builds/m9-device-UnityCache/"
        if rel.startswith(prefix):
            candidates.insert(0, "assets/avatar/bundles/" + rel[len(prefix):])
    for candidate in candidates:
        path = REPO / candidate
        if path.is_file() and path.stat().st_size == entry["bytes"]:
            return candidate
    return None


def main():
    for name, route in [("builds/m113-reviewed-assets.json", "/assets/pokemon"),
                        ("builds/m10-avatar-assets.json", "/assets/avatar")]:
        manifest = json.loads((REPO / name).read_text(encoding="utf8"))
        rows, missing, by_dir = [], [], Counter()
        bytes_by_dir = Counter()
        for sha, entry in manifest["content"].items():
            used = resolve(sha, entry, route)
            if used:
                key = "/".join(used.split("/")[:2])
                by_dir[key] += 1
                bytes_by_dir[key] += entry["bytes"]
            else:
                missing.append((sha, entry["file"].replace("\\", "/"), entry["bytes"]))
        print("==", name, "content", len(manifest["content"]),
              "bundles", len(manifest.get("bundles", [])))
        for key in sorted(by_dir):
            print("  %-40s %5d files %8.1f MiB" % (key, by_dir[key], bytes_by_dir[key] / 1048576))
        print("  unresolved:", len(missing), "total", sum(m[2] for m in missing) / 1048576, "MiB")
        prefixes = Counter("/".join(m[1].split("/")[:3]) for m in missing)
        for key, count in prefixes.most_common(8):
            print("    ", key, count)
        for row in missing[:6]:
            print("     sample:", row[1], row[2])
    return 0


if __name__ == "__main__":
    sys.exit(main())
