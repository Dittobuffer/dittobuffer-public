<!--
  Copyright (C) Dittobuffer Contributors
  Copyright (C) chucny
  Copyright (C) gekkedavi

  This file is part of Dittobuffer. It is distributed under the GNU General
  Public License v3.0 (see LICENSE). The copyright notices in this file must
  be preserved in all copies, forks, and derivative works. Removing or
  altering these notices violates the license and copyright law.
-->

# M33.1 — Universal LiveOps JSON import/export

> Historical acceptance record: branch/status statements below describe that verification session. The later published source is reconciled with current main in [the 3 November 2026 gameplay integration](GAMEPLAY_MAIN_INTEGRATION.md). Existing partial/native acceptance limits remain in force.

Implemented on `feature/m331-json-m34-native-victory-20261028` in the isolated
worktree `E:\DittoBuffer\.private\worktrees\m331-m34-20261028`.
Primary dirty M33 checkout remains on `feature/m33-seasons-events`, HEAD
`fdc55531ca013985bc8b1f30e8772b0d723a8b8b`. Latest fetched `origin/main` was
`22e45651f23e7e7133bc8290d882eb5b09a4840f`; it is an ancestor of that baseline.
No automatic merge, commit or push was performed.

## Implementation

The versioned `dittobuffer.liveops.bundle` format serializes the existing M33
editorial documents. The packaged JSON Schema rejects unknown declarative
fields; actual registry/gameplay validation checks model/shiny/form availability,
raid/Mega/released-Max capabilities, tasks, rewards, dates and geometry. Names
resolve only to unambiguous registry identities. The parser rejects duplicate
JSON keys, nonfinite values, oversized/deep documents and arbitrary file/code
fields. Validation reports bounded aggregate errors with JSON paths.

Studio accepts files, drag/drop and pasted JSON. Preview is read-only and shows
content, weekly pools/probabilities, raid bosses, shiny rates, Research rewards,
regions, dependencies, conflicts and warnings. Explicit **Import as Draft** is
separate from existing publication confirmation. Imported content uses normal
visual forms plus an advanced declarative-field editor.

One SQLite transaction saves all documents/regions and an import receipt with a
canonical fingerprint. Identical retries replay the receipt across restarts.
Existing IDs require comparison and explicit draft update or a separate copy.
Copy remaps dependencies consistently; updates require reviewed revisions and
cannot overwrite any published content. No player database is part of import.
Scoped Season/Event/Research, selected-bundle and template/generator exports use
the same schema and preserve semantic ordering/dependencies.

The loopback administrator website is open: no shared capability, cookie,
access key or Studio password exists. `scripts/open-admin.py` opens it
directly, and Studio uses the same open session. Data-changing actions still
require their documented confirmation. Native map/postcard resource routes
remain separate. Browser testing exposed a lock re-entry deadlock during first
Studio initialization; this was repaired and a real HTTP regression was added.

See [schema/operator documentation](LIVEOPS_JSON_SCHEMA.md),
[generator prompt](CHATGPT_SEASON_GENERATION.md), the packaged schema and all
seven original examples in `config/liveops-examples/`.

## Actual Chrome verification

Fresh Chrome profile, 1440×1100, real admin HTTP server at isolated port 28082:
pasted complete original four-week JSON → Preview → Import as Draft → opened
Week 1 → changed raid from Pidgey to Pikachu → saved → reviewed publication →
confirmed. No JavaScript page errors. Later exported the edited Season and
re-imported through a fresh independent HTTP/content service at port 28084.
Actual JSON file upload and drag/drop both previewed successfully. Cancel left every saved ID, revision and status unchanged (`file-drop-cancel.json`).
All four weeks matched effective spawns, raid pools, Research, bonuses, eggs and
released Max rules. No claim of new native Max/egg/Rocket coverage is made.

Private screenshots/evidence in this worktree's `.private/acceptance/`:
`studio-import-preview.png`, `studio-imported-draft.png`,
`studio-edited-raid.png`, `studio-publication-review.png`,
`studio-published-season.png`, `http-fresh-roundtrip.json`.
The Windows computer-use kernel failed to initialize (os error 3); verification
used the existing Playwright package and installed Chrome with fresh profiles.

## Actual Pixel 5 acceptance

