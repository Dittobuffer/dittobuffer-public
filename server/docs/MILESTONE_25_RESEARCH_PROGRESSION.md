# M25 Research and progression acceptance ? 2026-10-28

> Historical acceptance record: branch/status statements below describe that verification session. The later published source is reconciled with current main in [the 3 November 2026 gameplay integration](GAMEPLAY_MAIN_INTEGRATION.md). Existing partial/native acceptance limits remain in force.

Status: **PARTIAL; not deployed to the normal server.** Native Research flows are playable, but this is not a claim that every progression system, acquisition method, medal, or level has complete Android acceptance.

## Workspace and dependencies

Worktree: `E:/DittoBuffer/.private/worktrees/m25-research-20261028`.
Branch: `feature/m25-research-progression-20261028`.
HEAD: `f1468dd6bcc65a77babd681fdf106a2f2b5c7aa9` (existing M33 dependency).
Latest main inspected at start: `22e45651f23e7e7133bc8290d882eb5b09a4840f`.

The clean primary `feature/seasonsandevents` checkout and dirty M33.1/M34 worktree were preserved. M25 reused 61 reviewed dependency source files from `feature/m331-json-m34-native-victory-20261028` (HEAD `fdc5553`), without replaying commits or copying private configuration as source changes. M25 implementation remains uncommitted and unstaged. No push, main merge or normal deployment occurred.

Four machine-local overlays remain deliberately outside the source review list: `config/family.json`, both local avatar/Pokemon resource manifests, and `data/world/admin-pois.json`. Do not bulk stage this worktree. Its local assets, databases, APKs, credentials, screenshots and logs are ignored/private evidence, not publication inputs. Every `.gitignore` is unchanged.

## Coverage matrix

`PIXEL_VERIFIED` means the named narrow flow was actually observed on Pixel 5 `PIXEL_TEST_DEVICE`, not merely encoded in a response. `BACKEND_VERIFIED` means transaction/protocol regression tests passed. `PARTIAL` identifies working subsets and remaining native gaps; `BLOCKED` requires compatibility or unavailable resources; `NOT_TESTED` is not evidence of failure or success.

