# Research, Seasons and gameplay integration

Date: 3 November 2026. Integration branch: `integration/gameplay-main-20261103`.
Base main: `03c9c0ad17e365047552269570abb96323c30baf`.
Source feature head: `f64acd5` (`addon/betterresearchandfixes`).

This is a reviewed source consolidation for main through a GitHub pull request. It does not deploy a server, replace an APK, publish content, migrate players or establish new physical-device gameplay acceptance.

## Scope and branch audit

| Source | Treatment |
| --- | --- |
| `addon/betterresearchandfixes` | Integrated: M25/M25.1 Research, progression, native presentation, shared counters, schema import/export and earlier M24/M33/M34 dependencies. |
| `feature/seasonsandevents` / M33.1/M34 worktree | Their committed ancestry and reviewed source dependency are already included by the feature head. Preserved dirty M25 source has 92 normalized matching files; remaining differences are report redactions/status and four private overlays. M33.1's earlier implementation was extended by M25 rather than copied over it. |
| M24 gameplay / Pixel build branches | Included through the feature ancestry: raid summaries, Rocket balloons, daily rewards, onboarding, raid expiration and local gym access. |
| `fixture/gameplayfixes` | Its only independent commit changes four machine-local configuration/resource/world overlays. Those are preserved locally and excluded from this merge. |
| Routes/Mateo/Vivillon structural integration | Already represented in the canonical source ancestry; existing Rocket, PvP and Max modules remain. Legacy flat-layout branches have unrelated Git history and are not merged over the canonical tree. |
| `feat/phone-server-poi-import` and subsequent main commits | Already on main; retained with Android hosting, OSM POIs, map expansion, Platform rooms and full-path external catalog loading. |
| iOS milestone branches | Excluded. No iOS source, workflow, IPA or game-input modification is needed by this integration. Their existing worktrees remain intact. |

All feature branches, original worktrees and stashes are retained. A fresh integration worktree starts at the exact main base. There is no reset, clean, force-push or direct push to main.

## Implemented feature scope

The source includes Field, Special, Timed, Collection, branching and Research Breakthrough lifecycles, native quest projections, durable daily stamps/claim receipts, shared gameplay task events, variant Pokedex facts, first-registration XP, medals and explicit modern progression gates. Studio provides Seasons/Events scheduling, draft/preview/publication, original templates, Research Builder and bounded schema-v1 JSON import/export. M24/M34 provide earned raid summary and background repairs, alongside earlier gameplay fixes.

Acceptance remains **PARTIAL**; see [KNOWN-ISSUES.md](KNOWN-ISSUES.md). These reports contain earlier actual Android observations; they are not new device tests for this combined build. Natural completion of every level 71-80 requirement, all acquisition/combat counters and remaining native wording/presentation cases is not established.

## Compatibility resolutions

- Main's existing JSON live-ops API is retained in `liveops/legacy.py` and re-exported from the new Studio package. Platform snapshots, percentages, Community Days, shiny rules and Shadow overrides continue to work. Studio pool overrides apply to their own subsystem, without accidentally overriding unrelated raids or wild settings.
- Main's administrator implementation and HTML controls are retained, with additive Studio and shared authorization hooks. OSM expansion, Platform controls and existing feature switches are preserved. Existing Routes-editor adapters from main are retained; this integration does not establish their legacy `storage`/`list_rows`/`create_row`/`delete_row` helpers as working canonical Routes APIs.
- Main's content-addressed resource loader keeps full CDN catalog paths, registry-disabled behavior and current external-catalog support. Its stale flattened-path regression expectations are updated to exercise the actual path-preserving contract.
- The map listener uses the owned lifecycle/cleanup implementation while retaining main's POI and Platform startup. No per-tile synthetic stops or gyms are restored. Tests needing a POI insert an explicit isolated fixture.
- Main's six-member Gym/Raid party template and deduplication remain alongside new level and weekly-reward templates. The merged protobuf schema retains weather typing and adds the audited native Research fields; Python bindings are regenerated from that exact schema.
- The old deleted `START-SERVER.bat` and automatic asset-checkout CI workflow are not resurrected. Current launch instructions point to the private administrator-link helper.

## Android administrator and dependencies

