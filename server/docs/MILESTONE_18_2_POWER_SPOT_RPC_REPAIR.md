# M18.2 — Power Spot RPC repair and Max Ball presentation

Work date: 26–27 October 2026. Client: 0.429.2 ARM64, rooted Pixel 5
`15191FDD4003PY`, Android 13. Only this physical client was used.

Implementation is in `builds/structure-audit-main-20261026`, branch
`integration/routes-new-layout`, based on main `c4bb197`. The older parent
checkout is actually on `feature/routes` at `09460bf`; it was inspected, not
reset or replaced. Its staged work and index are preserved. No commit or push.

## Outcome and boundaries

Native Power Spot discovery, details, particle collection, lobby, timed normal
and Max combat, victories, rewards, optional Pokémon placement, earned Dynamax
Wooloo and Gigantamax Charizard captures were exercised on the Pixel. This is
actual Unity gameplay against the new-layout Python/Rust integration, not a
preview application or backend simulation.

All activity used two isolated fixture trainers, Alice and Bob. Each initially
received three level-30 Wooloo with unlocked Max moves, level-21 XP, Stardust
and 1,000 MP as **NOT_EARNED test setup**. Boss HP, damage factors and countdowns
were explicitly custom development tuning, initially 1,000 HP and later 200 HP
for capture-visual rehearsals. Their victories and subsequent catches were
earned through real native touches and server combat. This does not establish
official combat difficulty. No fake participants were added.

Fixture locations were explicitly simulated at three declared coordinates;
walking distance stayed zero. Physical walking, Samsung, simultaneous Android
multiplayer, defeat/retry and cheering UI are **NOT_TESTED in this pass**.

## Confirmed failures and repairs

1. **Battle HTTP transport was absent.** The runtime returned HTTP 404 for the
   native `/max/api/get-state`, `/get-time` and `/send-player-event` paths even
   though direct tests of `max_battle.handle` passed. The real HTTP listener now
   dispatches those exact paths, preserves protobuf response length/type/clock
   headers, and retains peer, account, PBS token, battle and event validation.
2. **Header migration discarded old-client location/session information.** Both
   `x-dittobuffer-*` and legacy `x-librenlp-*` are accepted. Conflicting dual
   headers are rejected before gameplay mutation. This also preserves distinct
   cold-start RPC receipt namespaces.
3. **Max private encounters lacked the generic owner guard.** A second account
   could reach another player's Max reward through ordinary encounter/catch
   handlers. Both RPC 102 and 103 now enforce the durable `max_members` owner,
   including when Max is disabled or an encounter has already been caught.
4. **Full Game Master wrappers were being treated like bare settings.** The
   Max importer accepts the actual wrapped source and legacy bare settings,
   preserves unknown fields, and can require a complete hash-pinned reference.
   The old compact reference lacked required definitions. It is preserved;
   the opt-in example points to the independently restored complete reference.
   Game Master cache revision is now 85.
5. **Form zero versus named normal forms differed.** Released, resource-backed
   species can use the verified equivalent default key. This does not permit
   arbitrary forms or bypass Gigantamax eligibility.
6. **Isolated debug GPS was rejected by the normal presence gate.** The first
   actual tap reached RPC 2462 but returned range failure; loot also failed.
   A strictly scoped M18.2 fixture exception accepts fresh, accurate provider-3
   fixes at an explicit allowlist, only with its private database/profile and
   flag. Normal profiles retain their existing validation. Replayed/stale,
   inaccurate or undeclared fixes cannot replace authoritative presence or
   earn walking distance. Native collection then succeeded for 120 MP.
7. **Blank boss details were a native tutorial gate.** The client binary's
   `BattleDetailsUnlocked` at RVA `0x87AA150` checks tutorial 123 or 116.
   Opening the owned Max Pokémon's native “View” introduction completed the
   real tutorial RPC 406/123; re-entering the Power Spot displayed its boss and
   Battle button. No global tutorial bypass was added.
