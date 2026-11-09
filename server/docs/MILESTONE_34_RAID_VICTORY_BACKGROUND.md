<!--
  Copyright (C) Dittobuffer Contributors
  Copyright (C) chucny
  Copyright (C) gekkedavi

  This file is part of Dittobuffer. It is distributed under the GNU General
  Public License v3.0 (see LICENSE). The copyright notices in this file must
  be preserved in all copies, forks, and derivative works. Removing or
  altering these notices violates the license and copyright law.
-->

# M34 - Native raid victory background

> Historical acceptance record: branch/status statements below describe that verification session. The later published source is reconciled with current main in [the 3 November 2026 gameplay integration](GAMEPLAY_MAIN_INTEGRATION.md). Existing partial/native acceptance limits remain in force.

Worktree: `E:\DittoBuffer\.private\worktrees\m331-m34-20261028`.
Branch: `feature/m331-json-m34-native-victory-20261028`.
Baseline HEAD: `fdc55531ca013985bc8b1f30e8772b0d723a8b8b`.
Fetched main: `22e45651f23e7e7133bc8290d882eb5b09a4840f` (ancestor).
The primary M33 checkout and its services were preserved. No commit, push,
main merge, reset or .gitignore edit was performed.

## Proven cause in 0.429.2

Read-only inspection of the independently supplied client and live IL2CPP
hierarchy established:

- `RaidResolveUI` prefab, `sharedassets1.assets`, GameObject 4416.
- Existing full-screen Background child: GameObject 6154; Image 122521.
- `Niantic.Holoholo.Raid.RaidResolveUIController.Initialize`, RVA `0x7dc6d24`.
- Reflected `backgroundImage` field offset `0x198`; anchors span the native quad.
- Initialize copies `TransitionScreenService.MainImageSprite`, material and color.
- On actual earned vNext victories, the provider's sprite/material were null and
  recipient color was transparent `(0,0,0,0)`. The battle scene showed through.
- The original resident `RaidTransitionRingBackground` material (Material 151)
  uses `NianticCustom/UI/RingScanlines`. GPU support was true. This is an
  animated native shader, not a missing downloadable stripe PNG.

The precise upstream provider-clear caller was not fully disassembled. The
missing handoff at the recipient was directly observed. Canvas sorting,
background geometry and required resident shader were present.

Read-only native GLES program inspection explains why assigning the intro
material alone fails: its serialized radius is zero. The shader masks a ring
using distance from UV center, then applies horizontal scanlines and animated
glow. A full resolve quad requires a radius/thickness covering center/corners.

## Repair and bounds

`client/local-raid-presentation.js` repairs only the existing native resolve
Image after Initialize, only for actual victory status 2. It copies the exact
resident material, sets radius 1, thickness 2 and gradient scale 0, and applies
an opaque charcoal recipient tint `(0.25,0.25,0.25,1)`. Original shader colors,
frequency 1000, intensity 0.4, glow frequency/speed and animation remain intact.
These geometry/tint adaptations are explicitly local presentation compatibility,
not a claim that those numbers were official resolve asset defaults.

There is no added GameObject, Canvas, texture, global overlay or server reward
row. Native layout, safe area, title, rewards, achievements, Summary and dismissal
animations remain responsible for navigation. Controller-local materials are
reused and destroyed on native dismissal; the original material is never edited.
Missing/mismatched resources retain native behavior and log a diagnostic.

The builder includes this source in the standalone agent. The optional
`client/inspect-raid-resolve.js` diagnostic is not automatically packaged.
Final APK acceptance runs without an external Frida server. Earlier inspection
used a temporary matching server, stopped before autonomous verification.

## Summary investigation

Real RPC 3010 completion produced earned rewards and native Summary data. The
initial mostly empty white panel filled during its normal animation. One-star
Summary: Boss Defeated 12 + Damage 3 + Speed 4 = 19 Premier Balls, with real
elapsed time and Mystic contribution. Three-star also showed 19 with its own
elapsed time. No fake statistics, reward entries or changes to ball calculation
were necessary. A 600 HP baseline timeout awarded no victory receipt; a real
rematch defeated that boss and earned a persistent Pidgey catch (owned ID 8).

## Evidence and honest limits

Private evidence remains in this worktree's `.private/m34/`, never in Git.
The reference screenshot mentioned by the request was not present in the
attachment/workspace. Visual assessment therefore uses the written criteria;
a direct pixel/stripe-spacing comparison to the reference is unavailable.