| System | Backend | Pixel 5 | Exact scope / remaining work |
| --- | --- | --- | --- |
| Field Research | BACKEND_VERIFIED | PIXEL_VERIFIED | Native spin assignment, real progress, claim/stamp; three normal slots, fort/day deterministic rotation, abandonment backend. Bonus slots not implemented. |
| Seven-stamp Breakthrough | BACKEND_VERIFIED | PIXEL_VERIFIED | Seven native Field claims, calendar, frozen XP/dust/items and Pidgey encounter, real catch. DST/retry/concurrent entitlement tests. |
| Multistage Special | BACKEND_VERIFIED | PIXEL_VERIFIED | Two stages, real catch/spin, child/stage claims, persistent completion. All prerequisite combinations not separately device-tested. |
| Timed Research | BACKEND_VERIFIED | PIXEL_VERIFIED | Native Events countdown, two stages, rewards; separate completed-unclaimed task expires and disappears without a claim. |
| Collection Challenges | BACKEND_VERIFIED | PARTIAL | Native Bulbasaur list/checkmark/reward and Elite Collector medal. Exact form/method tests backend; hatch/trade/evolve/raid methods not each newly device-tested. Completed-but-unclaimed card can say ?Ended?. |
| Permanent branching | BACKEND_VERIFIED | PARTIAL | Native chooser, named confirmation, irreversible choice and real spin path completion. Choice cards remain blank before confirmation despite valid text/color fields. |
| Timed branching | BACKEND_VERIFIED | NOT_TESTED | Context 17 / section 18, time-window and exclusive-path machinery. Needs its own native progression/expiry pass. |
| Mythical Research reward | BACKEND_VERIFIED | PIXEL_VERIFIED | Earned shiny Shaymin scripted scene, petals, two escapes, third throw/capture/storage. Audio NOT_TESTED. |
| Shared source-event registry | BACKEND_VERIFIED | PARTIAL | Catch/spin/throws exercised; additional source hooks transaction tested. Charged attacks currently cover Trainer combat only. No fake walking. |
| First-species Dex XP | BACKEND_VERIFIED | PIXEL_VERIFIED | Native catch reward explicitly lists 1,000 XP; once-only/restart/transfer/replay tests preserved. |
| Variant Dex categories | BACKEND_VERIFIED | PARTIAL | Existing gender/form/shiny/costume/Shadow/purified observations retained; Mega/Max facts projected durably. Every category?s native unlock/display not verified. |
| Legacy levels/rewards | BACKEND_VERIFIED | PIXEL_VERIFIED | Earned level-12 animation/rewards and retained level-11 startup behavior; no normal-profile curve change. |
| Modern 1?80 schedule | BACKEND_VERIFIED | PARTIAL | Pinned 80 thresholds, opt-in gated mode, source reward definitions, permanent capacity rewards and pending entitlements. Not every level/unlock/item has native acceptance. |
| Modern 71 gate | BACKEND_VERIFIED | PIXEL_VERIFIED | Native level 70 holds surplus XP, 0/4, opens exact level-71 requirements. Labelled seeded baseline plus final real Great throw/catch caused four auto-claims, level-71 animation, one reward receipt and cap 71. Not proof of naturally earning prerequisite medals/actions. |
| Gates 72?80 | BACKEND_VERIFIED | NOT_TESTED | All 50 source templates translate; isolated 70?71, 78?79 and 79?80 hold/unlock/restart tests. No natural Android completion through 80 claimed. |
| Medals | BACKEND_VERIFIED | PARTIAL | Elite Collector earned through actual challenge, visible and opened on Profile. Other source thresholds/counters tested or audited, not every medal/device bonus. |
| Studio Research Builder | BACKEND_VERIFIED | PIXEL_VERIFIED | Player output verified on Pixel; editor itself verified in real headless Chrome DOM: category menus, tasks/paths/pool, preview, draft, export/import preview; no JS errors. |
| Physical walking / two phones | BACKEND_VERIFIED | NOT_TESTED | User unavailable for walking; Samsung unavailable. No multi-device claim. |
| Ordinary catching/storage | BACKEND_VERIFIED | PIXEL_VERIFIED | Real catches and storage persist after app/server restart. |
| Raid/Rocket/Max regression | BACKEND_VERIFIED | NOT_TESTED | Existing Python/Node/Rust regression coverage retained; no fresh full native combat pass in this acceptance. Prior milestone success is not new M25 verification. |

## Implementation and native wire

The existing `research_stories`, `research_tasks`, `research_stage_claims`, inventory and RPC receipts remain authoritative. `research_lifecycle.py` provides adapters, not a competing progression database. Frozen definitions retain earned progress across editorial revisions. Shared `research_task_registry.py` is used by both gameplay and Studio.

Native requests remain `GET_NEW_QUESTS` (900), `GET_QUEST_DETAILS` (901), `COMPLETE_QUEST` (902), `REMOVE_QUEST` (903), `GET_QUEST_UI` (2008), the existing quest encounter/capture calls, inventory and `LEVEL_UP_REWARDS` (128). Controllers include the existing QuestService, Today/Events list, QuestListTaskView, BranchingQuestChoiceGuiController/ListItemView, Trainer level requirements and the M23 ShayminEncounter controller. No web overlay substitutes for a native screen.

`TodayViewSectionProto` now declares the actual 0.429.2 fields: Timed 6, Mini Collection 9, Timed Branching 18. Contexts are Special 1, Field 2, Timed 4, Breakthrough encounter 5, Level-up 6, Collection 9, Branching 11, Timed Branching 17. Three exporter-placeholder types were corrected against the native dump: Quest context enum, Invasion character category enum, and BreadDex modifier enum. Generated Python protobufs were regenerated; wire tests include literal encoded values.

Timed/Collection/timed branching start inclusively and expire exclusively. A completed task with unclaimed rewards expires; already granted encounter ownership survives. Special Research remains permanent after enrollment. Isolated scheduler time is translated to the native countdown while enforcement uses the configured scheduler clock. Normal time and distance validation do not use the fixture clock.

