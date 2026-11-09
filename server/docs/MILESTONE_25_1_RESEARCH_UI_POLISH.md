<!--
  Copyright (C) Dittobuffer Contributors
  Copyright (C) chucny
  Copyright (C) gekkedavi

  This file is part of Dittobuffer. It is distributed under the GNU General
  Public License v3.0 (see LICENSE). The copyright notices in this file must
  be preserved in all copies, forks, and derivative works. Removing or
  altering these notices violates the license and copyright law.
-->

# M25.1 - Research UI, progression and stability

> Historical acceptance record: branch/status statements below describe that verification session. The later published source is reconciled with current main in [the 3 November 2026 gameplay integration](GAMEPLAY_MAIN_INTEGRATION.md). Existing partial/native acceptance limits remain in force.

Work: 28-29 October 2026, isolated Pixel 5 acceptance on serial `PIXEL_TEST_DEVICE`, Android 13, 0.429.2 development client. Only `E:\DittoBuffer\.private\worktrees\m25-research-20261028` was developed. The old workspaces are preservation sources.

## Status

Research fixes are implemented and exercised in native Android. Full milestone acceptance is **PARTIAL**: natural completion of every level 71-80 gate, some combat/capture categories and the remaining presentation details below are not established. Backend tests are not substitutes for those checks.

Branch `feature/m25-research-progression-20261028`, HEAD `f1468dd6bcc65a77babd681fdf106a2f2b5c7aa9`; fetched main base/reference `22e45651f23e7e7133bc8290d882eb5b09a4840f`. Existing dirty M25 work and the 61 reviewed M33.1/M34 dependency files were reused. No branch reset, stash, merge, commit or push. Private configuration/resource overlays remain excluded from the source review.

## Changes and observed causes

- **Blank native branch cards:** a read-only Unity inspection found correct localized text, active white text and valid rectangles, but a white native Mask over the tinted button. The background image was inactive. `research_presentation.py` supplies an original 609-byte teal PNG over the existing local game/map HTTP routes, plus bounded original title/description rich text. No client asset or APK rebuild is needed. Presentation version 2 refreshes existing task projection.
- **Completed archive:** distinct original story-ending descriptions stop unrelated completed storylines coalescing into one archive label.
- **Collection history:** `mini_collection.completed` follows the persisted reward claim. Badge 1002 now projects account-owned, actually claimed collection history from the existing claim ledger. Unclaimed/other-account records do not inflate the Collector count.
- **Calendar gates:** refresh incomplete daily/streak criteria at polling and local-date boundaries. Completed but unclaimed criteria remain earned. No normal level curve or player progress was migrated.
- **Super-effective Charged Attacks:** accepted positive Gym/Raid damage and ordinary Max charged attacks emit deduplicated authoritative research events. Cancelled/stale/zero-damage actions and Fast/Max moves do not qualify. Durable per-battle action identities prevent repeated polls awarding extra progress.

## Fresh Pixel research verification

All evidence is private under `.private/m251`, not packaged as redistributable game assets.

| Native workflow | Observed evidence and persistent result |
| --- | --- |
| Branch labels and confirmation | `native-cards-fixed-check.png`, `branch-confirmation.png`: two readable named cards. Selected Observe Landmarks; only its spin path was available. |
| Branch gameplay and claims | Real Pokestop spin, child 25 XP, immediate Task Completed, final two Balls. `m251-branch` completed stage 1; stage 0/1 receipts persist. |
| Cold completed archive | `branch-cold-completed.png`: Completed Research (2), separate M25.1 Field Notes and M25.1 Nearby Explorer. |
| Collection | Real Bulbasaur and Charmander catches; in-progress check/silhouette, completion toast, both checks, native 100 XP/two-Ball claim. Persisted completed=1. |
| Collector medal/history | `collector-history-fixed.png`, `collector-history-detail.png`: count 1, one completed challenge and 10/29/2026 date. The history label still shows stable ID `m251-collection`. |
| Timed Research | Two real gameplay stages (catch/spin, then throw), native child claims, Task Completed, root rewards and completed archive after restart. |
| Timed expiry | Earned an objective with an actual spin and left it unclaimed. Advanced only the isolated Studio clock beyond its actual configured one-hour end; card removed, progress 1/claimed 0/zero stage claims. Clock override archived in isolated content settings afterward; normal clock unchanged. |
| New-species XP | `first-reward.png` includes the real first-registration 1,000 XP bonus. |
| Studio | Actual Chrome DOM menus/templates/preview/schema-v1 export/file import exercised; no JavaScript errors. `studio-*.png`, `studio-browser-errors.json`. |

The native Collection card still says **Ended** once its criteria are met, even before a claim within the future valid window. Server timestamps and claim eligibility are correct. The generic branch confirmation image area is still empty. Max reward-upgrade art is blank with exchange cost 0 (not purchased). The native MP toast says Collected 250 MP after the 250-MP battle debit; the ledger correctly decreases 1,020 to 770, so that toast wording is misleading. These are remaining native presentation issues, not claimed fixed.

