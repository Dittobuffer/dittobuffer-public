#!/usr/bin/env python3
"""Map every bundle in the Pixel 5 UnityCache export to its AssetBundle name.

Unity names an addressable bundle after the file the client asks for, so the
``AssetBundle.m_Name`` inside ``__data`` is the catalog bundle name. The cache
directory names are opaque URL hashes and cannot be reversed, which is why the
export has to be read this way.
"""
import json
import sys
import time
from pathlib import Path

import UnityPy

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "builds/pixel5-friend/UnityCache/Shared"
OUT = ROOT / "builds/pixel5-friend/index.json"
LOG = ROOT / "logs/pixel5-index.log"


def log(message):
    print(message, flush=True)


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    datas = sorted(BASE.glob("*/*/__data"))
    log("indexing %d cache entries" % len(datas))
    started = time.time()
    index, bad = {}, 0
    for number, path in enumerate(datas, 1):
        try:
            env = UnityPy.load(str(path))
            name = None
            for obj in env.objects:
                if obj.type.name == "AssetBundle":
                    name = obj.read().m_Name
                    break
            if name:
                index[name] = {
                    "file": path.relative_to(ROOT).as_posix(),
                    "bytes": path.stat().st_size,
                    "cache": path.parent.name,
                    "key": path.parent.parent.name,
                }
            else:
                bad += 1
        except Exception:
            bad += 1
        if number % 200 == 0:
            log("%d/%d named=%d unreadable=%d %.0fs"
                % (number, len(datas), len(index), bad, time.time() - started))
    OUT.write_text(json.dumps(index, indent=0), encoding="utf8")
    log("TOTAL named=%d unreadable=%d %.0fs" % (len(index), bad, time.time() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
