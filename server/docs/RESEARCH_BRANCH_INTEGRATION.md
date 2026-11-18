# Research branch integration

> Historical acceptance record: branch/status statements below describe that verification session. The later published source is reconciled with current main in [the 3 November 2026 gameplay integration](GAMEPLAY_MAIN_INTEGRATION.md). Existing partial/native acceptance limits remain in force.

Integration date: 29 October 2026. Destination: `addon/betterresearchandfixes` in the primary `E:\DittoBuffer` checkout.

## History and method

The destination and preserved source branch `feature/m25-research-progression-20261028` both started at `f1468dd6bcc65a77babd681fdf106a2f2b5c7aa9`. Their merge base is that same commit; there were no differing committed changes or unpublished ancestor commits. The base is already on `origin/feature/seasonsandevents`.

The source contained 98 reviewed source/documentation/example paths and four separate local overlays. A full-index working-tree patch was checked against the clean destination before applying. Forty-three new files were created only after verifying their destination paths were absent. No commits were cherry-picked, duplicated, reset or rewritten. No original worktree or stash was removed. The original source files and dirty status still match the pre-integration checkpoint.

The source files match after Git text normalization except for publication-only redactions in five reports: the private Pixel serial is replaced by `PIXEL_TEST_DEVICE`, and the device-specific UID is generalized. The unredacted originals remain in the preserved worktree. The only additional implementation is the independently tested repository size-guard correction below.

Fetching found `origin/main` at `28ac1a6020f4d544b28d90010c6db13c1b8994e2`. Its later release commits also contain broader source/asset changes. They were not merged as part of this explicitly scoped working-tree integration; no APK release, `.gitignore` change or unrelated remote implementation was imported.

## Feature preservation matrix

| Subsystem | Source | Destination and validation |
| --- | --- | --- |
| M33 Seasons/Events | Existing base plus reviewed source worktree | Existing liveops catalog/generator/rules/runtime, Studio and original examples retained; full liveops/Studio tests. |
| M33.1 JSON import/export | Reviewed M25 worktree's integrated dependency | `liveops/bundle.py`, schema-v1 schema, validation/store/preview, Studio UI and original examples; bundle/Studio tests and preserved prior Android/browser evidence. |
| M34 native raid results | Reviewed worktree | `client/local-raid-presentation.js`, diagnostic source, standalone builder inclusion and raid Node tests; existing native routing/GC-handle guards retained. |
| M25 Research | Reviewed worktree | Field, Special, Timed, Collection, branching and Breakthrough lifecycle/registry/preview/store integration and tests. |
| M25 progression | Reviewed worktree | Hash-pinned level requirements, modern gated progression example, templates and authoritative gameplay adapters. Normal profile/XP is not migrated. |
| M25.1 presentation/reliability | Reviewed worktree | Original locally generated branch-card art, completed archives, Collector history, calendar reset and deduplicated Charged Attack events. |
| Isolated Max presence | Reviewed M25.1 worktree | Exact private database/profile and finite geographic-site guard retained; no normal-profile bypass or walking credit. |
| Earlier gameplay | Existing base and narrow Research adapters | Rocket, Max, PvP, Gyms/Raids, Routes/Mateo/Vivillon, Mythical encounters, accounts, storage and inventory remain in their canonical modules; coexistence/native protocol tests pass. No old flat `server/` tree imported. |

## Local-only exclusions

These source overlays were reviewed and match the destination's current local versions. They were neither copied nor staged:

- `config/family.json`
- `config/resources/avatar-assets.json`
- `config/resources/pokemon-assets.json`
- `data/world/admin-pois.json`

Their existing skip-worktree flags remain unchanged. Every `.gitignore` remains byte-identical. No private environment/profile, account/player/world database, credential/key, original/modified APK, generated build, resource payload or temporary screenshot/log was added. Existing permitted manifests and already tracked baseline resources remain as they were. Test evidence and the exact preservation/file manifests remain in the ignored `.private/integration-m25-20261029` directory.

## Validation

- Destination full Python suite: **860 passed, 7 skipped**, 310.50 seconds. Skips are the existing Windows symlink limitation and unavailable pinned proprietary fixture inputs.
- Additional size-guard policy tests: **9 passed**. These cover correct/incorrect LFS hashes and sizes, malformed pointers, missing LFS filter, oversized ordinary index blobs even with a smaller/missing working file, and an empty index.
- All four Node suites pass: UI, raids, family and world.
- Repository pinned Rust script: **158 passed, 2 ignored**. The ignored rehearsal tests require their separate server.
- Repository content policy and whitespace checks pass. Staged source is checked for secrets and oversized/proprietary additions before commit.
- The original M25 dirty status and source bytes, stashes, private overlays, ignore files and existing index flags are preserved. Normal gameplay record hashes also match the pre-integration checkpoint.

The existing size guard incorrectly rejected the already tracked 189-MiB checked-out APK despite its canonical 134-byte LFS pointer. It now checks actual index blob sizes and accepts a large working LFS payload only when the indexed pointer, existing LFS attribute, exact length and SHA-256 match. The APK, its pointer and LFS configuration were not changed or newly staged. Oversized ordinary Git blobs still fail. No new LFS tracking rule was introduced.

## Acceptance limits and operation

This was a source integration. The earlier M25/M25.1 reports retain their actual Pixel/browser observations and partial acceptance limits; no new Android gameplay, APK installation or service deployment is claimed here. Collection wording/history labels and the remaining native capture/high-level progression checks remain open as documented in M25.1. Master Ball functionality remains excluded.

Normal player data, the published Netherlands world, local resources, APK backups and running services were not replaced or restarted. The integrated source is available in the primary checkout and will be used on the next planned service/build cycle. Review the existing configuration examples rather than copying another workstation's private profile. Merge through a later GitHub pull request; this integration does not merge into `main`.