Collection criteria match successful new acquisition events, exact configured form/gender/alignment/costume/Max values and permitted methods. Existing Storage gives no credit. Branch choice freezes one path, persists before acknowledgment and cannot claim both paths under a race/reconnect. The native blank choice-card issue remains open: correct text is visible on confirmation, but list-item binding is not yet reliable. Relevant verified native method: `BranchingQuestChoiceListItemView.Initialize`, RVA `0x7F1B890`; its ChoiceData has ButtonText/Color and image request fields. Next fix: inspect those values at initialization and correct native localization/binding/resource setup rather than painting a replacement screen.

Field assignment uses fort + timezone day seed and retains per-player progress. Three ordinary slots remain; bonus-slot protocol/capacity is a tracked extension. Daily stamps use ZoneInfo, with repeated-hour and local-midnight tests. Stamp seven freezes a weighted encounter and base rewards into `research_breakthroughs`; later Seasons cannot replace the entitlement. Claim, encounter creation, receipt and cycle rollover are atomic. Inventory-full failures retain the entitlement.

Additional committed event adapters cover Gym wins, Trainer wins, Mega Evolution, Buddy hearts, finalized trades, validated whole kilometres and once-per-route Zygarde Cells. Super-effective Trainer charge progress derives from actual resolved damage history and durable source indexes, not an asserted client outcome. Repeated polls and committed result retries cannot increment twice. Unknown generic area/unique-stop/Buddy-feed/Party counters stay unavailable. Gym/Raid/Max super-effective charged sources remain a follow-up.

## Modern progression provenance and migration

`progression_schedule: pinned-modern-gated` is explicit opt-in; legacy and old pinned-70 modes remain unchanged. No existing player is migrated automatically. The supplied reference contains 80 XP thresholds; `level-research-reference.json` preserves the 50 exact LEVEL_71_CAP?LEVEL_80_CAP root/child templates, source level rewards and additional medal thresholds from the existing authorized Game Master, SHA256 `5c947ac64d1de8859bea1b3bf044609d74b6e8429d53f8cb1aa30a72a46dce84`. This is pinned client/reference compatibility, not an independently researched claim about the latest production tuning.

A durable earned cap starts at 70 (or a preserved issued higher level). Surplus XP is retained. Each gate?s four children and XP must qualify before cap advances by one. Unknown qualifiers fail closed. Single-day and consecutive-day conditions have durable state; max-level Pokemon are unique by owned ID; GBL wins require league context; trade distance needs actual origin evidence. The native text revealed that Legendary power-ups include Mythical species; the predicate and regression now cover both.

Additive tables: `research_task_state`, `research_breakthroughs`, `trainer_level_caps`, `trainer_storage_rewards`, `trainer_level_entitlements`; existing Research/player tables retain their role. Level rewards use existing `level_reward_claims`; permanent storage increments are separate from consumable bag items. Native templates match deliverable source rewards. Unimplemented items and unmapped wardrobe articles stay durable pending entitlements, not fictional deliveries. Master Ball is explicitly excluded. Source-backed rewards that lack a verified native handler/assets remain PARTIAL.

Migration plan: retain the current profile and backup; compare each trainer?s highest issued level with the new curve at retained XP. If the curve would reduce an issued level, reject opt-in before creating a cap. Decide and approve an explicit per-account XP/level preservation policy offline; never silently decrease levels, backfill XP or delete previously earned rewards. Validate a copied database before any normal deployment. Automatic normal-profile migration is deliberately absent.

## Studio, schema and examples

M33/M33.1 was extended in place. Additive schema-v1 categories preserve old imports: field, special, timed, collection, branching, timed_branching, breakthrough. Category-incompatible nonempty fields reject import. Menus cover stages/tasks/rewards, collection species/method/form, paths and weighted Breakthrough encounter pools. Advanced JSON retains additional strict filters. Read-only preview reports eligibility, intervals, resources, task/reward/path details, circular prerequisites, partner dependencies and unavailable catch targets. It never writes player data or grants rewards.

The level-gate endpoint is read-only and exposes immutable source requirements/readiness. Six original templates and import bundles are provided in `config/liveops-examples/research/`: A New Adventure, Weekend Explorer, Kanto Discovery, Mysterious Signals, Seasonal Discovery and Field Notes Choose a Path. Edit dates and destination pools, preview and import as Draft; nothing activates automatically. UI documentation/schema/ChatGPT generation guidance were updated.

