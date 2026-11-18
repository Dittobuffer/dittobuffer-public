# M23 - Native mythical Research encounters

Native inventory and capture integration verified with remaining presentation gaps.
Run `m23-mythicals-20261027`; client 0.429.2; Pixel 5 `15191FDD4003PY`.

All twelve native mythical controllers were exercised through Android Research.
Nineteen scripted Research captures, one ordinary Research capture, one wild
capture and one raid capture completed with real native throws. All 22 owned
Pokemon and all 20 claimed Research stages survived app and server restarts.
This is not an unqualified completion claim: audio, full live-camera AR and
Celebi teleport visuals remain unverified; the stale orange objective button
still requires reopening Research.

## Checkout and scope

Work is in `builds/structure-audit-main-20261026`, branch
`integration/routes-new-layout`, HEAD
`c4bb197c4cb779053c90387d3f2fd6938c7767eb`. The original dirty checkout remains
on `hotfix/dynamaxbug`, HEAD `09460bf187547472a7cbb0952abdb000d786ae57`.
A read-only fetch found remote main `bd2ab84`; intervening changes concern
README/package metadata. No merge, reset, stash, staging, commit or push occurred.
Every `.gitignore` remains unchanged. Existing Routes/Mateo/Vivillon, Max,
Rocket, PvP and family integrations were retained in the canonical structure.

## Complete native inventory and coverage matrix

The target list derives from the supplied ARM64 binary, IL2CPP metadata, native
factory branches and embedded strategy prefabs: twelve controllers for eleven
species. Exact resource GUIDs/aliases, embedded object IDs, native RVAs and catch
rules are in `src/dittobuffer/gameplay/special_encounters.json`. Exact source and
derived bundle hashes/closures are in `config/resources/mythical-cinematics.json`.

| Pokemon / registry key | Native cinematic classification and controller | Main resources | Pixel playback | Catch verified | Status |
|---|---|---|---|---|---|
| Mew / `mew_research` | SCRIPTED_ENCOUNTER_WITHOUT_CINEMATIC; MewEncounter | Existing DefaultParkScene; embedded invisibility shader | Normal/shiny; translucent moving shiny observed | Both | Native scripted path verified; live AR unverified |
| Celebi / `celebi_research` | SCRIPTED_ENCOUNTER_WITHOUT_CINEMATIC; CelebiEncounter | Existing DefaultParkScene; embedded teleport references | Normal/shiny/repeated normal; teleport not positively observed | All three | Catch verified; movement/AR gap remains |
| Normal Jirachi / `jirachi_research` | UNIQUE_NATIVE_CINEMATIC; JirachiEncounter | Embedded awakening/supporting Pokemon; existing DefaultParkScene | Sleep, awakening and supporting musical animations | Yes | Verified |
| Shiny Jirachi / `shiny_jirachi_research` | UNIQUE_NATIVE_CINEMATIC; ShinyJirachiEncounter | ShinyJirachiScene; ShinyJirachiEncounterMusic | Starry sky/night forest | Yes | Verified; different Research variant |
| Victini / `victini_research` | UNIQUE_NATIVE_CINEMATIC; VictiniEncounter | VictiniScene | Lighthouse, coast, fireworks and terrace transition | Yes | Verified |
| Meloetta / `meloetta_research` | UNIQUE_NATIVE_CINEMATIC; MeloettaEncounter | MeloettaEncounterStage_Aria; Aria/Pirouette performance/finale/music references | Curtains, stage, notes, dance; both forms | Both forms | Verified for Aria and Pirouette |
| Shaymin / `shaymin_research` | UNIQUE_NATIVE_CINEMATIC; ShayminEncounter | ShayminScene; Land/Sky intro/emote/music references | Flowers, petals, mountains; normal/shiny Land and normal Sky | All three | Verified for tested variants |
| Diancie / `diancie_research` | UNIQUE_NATIVE_CINEMATIC; DiancieEncounter | DiancieScene; DiancieEncounterMusic | Crystal cave, camera, light and Sableye; normal/shiny | Both | Verified |
| Marshadow / `marshadow_research` | UNIQUE_NATIVE_CINEMATIC; MarshadowEncounter | MarshadowScene | Jungle intro, camera, animated model, catch transition | Yes, following interruption/recovery | Verified |
| Shiny Keldeo / `shiny_keldeo_research` | UNIQUE_NATIVE_CINEMATIC; ShinyKeldeoEncounter | KeldeoScene; MythicalEncounterMusic | Pond, waterfall, reflections and two Rufflet | Yes | Verified; different Research variant |
| Volcanion / `volcanion_research` | UNIQUE_NATIVE_CINEMATIC; VolcanionEncounter | VolcanionScene | Steam intro, river, island, waterfall and supporting Pokemon | Yes | Verified |
| Zeraora / `zeraora_research` | UNIQUE_NATIVE_CINEMATIC; ZeraoraEncounter | ZeraoraScene | Canyon/desert intro and animated model | Yes | Verified |
| Normal Keldeo | NORMAL_RESEARCH_ENCOUNTER; generic ResearchEncounter | Existing generic resources | Ordinary name/CP, bag balls and berry selection | Yes | Ordinary regression; no unique normal-Keldeo factory found |
| Hoopa, Zarude | NORMAL_RESEARCH_ENCOUNTER in this dispatcher | No species-specific factory found | NOT_TESTED in M23 | NOT_TESTED in M23 | No unique cinematic advertised |
| Other Mythicals without a native branch | NORMAL_RESEARCH_ENCOUNTER in this dispatcher | No distinct scene established | NOT_TESTED in M23 | NOT_TESTED in M23 | Not counted as native cinematic support |

