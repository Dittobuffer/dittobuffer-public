# Release notes — 1.1.0

## Fixes retained from main

- Team-leader billboards and the medal grid now resolve through the external
  addressables catalog (`catalog_ext_12`), including a prefix fallback.
- Streets/spawns/stops use the plain-HTTP map tile origin, expand `/%d/%d/%d`,
  and freeze the map epoch once the home area is cached.
- Automatic per-tile "Dittobuffer Stop"/"Dittobuffer Gym" creation is removed,
  including existing generated entries on publish.
- Android 1.0.5 catalog and platform POI admin are included.

## Platform

- First interactive start asks for a room address and creates/hosts the room.
- The hosted room is now published as a retained, signed HiveMQ document, so
  entering the room name in the app actually finds and verifies it. Previously
  the room existed only in local JSON and the app correctly reported that no
  room was online.
- The document is refreshed automatically and republished immediately after a
  signature/room recovery.
- Missing config, key or state JSON is regenerated automatically.
- One-click **Reset signature and room** backs up state, rotates the RSA key,
  clears room bindings, hosts a new room and republishes it.
- Server workspace auto-detection supports both the release layout and a source
  clone; no machine-specific path is stored or printed.
- Console and admin paths are portable labels instead of absolute home paths.
- `--reset` removes only platform config/state after backing it up, then asks
  for a new room address.
- New Dockerfile and Compose service; release Compose includes the server
  requirements for server process control.

## Platform live world

- The platform is standalone for live-ops: rooms, POIs, spawns and raids no
  longer need a game server, Game Master, assets or server database.
- Complete live-ops editing: species percentages/weights/allow-lists, shiny
  rates, Shadow overrides, raid tier and boss weights, events and Community
  Days. The values are published in the signed room document.
- Automatic OSM POI download is on by default and needs no opt-in. Fresh
  installs use a default centre so POIs begin arriving immediately; manual
  POIs survive scans and **Clear generated** removes only automatic rows.
- OSM scans use the reachable global `overpass.openstreetmap.fr` endpoint
  instead of the frequently unavailable main Overpass host.
- New **World simulator** page: configurable wild-spawn algorithm (weighted,
  percent, hybrid or uniform), species pool, interval, radius, level/IV range,
  lifetime and cap; configurable raid interval, cap, lifetime, egg time, boss
  pool and tier algorithm.
- Automatic wild Pokémon and raid generation run in the platform process and
  are published immediately in the signed room payload.
- Manual Pokémon spawns and manual raids can be created against a POI/gym, or
  with coordinates, and deleted individually. **Clear automatic** keeps manual
  spawns/raids.
- New admin pages: **World simulator**, **POI map**, **Room payload**,
  **Spawns & algorithm**, **Raids & bosses**, **Events & Community Days** and
  **Shiny & Shadow**. All work without a server workspace.

## Privacy/licensing cleanup

Removed APKs, build artifacts, TLS keys, signing keystore, local profiles,
platform tokens, player/world databases, OSM extracts, photos, Unity/Pokémon
bundles, Game Master binaries and Niantic plugin binaries. Added a neutral
loopback `server/config/server.json` and documentation for supplying authorized
resources separately.
- Removed internal development reports, device serials, workstation paths and
  hardcoded build-Python paths from the distributable. Android builds use a
  portable `python`/`python3` default; set `BUILD_PYTHON` to override it.

## Verification

- Platform unittest suite: **56 passed**.
- Real HiveMQ publish/discover round trip: the platform-signed retained room
  document was retrieved and verified by the same code path the app uses.
- A live room was republished and verified after the fix.
- The platform admin has a **POI map** with same-origin proxied OpenStreetMap tiles, platform POIs, active spawns and active raids, plus the standalone **World simulator**.
- New world tests cover automatic generation, manual spawns/raids (including
  coordinate-only manual spawns without a POI), deletion, payload publication
  and the no-workspace admin API.
- First launch creates the configured SQLite database and its 65-table schema automatically.
- Linux shell launchers pass `bash -n`; the POSIX server-stop path uses
  `SIGTERM` and is covered by a regression test.
- JavaScript syntax checks passed.
- Manifest JSON validation passed.
- Git history checks confirm the medals and map-tile commits are ancestors of
  this branch.
- Dockerfiles and Compose files were inspected but not launched because Docker
  is not installed on the build host.