8. **The portable Pokémon manifest omitted the existing Gmax resource path.**
   The opt-in `config/resources/pokemon-max-assets.json` restores the existing
   authorized Charizard Gigantamax dependencies with six hash-pinned inputs.
   All 2,739 prior bundle hashes and 3,774 variant keys are retained. The
   ordinary manifest and accepted Pokémon resource payloads are unchanged.
9. **Max Ball preparation and orientation were wrong.** A read-only native
   trace measured about 11.8 seconds before `HoldingBreadBall` became true.
   An early throw could use ordinary-ball art. The packaged adapter invokes
   the existing `Pokeball.btir(true)` immediate-ready branch once both art
   resources are loaded: the measured delay became about 0.9 seconds, before
   encounter controls appear. This keeps native Max Ball FX and assets.
   The shrink-completion closure at RVA `0x86EDE6C` also applies the authored
   `breadballRotation` as a world quaternion, ignoring encounter heading.
   The adapter applies that orientation relative to the animated physical
   parent after a confirmed Pokémon collision. It follows the actual art
   switch; the holding flag becomes false too early to identify that switch.
   Each art keeps its distinct loaded size. The native giant pink landing
   contracts for the final white-ball close-up; forcing giant scale onto that
   close-up was tested, visibly oversized, and is excluded from the final code.

The adapter changes art transforms only. It does not change the collider,
physical root scale, throw trajectory, collision classification, catch result,
ball consumption or reward response. It is embedded in the standalone APK;
external Frida was used only for temporary read-only diagnosis.

## RPC inventory

Types and numeric results below come from the supplied 0.429.2 descriptors and
the existing handlers. `P` means native Pixel workflow observed; `B` means
backend tests/static audit only. Envelope calls use authenticated `POST /rpc`,
`devrpc.RpcRequest.requests[].method/body` and response payloads. Routine
inventory calls are also visible in the native server log.