The shared website now exchanges a private operator capability for an HttpOnly session and checks CSRF on writes. Android's Admin button obtains the fragment link directly from its in-process Python bridge. The capability is not added to HTTP discovery, status JSON or logs. Platform-managed local edits remain disabled.

Studio JSON validation adds a dependency absent from the former zero-pip Android host. `android/app/requirements-studio.txt` pins `jsonschema==4.17.3`, `attrs==25.4.0` and `pyrsistent==0.20.0`; all three selected distributions are universal Python wheels. This retains Draft 2020-12 validation without assuming an Android build of the native `rpds-py` dependency used by later jsonschema versions. The Gradle configuration requires binary distributions, failing rather than compiling unsupported native packages.

The selected dependency set was downloaded with Python-3.10/universal-wheel constraints and tested in a separate local target directory. No dependency wheel is committed. The supported packaging mechanism is documented by [Chaquopy](https://chaquo.com/chaquopy/doc/current/android.html#requirements); upstream package metadata is available for [jsonschema 4.17.3](https://pypi.org/project/jsonschema/4.17.3/) and [pyrsistent 0.20.0](https://pypi.org/project/pyrsistent/0.20.0/).

A new Android APK/Gradle assembly and physical Android acceptance are **not performed** in this source-merge task. The expected private Android Gradle toolchain is absent here. Existing installed Android apps are untouched; they do not acquire these changes merely because main changes.

## Persistence and operation

Database changes are additive: Research Breakthrough entitlements/task state and trainer cap/storage/reward-entitlement tables. Existing player/reward data remains authoritative. Tests use temporary profiles/databases inside this integration worktree. No normal database is opened for a writable test.

Modern `pinned-modern-gated` progression remains explicit opt-in, with the migration restrictions documented in M25. The merge does not activate starter templates or change normal trainers' levels. Preserve backups and review the opt-in migration before a later deployment.

## Validation

- Latest comprehensive Python invocation: **871 passed, 10 skipped**, with one failure in the unchanged OSM test's directory-wide assertion. A downloader left by another test wrote a different valid cell (`8723_817`) into the patched directory while the rejected request targeted `(1.0, 1.0)`. The fixture now checks absence of its own rejected cell, retaining the ValueError assertion; the subsequent map/cache/lifecycle recheck is **17 passed**. This is a full run plus a targeted correction, not a claim of another all-green full invocation after that test-only change.
- The preceding run exposed a wall-clock-dependent Rocket fixture. It now starts at a defined rotation boundary and checks both cooldown and next-slot renewal; **36 Rocket tests passed**. Earlier stale fixture assumptions about generated POIs and the deleted batch launcher were reconciled with current main without restoring those removed behaviors.
- All **4 Node client suites passed**. The exact final administrator inline JS, Studio JS and access JS parse successfully.
- Pinned Rust 1.99.1 native host suite through the existing working Ubuntu-24.04 distribution: **158 passed, 2 ignored**. The obsolete Ubuntu registration's missing disk was not repaired or changed.
- Separate Platform-room suite: **17 passed**. Android-selected pure-Python schema dependencies: **48 import/export/Studio/progression checks passed**, with a separate administrator-bridge capability test. These host tests do not establish an assembled Android APK or device runtime.
- Headless Chrome against a temporary family-account/admin database: Research Builder opens, original map-expansion/Platform/Routes/feature controls remain present, no JavaScript page errors, zero players and no publication. The 62 existing Admin class methods are retained byte-for-byte; only handler hooks change.
- Explicit staged-path review, secret-pattern scan, local documentation links, whitespace and 100-MiB Git-blob guard pass. The eight original worktrees, dirty source bytes, index flags, all ignore bytes and stashes match the before snapshot.

No private profiles, player/world databases, new asset payloads, APKs, IPAs, certificates or credentials enter the merge. Every `.gitignore`, existing resource overlay and original worktree is checked against a private before snapshot. Main's Android payload, Platform files and current release artifacts are preserved except for the narrow Android source/requirements compatibility additions above.

No hosted build is dispatched and no workflow is added. The existing organization Actions $0 prevent-further-usage safeguard was read-only verified on 3 November 2026, with $11.306 included compute remaining. No billing, protection or visibility settings are changed.
