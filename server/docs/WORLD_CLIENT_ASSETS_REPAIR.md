# World and client asset repair ? 2026-10-27

Work was performed only in `E:\DittoBuffer`, on
`fix/world-client-assets-20261027`, starting at origin/main
`bd2ab84010b8a47cfd72d02d2f0cb275e4817e91`.
The preserved integration branch `integration/workspace-migration-20261027`
(`29ce82e6cd16c6dd62b52339b0c770710d1a37b7`) was merged into this feature
branch, without merging main or rewriting history. This reuses the existing
Routes/Mateo/Vivillon, Rocket, Max and Mythical integration rather than replacing
it with the older server code that was on main.

## Causes and fixes

1. Every native map RPC 106 failed with `Max eligibility is not explicitly
   released`. Reproduction on a database copy failed for all 40 nearby stations.
   The preserved M18.2 explicit-zero-form compatibility fix restores the source-
   backed base identities. It does not release arbitrary forms or disable the
   Max allowlist. A regression now covers all 13 configured base species in a
   map-plus-inventory RPC batch, including wild Pokemon and forts.
2. Server-side client resources were disabled and referenced an obsolete M16
   registry. The APK already embedded M23's catalog. The running local profile
   now uses the exact verified 388-file registry. The new public metadata file
   `config/resources/client-runtime-m23.json` describes those same resources;
   113 missing restoration entries were added to `docs/local-resources.example.json`.
   Every integrated resource came from existing local files; no asset downloader
   was run and no payload was added to Git.
3. `world.json` selected the small development world, despite the preserved
   Netherlands publication being available locally. The running local profile
   now explicitly selects `data/world/current.json`, with `world.json` absent.
   `/world/health` confirms `data/world/netherlands-261020-v2.sqlite3`, epoch
   1249208389. The publication itself was not modified.
4. Portable overlays pinned the historical manifests' pre-repair byte hashes.
   The user's local edits changed only `content[*].file`: 2,739 Pokemon paths
   and 46 avatar paths. A reviewed identity pin now permits only the two kinds
   of paths that the overlay replaces. Hashes, lengths, catalog identity,
   appearance mappings and dependency evidence remain pinned. Mutation tests
   reject changed content identities, sizes and costumes. The original legacy
   files and both `.backup` files remain byte-identical to the starting copy.

The launcher now checks the embedded runtime catalog digest against `/health`
before starting Unity. Disabled or mismatched resources produce an explicit
setup error. Standalone runtime requests for the official asset CDN are routed
locally; unsupported paths fail locally rather than downloading more resources.

The running `config/family.json` changes are intentionally local, unstaged
settings. The repository's default clone configuration remains the small JSON
world and does not claim that proprietary artwork ships with the source. Follow
[ASSETS.md](ASSETS.md) to restore authorized resources and opt in explicitly.

## Asset evidence

| Store | Checked content files | Bytes | Failed sizes/hashes |
|---|---:|---:|---:|
| Pokemon | 2,739 | 1,746,562,850 | 0 |
| Avatar | 46 | 6,482,521 | 0 |
| M23 runtime | 388 | Registry lengths and SHA-256 checked | 0 |

Catalog SHA-256:
`984af473b5db3b74247a1c264fc4bcb86070b3c48d65203a4f6d6c071d5a81f9`.

Pokemon manifest SHA-256:
`2953d2322c121f2369c045d50e17e8ea3c6449528f142d8a8562a1d1704f9753`.
Avatar manifest SHA-256:
`5f8e1b72a28b6b0976ed2da20187a355a32cf7cbc485dd3ddb74719c55f66dcc`.

## Android and server verification

Device: rooted Pixel 5, Android 13, ARM64 (`arm64-v8a`), serial
`15191FDD4003PY`, development package
`org.librenlp.pokemongo.dev`, client 0.429.2. No Samsung tests were performed.

| Check | Evidence and result |
|---|---|
| Map before repair | Roads and avatar visible; wild Pokemon and forts absent; RPC 106 fails |
| Corrected server on copied DB | Status 1, nine cells, 59 wild Pokemon, two forts and two Power Spots |
| Live published-world map | Native receipt: status 1, 22 cells, 60 wild Pokemon, eight Pokestops, two gyms, four Power Spots |
| Wild models | Multiple distinct models visibly rendered on the actual map |
| Avatar | Actual overworld model and profile portrait visibly rendered |
| Gym rendering | Neutral gym structure visibly rendered |
| Wild encounter | Actual map tap opened Magnemite; correct textured model and catch UI visible |
| Cold restart | Saved account opens the game and receives valid map responses |
| Pokestop icons | **NOT_VERIFIED visually**; eight stops are delivered, nearest about 123 m in the captured receipt. Screenshots examined did not establish visible stop icons |
| Power Spot/raid interactions | Map data checked; battle interaction **NOT_TESTED** in this repair |
| Unrelated gameplay | Automated regression coverage; no claim of fresh native coverage for every preserved subsystem |

