#!/bin/bash
# Build the APK with the project-local toolchain. No Android Studio, no admin.
set -euo pipefail
cd "$(dirname "$0")"
TOOLS="$(cd ../.private/toolchain && pwd)"
export JAVA_HOME="$TOOLS/jdk21"
export ANDROID_HOME="$TOOLS/sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
GRADLE="$TOOLS/gradle-8.9/bin/gradle"
# Chaquopy 17 requires exactly Python 3.10 as its build interpreter, so the
# toolchain carries a private per-user 3.10 (no admin, no system changes).
# Chaquopy needs a Windows path (C:/...); MSYS /c/... becomes a relative
# path inside Gradle and fails with "not a valid Python 3.10 command".
TOOLS_WIN="$(cygpath -m "$TOOLS")"
export BUILD_PYTHON="${BUILD_PYTHON:-$TOOLS_WIN/python310/python.exe}"

if [ ! -x "$GRADLE" ]; then
  echo "missing $GRADLE" >&2
  exit 1
fi

py make_bootstrap.py
# SKIP_PAYLOAD=1 reuses the already packed payload parts (they are ~5 GiB).
if [ "${SKIP_PAYLOAD:-0}" != "1" ]; then
  py make_payload.py
fi

# The patched family client ships inside the APK so a fresh phone needs nothing
# but this file. It is a separate package; the vanilla game is never touched.
CLIENT="../builds/kit-standalone/dittobuffer-family.apk"
if [ -f "$CLIENT" ]; then
  mkdir -p app/src/main/assets/client
  cp "$CLIENT" app/src/main/assets/client/dittobuffer-family.apk
  echo "bundled client: $(du -h "$CLIENT" | cut -f1)"
else
  echo "warning: $CLIENT missing; APK will not bundle the client" >&2
fi

# The launcher/wordmark icons are the exact committed PNGs under
# app/src/main/res/drawable-nodpi, so the build never redraws an external logo.

# One ABI only: the Pixel is arm64 and this keeps the APK small.
"$GRADLE" --no-daemon :app:assembleDebug "$@"

APK="app/build/outputs/apk/debug/app-debug.apk"
mkdir -p ../apk/built
rm -f ../apk/built/dittobuffer-mobile-debug.apk
cp "$APK" ../apk/built/dittobuffer-mobile-base.apk
# Payload parts are separate split APKs: a Zip32 base APK cannot exceed 4 GiB.
if [ "${SKIP_SPLITS:-0}" != "1" ]; then
  py make_splits.py
fi
ls -la ../apk/built | awk '{printf "%12s  %s\n", $5, $9}'
