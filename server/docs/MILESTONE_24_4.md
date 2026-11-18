# M24.4 ? Global raid expiry and Gym recovery

## Defects and changes

The scheduler could return an expired stored row when configuration-derived
lifetimes differed. The fixed development seed's INSERT OR IGNORE could likewise
return its old expired instance. Both paths now honor persisted start/end times.
Development windows do not silently resurrect; an operator may explicitly choose
a new bounded `dev_raid.instance`. Existing default seeds remain compatible.
Ordinary configured rotations continue through the existing scheduler.

A raid's removal now contributes its persisted end timestamp to the outgoing
Gym revision. This is newer than its egg/active-boss revision, including empty
Gyms, and survives process restarts. The existing raids(fort_id,end_ms) index
supports the lookup. Ended rows, reward receipts, private earned catches and
history are retained. No defender/team reset or public raid removal occurs when
only one trainer wins. Nearby cells carry the updated Gym normally; no client
restart, visual cover or new client hook is required.

The existing FortSearch handler rejected all Gym Photo Discs. It now shares the
validated range/capacity/cooldown/inventory path for Gym and Pokestop discs. Gym
base XP is the configurable `gyms.spin_xp` (local default25); current badge data
is projected. Gym spins do not issue Pokestop-only Field Research. Existing
Pokestop rewards and optional daily spin bonuses remain supported.

## Actual Pixel5 acceptance

Isolated `.private/m24-expiry-01`, fixed test location, M24.1 account copy only.
A second isolated Valor trainer owned a newly created full-health level10
Squirtle, deployed through the existing authoritative deployment function as a
fixture. Deployment itself was not a fresh native test. Mystic test trainer and
normal LibreTrainer/Netherlands data remained separate.

- `screen-egg.png`: actual pink egg,0:36 to hatch over the Valor Gym.
- `screen-active.png`: actual Pidgey boss,1:33 remaining.
- Only the isolated server was restarted during the egg phase. The native app
  stayed open, reconnected and displayed the same persisted raid.
- `native-lifecycle.jsonl` and lifecycle-frame00..12 record Android process10025
  continuously across expiry. No synthetic location/time/expiry RPC was sent.
- `lifecycle-frame-12.png`: Pidgey and countdown gone, original Squirtle defender
  visible again over the red Gym,18seconds after the natural deadline.
- Actual outgoing map responses show raid_seed/raid_end null, team2, guard7,
  advanced revision and24 neighboring cells.
- `screen-gym-recovered.png`: tapping the recovered Gym opens the native defender
  view, SquirtleCP246; owner/team and deployment were retained.
- `screen-gym-disc.png` / `screen-gym-spin.png`: Photo Disc opens and spins,
  turns purple. Native FortSearch returned success,25XP,2Pokeballs and1Nanab
  Berry, persisted cooldown and inventory revision. Badge/history remained valid.
- `screen-gym-party.png`: ordinary opposing-Gym party selection.
- `screen-ordinary-combat.png`, `screen-ordinary-damage.png`,
  `screen-ordinary-result.png`: real native battle requests; server HP112->28->0,
  state2 victory. Native motivation animation reduced displayed CP245->162.
  This verifies one ordinary battle round after expiry, not a new full takeover.

`native-recovery-state.json` records the retained team/defender before that
intentional ordinary battle changed motivation. Source normal server8445 and
admin8080 were never stopped. The standalone compatible APK and resources are
unchanged; no proprietary payload, database or screenshot is staged.

## Checks and reproduction

Four focused lifecycle tests cover egg/active/expiry, opposing battle admission,
Photo Disc/cooldown, empty-Gym monotonic revisions and restart, development-seed
expiry/new explicit instance, and individual-victory/global-raid separation.
Combined lifecycle, raid, Gym and daily-reward suite:53 passed.
Node raid routing/GC regression:21 connections passed. Source modifications: raids.py,
gyms.py, game.py, test_raid_lifecycle.py and M24 fixture/trace tools.

`scripts/m24-fixture.py --scenario expiry --output .private/m24-your-expiry`
creates the reproducible isolated scenario with a60second egg and120second active
window, plus an explicitly provisioned opposing defender. Authenticate with the
private generated account. Do not use normal player data for this fixture.

No commit is pushed. Final full-suite/native-build and original-device
restoration occur after M24.5.
