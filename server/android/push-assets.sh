#!/bin/bash
# Fast asset update: push the recovered team-leader and badge bundles plus the
# runtime registry to the app's server tree. No payload/APK rebuild.
set -euo pipefail
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'
cd "$(dirname "$0")"
REPO="$(cd .. && pwd)"
PKG=org.dittobuffer.mobile
DEST="/sdcard/Android/data/$PKG/files/server"
ADB="${ADB:-adb}"

# MSYS_NO_PATHCONV keeps the remote /sdcard path intact, but adb.exe still
# needs a Windows path for each local file.
local_path() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}

"$ADB" shell mkdir -p "$DEST/assets/client-runtime/recovered" "$DEST/assets/client-runtime/registry" "$DEST/assets/client-runtime/cdn"
"$ADB" push "$(local_path "$REPO/assets/client-runtime/recovered/npcicons_assets_teamleaders_recovered.bundle")" \
  "$DEST/assets/client-runtime/recovered/"
"$ADB" push "$(local_path "$REPO/assets/client-runtime/recovered/badges_assets_all_recovered.bundle")" \
  "$DEST/assets/client-runtime/recovered/"
# The catalog addresses three distinct badge bundles. The player atlas does
# not contain the legacy frame/default-badge sprites or the event alias/settings
# assets, so those two bundles must be served exactly as recovered.
"$ADB" push "$(local_path "$REPO/assets/client-runtime/recovered/badges_generallegacy_assets_all_1644f3cf4b8b0b1426d88365b0c86f27.bundle")" \
  "$DEST/assets/client-runtime/recovered/"
"$ADB" push "$(local_path "$REPO/assets/client-runtime/recovered/badges_eventdynamic_assets_all_fc1210107c70f65681170f4b76dbc413.bundle")" \
  "$DEST/assets/client-runtime/recovered/"
# The main catalog carries the merged badge/team-leader entries and must
# travel with the registry that describes it.
"$ADB" push "$(local_path "$REPO/builds/m151-client-runtime-v3/catalog_926.json")" \
  "$DEST/builds/m151-client-runtime-v3/"
"$ADB" push "$(local_path "$REPO/builds/m151-client-runtime-v3/catalog_926.hash")" \
  "$DEST/builds/m151-client-runtime-v3/"
# The external catalog pair is what makes the recovered atlases addressable.
# Pushing the bundles without it was the original blank-medals regression.
"$ADB" push "$(local_path "$REPO/assets/client-runtime/cdn/catalog_ext_12.json")" \
  "$DEST/assets/client-runtime/cdn/"
"$ADB" push "$(local_path "$REPO/assets/client-runtime/cdn/catalog_ext_12.hash")" \
  "$DEST/assets/client-runtime/cdn/"
"$ADB" push "$(local_path "$REPO/assets/client-runtime/registry/registry.json")" \
  "$DEST/assets/client-runtime/registry/registry.json"
# The app only reads these, but the registry must stay readable after an
# adb-pushed update owned by the shell user.
"$ADB" shell "chmod -R ugo+rX '$DEST/assets/client-runtime' 2>/dev/null || true"
echo "Recovered assets pushed to $DEST"
echo "Restart the server in Dittobuffer (or force-stop and reopen the app) to reload the registry."
