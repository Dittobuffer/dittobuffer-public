# M24.1 ? Native raid victory and summary

## Implementation

Existing timed combat, persistent victories, private bonus encounters and reward
receipts were retained. The vNext `BattleResultsProto.player_results` record
previously contained only attacker and XP; loot, participation, encounter and
badge were populated only in the legacy parallel arrays. `raid_rewards.project`
now supplies the actual supported per-player fields as projections of the same
persisted entitlement. No second grant or encounter is created.

Damage achievements use committed server damage. Premier Ball components are
computed once from the configured base, damage fraction, gym ownership, finish
speed and existing friendship bonus. Their sum is the stored encounter budget.
Mega speed continues to award energy rather than speed balls. Policies are
validated and frozen into newly scheduled raid definitions. Existing allocation
settings are preserved unless the new bonus policy is explicitly configured.
`config/raid-rewards.example.json` documents custom tuning; it does not present
unverified official numeric tables as fact.

Saved results keep both nested and legacy remaining-ball fields current.
A genuine winner can recover the owned summary after restart/global expiration;
that does not admit another trainer or restore a globally expired map raid.

Primary mechanism references: [official catch guidance](https://niantic.helpshift.com/hc/en/6-pokemon-go/faq/2739-i-m-unable-to-catch-a-raid-boss-after-defeating-it/),
[official speed bonus change](https://pokemongo.com/post/raid-changes-2020/?hl=en).
These establish the categories; numeric policies remain explicit configuration.

## Actual Pixel 5 verification

Client 0.429.2, serial `15191FDD4003PY`, isolated trainer/database. The normal
8445 server and its database were not stopped or used for the test. The Pixel's
8445 reverse temporarily points at the isolated 8448 listener. The original APK
and private app preferences are backed up for restoration after all M24 tests.

- Entered a visible Pidgey raid and its native lobby, using a real raid pass.
- Native combat: Dragonite versus Pidgey, visible HP/timer, 29 touch inputs.
- Server boss HP: **180 ? 146 ? 112 ? 78 ? 44 ? 10 ? 0**; outcome victory.
- Actual client sent `COMPLETE_RAID_BATTLE` (3010), receiving one persisted reward.
- Native XP transition and **Hardest Hitter ? 180 damage** appeared.
- Native summary displayed base **6**, damage **3**, gym **2**, speed **4**,
  total **15 Premier Balls**, a 00:59 finish and a full Mystic contribution bar.
- Native **Go to Bonus Challenge** opened Pidgey CP370 with 15 balls.
- First real touch throw caught it. SQLite assigned storage ID **7**, species16,
  Premier Ball5, origin raid level1; remaining balls14, owned count7.
- Catch screen displayed 100 XP/100 Stardust/3 candy. Stored totals after raid
  and catch: XP213100 (initial fixture210000), Stardust600 (initial0).
- Raid loot receipt contains potion2 and rare-candy1. Inventory quantities also
  include separate fixture level-up supplies; they must not be mistaken for raid loot.
- Cold restart returned to the map; native storage showed **7/300**, including
  Pidgey CP370. XP/dust, one reward receipt, ID7 and remaining14 balls persisted.

Battle damage and captures came from native touch requests, not fixture RPC wins.
Initial level access and the six owned party Pokemon are declared test fixtures.
No Master Ball behavior was modified or tested.

## Tests and evidence

**36 raid/co-op Python tests passed**; Node raid routing/GC-handle checks passed.
Regressions cover literal per-player wire fields, result equivalence, bounded
monotonic bonus policy, matching ball budget, retry/restart, expired earned
results, ownership, unsuccessful outcomes and independent co-op rewards.
Multiplayer remains host-tested here; no fresh two-device claim is made.

Private evidence (not committed) is inside this worktree at
`.private/m24-victory-01/`: `trace/events.jsonl`, `trace/server.log`, APK build/install
receipts, `native-victory-catch-state.json`, `restart-state.json`, and screenshots
`screen-raid-combat.png`, `screen-post-victory-04.png`,
`screen-raid-summary-late.png`, `screen-bonus-ready.png`,
`screen-raid-caught.png`, `screen-cold-restart-map.png`,
`screen-storage-after-restart.png`.
The achievement card's light background has reduced contrast; the measured name
and damage remain visible. Individual fast item-animation frames were not captured.
Audio was not assessed. Backend failure/retry simulations are separate from the
native victory/catch loop above.

APK: `.staging/m24-victory-build-01/dittobuffer-family.apk`, 201167810 bytes,
SHA256 `a833b09b547cf02383aa1689028909f0f9f1634061ae926cd8a6e6bada3c7b94`.
The existing ARM64 native plugin was reused unchanged; Java/D8, full assembly,
signing, installation and standalone execution succeeded.

## Local Git scope

Developed in `E:/DittoBuffer/.private/worktrees/m24-gameplay-20261027`, branch
`feature/m24-gameplay-completion-20261027`. Origin/main base is
`bd2ab84010b8a47cfd72d02d2f0cb275e4817e91`. Previously published source at
`5e367f83e12261e91320cc71505c9272bebf3b87` is an intentional prerequisite merge.
Machine-specific local commit `2032b56` is not an ancestor. No push or main merge
is authorized for this task. Stage explicit source/documentation paths only.
