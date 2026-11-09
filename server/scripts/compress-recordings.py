#!/usr/bin/env python3
"""Re-encode screen recordings so every file is just under 20 MB.

Two-pass H.264 with a bitrate computed from the clip's own duration: the
encoder gets exactly the budget the size limit allows, so long recordings are
not cut off and short ones keep as much quality as fits.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "recordings/raw"
OUT = ROOT / "recordings/under20mb"
LIMIT = 19_900_000          # safely below 19.99 MB, decimal or binary
AUDIO_KBPS = 96
OVERHEAD = 0.99             # container/muxing slack


def run(command, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, check=True, **kwargs)


def probe(path: Path) -> dict:
    result = run(["ffprobe", "-v", "error", "-print_format", "json",
                  "-show_format", "-show_streams", str(path)])
    return json.loads(result.stdout)


def encode(source: Path, target: Path, video_kbps: int) -> None:
    log = ROOT / "recordings" / (source.stem + ".pass")
    common = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
              "-i", str(source),
              "-c:v", "libx264", "-preset", "medium", "-profile:v", "high",
              "-pix_fmt", "yuv420p",
              "-b:v", f"{video_kbps}k",
              "-maxrate", f"{int(video_kbps * 1.4)}k",
              "-bufsize", f"{int(video_kbps * 2)}k",
              "-passlogfile", str(log)]
    run(common + ["-pass", "1", "-an", "-f", "null", "NUL" if sys.platform == "win32" else "-"])
    run(common + ["-pass", "2", "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k",
                  "-movflags", "+faststart", str(target)])
    for leftover in ROOT.glob("recordings/" + source.stem + ".pass*"):
        leftover.unlink(missing_ok=True)


def main() -> int:
    if not shutil.which("ffmpeg"):
        print("ffmpeg is not on PATH", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    sources = sorted(RAW.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    if not sources:
        print("no recordings in " + str(RAW), file=sys.stderr)
        return 1
    for source in sources:
        info = probe(source)
        duration = float(info["format"]["duration"])
        has_audio = any(stream.get("codec_type") == "audio" for stream in info["streams"])
        target = OUT / (source.stem + "-under20mb.mp4")
        audio = AUDIO_KBPS if has_audio else 0
        budget_kbps = LIMIT * 8 / 1000 / duration * OVERHEAD - audio
        kbps = max(120, int(budget_kbps))
        print(f"{source.name}: {duration:.1f}s -> {kbps} kbps video", flush=True)
        for attempt in range(4):
            encode(source, target, kbps)
            size = target.stat().st_size
            print(f"   attempt {attempt + 1}: {size} bytes ({size / 1e6:.2f} MB)", flush=True)
            if size <= LIMIT:
                break
            kbps = int(kbps * LIMIT / size * 0.98)
        if target.stat().st_size > LIMIT:
            print("FAILED to get under the limit: " + target.name, file=sys.stderr)
            return 1
    for path in sorted(OUT.glob("*.mp4")):
        print("%-58s %8.2f MB" % (path.name[:58], path.stat().st_size / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
