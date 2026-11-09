#!/bin/bash
# Push the big payload once. Later updates only need push-code.sh.
set -euo pipefail
# Git Bash would rewrite /sdcard/... into a Windows path before adb sees it.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'
cd "$(dirname "$0")"
REPO="$(cd .. && pwd)"
PKG=org.dittobuffer.mobile
DEST="/sdcard/Android/data/$PKG/files/server"
TAR="$REPO/.private/payload.tar"

adb shell mkdir -p "$DEST" "$DEST/logs"

echo "packing payload (assets, builds manifests, data, tls)…"
tar -C "$REPO" -cf "$TAR" \
  assets/pokemon assets/avatar assets/world assets/static assets/game-master \
  builds/m113-reviewed-assets.json builds/m113-reviewed-assets.catalog.json \
  builds/m10-avatar-assets.json builds/m10-avatar-assets.catalog.json \
  builds/registry.json \
  data/maps data/static data/world \
  "$([ -f data/family-development.sqlite3 ] && echo data/family-development.sqlite3)" \
  config/tls

SIZE=$(du -h "$TAR" | cut -f1)
echo "pushing $SIZE …"
adb push "$TAR" "$DEST/_payload.tar"
echo "extracting on device…"
adb shell "cd '$DEST' && tar -xf _payload.tar && rm -f _payload.tar"
rm -f "$TAR"
adb shell "du -sh '$DEST'" | tail -1
echo "done. Restart the server in the app."