Shiny Mew/Celebi/Shaymin/Diancie reuse their species controller. Shiny Jirachi
and shiny-only Keldeo have distinct factories. Other shiny/form combinations are
not accepted solely because the registry permits them. Snorlax, FTUE, Ultra
Beast, Transparent, Rocket, coordinated-raid and Eternamax are separate dispatch
cases, excluded from this mythical inventory. Transparent is not an additional
StoryQuest mythical controller.

## Actual client binding

- `EncounterState.SetupResearchEncounter`, RVA `0x870BEB8`, maps wire
  `EncounterType_STORY_QUEST` **4** to internal strategy context **2**.
  Timed StoryQuest **10** and challenge rewards normally follow context **1**.
- `EncounterState.btov`, RVA `0x8709A24`, dispatches by species and, for
  Jirachi/Keldeo, `PokemonDisplayProto.shiny`. Victini is an explicit exception:
  its native branch also accepts context 1. The registry records wire contexts
  `[4,5,6,9,10]` for Victini; others use `[4]`. Only StoryQuest Victini was tested
  on Android; its generic-context exception has binary and automated evidence.
- `EncounterInstaller` embeds strategy prefabs in `sharedassets1.assets`.
  Their exact serialized GUIDs identify scene/music resources. There is no new
  cinematic wire ID or invented event/world-spawn trigger.
- Actual device RPCs: GET_NEW_QUESTS **900**, GET_QUEST_DETAILS **901**,
  COMPLETE_QUEST **902**, QUEST_ENCOUNTER **904**, GET_QUEST_UI **2008**,
  CATCH_POKEMON **103**, inventory **4**, USE_ITEM_ENCOUNTER **154**.
  `QuestEncounterProto` has fixed64 Pokemon ID field **1**, quest ID field **2**.
  `QuestPokemonEncounterProto.encounter_type` field **3** is 4 for StoryQuest;
  `quest_encounter_type` field **10** remains Unset (0). Values 1/2 mean Ultra
  Beast/FTUE, not mythical scene IDs. No protocol schema changed in M23.
- Native `ResearchEncounter.OverridesAttemptCapture` (`0x87320D4`) and
  `AttemptCapture` (`0x87323BC`) process authored fake escapes locally. Real hit
  throws produced local steps 0->1 and 1->2; the final hit sent the catch RPC.
  Additional server breakouts would incorrectly double the sequence.
- All twelve subclasses override `UsesInventory` to false; generic Research
  uses the bag. Their server consumption paths remain distinct.