| ID | Request → response | Handler and persistence | Verification |
|---|---|---|---|
| 106 | `GetMapObjectsProto` → `GetMapObjectsOutProto` | `Game.rpc_get_map_objects`; materializes stable `max_stations`, schedule segments; returns Station field **17**, Routes field **20** | P+B |
| 3051 | `GetStationInfoProto` → `GetStationInfoOutProto` | `rpc_get_station_info` / `max_system.info`; projects boss/availability; result 1 success, 2 disabled, 3 missing | B; first native tap uses cached map station instead |
| 2462 | `GetStationedPokemonDetailsProto` → `GetStationedPokemonDetailsOutProto` | `rpc_get_station_details` / `max_station.details`; settles returns/assists; success 1, missing 2, range 3 | P+B |
| 2461 | `LootStationProto` → `LootStationOutProto` | `max_system.loot`; wallet, daily total, ledger, durable claim/replay; success 1, full 3, missing 4, disabled/level 5, range 6, daily cap 7 | P+B |
| 2467 | `GetMpSummaryProto` → `GetMpSummaryOutProto` | `max_system.summary`; daily collected/limit; actual wallet is inventory item **1506** | P+B |
| 4 | `GetHoloholoInventoryProto` → `GetHoloholoInventoryOutProto` | Max wallet, canonical owned Pokémon/deployment, returns, earned stats and walk quest; existing inventory revisions | P+B |
| 2458 | `StartMpWalkQuestProto` → `StartMpWalkQuestOutProto` | `max_system.start_walk`; one authoritative `max_walks` row; success 1/already 2/disabled 3 | B |
| 902 | `CompleteQuestProto` → `CompleteQuestOutProto` | Existing quest handler delegates `max_system.claim_walk`; validated distance, once-only wallet/daily/ledger/claim | B |
| 2459 | `EnhanceBreadMoveProto` → `EnhanceBreadMoveOutProto` | `max_system.enhance`; owner/lock/cost checks, MP/candy/XL/dust debit, canonical move update; target-level replay safety | B |
| 2457 | `GetBreadLobbyDetailsProto` → `GetBreadLobbyDetailsOutProto` | `max_system.lobby_details`; eligibility, room/boss and pending rewards; may persist group migration | B |
| 2450 | `JoinBreadLobbyProto` → `JoinBreadLobbyOutProto` | `max_battle.join`; room/member/session/PBS binding and MP reservation; real participant capacity; no fee debit before victory | P+B |
| 2453 | `PrepareBreadLobbyProto` → `PrepareBreadLobbyOutProto` | `max_battle.prepare`; readiness, pending lobby countdown | P+B |
| 2455 | `LeaveBreadLobbyProto` → `LeaveBreadLobbyOutProto` | `max_battle.leave`; left/quit, releases holds and locks, expires empty room | B |
| 2456 | `StartBreadBattleProto` → `StartBreadBattleOutProto` | `max_battle.start`; 1–3 distinct owned eligible Pokémon, range/session/countdown, frozen rules/party and combat clock; reconnect preserves injuries | P+B; see native server log |
| 2473 | `CompleteBreadBattleProto` → `CompleteBreadBattleOutProto` | `max_battle.complete`; server victory and contribution required; fee, XP/dust/items and private encounter once; stored response replay | P+B |
| 3062 | `GetBattleRejoinStatusProto` → `GetBattleRejoinStatusOutProto` | `max_battle.rejoin_status`; owner-scoped recovery; can rotate session/PBS token, never refills party/rewards | B |
| 102 | `EncounterProto` → `EncounterOutProto` | `Game.rpc_encounter`; canonical encounter reopening with repaired Max-owner check | B for repaired adversarial cases |
| 103 | `CatchPokemonProto` → `CatchPokemonOutProto` | `Game.rpc_catch_pokemon` / `max_battle.catch_member`; actual hit, owner's remaining balls, one storage identity and catch rewards | P+B |
| 2460 | `StationPokemonProto` → `StationPokemonOutProto` | `max_station.assign`; earned victory, ownership/range/capacity; `max_stationed` and canonical deployment fields, no copied Pokémon | P+B |
| 2472 | `ReleaseStationedPokemonProto` → `ReleaseStationedPokemonOutProto` | `max_station.release`; canonical return plus once-only optional configured assists | B |
| 2476 | `GetNumStationAssistsProto` → `GetNumStationAssistsOutProto` | `max_station.statistics`; owner check, settles placements and candy/XL credits | B |

The first native tap sent 2462 twice, then 2467 and 2461. A missing call to
3051 is not evidence that tap transport failed. Availability is carried in the
Station's start/end/battle/inactive/cooldown fields, not a fabricated RPC.
Station IDs, battle seeds and the map-cell distinction are preserved.

| Native PBS path | Request → response | Validation/effect | Verification |
|---|---|---|---|
| GET/POST `/max/api/get-state` | Empty → `BattleStateOutProto` | Owner/session, authoritative clock/AI, HP, transforms, victory/defeat and durable room | P+B |
| POST `/max/api/get-time` | Empty → `BattleClockSyncOutProto` | Bound battle/player/PBS session, clock only | P+B |
| POST `/max/api/send-player-event` | `SendBattleEventProto` → `SendBattleEventOutProto` | Native timed moves/energy/damage/guard/heal; receipt keyed by room/player/credential/sequence; changed replay rejected | P+B |

PBS headers are `Authorization: Bearer <PBS token>`, `nbl-battle-id` and
`nbl-player-id`, with the family account session validated independently.
Responses include `nbl-gs-rsp-ts`. No credentials are reproduced here.

Attack event type 4 uses `attack_type` 1/2/3 for normal moves and 5/6/7 for
Max Attack/Guard/Spirit. Selection event 24 is not a separate attack RPC.
Dodge is event 5, substitution 7, quit 3, cheering 25. Existing subgroup
selection/transformation events 23/24/31/32/33 remain server-authoritative.

Optional descriptor-only calls remain unimplemented: 2452 lobby publicness,
2463–2466 save-for-later, 2468 replenishment, 2470 reporting, 2471 debug reset,
2475 station spawn encounter and 1505 invitations. No invented success stubs
were added. Loot does not currently create a station wild encounter. Regular
raid RPC 3094 is not claimed to support Max; the observed Max reward loop did
not require it. Cheering requires explicitly labelled custom numeric tuning
when official numeric provenance is unavailable.

