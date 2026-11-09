#!/usr/bin/env bash
# Start the Dittobuffer server and admin panel on Linux, macOS, ARM or RISC-V.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [ ! -x ".venv/bin/python" ]; then
    bash "$ROOT/scripts/bootstrap.sh"
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export DITTOBUFFER_ROOT="$ROOT"

echo "Dittobuffer family server"
echo "  project: $ROOT"
echo "  host:    $(uname -s) $(uname -m)"
echo "  python:  $(.venv/bin/python --version 2>&1)"
echo

.venv/bin/python - <<'PY'
import sys
from dittobuffer.cli.services import ServiceManager

manager = ServiceManager()
results = [manager.start(name) for name in ("server", "admin")]
for ok, message in results:
    print(("  [ok]   " if ok else "  [fail] ") + message)
if not all(ok for ok, _ in results):
    sys.exit(1)
print()
print("  Admin:  .venv/bin/python scripts/open-admin.py (private access link)")
print("  Server: https://127.0.0.1:8445")
print("  Map:    http://127.0.0.1:8081/map/tiles")
PY

echo
echo "Phone setup (USB):"
echo "  adb reverse tcp:8445 tcp:8445"
echo "  adb reverse tcp:8080 tcp:8080"
echo "  adb reverse tcp:8081 tcp:8081"
echo
echo "Follow logs with: tail -f logs/server.log logs/admin.log"
echo "Stop with:        bash stop-server.sh"