Full flow: Research claim -> owned frozen entitlement -> QuestEncounter 904 ->
native species/shiny strategy -> authored playback -> scripted throws -> catch
103 -> authoritative transaction -> owned Storage.

## Resource restoration and isolation

Read-only inspection of the operator's existing official `catalog_952.json`,
cache and embedded APK dependencies resolved the selected source graphs. No
network asset download or official-cache write was used. These proprietary
operator inputs carry no implied redistribution permission.

Active ignored output:
`.private/m23-mythicals-20261027/resources-v6/`.

- **111 copied/derived bundles; 26,226,714 derived bytes**;
  Unity `6000.3.21f1` / `6000.3.22f1`.
- **25 script field contracts**, including namespaces, inherited fields,
  nested-class boundaries and compiler-generated backing fields.
- **24,051 non-null serialized references**, zero missing references and zero
  script-field compatibility errors. Structural checks alone do not prove ABI
  or rendering; the twelve native-controller tests add runtime evidence.
- Existing CAB isolation plus a distinct M23 component/dependency namespace.
  **97 shader identities** isolated with fixed-size substitutions; compiled
  shader programs and unrelated serialized bytes verified unchanged.
- **19,043 accepted catalog keys** compared semantically, zero unrelated graph
  changes. **48 selected mythical aliases intentionally retargeted** to isolated
  copies. Prior Mateo/Max graphs and Pokemon registrations retained.
- Existing `DefaultParkScene` and GUID `21c27dd1dad1d44a680cacf34d88cfcf`
  preserve their accepted graph. The newer source park is inventoried for
  provenance, not globally overlaid onto ordinary encounters.

Source catalog SHA-256:
`aa9c98c36dfae8096e43fbfc2f77cf3c2b6102a9938f57c0c3327ae5340e027d`.
Derived catalog SHA-256:
`984af473b5db3b74247a1c264fc4bcb86070b3c48d65203a4f6d6c071d5a81f9`.
The public manifest contains individual hashes, cache versions, lengths, Unity
versions, aliases and closures, without proprietary bytes. Main/supporting
Pokemon use the existing `pokemon-assets.json` registry. Native animation,
camera, particles, lights, shaders, props and audio/music references are retained.
No replacement image, video or fabricated animation is used.

No references remain unresolved in the selected audited graph. This does not
prove audible playback or the AR/teleport acceptance gaps below. Each operator's
inputs need their own import gate and Android test. Missing/incompatible inputs
produce an exact audit and no final runtime registry. Explicit native rewards
retain recoverable RESOURCE_BLOCKED state instead of discarding the encounter
or silently using a generic scene.

## Server implementation

`special_encounters.py` and its JSON registry provide validated configuration
keys, not new network IDs. An explicit reward can use:

```json
{"type":"encounter","species":494,"level":15,"shiny_rate":0,"special_encounter":"victini_research"}
```

The existing Research transaction validates objectives/claims and creates the
owned entitlement plus frozen native metadata in the additive
`special_research_encounters` table. Species, form, shiny, IVs, CP and properties
are assigned once. Reopening does not reroll. Snapshots retain source, context,
resource requirements, catch rules, open count and Storage identity. Existing
eligible entitlements receive additive metadata without changing Pokemon bytes.

The client has no cinematic-completion acknowledgement. OPEN_REQUESTED is only
an opening request, never proof that a scene was watched. CAPTURED and Storage
identity commit in the canonical catch/replay transaction. Captured IDs have no
Pokemon foreign key that could block later legitimate transfer.

Only native scripted Research receives unlimited standard ball 1, rejects other
balls/berries, does not flee and succeeds on the native final hit. The wild RPC
cannot bypass ownership. Ordinary Research/wild/Raid/Rocket/Max retain existing
rules, including Victini's native context exception. Invalid IDs, species and
shiny combinations fail validation. Field/Breakthrough definitions cannot
request StoryQuest-only strategies.

An actual Android regression found the ordinary Research berry mismatch: the
client sends its visible quest ID, but the canonical spawn point is `quest:<key>`.
`items.py` now accepts either ID only for that player's matching owned encounter.
Arbitrary IDs/foreign ownership are rejected; repeating an active berry does not
consume another. Normal Keldeo verified Razz 10->9 and an ordinary bag-ball catch.