The Pixel was being used for additional live gameplay during verification.
Further agent touch input was paused to avoid conflicting actions; the
coordination question remains pending. No visibility fixture was connected to
Android. The unused isolated listener was stopped. The current device connection
continues to use the real family database, not the test copy.

Next native check: inspect the stop view/renderer at a known delivered stop,
including its visibility state, prefab resolution and map-side image reference.
Compare those observations against its decoded `PokemonFortProto` before
changing model registrations or proto fields. Do not report Pokestop rendering
as fixed solely from the successful RPC counts.

## APK

New output, installed with `adb -s 15191FDD4003PY install -r`:
`apk/built/dittobuffer-world-assets-8445-20261027.apk`.

- Size: 201,176,002 bytes.
- SHA-256: `e6ac41dd2a8b9ddc9ac9d7f0bb4b22a9ff68727f349d94e59a2e2e61f3c9b4cb`.
- Fresh ARM64 native plugin SHA-256:
  `5719e5d6d41dfd8ca4a207a46a8c312982769f2ac86f07e7b97866395739b709`.
- Full Java/D8, apktool, zipalign, signing and signature verification completed.
- Original `classes.dex`, `classes2.dex`, `classes3.dex`, `libil2cpp.so` and the
  previous embedded catalog remain byte-identical.
- Current family CA is embedded; origin is HTTPS loopback port 8445. No fixture
  origin or fixture-account profile is packaged.
- Installed APK hash matches the named output. The earlier installed APK and
  app-private files were backed up under the ignored evidence directory.

## Tests

- Complete Python suite: **680 passed, 7 skipped**. Skips: unavailable Windows
  symlink creation and six optional historical proprietary palette/prop fixtures.
- Four Node suites: UI, raids, family and world passed. Includes standalone
  local-only runtime routing and 20-cycle navigation/Max Ball checks.
- Rust: **158 passed, 2 ignored**; successful ARM64 NDK release build.
- ARM64 exports and SONAME verified against the independently supplied split.
- Produced APK preservation/client/form/TLS tests: **15 passed**.
- Final updated restoration and real TLS HTTP tests: **17 passed, 1 skipped**.

The first full run exposed the repaired-manifest pin incompatibility. That
failure was fixed without reverting the local repairs; the complete subsequent
run passed. Host tests are distinct from the native observations above.

## Preservation and private evidence

The live SQLite database was never reset, deleted or restored from a snapshot.
New captures/progression made while the phone was in use were retained. The
baseline snapshot remains a backup, not a replacement for current player data.
SQLite `quick_check` returned `ok`.

Both `.gitignore` files, both repaired manifests, their backups and
`data/world/admin-pois.json` match their starting hashes. The published world
still hashes to
`ca7568429854a23d5d46ffd218acbab5378d56d9d49779d9041d808b7dfec81e`.
Both official Pokemon GO APK hashes match the pre-install check. No official
app or official resource cache was modified. Original workspaces and private
transfer archives were not altered.

Local evidence is under `.private/world-client-assets-20261027/` (not uploaded):

- `before.json`, `checkpoint/`, `family-before.sqlite3`;
- `reproduce-before-stations.json`, `map-after.json`, `live-map-summary.json`;
- `full-asset-audit.json`, `preservation-after.json`;
- `screen-before.png`, `screen-new-map.png`, `screen-wild-encounter.png`,
  `screen-map-recovered.png`, `screen-map-rotated.png`;
- Android logs, scoped server logs, build/install and artifact receipts;
- test logs and `.private/test-runs/20261027-192354-98c50ba6/results.json`.

Screenshots/logs can contain a trainer identity, location and copyrighted visuals;
keep them local. No assets, APKs, credentials, databases or screenshots belong
in this source change. Main remains unmerged; the feature branch is for review.

Staged review: 161 source/documentation/metadata files, approximately 8.7 MiB.
No new APK, bundle, database, key or `.gitignore` changes are staged. Gitleaks
reported one generic-key match in the existing M23 fixture's synthetic peer
marker `m23-isolated-NOT-EARNED`; that is not an authentication token. Its
password is generated at runtime, not stored in source. The finding was
reviewed without adding a scanner suppression.

Unstaged local work remains preserved: repaired legacy manifests, their
backups, custom POIs, the corrected running family configuration and the
admin-created stop-loot settings. Generated test-only temporary directories
were retained under the ignored evidence directory instead of staged.

## Review branch

Implementation commit: `fd040bad3278a637077f5005d105188e103dc8fe`.
The feature branch was pushed without force. [Draft pull request #4](https://github.com/Dittobuffer/dittobuffer/pull/4)
records the remaining native Pokestop check. Remote main remains at the starting
`bd2ab84010b8a47cfd72d02d2f0cb275e4817e91`; nothing was merged into it.
Subsequent documentation-only receipts may have a newer branch head.
