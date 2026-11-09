#!/bin/bash
# Fast update: push server source and config only. No APK rebuild.
set -euo pipefail
# Git Bash would rewrite /sdcard/... into a Windows path before adb sees it.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'
cd "$(dirname "$0")"
REPO="$(cd .. && pwd)"
PKG=org.dittobuffer.mobile
DEST="/sdcard/Android/data/$PKG/files/server"
TAR="$REPO/.private/code.tar"

adb shell mkdir -p "$DEST"
tar -C "$REPO" -cf "$TAR" src protocol config \
  data/static/gmts.binpb.gz data/static/max-reference.json data/static/pvp-reference.json
# adb needs a Windows-native path; MSYS_NO_PATHCONV leaves /c/... unchanged.
cd "$REPO"
adb push .private/code.tar "$DEST/_code.tar"
adb shell "cd '$DEST' && rm -rf src protocol config && tar -xf _code.tar && rm -f _code.tar"
# tar extracted these as the adb shell user; the app must still write
# config/mobile.json and its logs, so hand the trees back to every uid.
adb shell "chmod -R ugo+rwX '$DEST/src' '$DEST/protocol' '$DEST/config'"
rm -f "$TAR"
echo "code pushed. Restart the server in the app (no APK rebuild)."