`config/special-research.example.json` supplies an original, disabled-by-default
Victini storyline. No normal trainer automatically receives Mythicals. Special,
Masterwork, eligible legacy/event rewards share the verified native binding;
proprietary dialogue is not copied. Registry extensibility is limited to verified
native strategies; arbitrary custom Unity scenes/editor are not implemented.

## Reproduction and isolated development controls

From the integration root, after existing setup and independent model/avatar
asset restoration, supply the exact catalog, bundles and 0.429.2 metadata:

```powershell
python scripts/m23-encounter-resources.py --catalog OPERATOR_CATALOG.json --bundle-dir OPERATOR_BUNDLES --dump OPERATOR_04292_dump.cs --output .private/mythical-resources
python scripts/m23-fixture.py --development-mode --output .private/m23-rehearsal --registry .private/mythical-resources/registry.json --encounter mew_research --encounter shaymin_research --encounter meloetta_research --shiny-encounter mew_research --form-encounter shaymin_research=92 --form-encounter meloetta_research=152
python scripts/m23-server.py --config .private/m23-rehearsal/server.json --evidence .private/m23-rehearsal/evidence
python scripts/build-standalone.py --profile .private/m23-rehearsal/profile.json --output .staging/m23-rehearsal --key-alias EXISTING_DEVELOPMENT_ALIAS
```

Authorized rooted-device `--serial 15191FDD4003PY` can replace `--bundle-dir`;
it reads exact cache/APK resources without starting or modifying the official
app. `--base` chooses the accepted runtime registry. Fresh ignored output is
mandatory. Build prerequisites and independent model/avatar resources remain
required; do not distribute APK/bundle bytes.

The fixture creates separate family accounts, SQLite, TLS and DPAPI-encrypted
credentials. Initial objectives are explicitly **NOT_EARNED**, pre-completed for
testing. Four later variant objectives completed from a real Android wild catch;
the last form objectives were again explicitly fixture pre-completions. All 20
encounter rewards were claimed in Android. Reset means creating a fresh fixture,
never resetting normal progression or automatically enabling a live reward table.

Checkpoint the development APK, private state, catalogs, permissions and app-op
modes before installing. All device commands must be serial-scoped:

```powershell
E:\platform-tools\adb.exe -s 15191FDD4003PY reverse tcp:8448 tcp:8448
E:\platform-tools\adb.exe -s 15191FDD4003PY install -r .staging/m23-rehearsal/dittobuffer-family.apk
```

Use the existing development launcher `org.dittobuffer.launcher.FamilyActivity`
in `org.librenlp.pokemongo.dev`. Sign in with the isolated encrypted credentials,
open Special Research, claim the objective, reopen Research if it stays orange,
claim the stage, watch the sequence, throw and inspect Storage. Restart both app
and fixture server. Interrupt an unclaimed encounter and reopen its pending
reward. Afterward restore the exact checkpoint and only task-owned ports/processes.

`scripts/m23-server.py` records redacted real protobuf traffic.
`client/inspect-special-research.js` is opt-in observation-only diagnostics
(`native_research_diagnostics`), recording context/controller/local steps. It
does not override controller selection, animations, collisions or catch results.

## Actual Pixel evidence

Private root: `.private/m23-mythicals-20261027/`. PNG names below are under
`screenshots/`. H.264 recordings are silent native screen captures. These files
remain locally preserved and intentionally absent from source control.

