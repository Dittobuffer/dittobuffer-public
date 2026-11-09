"""Static admin fallback page for the platform.

The console is plain HTML, CSS and JavaScript with no build step and no
external requests.  ``INDEX`` is kept for compatibility with older callers
that imported the embedded page; the browser assets live in ``static/``.
"""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"


def read_index() -> str:
    try:
        return (STATIC / "index.html").read_text(encoding="utf-8")
    except OSError:
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Dittobuffer Admin Studio</title></head><body>"
            "<h1>Dittobuffer Admin Studio</h1>"
            "<p>The static console is missing from this installation.</p>"
            "</body></html>"
        )


INDEX = read_index()
