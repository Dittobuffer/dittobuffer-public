<!--
  Copyright (C) Dittobuffer Contributors
  Copyright (C) chucny
  Copyright (C) gekkedavi

  This file is part of Dittobuffer. It is distributed under the GNU General
  Public License v3.0 (see LICENSE). The copyright notices in this file must
  be preserved in all copies, forks, and derivative works. Removing or
  altering these notices violates the license and copyright law.
-->

# M24.5 - Native new-trainer onboarding

The classic 0.429.2 introduction is playable on Pixel 5 15191FDD4003PY.
This is fresh-only, opt-in server behavior; existing accounts retain their
previous tutorial flags. No normal account or primary runtime config was changed.

## Implementation and causes

Previously GetPlayer unconditionally advertised avatar, capture, name and FTUE
completion. There were no handlers for tutorial capture RPC127 or nickname RPC403.
The existing invite registration, credential encryption, sessions, avatar save,
inventory, Pokemon creation, Dex, research and journal systems were reused.

`onboarding_players` stores validated avatar-save, nickname and starter proofs,
one serialized starter receipt, and completion time. RPC127 grants exactly one
owned starter transactionally and returns the same receipt on retries/restart,
including a retry requesting another starter. The scene owns its local three
starter map objects and throwing animation. It reports the chosen Pokedex ID
after a genuine hit/catch; it carries no throw-quality telemetry. Starter balls
are native scripted tutorial supplies and do not consume ordinary bag items.
The common reward path supplies XP, Stardust and family candy. Storage capacity,
avatar readiness and supported species are checked before granting anything.

RPC403 validates 3-15 ASCII alphanumeric characters, case-insensitive uniqueness,
availability queries and one initial claim. The local account username remains
the login identity when its trainer nickname changes. Core tutorial completion
requires the corresponding durable action proof; an arbitrary MarkTutorial
request cannot grant a starter or finish onboarding.

The native client sends AVATAR_SELECTION and SetNeutralAvatar concurrently.
Rejecting the early UI callback reset the introduction once. The repaired path
acknowledges that transient UI intent while persisting avatar completion only
after a successful validated save. A cold start before that save still resumes
avatar selection. This order was reproduced and repaired on a second native
account; the first confirmation then advanced directly to the starter map.

The avatar-selection screen also depended on a missing 3,370-byte preset bundle
`neutralavatarpresets_assets_all_56a9e2ffc9ee44df5b3ba1d503da3976.bundle`.
`scripts/m24-onboarding-resources.py` restores exact preset, icon, body and hair
closures from independently supplied inputs or an explicit --download from the
public location encoded by this client. Nine bundles, 2,345,662 bytes, were
recovered into a fresh private directory. The accepted 46 contents and catalog
remain untouched. All nine portrait icons display. Only the existing supported
`n_avatarpreset_ftue_0` appearance was saved/native-verified; other looks still
require wardrobe eligibility/template validation and must not be advertised as
fully supported. This recovery does not broadly unlock clothing.

## Android acceptance evidence

Private evidence: `.private/m24-onboard-01/`, retained locally, not committed.

| Actual action | Observation / persistent result | Screenshot |
|---|---|---|
| Invited registration through standalone FamilyActivity | Database initially had zero players/Pokemon; one fresh account created through the real registration endpoint | declaration.json / trace |
| Willow introduction | Native animated Willow and original built-in introduction | screen-willow-intro.png |
| Avatar selection | Native portraits, selected supported style, RPC408 saved appearance | screen-style-selected.png |
| Starter map | Bulbasaur, Charmander and Squirtle simultaneously rendered near the avatar | screen-starters-visible.png |
| Squirtle tap and throw | Native capture scene; real ADB swipes, successful native capture, RPC127 species7 | screen-starter-throw-ready.png |
| Catch and Dex | 100 XP, 100 Stardust, 3 candy; persistent Pokemon ID1, CP12, level1 | screen-starter-caught.png / screen-starter-registered.png |
| Nickname | Native entry, confirmation and successful RPC403; name M24Journey | screen-nickname-confirm.png |
| First stop | Native map/Photo Disc swipe guidance; actual spin, 50 XP, three balls, egg and research progress | screen-intro-stop-details.png / screen-intro-spin.png |
| Bag | 65/350 items, including 53 balls, 5 berries, 5 potions, 2 revives | screen-new-account-bag.png |
| Completion | Core proof complete, FTUE7 and stop-intro8 persisted; XP150, dust100, one Pokemon | trace/events.jsonl |
| Cold restart | Encrypted saved login, direct normal map, no repeated intro; Squirtle CP12 and one egg remain | screen-cold-restart-map.png / screen-cold-restart-storage.png |
| Avatar callback repair | Second isolated account, first selection, early406 successful and408 successful, direct starter map | screen-avatar-race-fixed.png |

Research assignment/progress uses the existing system; a native progress toast
was observed. A new level1 account's full Research interface/introduction was
not demonstrated. No artificial level grant or physical walking was used.
The hidden Pikachu starter path and modern FTUE V2 were not native-tested.
One overnight attempt had an expired login and showed a native error; renewing
its existing saved session recovered normally, without editing tutorial state.

## Reproduction and activation

1. Restore the established resources per docs/ASSETS.md. Keep payloads private.
2. Run `python scripts/m24-fixture.py --scenario onboarding --output .private/m24-your-intro`.
   It creates a private invitation/config and ZERO trainers or owned Pokemon.
3. Restore resources with `python scripts/m24-onboarding-resources.py --cache YOUR_AUTHORIZED_CACHE --output .private/m24-your-intro/avatar-resources`.
   Explicit `--download` is optional. Point only the isolated config's
   avatar_assets.manifest to that output's manifest.json.
4. Use the existing standalone build/profile system with this fixture's CA and
   exact profile identity. For USB testing, use device origin8445 and explicit
   `adb -s 15191FDD4003PY reverse tcp:8445 tcp:8448` for a host8448 fixture.
   Do not reuse an APK pinned to a different fixture CA.
5. Enter generated private invitation credentials in Create invited account.
   Keep the normal trainer database separate. Enable onboarding.example.json
   only for deliberately new accounts; it does not retrofit existing trainers.

Source changes: gameplay/onboarding.py, game.py, M24 fixture/trace tools,
resource restoration script, configuration example and focused tests. A small
eligibility guard in the newly added reward helper limits M24 bonuses to the
in-scope standard/Premier ball IDs; excluded ball mechanics were not changed or
tested. No Java/Rust implementation changes were needed for this milestone.

Seven focused onboarding tests cover legacy preservation, action proofs,
concurrent starter claims, replay/restart, nickname validation/ownership,
completion guards and capacity. Combined onboarding/standalone checks:11 pass,
including the actual assembled APK's original dex/native-content preservation.
See MILESTONE_24.md for the complete cross-milestone test and preservation audit.
