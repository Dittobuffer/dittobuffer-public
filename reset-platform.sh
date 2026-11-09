#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3

echo "This backs up and removes only platform config/state JSON."
echo "Server profiles, player databases and game data are not touched."
exec "$PY" "$ROOT/platform/run.py" --reset --server-root "$ROOT/server"