## Levels 71-80

Automated tests cover every transition 70->71 through 79->80, the source requirements, 30 qualifying non-medal event cases, 50 native templates, daily/streak reset and retry safety. This does not demonstrate natural native completion of all ten gates. Previous M25 native 70->71 evidence used an explicitly provisioned prerequisite fixture; it is not relabelled as earned progression. Fresh M25.1 access XP/parties/MP likewise remain **NOT_EARNED setup**. No 999 throws, platinum medals, walking or high-level achievements were inserted to claim completion.

## Combat regression evidence

The separate M251Combat account began with explicitly provisioned access XP (native level 21), six level-35 Pikachu, medicine and five paid passes. Later three Max-capable level-35 Charizard and an MP access budget were provisioned, clearly labelled NOT_EARNED. Raid HP/countdowns and Max tuning are explicit custom rehearsal balance, not official difficulty. All victories, damage, rewards and captures below came through real native inputs and authoritative server transactions.

| Category | Fresh native result |
| --- | --- |
| One-star Pidgey | Real 600-HP combat, Fast and Charged Attacks, victory, striped native results, 19 Premier Balls, genuine catch, stored species 16. |
| Three-star Pikachu | Real victory and one reward receipt; native bonus encounter CP 510. Capture remains **UNVERIFIED** after automated throws failed to land. Earned encounter retained. |
| Five-star Articuno | First attempt genuinely lost (boss 39 HP), no victory reward. Native Revive All consumed six Revives, three Potion rounds consumed 18 Potions. Same active raid retried with a fresh session and the same paid pass; genuine victory, 19 Balls, catch, details CP 1725/HP 131. |
| Mega Venusaur | Real Mega model/lobby/combat, victory and reward; standard Venusaur CP 1539 genuinely caught on the third attempted trajectory. Stored species 3. |
| Rocket Balloon | Visible balloon, native Grunt dialogue/party/three opponents, genuine victory, component tutorial, persistent receipt and eight-ball Shadow Bulbasaur CP 229 rescue. Four attempted throws missed; Shadow capture **UNVERIFIED**, not synthesized. |
| Dynamax/Gigantamax | Dynamax Wooloo: real combat/Max phase/victory, native 500 dust/two Potions/1,000 XP/12 Balls, actual catch CP 201 and stored Max mode 1. Powered ball is large before throw and remains large with its front button visible on landing (`dmax-earned-encounter.png`, `dmax-native-throw.png`). Gigantamax Charizard: genuine normal/Max combat, victory, rewards, actual CP 825 catch and stored Max mode 2. Both survive a cold restart and appear beside the earned raid catches in `combat-storage-after-restart.png`; IDs/CP/modes are in `cold-restart-captures.json`. Two single-participant Max receipts, boss HP 0, and MP ledger 900+120-250+120-800=90. This is explicitly custom solo rehearsal, not official Gigantamax difficulty. |

One-star, five-star, Mega, Dynamax and Gigantamax catches were present after cold restart. Three one-star and four five-star-rematch super-effective charged events were observed (seven total before later categories); per-event ledger evidence is retained. The failed five-star attempt has no reward receipt. Single real participant only; no multiplayer claim.

Selected evidence: `tier1-live-combat.png`, `tier1-earned-rewards.png`, `tier1-catch-attempt.png`, `tier3-current.png`, `tier3-encounter.png`, `five-star-rematch-proof.json`, `native-healing-proof.json`, `tier5-bonus-throw.png`, `legendary-storage-view.png`, `mega-battle-final.json`, `max-owned-storage.png`, `rocket-battle-final.json`, `rocket-proof.json`, `rocket-rescue-entry.png`.

## Asset and environment observations

The existing compact Max sample reference did not contain the needed complete definitions. Only the private profile now uses the existing full hash-pinned Max reference. The original opt-in Max manifest wrapper has a stale base-byte pin. A fixture-only wrapper was reviewed against the accepted catalog: all 2,739 accepted content hashes and 3,773 unchanged variant records are preserved; the previously missing `pm6.fLOAF` dependencies are resolved by four verified existing payloads. Full catalog graph validation passed. Original manifests and payload files were not changed/copied. `max-manifest-preservation.json` records the comparison. Portable deployment of that opt-in wrapper still needs an explicit source-manifest review; this task does not silently replace the normal configuration.

The native Max range guard initially rejected debug GPS correctly. The existing finite-site social-presence guard now also accepts only profile `m25-isolated` with exactly `.private/m251/trainer.sqlite3`, an explicit finite geographic allowlist, fresh timestamps and the opt-in fixture flag. Seven new tests cover valid presence without distance credit plus normal-path/wrong-profile/undeclared/stale/inaccurate/disabled rejection. No normal profile or walking gate changed. Afterward native collection awarded 120 MP and real Dynamax combat started. A private Game Master epoch refresh was diagnostic only; it was not the cause of the range failure.

