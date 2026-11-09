"""Filesystem anchors for the Dittobuffer server.

Every module resolves project paths through this file so the package can be
moved, vendored, or run from an installed wheel without breaking.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: Repository root (the directory that holds ``src/``, ``assets/`` and ``data/``).
ROOT = Path(__file__).resolve().parents[2]

#: Absolute path of the installed package itself.
PACKAGE = Path(__file__).resolve().parent

DATA = ROOT / "data"
ASSETS = ROOT / "assets"
CONFIG = ROOT / "config"
LOGS = ROOT / "logs"
BUILDS = ROOT / "builds"
PROTOCOL = ROOT / "protocol"
RESOURCES = PACKAGE / "resources"
GAMEPLAY = PACKAGE / "gameplay"
WORLD = PACKAGE / "world"

# Generated protobuf modules import each other by their top-level name, so the
# protocol directory has to be importable on its own.
if str(PROTOCOL) not in sys.path:
    sys.path.insert(0, str(PROTOCOL))


def ensure_directories() -> None:
    """Create the runtime directories the server writes into."""

    for path in (DATA, CONFIG, LOGS, DATA / "admin", DATA / "world"):
        path.mkdir(parents=True, exist_ok=True)