## Native evidence

Private local evidence root: `.private/m182-power-spots-20261026/`. Screenshots,
videos, private database, APKs, certificates and DPAPI account material stay
ignored. Links below refer to that local evidence, not distributable assets.

| Native observation | Screenshot/evidence |
|---|---|
| Correct boss, Battle button and 1,120 MP after collection | [Wooloo details](../.private/m182-power-spots-20261026/screenshots/battle-details-ready.png) |
| Real combat, server HP and Max selection | [Combat](../.private/m182-power-spots-20261026/screenshots/wooloo-combat-ready.png), [Max moves](../.private/m182-power-spots-20261026/screenshots/wooloo-max-moves.png) |
| Native “HP RESTORED” and Guard | [Spirit](../.private/m182-power-spots-20261026/screenshots/wooloo-max-spirit.png), [Guard](../.private/m182-power-spots-20261026/screenshots/wooloo-guard-second-cycle.png) |
| Server-earned victory and 500 dust, 2 Potions, 1,000 XP, 12 balls | [Rewards](../.private/m182-power-spots-20261026/screenshots/wooloo-reward-summary.png) |
| First earned Wooloo, CP 201, Dynamax and catch rewards | [Details](../.private/m182-power-spots-20261026/screenshots/earned-wooloo-details.png) |
| Storage after force-stop/relaunch, earned catch and stationed Pokémon | [Cold storage](../.private/m182-power-spots-20261026/screenshots/cold-storage-persistence.png) |
| Actual Gigantamax model and 800 MP entry | [Gmax details](../.private/m182-power-spots-20261026/screenshots/gmax-native-boss-details.png) |
| Gmax victory/rewards and capture | [Gmax rewards](../.private/m182-power-spots-20261026/screenshots/gmax-reward-summary-ready.png), `max-capture-before.h264` |
| Bob's earned Gmax Charizard survives APK update/restart and is eligible in native party selection | [Restarted party](../.private/m182-power-spots-20261026/screenshots/bob-v4-lobby.png) |

Alice's first Wooloo battle included 29 native Fast requests, a Charged request,
two Max Attacks, one Max Guard and two Max Spirits. The authoritative boss HP
reached zero after about 323 seconds; reward completion was not an instant-win
response. Her placement selected owned Pokémon 1 and her actual hit caught
Wooloo into storage ID 7. Her subsequent Gmax encounter was also caught. Bob
independently caught his own Wooloo and Gmax Charizard, with separate fees,
balances, rewards and storage IDs. See the final database evidence below for
the complete per-trainer ledger and capture identities.

Source/resource load logs include successfully resolved native `BreadBall`,
its FX and Gmax prefab `pm6.fGIGANTAMAX`. Existing unrelated unsupported startup
RPCs still log errors. A native reticle warning, “No capture probability to set
reticle with,” also occurred despite supplied reward probability fields and
successful hit/capture. Its rendering/probability consumption needs a separate
targeted trace; this report does not call the native log error-free.

## Tests and build

| Check | Actual result |
|---|---|
| Full unified Python regression | **605 passed, 15 skipped**, 578.01 s; `full-regression-console.log` and `.private/test-runs/20261026-215146-2cd632c4/results.json` |
| Four Node suites from that run | UI, raids, family and world all passed |
| Later location/resource/reference tests | **33 passed, 1 skipped**, 6.87 s, including five narrowly scoped fixture-location cases |
| Final standalone/client/structure/location unittest run | **16 passed, 1 skipped**; 17 executed in 5.682 s |
| Final Node UI checks | Passed, including 20 readiness/capture lifecycles, async loading, early throws, ordinary-catch isolation, exact handle cleanup and both authored sizes |
| Final other Node suites | Raids, family, world passed |
| Rust library tests | **158 passed, 2 ignored**; ignored cases require a separately running rehearsal server |
| Fresh Android ARM64 Rust release | Built; ABI check passed, five exports/JNI, AArch64 ELF/SONAME |
| Java/standalone APK build and signing | Fresh separate signed APK attempts built; no uninstall or app-data clear |