| Native case | Recording | Key PNG evidence (scene -> catch/details) |
|---|---|---|
| Diancie | diancie-native.h264 | diancie-scene; diancie-after-intro; diancie-third-hit; diancie-storage-confirmed |
| Marshadow/recovery | marshadow-native.h264; marshadow-recovery-catch.h264 | marshadow-opening; marshadow-animation; marshadow-recovery-card; marshadow-reopened-catch; marshadow-storage-confirmed |
| Meloetta Aria | meloetta-native.h264 | meloetta-opening; meloetta-main-animation; meloetta-storage-confirmed |
| Shaymin Land | shaymin-native.h264 | shaymin-opening; shaymin-catch-screen; shaymin-storage-confirmed |
| Shiny Jirachi | shiny-jirachi-native.h264 | shiny-jirachi-opening; shiny-jirachi-catch-screen; shiny-jirachi-storage-confirmed |
| Shiny Keldeo | shiny-keldeo-native.h264 | shiny-keldeo-opening; shiny-keldeo-catch-screen; shiny-keldeo-storage-confirmed |
| Victini | victini-native.h264 | victini-opening; victini-catch-screen; victini-storage-confirmed |
| Volcanion | volcanion-native.h264 | volcanion-opening; volcanion-catch-screen; volcanion-fast-final; volcanion-owned-detail |
| Zeraora | zeraora-native.h264 | zeraora-opening; zeraora-main-animation; zeraora-three-throws; zeraora-owned-detail |
| Celebi | celebi-native.h264 | celebi-opening; celebi-scripted-screen; celebi-after-first-hit; celebi-owned-detail |
| Normal Jirachi | jirachi-native-retry.h264 | jirachi-real-opening; jirachi-real-awakening; jirachi-catch-screen; jirachi-owned-detail |
| Mew | mew-native.h264 | mew-opening; mew-scripted-screen; mew-three-throws; mew-owned-detail |
| Shiny Celebi | shiny-celebi-native.h264 | shiny-celebi-opening; shiny-celebi-scripted-screen; shiny-celebi-owned-detail |
| Shiny Mew | shiny-mew-native.h264 | shiny-mew-scripted-screen; shiny-mew-adjusted-throw; shiny-mew-final-hit; shiny-mew-owned-detail |
| Shiny Shaymin Land | shiny-shaymin-native.h264 | shiny-shaymin-opening; shiny-shaymin-three-throws; shiny-shaymin-slower-hit; shiny-shaymin-owned-detail |
| Repeated Celebi | celebi-teleport-native.h264 | celebi-teleport-ready; celebi-teleport-later; celebi-teleport-first-escape; celebi-teleport-rehearsal-owned-detail |
| Shiny Diancie | shiny-diancie-native.h264 | shiny-diancie-scene; shiny-diancie-three-throws; shiny-diancie-final-hit; shiny-diancie-owned-detail |
| Meloetta Pirouette | meloetta-pirouette-native.h264 | meloetta-pirouette-performance; meloetta-pirouette-owned-detail |
| Shaymin Sky | shaymin-sky-native.h264 | shaymin-sky-intro; shaymin-sky-three-throws; shaymin-sky-owned-detail |

`jirachi-native.h264` is an early attempt obscured by a legitimate isolated
level-up overlay; use the retry for awakening evidence.
`volcanion-storage-confirmed.png` is an early encounter image, not Storage proof;
use `volcanion-owned-detail.png`. Early throws sometimes missed. Final capture
claims rely on actual hit/catch traffic, owned details and database records,
not a gesture count or optimistic filename.

### Persistent captured identities

All rows belong to the same isolated trainer. Final before/after restart exports
have identical Pokemon bytes, ownership, IVs, CP, form, shiny and Storage identity.
HP is final **current HP**: Aria Meloetta legitimately took raid damage (123->38).

