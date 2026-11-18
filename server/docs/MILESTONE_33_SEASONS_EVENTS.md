# M33 - Seasons, Events & Research Studio

> Historical acceptance record: branch/status statements below describe that verification session. The later published source is reconciled with current main in [the 3 November 2026 gameplay integration](GAMEPLAY_MAIN_INTEGRATION.md). Existing partial/native acceptance limits remain in force.

Date: 2026-10-28. Workspace: `E:\DittoBuffer`.

**Status: substantial playable implementation, with the core Season/Field Research/bonus/rotation loop verified on Pixel 5. The complete M33 definition of done is NOT met.** Native Timed/Collection Research and several event-format mechanics remain missing; they are disabled or clearly labelled, not simulated as working features.

The additional user request for a **1,000 XP first-species capture bonus** was implemented and demonstrated on Android. The user subsequently requested removal of the extra Studio login: the normal admin website now opens Studio directly, with no password.

## Git audit and preservation boundary

- Branch: `feature/m33-seasons-events`.
- Existing integrated HEAD: `fdc55531ca013985bc8b1f30e8772b0d723a8b8b`.
- Audited `origin/main` base: `22e45651f23e7e7133bc8290d882eb5b09a4840f`.
- Existing M24 and raid-entry fixes remain ancestors; no flat-layout checkout was substituted.
- PR #4 was inspected: open/unmerged, mergeable false, state `dirty`, head `5e367f83e12261e91320cc71505c9272bebf3b87`. It was not silently merged or treated as reconciled.
- M33 changes remain local and uncommitted for review. Nothing was pushed or merged into main. No history rewrite, stash operation, reset or cleanup was performed.
- Existing skip-worktree flags on family configuration, Pokemon/avatar manifests and admin POIs were preserved. Their actual bytes were checked independently of Git status.

The old `E:\PokemonGO-libreNPL` project and integrated backups were not development targets. The only old-workspace tool used was its existing pinned Gitleaks executable, read-only, to scan a private copy of the intended changed source.

## Implemented systems and existing dependencies

| Subsystem | M33 implementation | Existing authoritative system retained |
|---|---|---|
| Editorial content | Separate versioned SQLite drafts, revisions, publication plans, lifecycle, audit and entitlements | Normal trainer database is not the editorial store |
| Schedule resolver | Named zones, DST ambiguity/gap handling, local weekly boundaries, priorities, elapsed habitat windows | Request-driven authenticated gameplay RPCs |
| Wild pools | Weighted available species/forms, level ranges, shiny policy, supported habitat/weather/time conditions, stable generation IDs | `Game.seed`, supplied Game Master and asset registry |
| Raid pools | Ordinary/Mega pool projection and immutable per-pool generation identity | Existing raid admission, combat, snapshots, rewards and catches |
| Max rotations | Shared Selection/materializer, retirement/fallback, pause/resume identities | Existing stations, rooms, participants and reward ledgers |
| Eggs/Rocket | Weighted egg pools and selected supported Rocket cadence overrides | Existing persistent eggs/incubators and Rocket mechanics |
| Bonuses | Catch XP/dust/candy, transfer candy, evolution XP; bounded deterministic combination | Canonical score and inventory updates |
| Research | Visual Field/Special steps, registered conditions/rewards, eligibility and frozen enrollment | Existing task/story/claim/encounter/stamp tables |
| Generator | Seeded editable Seasons, weekly raids/wild/eggs, released Mega/Max, original Field/Special Research, weekly mini-events/Spotlights and a Community Day | Actual restored species/resources and enabled modules |
| Admin UI | Thirteen navigation areas in the existing website, forms, search, history, previews, templates, region import and confirmation | Existing admin service and design variables |
| First Pok?dex capture | Base 1,000 XP activity 8, lifetime registration check, replay/concurrency safety | Existing capture transaction and Pok?dex observations |

No new battle engine, fake participant, proprietary model payload, official event copy or replacement player inventory was introduced. All 958 existing registrations remain represented by the unchanged local resource manifests; this milestone did not re-run 958 individual model previews.

## Important fixes found through real acceptance

1. The family client installs PvP encounter/catch RPC wrappers bound to its original Game object. A shallow request projection initially changed map spawns but bypassed catch bonuses through those wrappers. Request-installed methods are now rebound, and the wrappers invoke their underlying implementation with the same request object. Native 2x scoring was re-tested successfully.
2. A Field task initially lost its separate stage reward. Newly earned Field definitions now bundle task and stage rewards into the native single claim. Already earned tasks retain their original frozen revision.
3. Legacy 2x calendar and Studio 2x factors no longer accidentally become 4x by crossing two independent reward adapters.
4. Max pause/resume/end now use durable activation and fallback identities, retaining history while leaving one active station per eligible candidate. Spawn-only events no longer reroll unchanged raid/Max pools.
5. Initial SQLite file creation exposed a brief partial schema to gameplay readers. Schema initialization is now one transaction, and readers of a not-yet-committed first schema preserve ordinary defaults. Isolated Database overrides cannot inherit the normal trainer's calendar.
6. Region export/import now includes geometry, remaps scope references and commits the imported drafts/regions transactionally. Missing geographic references fail publication.
7. The user-requested first-registration XP uses actual protocol activity `HoloActivityType_ActivityPokedexEntryNew` (8). It is based on lifetime species ownership, not current storage count. Updated exact XP assertions reflect the intentional new reward.