The isolated map rendered objects/avatar/Power Spots but lacked visible road geometry in these screenshots. No world/map source was edited in M25.1. After restoring the original APK and normal profile, roads were visibly present again (`normal-restored-world.png`); the isolated test APK/profile map path still needs its own tile-origin check before a future release. Physical walking, fresh Samsung checks, audible audio and multiple independent clients are NOT_TESTED.

## Regression/build checks

- Repository `scripts/test.ps1`: **860 passed, 7 skipped**, 366.63 seconds. All four Node suites pass. `.private/m251/full-regression-after-presence.log` and the matching `.private/test-runs` receipt.
- Rust repository `scripts/test-native.sh` under Ubuntu-24.04 WSL: **158 passed, 2 ignored** (the ignored tests require the separate rehearsal server). `.private/m251/rust-tests.log`.
- Python skips: Windows symlink privilege plus six unavailable hash-pinned proprietary fixture inputs. The actual final APK preservation test was enabled using explicit local base/split/APK paths.
- No Rust/JavaScript/protocol payload changes were needed for these server presentation fixes. Existing M25 complete APK reused: `.staging/m25-native-final/dittobuffer-family.apk`, 201,176,002 bytes, SHA256 `d2c2322a1724c31981641bb1a90970d592fe2a5209411769943423d60cee26cc`. No new ARM64 or APK build claimed.
- Initial full-run path error referenced absent `builds/client-base/classes.dex`; corrected the three test input environment paths, preservation passed, then the complete final suite passed. No missing asset was fabricated to satisfy the test.

## Preservation and storage

The original development APK was reinstalled with `install -r`, without uninstalling or clearing data: `E:\DittoBuffer\.private\m25-20261028\installed-before.apk`, SHA256 `fd5023bc1ddab582dd191e9f90dc428f3ea1035ca783ad8dff4e4939f577f91a`. All **64 original files/shared preferences match byte-for-byte before normal launch**. The base TAR plus the small before-delta were reused; fixture changes were retained in a separate delta TAR. Extra task files remain in place; the restored original APK has `fixture_origin=false` and ignores the fixture selector. No file was renamed or deleted. the original Android UID and all 11 permission grant/flag records match.

Normal saved-account login and the overworld/visible roads were confirmed on the original APK (`normal-restored-world.png`). Login resumed against primary services: game/TLS **8445**, admin **8080**, maps **8081** (listeners 32644/30524). Fixture listeners 38445/38082/38083 are stopped. Original ADB reverses 8445/8080/8081 restored, temporary reverses/forward removed, temporary diagnostic Frida daemon stopped. No external Frida is required by the restored APK.

Normal progression/XP/dust/throws, 19 owned Pokemon, inventory, candy, appearance, account, Research and egg records match the pre-restoration hashes. The older M25 backup contains 13 Pokemon; those 13 still retain exact record bytes, and six additional legitimate normal catches occurred at least 25 minutes before the M25.1 checkpoint. They were preserved, never rolled back to that outdated baseline. See `normal-player-preservation.json` and `normal-after-restoration-records.json` for the distinction.

All six original machine configuration/manifests/pointer hashes match, including `.gitignore`. The published Netherlands SQLite database is 7,056,125,952 bytes and still hashes to `ca7568429854a23d5d46ffd218acbab5378d56d9d49779d9041d808b7dfec81e`. Both official package APK hashes match the existing preservation receipt; the actual installed official version is 0.433.1. No official app/cache was written. Accepted Pokemon/avatar resource manifests and assets are preserved.

Primary `E:\DittoBuffer` remains clean at its original HEAD. The M25 integration branch remains dirty for review, with no staging/commit/push/main merge. The four pre-existing local config/resource/world overlays are explicitly excluded from the approximately 6-MB source-only review. Gitleaks found no leaks. Exact final file list/status and receipts are private under `.private/m251`.


See [read-only storage audit](DEVELOPMENT_STORAGE_AUDIT.md). No cleanup/deletion/pruning/compression/move occurred. The user independently freed disk space and confirmed it. Shared hardlinks were counted by NTFS identity, so the 6.57-GiB Netherlands database and shared worktree inputs were not counted as independent copies. No new worktree, full checkout, full resource archive, APK copy/build or full normal-database backup was made. Existing validated backups were reused; changed Android settings were saved as a 40,960-byte delta. Final drive/evidence sizes are recorded in the final receipt.

## Next concrete work

Resolve the native Collection Ended/history-label/confirmation-image polish. Rehearse the missing three-star and Shadow captures with reliable touch input; never treat provisioned storage as catches. Complete representative high-level native counters before calling all M25.1 acceptance complete. Preserve normal-world geometry and review the stale optional Max wrapper separately. Master Ball remains excluded.
