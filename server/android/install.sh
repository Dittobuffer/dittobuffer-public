#!/bin/bash
# Install the base APK and every payload split as one app.
set -euo pipefail
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'
cd "$(dirname "$0")"
OUT="$(cd ../apk/built && pwd)"
# adb on Windows cannot stat an MSYS /c/... path; relative names always work.
cd "$OUT"
shopt -s nullglob
SPLITS=(payload[0-9]*.apk)
[ -f dittobuffer-mobile-base.apk ] || { echo "missing base APK; run build.sh" >&2; exit 1; }
[ ${#SPLITS[@]} -gt 0 ] || { echo "missing payload splits; run make_splits.py" >&2; exit 1; }
echo "installing base + ${#SPLITS[@]} payload splits…"
adb install-multiple -r -g dittobuffer-mobile-base.apk "${SPLITS[@]}"
adb shell pm grant org.dittobuffer.mobile android.permission.POST_NOTIFICATIONS 2>/dev/null || true
echo "installed $(adb shell dumpsys package org.dittobuffer.mobile | grep -m1 versionName | tr -d '\r')"