| Storage ID | Pokemon | Source | Form | CP | Current HP | IVs Atk/Def/Sta |
|---|---|---|---|---|---|---|
| 1 | Diancie | scripted Research | 3074 | 1139 | 78 | 11/10/15 |
| 2 | Marshadow | scripted Research | 3145 | 1564 | 113 | 13/11/13 |
| 3 | Meloetta Aria | scripted Research | 151 | 1679 | 38 | 13/14/13 |
| 4 | Shaymin Land | scripted Research | 93 | 1360 | 121 | 13/11/10 |
| 5 | Shiny Jirachi | scripted Research | 1646 | 1387 | 122 | 15/14/12 |
| 6 | Shiny Keldeo | scripted Research | 149 | 1562 | 115 | 11/15/15 |
| 7 | Victini | scripted Research | 1886 | 1368 | 123 | 12/12/14 |
| 8 | Volcanion | scripted Research | 3075 | 1506 | 103 | 12/10/11 |
| 9 | Zeraora | scripted Research | 3150 | 1423 | 113 | 10/11/15 |
| 10 | Celebi | scripted Research | 1346 | 1369 | 122 | 12/15/11 |
| 11 | Jirachi | scripted Research | 1646 | 1371 | 124 | 11/14/15 |
| 12 | Mew | scripted Research | 1115 | 1369 | 122 | 13/13/11 |
| 13 | Bulbasaur | ordinary wild | 163 | 531 | 76 | 7/0/0 |
| 14 | Shiny Celebi | scripted Research | 1346 | 1393 | 123 | 15/14/14 |
| 15 | Shiny Mew | scripted Research | 1115 | 1359 | 124 | 11/10/15 |
| 16 | Keldeo | ordinary Research | 149 | 1535 | 113 | 10/14/10 |
| 17 | Shiny Shaymin Land | scripted Research | 93 | 1356 | 122 | 10/14/12 |
| 18 | Pidgey | ordinary raid | 962 | 367 | 80 | 11/13/14 |
| 19 | Celebi | scripted Research | 1346 | 1372 | 123 | 13/12/13 |
| 20 | Shiny Diancie | scripted Research | 3074 | 1122 | 77 | 10/10/12 |
| 21 | Meloetta Pirouette | scripted Research | 152 | 1658 | 123 | 15/12/13 |
| 22 | Shaymin Sky | scripted Research | 92 | 1512 | 123 | 13/12/14 |

### Recovery and transaction evidence

Marshadow was interrupted before capture by both app and server restart.
`recovery-before.json` / `recovery-after.json` preserve its entitlement and Pokemon
hash. The pending reward reopened the native scene; open_count became 2; catch
stored ID 2 without reroll or duplication.

- `final-twenty-two-before-restart.json` and
  `final-twenty-two-after-restart.json`: exact equality across all 22 Pokemon,
  native states, 20 completed stages and bag counts after both restarts.
- `screenshots/final-twenty-two-native-storage.png`: real Storage **22/300**,
  including the last Sky Shaymin, Pirouette Meloetta and Shiny Diancie.
- `screenshots/final-no-pending-research.png`: no pending Special Research,
  with completed Research cards after restart.
- `replay-safety-final-result.json`: **BACKEND_REPLAY_OF_NATIVE_EVIDENCE_COPY**.
  A fresh SQLite backup of the native-earned fixture was used: 22 capture
  requests retried twice returned saved results; 20 captured Research reopen
  attempts and 20 completed stage claims were rejected. Storage/wallet/items/
  candy/rewards did not change. This is backend replay, not additional UI tests.

### Ordinary gameplay regression

Real Android catches: wild Bulbasaur ID 13, ordinary Research Keldeo ID 16,
one-star raid Pidgey ID 18. The raid used explicit development tuning: boss
HP 60/CP 1000, lobby 20 seconds, battle 120 seconds. Native Mystic selection,
join, party selection, attacks, charged attack, damage, AI/HP changes, victory,
bonus encounter and capture ran through the client. No instant-win response or
fake participant was inserted.

Raid seed `22374323233956134`, battle
`raid-22374323233956134-0be0e08b2e1edfd9`; real CompleteRaidBattle reported victory.
Evidence: `raid-regression-native.h264`, `raid-regression-lobby.png`,
`raid-regression-attacks.png`, `raid-regression-results.png`,
`raid-regression-bonus-catch.png`, `raid-regression-owned-detail.png`.

Scripted catches consumed no bag balls. Initial 100 plus legitimate isolated
level-21/22 rewards (31+32) totalled 163. Wild Bulbasaur and ordinary Keldeo used
one each: final Pokeballs **161**, Great/Ultra **20 each**. Keldeo consumed one
Razz (10->9). Raid Premier Balls changed **12->11**, bag balls stayed 161.
Catch/Research progress toasts and inventory updates were observed. None of
these rewards, team selection or combat HP changes touched the normal trainer.

Rocket, Max, PvP, eggs, Routes, multiplayer, Samsung and physical walking were
not freshly exercised on Android in M23. Passing automated regressions do not
constitute fresh native verification of those systems.

## Automated and build results

