# Clean branch notes

This branch is based directly on `main` at `bf514d9`. It keeps the requested
medals/team-leaders asset fix (`489f388`) and map-tile fixes (`8ba1ab7`,
`8596d82`, `5ec5793`) while removing material that should not be published.

Removed:

- APKs, patched client artifacts, build/injection metadata and SHA sums.
- TLS certificates/private keys and the APK signing keystore.
- Tracked local profiles, admin configuration and generated platform state.
- Player/world databases, OSM extracts, map caches and world photos.
- Pokémon/Unity asset bundles, Niantic plugin binaries, photos and generated
  artwork. Game Master data is included in this source release.

Added:

- `config/server.json`: safe loopback profile with no credentials, local paths
  or home coordinate.
- Platform recovery/reset support and Docker files under `platform/`.
- This notice and a rewritten README/config guide.

This is a working-tree cleanup, not a history rewrite. Old blobs still exist in
the clone's commit history and on the remote until a coordinated history purge
and force-push. Do not treat this branch as a history scrub by itself.
