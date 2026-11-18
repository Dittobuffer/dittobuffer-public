# M24.2 ? Native Rocket balloons

Worktree: `feature/m24-gameplay-completion-20261027`; source-only local commit,
no push. The normal server/database, original application, assets and `.gitignore`
are unchanged.

## Fix and reuse

Native balloon scheduling assumed `social_locations` even with social disabled.
It now uses recent server-observed walking/social fixes, falling back to the
recent authenticated map fix. Positioning never credits distance. Minimum level
is enforced. An active identity survives slot/configuration boundaries; completed
or expired identities cannot be reissued within their slot. Configurable
`balloon_themes` rotates supported existing teams. Radar ownership and the frozen
Leader/Giovanni definition, shields, ticket consumption, battle engine and
immutable reward receipt are reused, not replaced.

Schedules use configurable UTC period slots (default six hours) and a lifetime
from first eligible poll (default twenty minutes). These are explicit local
schedule rules. A published stop is still needed as the incident's schema anchor;
the balloon itself has no stop-distance restriction. Stale fixes do not create
balloons. Completed balloons receive the client's cooldown status on polling.

The existing reviewed runtime catalog already includes the actual balloon prefab
inside its compatibility bundle. Its historical `librenlp_grunt_*` filename does
not describe every included prefab. No asset replacement or download was needed.
At maximum map zoom the balloon was above the viewport; native double-tap/drag
zooming out revealed its correctly rendered model.

## Android evidence (Pixel 5, 15191FDD4003PY)

Isolated `.private/m24-balloon-01`, copied only from the isolated M24.1 account.
Normal trainer data was not used. Standalone M24.1 APK, separate host8448 fixture
through device8445, fixed fixture origin, native NPC level8 declared as test tuning.

- `screen-zoom.png`: native R balloon above gym.
- `screen-dialogue.png`: tapping opens Grunt and balloon dialogue.
- `screen-party.png`: native party selection; full bag warning correctly shown.
- `screen-combat-progress.png`, `screen-combat-02.png`: real combat and shield.
- `screen-combat-03.png`: fainted Venusaur, native Machamp substitution.
- `screen-result.png`: actual Heavy Slam mini-game.
- `screen-victory.png`: defeated Grunt's balloon-specific dialogue.
- `screen-reward.png`: YOU WIN native animation.
- `screen-rewards-late.png`: summary and RESCUE POKEMON.
- `screen-rescue.png`: earned Shadow Bulbasaur CP209, eight Premier Balls.

The committed server state was `won`, all three enemy HP0, one18-byte reward
receipt, XP213100 ->213600, Stardust600 ->1100. The full bag prevented a component
reward, as the native warning indicated. No synthetic victory or catch request
was submitted. Five actual ADB throws missed; the user then made a real manual throw and
caught the earned Shadow Bulbasaur. `screen-manual-catch.png` shows100XP,
100Stardust,3candy. Storage ID8 has alignment1; two Premier Balls remain.
`screen-shadow-storage.png` shows native Dex registration;
`screen-shadow-detail.png` shows the Shadow model in details.
`screen-map-departure.png` verifies that the balloon disappeared when returning
to the map without restarting. Final totals XP213700, Stardust1200, storage8.
The client displayed CP209 before capture and CP168 in details; this existing
reward-display inconsistency is recorded for follow-up, not claimed resolved.
Fresh native Leader/Super Radar balloon battles remain NOT_TESTED in this pass;
existing engine and automated ticket tests cover their backend behavior.

## Checks

`pytest tests/test_rocket_native.py tests/test_rocket.py -q`:39 passed.
Tests include disabled social, authenticated map fix without distance credit,
stale location/level gating, persistent slot rollover, independent accounts,
Radar snapshot, native combat reward replay after restart and existing Rocket
combat/shield/return behavior. `node scripts/test-client-ui.js`:all checks passed,
including Rocket research confirmation guards and HUD navigation. No Master Ball
tests or mechanics were changed.

Reproduce a fresh isolated scenario with `scripts/m24-fixture.py --scenario balloon
--output .private/m24-your-balloon`; build its generated profile using the existing
standalone builder. Source scripts never supply a battle outcome or capture.

Cold restart using the saved encrypted fixture credential returned to the native
map (`screen-restart-map.png`). Balloon polling returned InCoolDown; storage8,
one Rocket receipt, XP213700 and dust1200 remained unchanged.