## Native Pixel 5 acceptance

Device: rooted Pixel 5 `PIXEL_TEST_DEVICE`, Android 13, arm64, development client 0.429.2. Samsung was not used and fresh Samsung acceptance is **NOT_TESTED**.

All reward/progression tests used generated account **M33Studio** and separate trainer/editorial databases below `.private/m33-20261028/`. Fixture ports were 18445 (game), 18082 (admin) and 18083 (map resources). The temporary fixture APK retained the existing native integration and client flags. No normal accounts were used for reward testing.

| Acceptance | Observed on the device | Durable evidence |
|---|---|---|
| Generate/edit/publish Season | Real Chrome forms generated a four-week Season; Week 1 was edited to Bulbasaur/Charmander/Squirtle and confirmed for publication | Frozen publication bundle and audit history |
| Native configured spawns | Those starter models appeared on the real overworld | Actual `GET_MAP_OBJECTS` species/form replies |
| Scheduled raid | Opened three-star Hoothoot, CP 9000, in Studio Raid Garden; normal raid pass UI worked | Successful `GET_RAID_DETAILS`, persistent raid definition |
| Catch Season Pokemon | Two Charmander and one Squirtle caught with actual touch throws | Owned IDs 1-3 and canonical catch receipts |
| Earn Field Research | Native spin of Studio Field Station awarded the Season's catch-three Field task | `fort_visits`, `research_visits`, frozen task definition |
| Complete and claim | Three actual captures completed it; native 300 XP claim succeeded and daily stamp appeared | Task progress 3, claimed 1, stamp row, successful completion receipt |
| Community Day | Published/resumed original Community gathering through the website; Bulbasaur appeared and was caught | Published pool, owned Bulbasaur records |
| Catch bonus | After fixing the PvP wrapper, native summary showed 200 catch XP + 40 Nice Throw + 100 First Throw = 340 XP | Canonical activity/XP arrays `[1,10,9]` / `[200,40,100]` |
| Spotlight / first Dex XP | Pidgey Spotlight created through forms with ordinary multipliers; first Pidgey capture showed **NEW POKEMON CAUGHT BONUS: 1,000 XP**, total 1,170 | Activities `[1,10,9,8]`, XP `[100,20,50,1000]` |
| Next rotation | Only isolated scheduler advanced to Week 2; native wild pool changed, including visible Slowking/Girafarig, and Raid Garden opened Rapidash CP 9000 | Week 2 wild IDs `[237,212,1,153,139,75,163,234,199,121,203,78]`, boss pool `[237,78,144]` |
| Cold restart | Actual Android storage displayed six retained catches; server restart retained the editorial fixture clock and player state | Owned IDs 1-6, claimed Field task and stored scheduler clock |
| End / ordinary defaults | Season/events ended through the website; cold-loaded Android returned to the ordinary mixed wild pool and default Pikachu raid | Effective bonuses all 1x, no active Studio rule sources, native map receipts |
| Return to normal | Original APK/settings restored; normal account reached its overworld with roads, avatar and existing objects | Exact restoration/preservation checks below |

Fixture-only setup: trainer started at level 5 with test Balls; its item capacity was raised to 1000 after a native spin hit its bag-capacity guard. Public-position test fort identities were isolated to its database. Successful native reward/quest counters were never fabricated. Missed ADB throws were retained as misses. The earned Field task above was enrolled before stage-reward bundling and correctly kept its original 300 XP reward.

The global OS/server clock, real walking state and normal player progression were not advanced. The persisted scheduler override is accepted only for explicitly isolated development profiles beneath `.private/` or `builds/`.

The Hoothoot and Rapidash screens demonstrate scheduled raid compatibility; this milestone did not repeat a full native raid battle. New Special storyline enrollment uses existing tables, but complete native progression of the generated multistage story remains unverified. Native egg/Max/Rocket event rotations and four separate template battle loops were not all demonstrated on Android.

## Local screenshots and records

Evidence remains private, rather than adding copyrighted game imagery, APKs or player databases to Git:

- Website: `.private/m33-20261028/studio-final-overview.png`, `studio-generated-season.png`, `studio-season-publication.png`, `studio-province-library.png`, `studio-province-selected.png`.
- No-password normal website: `.private/m33-20261028/normal-studio-no-password.png`.
- Season map/catches: `native-after-earned-task.png`, `native-season-throw.png`, `native-second-throw3.png`, `native-third-throw.png` in that directory.
- Native Field: `native-field-complete.png`, `native-field-claimed.png`.
- Raid screens: `native-community-map.png` (Hoothoot), `native-week2-raid.png` (Rapidash).
- Corrected bonuses: `native-community-bonus-fixed.png`, `native-pokedex-1000xp.png`.
- Restart/end: `native-storage-after-restart.png`, `native-defaults-final.png`, `native-final-fixture-storage.png`.
- Normal restoration: `normal-restored-first.png`, `android-restoration.json`, `normal-after-restart.json`, `file-preservation-final.json`.
- Receipts/state: isolated `trainer.sqlite3`, `content.sqlite3`, `week2-evidence.json`, `ended-event-preview.json` and service log snapshots.