| Check | Result | Evidence / qualification |
|---|---|---|
| Full Python suite | **624 passed, 15 skipped**, 236.41 seconds | final-forms-full-python.log; unique collection |
| Focused M23 tests | **14 passed** | 10 special encounter + 4 resource/overlay tests |
| Four Node suites | **All passed** | test-client-ui.js, test-client-raids.js, test-client-family.js, test-client-world.js |
| Rust host suite | **158 passed, 2 ignored** | native/test.log; ignores require rehearsal server |
| Standalone Java compilation/signing | **Passed** | v1/v2 signed private test APKs |
| Pixel install/launch | **Passed** | Actual playback/catches/restarts, not ADB detection alone |
| UnityPy/catalog validation | **Passed** | 111-bundle closure, 25 contracts, 97 shader identities, graph preservation |
| Fresh Unity Editor / Android NDK Rust rebuild | **NOT_RUN** | Native Rust source unchanged; no Editor build claimed |

Python skips: one Windows symlink restriction; thirteen optional NumPy or pinned
legacy palette/prop/effect/research fixtures absent; one conventional-path
standalone-build fixture absent. The actual M23 APK build/install passed
separately. Node suites cover HUD/navigation ownership, raid routing, Max Ball
readiness/collision-art ownership, family boundaries and world resume behavior.

Tests cover every registry ID, species/shiny mapping, source exclusions, owned
entitlements, wire responses, frozen properties/restarts, resource recovery,
free balls, invalid balls/berries/wild bypass, replay, ordinary Research berry
IDs/retries, legacy backfill, Victini's exception, alternate Sky/Pirouette forms
and prior overlay preservation. No tests were removed to obtain passing results.

Test APK v2 SHA-256:
`727cfe3c5fd86e551392931e375f7fa84b45b33b4413d11a8045d6f1f7e8cfde`
(201,167,810 bytes), retained privately in `.staging/m23-native-v2/`.

## Remaining gaps and next concrete fixes

1. **Audio UNVERIFIED**, per the user's explicit reply that they were not near
   the phone. Resource presence is checked; silent H.264 cannot prove audibility,
   volume handling or synchronization.
2. **Full live-camera AR UNVERIFIED** for Mew/Celebi. Both used the park. CAMERA
   was temporarily granted; shiny Mew's native translucency/movement was visible
   afterward, but permission causality was not isolated from timing. Do not
   force the availability getter or claim the camera path fixed.
3. **Celebi teleport/repositioning NOT_POSITIVELY_OBSERVED** in normal, shiny
   and repeated tests. Correct controller, scripted escapes and persistent
   catches are verified. Next: observe `ArStandardDeviceService.get_Available`
   (`0x77327B4`), `WebCamUtil.get_Status` (`0x7E3A67C`, Ready=4), Mew init
   (`0x874B1FC`) / Celebi init (`0x871A3D4`), background selection and teleport
   activation without overriding them. The exact blocking AR/FX condition is
   not yet established.
4. **Claimed objective remains orange until full Research reopen.** Server
   claims persist without duplicate rewards; tab switching is insufficient.
   CompleteQuestOut.Quest is correctly ClientQuestProto. Native success closure
   `QuestService.bki.bbsc` (`0x7F85B14`) calls toast service +0xD8; returning a
   child QuestProto would break reward extraction (`0x7F12F50` / `0x7F11670`).
   Next: trace inventory quest replacement via `bbtm` (`0x7F7F14C`), `bbtg`
   (`0x7F7BCD8`), `bbti` / `bbtk` and QuestsHaveBeenUpdated at +0x198. No
   speculative menu-forcing or incorrect protobuf workaround was added.
5. Native authored timelines/controllers and actual environments, camera and
   animation were observed. Exact retail frame/timing comparison, every clip,
   all appearance combinations and audio synchronization are not established.
   Generic Hoopa/Zarude captures were not repeated in M23.

These are stopping conditions for this verification pass, not a declaration
that every M23 acceptance criterion is complete. Next work is the native camera/
teleport gate and Research cache refresh, followed by an audible phone check
when available.

## Preservation and restoration

`device-restoration-result.json` records completed restoration:

