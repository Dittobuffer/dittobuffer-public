#!/usr/bin/env bash
# Copyright (C) Dittobuffer Contributors
# Copyright (C) chucny
# Copyright (C) gekkedavi
#
# This file is part of Dittobuffer. It is distributed under the GNU General
# Public License v3.0 (see LICENSE). The copyright notices in this file must
# be preserved in all copies, forks, and derivative works. Removing or
# altering these notices violates the license and copyright law.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"

if [ ! -x "$PY" ]; then
    echo "Creating Python environment..."
    python3 -m venv "$ROOT/.venv"
fi

echo "Installing/updating server requirements..."
"$PY" -m pip install --disable-pip-version-check -q -r "$ROOT/server/requirements.txt"

export PYTHONPATH="$ROOT/server/src:$ROOT/server${PYTHONPATH:+:$PYTHONPATH}"
echo
echo "Starting platform at http://127.0.0.1:8780"
echo "The first run asks for a room address."
echo
exec "$PY" "$ROOT/platform/run.py" --server-root "$ROOT/server"