These are exact local filenames; remote clones will not receive ignored private evidence automatically.

## Automated validation

- Complete final Python suite: **761 passed, 7 skipped** in 267.21 seconds. Skips are six absent pinned proprietary visual fixtures and one Windows symlink-privilege case. The fixture APK preservation check passed rather than being skipped. Evidence: `.private/m33-20261028/python-completion.log` / `.xml`.

- Four maintained Node suites passed, including repeated HUD navigation, raids, family credentials/resource routing, Rocket confirmation, Max Ball readiness/capture art, Routes and Vivillon behavior.
- Final Studio JavaScript syntax check passed. Real headless installed Chrome verified dashboard/editors, generation/publication, read-only previews, province selection, templates and a fresh no-password normal session.
- Rust: **158 passed, two ignored**, and the ARM64 Android release library built successfully. Native source was not changed; Master Ball remains excluded.
- The actual fixture APK passed all four standalone preservation/TLS/build tests against its exact base/split inputs. The main full suite also used these explicit APK inputs instead of silently skipping the installed build check.
- Four original starter templates were exercised through actual isolated backend spin -> catch -> claim transactions with duplicate-claim checks. Raid Hour also materialized a persisted raid. These are automated gameplay tests, not claimed as four Android end-to-end sessions.
- Gitleaks scanned the intended source/tests/examples/docs without finding secrets. Private credentials, databases, APKs and resource bundles were not staged.

## Preservation and restored state

The original development APK was reinstalled with `-r`; app data was never cleared or uninstalled. Its SHA256 is:

`fd5023bc1ddab582dd191e9f90dc428f3ea1035ca783ad8dff4e4939f577f91a`

Before restoring, the complete final fixture private state was backed up separately. All **62 original regular files** in `files/` and `shared_prefs/` matched their original byte hashes after extraction and before launch. UID remained the original app UID/GID. An initial streamed extraction stalled; it was replaced with a SHA-verified pushed archive and explicitly quoted root commands. The completed hash comparison is the restoration evidence, not the earlier failed attempts. No delete/reset/clear command was used.

Normal trainer data: **13 existing owned Pokemon**, item rows, Candy rows and account rows matched the pre-test backup. XP, Stardust, captures, throws and stops were preserved. Only the expected player inventory revision changed through normal authentication/refresh. Account credentials remained unchanged.

The family config, both local Pokemon/avatar manifest configurations, admin POIs and `.gitignore` retained their original hashes. Published Netherlands snapshot matched `ca7568429854a23d5d46ffd218acbab5378d56d9d49779d9041d808b7dfec81e`. Both official APK hashes matched their earlier preservation baseline. No original asset payloads, private transfer archives, backup workspaces, branches or stashes were removed.

The normal admin has no test Season/event automatically activated. Templates are original editable factories, ready to create drafts. Extra Studio login is disabled by default at the user's request; same-origin/CSRF checks remain. Explicit `liveops.admin_auth=true` retains optional publisher/editor/viewer mode for deployments that choose it.

Normal services and exact final PIDs are recorded in the private service-status report. Game/TLS is 8445, admin 8080 and maps 8081. Temporary ADB reverses were removed; normal reverses were retained. Open the primary workspace `E:\DittoBuffer` for continued work.

## Remaining gaps and next concrete work

1. Reconstruct/verify the 0.429.2 Today View wire routing for native Timed Story and Mini Collection sections. This checkout currently declares only stamp, Field and Story section variants. Add expiration, removal, enrollment and claim semantics through the existing ownership tables, then verify those tabs and rewards on Pixel. An enum/context alone is insufficient.
2. Add missing event mechanics independently: Collection/Global Challenges, GO Tour paths/medals, featured evolution moves/grace periods, Mighty encounters/Safari Ball expiration, and supported native news/banner delivery. Wild Area and GO Battle templates remain dependency-blocked; other templates explicitly omit those unavailable mechanics.
3. Finish remaining advanced form controls (full prerequisite/filter/reward lists, selective generator re-generation, broader Rocket lineup editing). Some capabilities currently require optional declarative import. Habitat previews require an explicit biome argument rather than automatic map inference.
4. Verify new Special storyline stages and egg/Max/Rocket rotations on Android, plus any multiplayer entitlement implications, using isolated trainers. No second-device success is claimed.
5. The custom region drawing surface is a coordinate grid; twelve OSM provinces can be imported. Missing country/city boundaries are not invented. A richer licensed basemap/region catalog is follow-up work.

M33 should remain labelled partial until these native and builder gaps are implemented and demonstrated. Core playable acceptance is established; passing tests or a finished-looking website are not substitutes for the remaining Android workflows.

See [operator guide](SEASONS_AND_EVENTS.md), [Research Builder](RESEARCH_BUILDER.md), [generator](SEASON_GENERATOR.md) and [first-capture XP](POKEDEX_XP.md).