- Original development APK restored byte-for-byte, SHA-256
  `8a1cac1cd992ee1a4d0c6901647e3ba3f31c7d4b451339a90cb2564d45cd04da`.
- All **66 original private files** restored with identical bytes, owner/group
  and modes. Four task-created launcher/settings files were archived in
  `device-after-test/private-state.tar`, then removed only to restore the
  checkpoint. All fixture evidence remains preserved on the PC.
- Original catalog_926.json SHA-256
  `3cb21dba7b8d390b368af65b9801eef4b7ce0eab4d49d1bc96dfb33cc071a9cb`
  and catalog_926.hash restored exactly; original profile/settings restored.
- Runtime grants/flags match the original, including CAMERA revoked to its
  original state; app-op modes restored. Usage timestamps changed during actual
  testing and are not claimed restored.
- Both official APK hashes and **all 7,059 official cache-file hashes identical**.
  The official application was neither launched nor modified.
- Only the verified M23 listener was stopped and task-added reverse tcp:8448
  removed. Both apps remain stopped, phone on Home, matching initial state.
- All **652 original-checkout files** and **464 pre-existing integration files**
  retained: zero missing/unexpected changes; only six intended existing files
  below changed during M23. HEADs, branches, index hashes and stashes identical.
  Existing 958 model registrations and accepted resource graphs preserved;
  this is not a fresh 958-species Android retest.
- Normal trainer/family databases, Netherlands world and private transfer
  archives were not used as writable test inputs. All rewards/captures and
  battle changes remain isolated.

Three early task-generated proprietary audit directories remain intact and
untracked: `builds/m23-native-scenes-v1/`, `v2/`, `v3/`. Automatic approval review
rejected both verified-copy cleanup (preservation/no deletion or move) and a
local `.git/info/exclude` addition (no-exclusion rule). Neither action executed.
No ignore/exclude file changed. **Do not stage or push these directories.** The
subsequent output guard requires a fresh already ignored location; active v6
resources are private. No proprietary payload/account was added to the index.

## M23 file inventory and Git status

Six pre-existing files modified during M23; every other pre-existing source hash
preserved relative to its start:

- `src/dittobuffer/core/database.py`: additive durable native metadata table.
- `src/dittobuffer/gameplay/research.py`: validated selection, ownership and recovery.
- `src/dittobuffer/gameplay/game.py`: scoped native catch transaction/bypass guard.
- `src/dittobuffer/gameplay/items.py`: scripted berry restrictions and ordinary
  owned quest-ID compatibility fix.
- `scripts/build-standalone.py`: optional read-only native Research diagnostic.
- `scripts/m20-compatible-resources.py`: optional independent namespaces,
  preserving existing defaults.

Eleven new source/manifest/test/documentation files:

- `src/dittobuffer/gameplay/special_encounters.py`
- `src/dittobuffer/gameplay/special_encounters.json`
- `scripts/m23-encounter-resources.py`
- `scripts/m23-fixture.py`
- `scripts/m23-server.py`
- `client/inspect-special-research.js`
- `tests/test_special_encounters.py`
- `tests/test_special_encounter_resources.py`
- `config/resources/mythical-cinematics.json`
- `config/special-research.example.json`
- `docs/MILESTONE_23_MYTHICAL_CINEMATICS.md`

The tree also contains earlier uncommitted structural/Routes/Max work; its whole
Git diff is not an M23-only diff. Nothing was staged, committed or pushed. Future
publication requires a reviewed explicit source-file list and must not include
the untracked proprietary scratch outputs or private evidence.

## External corroboration

Official pages corroborate Research sources/variants, not private protocol,
camera gates or exact timings:
[Shiny Keldeo Masterwork](https://pokemongo.com/news/final-justice-2025),
[Shiny Mew Masterwork](https://pokemongo.com/news/pokemongo-7th-anniversary),
[Shiny Jirachi Masterwork](https://pokemongo.com/news/pokemongo-9th-anniversary?hl=en),
and [Shiny Diancie Masterwork](https://pokemongo.com/gotour/global?hl=en).
Internal conclusions derive from the specific 0.429.2 binary/resources and
recorded Pixel tests.