The final standalone APK is `.staging/m34-native-final/dittobuffer-family.apk`,
201,176,002 bytes; SHA-256:
`dbf44601c753e638e0c898417de9df69e718c215212fac89725eda8d409cf803`.
It is an isolated stationary acceptance profile, not the normal player's APK.
The normal APK/settings were restored after acceptance.

Native category details, final test totals and restoration evidence follow in
the final verification section below.

## Final native verification

Only Pixel 5 `PIXEL_TEST_DEVICE` was used. Fresh Samsung verification: NOT_TESTED.
All category fights used actual entry, passes, lobbies and touch combat against
server-authoritative state. Isolated HP 180 and strong supplied test parties are
explicit presentation fixtures, not official difficulty or earned collection.
No battle victory was written directly. Normal data was never copied into these
fixtures. Separate original M33.1 catch ID 1 remained untouched.

| Category | Actual outcome and Summary | Bonus/navigation |
| --- | --- | --- |
| One-star Pidgey | Victory, 00:16, striped rewards/achievements/Summary, 19 balls | Real catch; owned ID 9; native details |
| Three-star Pikachu | Victory, 00:24, striped Summary, 19 balls | Earned encounter opened; ADB throws did not catch it; subsequent cold restart returned to map |
| Five-star Articuno | Victory, 00:33, striped Summary, 19 balls | Earned encounter opened; native flee returned to map |
| Mega Venusaur | Victory, 00:36, striped Summary, 200 Mega Energy + 15 balls | Ordinary Venusaur bonus opened; native flee returned to map |
| Shadow raid | UNSUPPORTED by current runtime/publication capability | No false Shadow gameplay claim |

Six persistent victory receipts exist including the baseline and final rematch.
Mega Energy balance is exactly 200 for Venusaur. Owned Pidgey IDs 8 and 9 are
real captures; IDs 2-7 are explicitly supplied unearned fixture party members.
Bonus catches for Pikachu, Articuno and Venusaur are not claimed.

Final-APK timeout used existing configurable 20-second battle rules and a 60 HP
fixture Pidgey. No attacks were sent: authoritative status 4, HP 60, native
white 'Time's up!' screen, no striped victory background. Dismissal returned
to map. Re-entering that exact raid opened a lobby without another pass; real
attacks won the fresh attempt. The same raid has exactly one pass receipt.
A separate fresh party-wipe defeat/re-lobby run is NOT_TESTED in this pass.
The earlier full-length baseline timeout/rematch is separately retained.

Cold restarts preceded final one-star, Legendary and Mega acceptance. No external
Frida server ran during final APK verification. Ordinary map/menu and bonus
screens do not carry the stripes. Exact controller/status guards exclude Rocket
and Max; Node regression exercises their contexts across 20 cycles. Fresh native
Rocket/Max battles were not replayed and remain NOT_TESTED for this repair.

## Screenshot/video evidence

Private `.private/m34/` files:

- `tier1-outcome-4.png`: native YOU WIN fading over full scanlines.
- `tier1-summary-early.png`, `tier1-summary-loaded.png`: native animation to 19-ball Summary.
- `tier1-catch.png`, `tier1-details.png`: earned persistent Pidgey acquisition.
- `tier3-summary-loaded.png`, `tier5-summary-loaded.png`: real category data.
- `tier5-transition.mp4`: uninterrupted actual Pixel transition recording.
- `tier5-video-9.5.png`: opening stripe transition; `tier5-video-10.png`:
  large white YOU WIN title; `tier5-video-11.5.png`: native reward animation.
  These are unaltered frames extracted locally from that native recording.
- `mega-current.png`: 200 Mega Energy + 500 Stardust reward animation.
- `mega-summary-loaded.png`, `mega-bonus.png`, `mega-map-return.png`.
- `timeout-result.png`, `timeout-dismissed.png`, `rematch-lobby.png`,
  `rematch-summary.png`, `rematch-map-return.png`.
- `final-gameplay-evidence.json`: battle status, HP, time, reward presence,
  owned identities, Mega balance and single-pass rematch evidence.

Fine horizontal stripes cover the Unity portrait viewport behind the white
native title and panel; OS status/navigation bars retain their normal behavior.
Animations still fade/title/reward/achievement/Summary in native order. Original
shader spacing and animated glow are retained. Exact reference-image brightness
and stripe-spacing equivalence cannot be asserted without the reference image.

## Automated verification