The full Python run preceded the later fixture-location and presentation
changes. The later focused tests and final Node checks cover those changes;
605 is not represented as a second full final run. Skips include unavailable
optional Unity/Explorer/NumPy/APK fixtures and Windows symlink creation.
The final targeted Python rerun used unittest because pytest was absent from
the invocation environment; the prior full pytest log is retained separately.

The release plugin SHA-256 is
`28870bd87a5de756225dea210c1ca1639eb665516b9cbdfb255bd8ec75c13d93`.
No Rust source changes were needed. Test APKs use the same existing local
development signing identity; the builder now accepts an explicit `--key-alias`
without replacing or changing its keystore.

## Files changed for this milestone

- `src/dittobuffer/runtime/http.py`: actual PBS bridge, listener factoring and
  header migration validation.
- `src/dittobuffer/gameplay/game.py`: private Max encounter owner guards.
- `src/dittobuffer/gameplay/max_system.py`: complete-reference compatibility,
  wrapper handling and verified default-form matching.
- `src/dittobuffer/gameplay/friends.py`: tightly scoped fixture presence.
- `src/dittobuffer/core/master.py`: cache revision.
- `client/local-max-ball.js`, `client/inspect-encounter.js`: packaged native
  readiness/capture-art correction and read-only diagnostics.
- `scripts/build-standalone.py`, `scripts/test-client-ui.js`: integration build
  and presentation regression cases.
- `scripts/m182-fixture.py`, `scripts/m182-server.py`: explicit isolated fixture
  and redacted wire-evidence runner.
- `config/max.example.json`, `config/resources/pokemon-max-assets.json`,
  `docs/local-resources.example.json`: portable opt-in configuration and pins.
- `tests/test_max_http.py`, `test_max_encounter_ownership.py`,
  `test_max_reference.py`, `test_max_form_compatibility.py`,
  `test_m182_fixture_location.py`: real transport, transaction, ownership,
  descriptor/form and fixture-presence regressions.

No protocol field edits were necessary. Map Station 17 and Routes 20 retain
literal-wire regression coverage. No legacy flat package was recreated.

## Preservation and final validation record

The final standalone test APK is
`.staging/m182-native-v5/dittobuffer-family.apk`, 200,168,386 bytes, SHA-256
`bc57bfea72deeb27ff956ee80629805d8dc320120c2a30a8e8396acc4beb064c`.
Its embedded adapter matches the current source after newline normalization;
its plugin matches the freshly verified release above. Signing verification
passed with the original development certificate SHA-256
`61446bcaebceff8354565beaee066b2abb6d98b8b42a58bf76a600076cf2c797`.
This APK contains the isolated development profile, not a normal-player release.

Final-build native capture evidence:

- `max-ball-v5-ready.json`: powered native art **949 ms** after the first ball
  object, while the encounter introduction was still running. No external
  diagnostic hook supplies the fix; it is packaged in the APK.
- [Giant pink landing with visible front button](../.private/m182-power-spots-20261026/screenshots/max-capture-v5-03.png).
- [Normal closing ball with front button](../.private/m182-power-spots-20261026/screenshots/max-capture-v5-06.png).
  Native contraction is retained; the final white ball is deliberately not
  forced to fill the screen. This is the implemented size boundary.
- `max-capture-v5.h264` and `ball-native-events.jsonl` preserve the real throw,
  collision and capture frames. The final full sequence is Dynamax Wooloo.
  Gigantamax Charizard's real combat/capture and immediate readiness were
  observed in earlier APK attempts; the final v5 orientation change was not
  separately rerun against Gmax. Both use the same native Max encounter path.
- [Storage after the final force-stop/relaunch](../.private/m182-power-spots-20261026/screenshots/final-cold-storage.png):
  six owned Pokémon, including both earned Wooloo and the Gmax-capable
  Charizard. The photographed collection matches the server rows.
