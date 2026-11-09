#!/usr/bin/env bash
# Copyright (C) Dittobuffer Contributors
# Copyright (C) chucny
# Copyright (C) gekkedavi
#
# This file is part of Dittobuffer. It is distributed under the GNU General
# Public License v3.0 (see LICENSE). The copyright notices in this file must
# be preserved in all copies, forks, and derivative works. Removing or
# altering these notices violates the license and copyright law.

# Create the project-local virtual environment for any POSIX host.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -x ".venv/bin/python" ]; then
    echo "Using existing .venv ($(.venv/bin/python -c 'import platform; print(platform.machine())'))"
    exit 0
fi

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && \
           "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            PYTHON="$candidate"
            break
        fi
    done
fi
if [ -z "$PYTHON" ]; then
    echo "Python 3.11 or newer is required. Install it with your package manager, then rerun." >&2
    echo "Debian/Ubuntu: sudo apt install python3 python3-venv python3-pip" >&2
    echo "macOS:         brew install python@3.13" >&2
    exit 1
fi

ARCH="$(uname -m)"
echo "Bootstrapping Dittobuffer on ${ARCH} with $("$PYTHON" --version 2>&1)"
"$PYTHON" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

echo
echo "Runtime ready: $ROOT/.venv"
echo "Start it with:  ./start-server.sh"