Only serial `PIXEL_TEST_DEVICE` was used; Samsung fresh verification is NOT_TESTED.
A complete isolated APK was built and installed with `adb -s … install -r`.
A new private account was created, never a copy of the normal trainer.
Public PokéStop/gym coordinates were packaged as explicit stationary fixtures;
no physical walking or Adventure Sync success is claimed. The fixture's initial
level rewards filled its bag; only its account capacity was increased to permit
further spins. Saved launcher origin initially referred to the normal server;
correcting the isolated saved origin restored scoped CA trust, without bypassing
TLS validation.

Observed:

- Imported Week 1 visibly rendered Bulbasaur, Charmander and Squirtle.
- Edited Pikachu raid visibly appeared at the public Enigma gym.
- Real Herman Berkien PokéStop spin awarded the imported Catch one Pokémon task.
- Real touch throws: two misses, then a Great Throw caught Bulbasaur. Native
  reward panel showed 2,700 XP including the preserved 1,000 XP new-dex bonus.
- Native Today tab displayed the completed imported Field task. Touch claim
  removed it, displayed a stamp, and granted **exactly 300 XP + five Poké Balls**.
- Imported Special Research also appeared, with real catch/spin progress.
- Week 2 clock selected Pidgey/Rattata/Pikachu; they visibly appeared after a cold
  app and service restart. The raid changed visibly from Pikachu to Slowpoke.
- Exact owned Pokémon bytes/ID, XP, Stardust, captures, throws, stops, completed
  Field task (`progress=1, claimed=1`) and stamp count survived the restart.
- Season and child Events were explicitly ended; read-only resolution returned
  ordinary defaults; `native-defaults-return.png` visibly shows ordinary mixed spawns and the default Pidgey raid.

Key native files: `native-map-first.png`, `native-season-encounter.png`,
`native-real-throw3.png`, `native-catch-earned.png`, `native-detail-clear.png`,
`native-field-complete.png`, `native-field-claimed.png`,
`native-gym-visible.png` (Pikachu), `native-week2-loaded.png`,
`native-week2-raid-visible.png` (Slowpoke),
`persistent-before-restart.json`, `persistent-after-restart.json`.

## Automated results and limits

Final full Python suite: **777 passed, eight skipped**. Six skips are absent pinned proprietary fixtures, one is Windows symlink privilege, and one expects a default APK output path. The latter was separately run against the exact final APK and **passed**. Final LiveOps/import/Studio suite: **57 passed**. All four existing Node suites
passed. Rust: **158 passed, two ignored**; ARM64 release compilation succeeded.
Complete APK builds succeeded; exact hashes are in private build receipts. A Windows Node stdin syntax-check hang was repaired by checking the exact assembled source through a temporary file with a bounded child process.
Master Ball functionality remains excluded.

Timed Research, native Collection Challenges, unavailable task counters,
unsupported event mechanics and Shadow raid publication remain rejected and
honestly unavailable. The GUI's advanced JSON editor exposes fields beyond the
existing visual forms; it does not invent unsupported adapters. Numeric raid
balance is explicitly custom. Overlap warnings report possible equal-priority
intersections; they do not claim polygon-intersection proof. At most 200 errors
are returned per review.

Preservation checkpoint is `E:\DittoBuffer\.private\m331-m34-20261028/`:
Git/index patches, untracked M33 source, branches/stashes, private config copies,
SQLite snapshots, installed APK and app-private archive/permissions. `.gitignore`
is byte-identical. Normal services and databases were not used for acceptance.
No proprietary assets/APKs, screenshots, credentials or private databases are
intended for staging. Final Android restoration and preservation checks are
shared with the M34 report.

Mega import acceptance exposed an existing validation mismatch: the gameplay engine enables Mega Evolution when its optional configuration is omitted, while the Studio validator required an explicit flag. Validation now uses the engine default, still rejects explicit disable and invalid branches, and has a regression covering all three cases. Mega Venusaur was then published and defeated natively; see M34.

Final restoration: original APK and all 62 original private app files restored; normal trainer map and 13 owned Pokemon verified. Normal source/Git state and protected world/config/manifest hashes are unchanged. Full evidence and exact limitations are in [M34](MILESTONE_34_RAID_VICTORY_BACKGROUND.md). The new import endpoints remain in this isolated integration worktree pending review/deployment.