- [Ordinary encounter regression](../.private/m182-power-spots-20261026/screenshots/ordinary-v5-ready.png):
  ordinary Bulbasaur retained a standard Poké Ball, with no Max power-up or
  scale correction. Two real ADB throws missed and consumed two ordinary
  balls. This is not a newly verified ordinary capture.

The final read-only SQLite receipt is
[final-database-evidence.json](../.private/m182-power-spots-20261026/final-database-evidence.json).
Each trainer owns the original three **unearned fixture Pokémon** plus three
earned Max captures. Each has three durable collection claims, six Max rooms
in total have a server victory/zero boss HP and exactly one real participant.

| Trainer | Earned native storage IDs | Final MP | XP | Stardust |
|---|---|---:|---:|---:|
| Alice | 7 Wooloo, 8 Charizard (mode 2), 12 Wooloo | 60 | 213,300 | 101,800 |
| Bob | 9 Wooloo, 10 Charizard (mode 2), 11 Wooloo | 60 | 213,300 | 101,800 |

For each trainer, the ledger is exactly `1000 + 120 - 250 + 120 - 800 + 120 -
250 = 60 MP`; each of the three battle fees occurs once. Every earned encounter
has 11 of its 12 reward balls remaining and one owned storage record. Automated
real-HTTP tests cover duplicate completion/retries and adversarial account
access; those negative requests are **BACKEND_VERIFIED**, not simulated native
touch evidence. A native toast after completion said “Collected 250 MP” even
though SQLite correctly debited 250; that presentation label remains a gap.

Restoration completed after testing:

- Original development APK reinstalled with `install -r`, SHA-256
  `8a1cac1cd992ee1a4d0c6901647e3ba3f31c7d4b451339a90cb2564d45cd04da`.
  Neither uninstall nor app-data clearing was used. The tested v5 fix remains
  in the new-layout source/build, not installed over the restored original.
- All **66 original private files** match their saved bytes, owners and modes;
  original runtime permission grants/flags, app-op modes and both external
  catalog files match. The five new task-only launcher/fixture files were
  archived with the complete test state before removal. Disposable generated
  code cache is retained. Android Keystore was not enumerated in the baseline;
  no original key was deleted. Actual app-op usage timestamps change during
  testing and are not claimed restored.
- Both official APK hashes and all **7,059 official cached resource hashes**
  match. The official application was never launched or written by the task.
- Both apps were returned stopped and the home screen restored. Only the
  verified private server worker/launcher and task-owned diagnostic process
  were stopped. Only forward 27049/reverse 8447 and the task's diagnostic/UI
  temporary files were removed; the user's separate scrcpy session was left
  alone. The diagnostic was not installed as a permanent service.
- All **652 original checkout file hashes** match. All **459 integration
  baseline files** remain. Both HEADs, branches and Git index hashes match
  their checkpoints. Every checked `.gitignore`, including native's, is
  unchanged. No normal/family database, published Netherlands world or private
  transfer archive was used as a writable test input.
- Existing 958 Pokémon registrations and accepted asset graphs were retained;
  this is preservation evidence, not a new 958-species Android visual test.
  Runtime resource comparison retains the existing 23 explicit Max graphs.

Receipts: `device-restoration-result.json`, `device-cleanup.json`,
`server-cleanup.json`, `source-preservation.json`, `final-build-evidence.json`
and `native/runtime-address-preservation.json` in the private evidence root.
The initial archive restore stalled because `adb exec-out` does not supply
input EOF. A binary write/readback probe verified `exec-in`; restoration then
resumed from the untouched saved checkpoint and passed all comparisons.

The final four Node suites and scoped `git diff --check` passed. Known remaining
work is the Max reticle probability warning, the misleading MP fee toast,
native defeat/retry/rejoin acceptance, optional Power Spot RPC workflows and
actual multi-device combat. No multiplayer, physical-walking, complete-game
Android regression or Samsung verification is claimed. No commit or push was
performed.