## Pixel acceptance evidence

All screenshots/logs remain private under this worktree?s `.private/acceptance/`. They contain game imagery and isolated-account information and are not added to Git. Selected exact files:

| Evidence | Observation |
| --- | --- |
| `native-first-dex.png`, `native-first-throw.png` | Real Bulbasaur catch and native new-species 1,000 XP line. |
| `native-events-first.png`, `native-timed-completed.png` | Timed Events card and completed two-stage flow. |
| `native-collection-first.png`, `native-collection-claimed.png` | Collection criterion and claimed reward. |
| `native-path-second.png`, `native-path-confirmed.png`, `native-branch-completed.png` | Named exclusive confirmation, persisted selection, completed spin path. |
| `native-gate-special-ready.png` | Remaining blank native choice cards after latest text/color data. |
| `native-special-final-task-claimed.png`, `native-special-completed.png` | Immediate completed row and final Special claim. |
| `native-first-stamp.png`, `native-stamp-day2.png`?`native-stamp-day6.png`, `native-seven-stamps.png` | Seven real Field claims across isolated local dates, native calendar/dialogue. |
| `native-breakthrough-chest.png`, `native-breakthrough-rewards.png`, `native-breakthrough-pidgey.png` | Native box, rewards and earned encounter. |
| `native-shaymin-scene.png`, `native-shaymin-throw1.png`, `native-shaymin-throw2.png`, `native-shaymin-stored.png` | Actual scripted scene/two escapes/capture. |
| `native-expiry-unclaimed.png`, `native-expired-removed.png` | Real spin completes timed objective, reward unclaimed, card removed after isolated expiry. |
| `native-medals-final.png`, `native-elite-collector.png` | Earned medal in Profile and its native detail screen. |
| `native-modern-level70.png`, `native-level71-research.png` | Surplus XP held at 70, native 0/4 and source requirements. |
| `native-gate-real-catch.png`, `native-gate-updated-profile.png`, `native-modern71-rewards.png` | Final real catch, level-71 animation, returned overworld at 71. |
| `studio-*.png`, `browser-research.log`, `studio-browser-errors.json` | Actual Chrome menus/preview/export/import; no JS errors. |

M25Pixel earned Bulbasaur, Pidgey and Shaymin (owned species 1/16/492), completed five stories/challenges and has eight durable stage claims. The breakthrough encounter is captured once and cycle advances to two. Native calendar stamp dates were Oct 22?27 and Oct 29, including Amsterdam DST on Oct 25; an ineligible repeat of the same fort/day was rejected rather than bypassed. Seven stamps need not be consecutive. Expiry fixture has progress 1, claimed 0, zero stage claims.

M25Gate is a separate account: an explicitly synthetic high-level XP/preclaimed baseline and task progress [15,20,998,99] was recorded in `gate-baseline.json`. A real Great throw/catch supplied the final Nice-or-better/catch increments, native automatic child/root claims advanced cap 70?71, and the native level-up animation appeared. Child XP is 4?800; no duplicate reward receipt. This is native transaction/UI acceptance with fixture prerequisites, not legitimate natural completion of 15 platinum medals/20 Legendary power-ups/999 throws/100 catches. Levels 79/80 are backend tests only.

Initial command-path mistake: before setting explicit `PYTHONPATH=src;.`, one isolated acceptance identity was created in the primary `.private/acceptance/trainer.sqlite3` via its editable environment. This is not the normal family/player database. That extra isolated artifact was preserved, the path assertion was added, and all gameplay acceptance used the M25 database. No original file was removed to hide the mistake.

## Tests and remaining validation

Full Python: **809 passed, 7 skipped**, 331.61 seconds (`python-full-release-check.log`). A final targeted qualifier regression: **5 passed** (`mythical-gate-fix.log`). Skips are Windows symlink privilege and six missing pinned proprietary fixtures. Actual final APK preservation was enabled in the full run; original game DEX/native contents pass preservation checks.

