# Dittobuffer Family 1.0.0

Release date: 2026-10-28

This release packages the current family server, standalone Android client and
portable startup tooling.

## Fixed in this release

- **Five-minute loading screen / ~90% stall.** The 0.429.2 client probes the
  optional method `610001` during startup. The server returned HTTP 400 for the
  unknown method, so the native transport retried on a five-minute backoff.
  The probe now receives a successful empty reply and map loading continues.
- **Missing Pokéstops and gyms.** A missing `pokestopimage.png` made the whole
  map-forts handler raise `FileNotFoundError`; the map RPC then returned 400.
  The project-owned placeholder is included at the repository root.
- **Runaway OpenStreetMap downloader.** The old profile expanded outward forever
  and requested a 5 km radius around every map poll. Each downloaded cell
  invalidated the client map epoch, so the phone kept regenerating tiles. The
  release profile uses a 700 m prefetch radius and disables background ring
  expansion; the downloader also has a hard 12-ring safety cap.
- **Null Island poll.** A map request with `0,0` before the GPS fix downloaded a
  cell at `(0,0)` and dropped the live map epoch. Empty location polls are
  ignored by the OSM queue and no longer overwrite the admin location.
- **Avatar bundles.** The asset catalog falls back to the portable
  `assets/avatar/bundles/...` layout when the historical Unity cache path is
  absent.
- **Loopback profiles and gnirehtet.** Loopback profiles do not start the relay
  VPN; reconnect force-stops a dead relay and relies on ADB reverse tunnels.
- **Standalone splash/resource routing.** Client resources only route through
  the local server when `client_resources` is actually enabled.

## Included artifacts

- `artifacts/dittobuffer-family-127.0.0.1.apk` — signed arm64-v8a standalone
  client, last verified working build (`70ff8bee…`). The `client/` sources in
  this folder include newer routing fixes; rebuild the APK to pick them up.
- `artifacts/dittobuffer-family-127.0.0.1.build.json` — build profile and SHA-256.
- `SHA256SUMS` — checksums for the APK and release archives.

## Verification performed

- `python -m unittest tests.test_osm_cache tests.test_service_safety` — 22 tests.
- `node scripts/test-client-family.js` — credential and resource-routing regressions.
- Standalone APK installed over the existing app without deleting data;
  launcher, saved account, map objects, roads, stops and spawns verified.
- `apksigner verify` passed for the build inputs; the shipped APK is the last
  known-good signed family build.

## Known limits

- The Android native libraries are **arm64-v8a only**.
- A published Netherlands world snapshot is not included. The server starts
  with an empty 3D world and uses OSM vector tiles plus the player database.
- The APK is ~190 MB, above GitHub's regular file limit. The repository uses a
  Git LFS rule for `*.apk`; alternatively attach it to a GitHub Release.
- The Docker image is multi-arch for amd64/arm64. RISC-V is supported through
  the native Python bootstrap and the best-effort `Dockerfile.riscv64`.
