<!--
  Copyright (C) Dittobuffer Contributors
  Copyright (C) chucny
  Copyright (C) gekkedavi

  This file is part of Dittobuffer. It is distributed under the GNU General
  Public License v3.0 (see LICENSE). The copyright notices in this file must
  be preserved in all copies, forks, and derivative works. Removing or
  altering these notices violates the license and copyright law.
-->

# M24.3 ? Persistent catch/spin calendars

## Implementation

`reward_calendar.py` adds account-scoped transactional daily receipts, streaks,
per-encounter throw counts and separate validated-distance weekly rewards.
Existing catches, inventories and RPC receipts remain authoritative. A repeated
successful catch returns its saved result before touching a bonus or consuming
another ball. Full bags and cooldown failures do not consume daily spin bonuses.
Streaks use IANA Europe/Amsterdam calendar dates, reset after day7 or a missed day,
and resist clock rollback. Existing player/collection rows are not replaced.

Optional `reward_bonuses` enables daily XP/dust, seventh-day rewards, first throw,
Nice/Great/Excellent and Curveball XP, bounded event factors, weather dust and
species bonuses. Defaults preserve existing runtimes until explicitly enabled.
The example contains the selected local policy and source/provenance. Day7 uses
`seventh_xp` as the total daily XP bonus (configurable6000), replacing daily1500;
`seventh_dust` is total bonus3000, replacing600. Spin extra item counts are
explicit custom local tuning, not a recovered official random item table.
Existing base catch/spin XP configuration remains honored.

Throw grading uses the existing served ENCOUNTER_SETTINGS modifiers1.3/1.7/1.85
and spin threshold0.5. These are increasing native modifiers, not ring radii.
The supplied PokemonEncounterAttributes bonus Candy/dust fields10/11 are parsed
per form; operators may override species reward amounts. Weather is tagged from
server weather/type context, not a client-supplied claim. Mega bonus projection,
ball inventory/entitlements, capture probability and storage are retained.

Native dump confirms PokemonDisplay field5 uses
GameplayWeatherProto.WeatherCondition, not an empty message. The protocol and
Python generation were corrected. Descriptor comparison proved this was the
only semantic generated-protocol change; other generated offsets shifted.
No proprietary resources were added or changed.

## Weekly scope and limitations

Weekly distance is counted only after the existing walking sampler accepts an
actual device fix. Duplicate/stale/implausible/debug fixes still earn nothing.
Monday09:00 local boundaries are calculated separately from daily streaks;
claims require level5, the previous eligible week, capacity and an unclaimed
receipt. Missed weeks do not stack. Table entries are total rewards for the
highest eligible tier. The example's deterministic table is explicitly custom;
it does not claim the official random egg/dust distribution.

GET_FITNESS_REPORT now projects weekly meters and HoloFitnessReport game data;
GET_FITNESS_REWARDS grants atomically once. Optional native background-mode
threshold templates have a distinct GM cache epoch. Native startup actually
requested GET_FITNESS_REPORT and received a successful response. A full native
weekly reward dialog with earned distance is NOT_TESTED. External Google Fit/
Health ingestion, sample provenance and native week-bucket notification behavior
remain integration work; no external account login or synthetic walking claim
was used. Physical walking remains NOT_TESTED per the user's instruction.

## Android evidence

Pixel5 serial15191FDD4003PY, existing standalone APK, isolated
`.private/m24-rewards-01`, separate SQLite and host8448. The copied source was the
isolated M24.1 trainer only. Fixture bag capacity5000 and cooldown1second are
explicit test settings; normal configuration and normal server8445 are unchanged.
Only the M24 runner's private `--calendar-file` changes reward dates. Authentication,
battle, real spawn expiry and the device clock remain real. New lab-day wild
identities are generated only in this guarded private runner.

- `screen-first-catch-summary.png`: actual Pokeball throw/catch;100XP base,
  First Throw50, First Catch of the Day1500; total1650XP,700dust,3candy.
- `screen-first-spin.png`: real spinning disc, reward bubbles and an egg.
- `screen-spin-day-20.png` through `screen-spin-day-25.png`: six further actual
  native spins after advancing the server reward calendar. SQLite contains seven
  separate earned spin dates, Oct19..25, streak1..7. Seventh spin6050XP
  (configured base50 +6000), bonus15balls plus normal world rewards.
- For the seventh-catch UI test only, middle-date catch counters Oct20..24 were
  provisioned as an isolated fixture (no Pokemon/XP/dust awarded). The actual
  Oct25 capture then advanced6->7. This is not seven days of native catches.
- `screen-seventh-summary.png`: native Great Throw100, First Throw50,
  Streak Bonus6000, base100; total6250XP,3100dust,3candy.
- `screen-restart-map.png`: cold restart with saved encrypted account credential;
  native trainer level23, same XP236350/dust4400, storage9, both streaks7.
  Eggs awarded by native spins are separate from owned Pokemon.

The native XP rows agree with the authoritative database. Weather/event/Candy
variants and Excellent/Curve grading were validated in automated tests, not each
freshly demonstrated on Android. A distinct seven-day circular popup was not
captured; the native catch breakdown and stop reward bubbles were observed.
The baseline legacy balloon overlay still polls when native Rocket is disabled;
this separate compatibility path was retained, not treated as a reward test.

## Tests and preservation

121 Python checks passed across test_reward_calendar, test_game, test_eggs,
test_raids, test_family_raids and test_rocket_native; one Master-related test was
explicitly deselected. Focused tests cover DST spring/fall, midnight, missed days,
7->1, clock rollback, native GM throw thresholds, missed first throws, retries,
restart, species/weather/event factors, full bags/cooldown and separate weekly
eligibility/exactly-once claims. Master Ball mechanics were not changed or tested.

All work is isolated on the M24 feature branch, local commit only. No normal
profile, personal database, Netherlands world, APK/resource cache or `.gitignore`
was modified. Full suite/build and final device restoration follow M24.5.

After restarting only the isolated server, another real spin on the same reward
date returned base50XP and normal items, with no second daily/seventh bonus
(`screen-ordinary-repeat-spin.png`). Totals became XP236400/dust4400. The daily
quest bucket now reflects its last actual claim date; unrelated ten-stop chain
metadata is left unset. Focused7 tests and all Node UI guards passed again.

## References and reproducibility

[GO Beyond XP update](https://pokemongo.com/news/gobeyond-level40) supplies the
modern XP categories/amounts. [Daily bonus help](https://niantic.helpshift.com/hc/en/6-pokemon-go/faq/105-daily-bonuses/)
establishes local midnight, streak reset and dust; its older XP table predates
that update. [Adventure Sync help](https://niantic.helpshift.com/hc/en/6-pokemon-go/faq/3265-adventure-sync/)
establishes optional fitness reports/rewards, distinct from streaks.

Create an independent scenario with `scripts/m24-fixture.py --scenario rewards
--output .private/m24-your-rewards`. Merge reviewed config examples into its
private config, create its private lab-calendar JSON with `unix_ms`, and pass it
to `scripts/m24-server.py --calendar-file`. Never use that runner for normal data.