Four Node suites pass: world, UI, raids and family. Rust: **158 passed, 2 ignored**; ARM64 release build succeeds under Ubuntu-24.04 WSL. Default Ubuntu?s missing VHDX is an environment limitation, not a code failure. Final build log: `rust-final.log`. Full APK was rebuilt, installed with `adb -s PIXEL_TEST_DEVICE install -r`, and used for native acceptance. Test APK: `.staging/m25-native-final/dittobuffer-family.apk`, 201176002 bytes, SHA256 `d2c2322a1724c31981641bb1a90970d592fe2a5209411769943423d60cee26cc`.

Remaining before calling M25 complete: native branch list binding; Collection completed-card/archived-story presentation; bonus Field slots; every Collection acquisition variant; timed-branch native acceptance; full native gates through 80, pending item/wardrobe delivery and level-dependent unlock audit; medal bonuses without invented multipliers; broader combat charged-event hooks; fresh Raid/Rocket/Max Android regression. Physical walking and audio remain unverified. Normal deployment must wait for review; this report deliberately does not label these gaps complete.

## Preservation and deployment state

Final restoration and source-review evidence are recorded in the acceptance receipt and preservation section appended after checks. No auto-commit, push, merge or normal deployment is authorized by this task. Review dependency changes and M25 code separately using `M25_REVIEW_FILES.txt` and the preserved dependency manifest before staging anything.


### Completed preservation receipt

The exact original development APK is installed again (SHA256 `fd5023bc1ddab582dd191e9f90dc428f3ea1035ca783ad8dff4e4939f577f91a`). All **63** original files/shared preferences matched byte-for-byte before relaunch; original UID 10272:10272 and all granted-permission states match. Fixture app state is archived; the task-created location selector was preserved under a suffix rather than deleted. ADB reverses match the original 8445/8080/8081 set. The normal account reopened on the map at level 9 with all 13 Pokemon.

Primary branch/HEAD/status/stashes, private configuration, both asset manifests, world pointer and `.gitignore` match the checkpoint. Existing normal player/Pokemon/avatar/items/Dex/Research/reward/account records were preserved. Player revision changed as runtime freshness. During the session the unchanged normal server automatically enrolled one pre-existing M33 storyline and one task (18:23:29 local); these are additional unclaimed records, not M25 content. No existing story/task was removed or reset, no M25 definition reached the normal database, and XP, currency, collection and earned reward ledgers match. These benign additions were preserved rather than rolled back.

Normal service owners remain game/maps PID 971104 on 8445/8081 and admin PID 973332 on 8080. The M25 game/admin processes were stopped only after command-line/profile guards; ports 38445/38082/38083 and their extra reverse mappings are no longer active. The test database, source worktree, test APK and private evidence are preserved for follow-up. No uncommitted work in other worktrees was reset/stashed/overwritten.

Evidence: `E:/DittoBuffer/.private/m25-20261028/android-restoration.json`, `permission-preservation.json`, `host-preservation.json`, `normal-player-preservation.json`; native `restored-original-current.png`. Source-only Gitleaks 8.24.2 scan found no leaks. `git diff --check` passes, the index is empty, and no commits/push/merge were made. Source review list: [M25_REVIEW_FILES.txt](M25_REVIEW_FILES.txt); private dependency/source manifests separate copied M33.1/M34 work from M25 changes. Local overlays must not be staged.

The level-71 account was cold-restarted after its final cap/rewards; native `native-gate-restart-confirmed.png` shows level 71 retained. Studio?s final read-only source gate view was also verified in Chrome (`studio-level-gates.png`).

Published Netherlands database content also matches its pinned SHA256 `ca7568429854a23d5d46ffd218acbab5378d56d9d49779d9041d808b7dfec81e`. The untouched official Android package currently reports 0.433.1; hashes are recorded without claiming an unavailable pre-M25 official-APK comparison.


## M25.1 follow-up

The blank branch cards, archive coalescing and Collector history gaps were investigated further on the Pixel 5. See [the M25.1 report](MILESTONE_25_1_RESEARCH_UI_POLISH.md) for the actual fixes, 860-test regression result, fresh combat checks and still-unverified acceptance categories. This follow-up does not convert the synthetic high-level fixture into earned progression.