- Final complete Python: **777 passed, eight skipped**, 280.19 seconds.
- Full APK dex/native content preservation separately passed against the exact
  final APK and original decoded inputs. Seven skips therefore remain unresolved:
  six unavailable proprietary pinned fixtures and Windows symlink privilege.
- LiveOps/import/Studio targeted rerun: **57 passed**, including shared admin
  authorization, HTTP round trips, transaction rollback, retry/published-content
  protection, initialization deadlock and Mega-default compatibility.
- Four Node suites passed; raid-only eligibility, 20 repeated scenarios, opaque
  charcoal tint and all-corner/center shader coverage regressions included.
- Rust: **158 passed, two ignored**, zero failures. ARM64 release build passed.
- Full standalone Android build, data-preserving install and real gameplay passed.

The enlarged assembled agent exposed a Windows Node `--check` stdin hang. The
source-integration test now checks the same complete bytes from a temporary
UTF-8 file, with a 60-second subprocess timeout. Coverage was retained.

## Remaining scope and publication

M33 Timed Research, Collection Challenges and unsupported advanced counters are
still rejected. Master Ball functionality stays excluded. Shadow raid support,
fresh party-wipe defeat and fresh native Rocket/Max checks remain explicit gaps;
this is not a claim that every native battle variation is tested.

All implementation is uncommitted in the isolated integration worktree. The
primary normal service still runs its original M33 source, so importing through
its old process does not automatically gain the new endpoints. Review/integration
and a deliberate deployment are still needed; nothing was pushed or merged.
Private local overlays (`config/family.json`, both avatar/Pokemon manifests and
`data/world/admin-pois.json`) must not be staged. No proprietary payload was added.

## Preservation and final state

Checkpoint: `E:\DittoBuffer\.private\m331-m34-20261028`.
The exact original development APK was reinstalled with `-r`; SHA-256
`fd5023bc1ddab582dd191e9f90dc428f3ea1035ca783ad8dff4e4939f577f91a`.
All **62 original app files/settings matched byte-for-byte before normal launch**;
UID remained the original app UID/GID, ownership and SELinux labels were restored. Fixture
state is archived separately. Only the task-created extra location selector was
renamed to a preserved evidence name so it cannot affect normal startup.

The original normal trainer's level-9 overworld was visibly restored
(`normal-restored-screen.png`). The normal database still owns **13 Pokemon**.
Pokemon bytes, items, candy, eggs/incubators, Field/Special progress, account rows
and appearance rows match the checkpoint. XP, Stardust, name and level match;
normal startup only bumped player revision. Streaming comparison before normal
launch found 165 identical tables; six differing tables contained RPC receipts,
location freshness/coordinates or gym/defender timestamps. Counts and defender
motivation values were unchanged. Those ordinary runtime changes were preserved,
not rolled back. No claim of a byte-identical entire live database is made.

Original official app base/split APK hashes match their existing checkpoint.
Normal .gitignore, family configuration, both asset manifests, Netherlands world
manifest/database and admin POI file hashes match this milestone's checkpoint.
No original asset payload was rewritten. APK preservation tests verify original
DEX files and native libraries against the supplied decoded client inputs.

Normal game TLS **8445**, maps **8081** and admin **8080** remain running with
HTTP 200 on their actual health/settings/root endpoints. Original service PIDs
971104 and 973332 remained running. Added ADB reverse ports were removed;
original 8445/8080/8081 routes remain. All four isolated listeners
28445/28082/28083/28084 are stopped. Test Events were ended and the isolated
20-second timer configuration was restored to its earlier 300 seconds.

Primary branch, HEAD, Git status, stashes and every checkpointed M33 source file
are unchanged. Final branch is `feature/m331-json-m34-native-victory-20261028`,
HEAD `fdc55531ca013985bc8b1f30e8772b0d723a8b8b`, with uncommitted integration
changes. Nothing is staged, committed, pushed or merged. The reviewed source-only
file list is `.private/source-review-files.json` (61 files, largest 112,091 bytes).
Gitleaks 8.24.2 found no leaks; `git diff --check` passed. The private overlays and
all fixture credentials/DBs, APKs, screenshots and proprietary resources remain
outside that review list and must not be staged.

Private checkpoint receipts: `android-restoration.json`, `git-preservation.json`,
`preservation-before-restore.json`, `background-table-changes.json`,
`final-preservation.json`. The final fixture cold restart preserved every owned
Pokemon byte, all six reward receipts and 200 Mega Energy
(`final-restart-persistence.json`).
