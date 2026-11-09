#!/usr/bin/env bash
# Copyright (C) Dittobuffer Contributors
# Copyright (C) chucny
# Copyright (C) gekkedavi
#
# This file is part of Dittobuffer. It is distributed under the GNU General
# Public License v3.0 (see LICENSE). The copyright notices in this file must
# be preserved in all copies, forks, and derivative works. Removing or
# altering these notices violates the license and copyright law.

# Stop only the server and admin processes owned by this checkout.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [ ! -x ".venv/bin/python" ]; then
    echo "No .venv in $ROOT; nothing to stop." >&2
    exit 1
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export DITTOBUFFER_ROOT="$ROOT"

.venv/bin/python - <<'PY'
from dittobuffer.cli.services import ServiceManager

manager = ServiceManager()
for name in ("admin", "server"):
    ok, message = manager.stop(name)
    print(("  [ok]   " if ok else "  [fail] ") + message)
PY
