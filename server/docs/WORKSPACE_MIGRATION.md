# Safe primary workspace migration - 2026-10-27

Primary development folder: **`E:\DittoBuffer`**.
Source base: latest GitHub main **`bd2ab84010b8a47cfd72d02d2f0cb275e4817e91`**.
Review branch: **`integration/workspace-migration-20261027`**.
Remote: `https://github.com/Dittobuffer/dittobuffer.git`.

The new checkout retains every path from main and adds intentional source,
configuration metadata, tests and documentation in the canonical layout.
There is no top-level `server/` or duplicate `client/librenlp/` architecture.
No old checkout was switched, reset, cleaned, stashed, overwritten or committed.
The previous workspace instructions were superseded only in the new AGENTS.md.

## Sources and preservation

| Source | Starting branch / commit | Treatment |
|---|---|---|
| Original E:\PokemonGO-libreNPL | feature/catchbganimations / 09460bf187547472a7cbb0952abdb000d786ae57 | Read only; 652 tracked files, original staged changes, branches and stash preserved |
| Integrated builds/structure-audit-main-20261026 | integration/routes-new-layout / c4bb197c4cb779053c90387d3f2fd6938c7767eb | 392 tracked and 88 untracked source files audited; all working changes reused |
| Recovered native Rocket | feature/dynamaxfeature 59635c9; stash 4537d7ffbd700bd4092674b5176e813d395bfe18 | Extracted source without popping stash or changing its refs |
| New E:\DittoBuffer | origin/main bd2ab840 | Dedicated integration branch; main remains separate |

Private `.private/migration-20261027/` records source hashes, complete refs,
statuses, index bytes, staged/unstaged binary patches and all-ref Git bundles
for both old repositories. Old branches/stashes are preserved there and in the
original repositories; old flat-layout history is not merged into this branch.
Historical docs/scripts and private evidence not active in the new tree remain
in their original locations and preservation bundles. They are not deleted.
Seven hundred and four generated/untracked non-source integration outputs were
not copied into source control. The old proprietary M23 scratch folders stay
untouched in that old workspace.

A three-way review was required only for README and pyproject metadata changed
upstream after c4bb197. Main's 0.429.2 description and existing LICENSE were
retained; inaccurate generic GPL labels were made consistent with its GPLv3
text. Integrated dependency/setup fixes were retained. No incoming gameplay
module was overwritten with a flat-tree counterpart.

## Feature-preservation matrix

| Subsystem | Source | Destination | Validation boundary |
|---|---|---|---|
| Accounts and authentication | latest main + integrated family/security fixes | src/dittobuffer/core/accounts.py;core/family_network.py;gameplay/friends.py | family account and HTTP tests |
| SQLite and persistent ownership | latest main + M23 additive table | src/dittobuffer/core/database.py | transactions, replay, restart and FK tests |
| Catching and inventory | main + integrated + M23 scoped capture/berry fixes | src/dittobuffer/gameplay/game.py;items.py | ordinary/Research/raid/Max/Rocket isolation tests |
| Species, forms, shiny and model registrations | main reference + integrated permitted manifests | src/dittobuffer/gameplay/species.py;config/resources/pokemon*.json | accepted pins/closure restored; no fresh 958-species Android retest |
| Storage, evolution, power-up and moves | main + recovered native Shadow hooks | src/dittobuffer/core/management.py;gameplay/pokemon_moves.py | management, evolution, XL, TM and Shadow tests |
| Avatar, wardrobe, medals, buddy, journal | latest main retained | src/dittobuffer/gameplay/avatar.py;wardrobe.py;medals.py;buddy*.py;trainer.py | existing regression suites |
| Pokestops, items and Shop | main + integrated safety hooks | src/dittobuffer/gameplay/game.py;items.py;shop.py | inventory/reward/economy tests |
| Gyms and ordinary raids | latest main retained | src/dittobuffer/gameplay/gyms.py;gym_battle.py;raids.py;raid_transport.py | gym/raid/battle regression suites |
| Family cooperative raids | latest main retained | src/dittobuffer/gameplay/cooperative.py | independent ownership, retries and rewards tests |
| Mega systems | latest main retained | src/dittobuffer/gameplay/mega.py | existing evolution/raid tests |
| Eggs, incubators and walking | latest main retained | src/dittobuffer/gameplay/eggs.py;walking.py | persistent distance/hatched reward tests; no physical walk |
| Field and Special Research | main + integrated + M23 + Rocket event types | src/dittobuffer/gameplay/research.py;stories.py | owned claims/progress/replay tests |
| Mythical native cinematics | integrated M23 worktree | src/dittobuffer/gameplay/special_encounters.py;.json;client/inspect-special-research.js | 12 registry IDs; previous Pixel evidence remains historical |
| Rocket native combat and shields | feature/dynamaxfeature 59635c9 / saved stash 4537d7f | src/dittobuffer/gameplay/rocket_native.py;rocket_battle.py | 42 recovered transaction/defeat/resource tests; actual hit/AI state, not instant-win |
| Rocket Radars, leaders, Giovanni and balloons | same recovered branch/stash | src/dittobuffer/gameplay/rocket_progression.py;rocket-*.json | tickets, ownership, private balloon identities and rewards tests |
| Shadow purification and Frustration | same recovered branch/stash + original M17.2 tests | src/dittobuffer/gameplay/rocket_shadow.py;core/management.py;gameplay/pokedex.py | exactly-once purification, categories and gated TMs |
| Rocket defeat-resource repair | original source + saved composer + M23 accepted runtime | src/dittobuffer/gameplay/rocket_resources.py;scripts/m172-rocket-defeat-resources.py | three exact native coach closures verified locally |
| Legacy main balloon interface | latest main retained separately | src/dittobuffer/gameplay/rocket.py | original tests retained; no schema replacement |
| Dynamax/Gigantamax and Max Ball | main + integrated M18.2 | src/dittobuffer/gameplay/max_*.py;client/local-max*.js | M18.2 RPC/ownership + Node Max Ball regressions |
| PvP, GBL and friend battles | latest main retained | src/dittobuffer/gameplay/pvp*.py;client/local-pvp.js | PvP and challenge suites; native Rocket coexists through ID routing |
| Routes, creator and publication | integrated M20/M20.1/M20.2 port | src/dittobuffer/gameplay/route*.py;client/local-routes.js | graph/creator/rewards/protocol tests |
| Mateo, saved Postcards and Vivillon | integrated M20.2 port | src/dittobuffer/gameplay/route_mateo.py;route_npc_resources.py;world_postcards.py;vivillon.py | NPC/Collector/persistence + prior overlay preservation tests |
| Friends, gifts, trading and social | main + integrated fixes | src/dittobuffer/gameplay/friends.py;gifts.py;trading.py;client/local-social.js | existing social and account-isolation tests |
| Netherlands, roads and world geometry | main world engine + integrated source + private published snapshot | src/dittobuffer/world/;client/local-world.js;local-maps.js | world/HTTP/Node tests; published 7 GB snapshot copied privately |
| Android standalone launcher | integrated + recovered Rocket adapter | client/standalone/org/dittobuffer/launcher/;scripts/build-standalone.py | Java/D8/APK build and actual artifact byte checks |
| Rust transport and native ABI | latest main source retained byte-for-byte | client/native/ | 158 host tests; pinned ARM64 release build; required exports/SONAME |
| Protocols | integrated merged main + audited Routes codecs | protocol/game.proto;protocol/game_pb2.py | native Station field 17 / Routes field 20 retained; 47 recovered Rocket message contracts match |
| Resource restoration and model tools | integrated + four recovered original model helpers | scripts/restore-local-resources.py;convert-pokemon-fbx.py;retarget-*.py;convert-source-palette.py | 3073 restoration pins; synthetic motion/palette tests; proprietary inputs stay ignored |
| Server/admin/CLI/build/CI | main + integrated source + relocation fixes | src/dittobuffer/runtime/;admin/;cli/;scripts/;.github/workflows/ci.yml | isolated services, setup, patcher, Python/Node/build validation |

## Important integration repairs

Main's balloon interface did not include the authoritative Rocket battle module
referenced by earlier Pixel reports. That missing work was recovered rather than
silently treating an encounter-only balloon as native combat. Native Rocket is
explicitly selected by `rocket.enabled=true` and `rocket.server_combat=true`;
`src/dittobuffer/gameplay/rocket.example.json` is disabled by default and points
at an independently supplied ignored reference. Its private balloon table is
`rocket_native_balloons`, preserving main's existing `rocket_balloons` schema.
Owned native reward records participate in the existing exactly-once catch
transaction. PvP still handles pvp- IDs first. A combined Rocket/Routes/Max/PvP
regression exercises native dispatch and prevents party mutations during battle.
Shadow costs patch only the relevant protobuf field and preserve unknown data.

Relocation also repaired a Max reference test that indexed ROOT.parents[1],
package-qualified M23 test imports, remaining active server/ palette/photo paths,
workstation-specific ADB defaults, and required offline model-tool dependencies.
ADB helpers accept an explicit --adb or ADB/PATH and always include -s SERIAL.
Shell scripts have LF endings in the new tree; all ignore files retain exact
original bytes. Decoded APK inputs use ignored .staging/client-inputs with a
legacy relative fallback; the standalone builder accepts --base-tree/--split-tree.

## Local environment and data

- Independent **`.venv`**, Python 3.13, editable package, pinned requirements and
  development dependencies plus asset/world tools. `pip check` passed. VS Code's
  ignored settings select this interpreter and this workspace.
- Independent ignored `.tools` copies of the pinned Rust 1.99.1, protoc, NDK r27d
  and Android build inputs. No toolchain is a link back into the old workspace.
- **3,072** original portable asset files restored (2,034,298,736 bytes), plus
  the native Rocket reference. Only metadata is public. M23 v6's private runtime
  was relocated with **388** registry entries reverified and **113** extra files
  copied. Its catalog bytes/hash and model registrations are unchanged. All three
  native defeat-coach resource closures passed the read-only validator.
- Published Netherlands world copied to the existing ignored path
  `data/world/netherlands-261020-v2.sqlite3`: **7,056,125,952 bytes**, verified
  against the publication hash. Existing public publication pointers are retained.
- Stable normal-player snapshot copied privately to
  `.private/preserved-runtime/game.sqlite3`: **1,242,374,144 bytes**, source/dest
  SHA-256 verified. Source size/mtime were stable and no nonempty WAL was ignored.
  The copy is not automatically selected, opened for gameplay or uploaded.
  Original normal/family databases and all credentials remain untouched.
- Existing account/TLS provisioning is unchanged. Default `config/family.json`
  retains main's fresh development database; do not mistake a new profile for
  lost old progression. Select a reviewed private configuration to use a copy
  of existing state; never point new tests at an original database.

Operator restoration remains explicit:

```powershell
.\.venv\Scripts\python.exe scripts/restore-local-resources.py --source D:/AuthorizedInputs --all
.\.venv\Scripts\python.exe scripts/restore-local-resources.py --source D:/AuthorizedInputs --all --apply
```

The `rocket` restoration group pins the independent Rocket reference. See
[ASSETS.md](ASSETS.md), the original compatibility records and
[M23 evidence/limitations](MILESTONE_23_MYTHICAL_CINEMATICS.md). Resources, modified
APKs, local TLS/signing material, caches and private accounts remain ignored.
Incoming main already tracks some third-party inputs and shared test key
material; this branch neither adds more of those nor establishes redistribution
rights. Keep independently supplied private inputs out of every Git add command.

## Validation

| Check | Result |
|---|---|
| Final full Python run | **678 passed, 7 skipped**, 263.71 seconds; includes real rebuilt APK preservation |
| Relocation/build-focused checks | **36 passed, 6 optional fixtures skipped** |
| Four Node suites | **Passed**: UI, raids, family, world |
| Rust locked host tests | **158 passed, 2 ignored**, x86_64 Linux under WSL |
| Fresh Android ARM64 native release | **Built**, Rust 1.99.1 / NDK r27d; required exports and SONAME verified |
| Standalone 0.429.2 APK | **Built, signed and verified**, Java/D8/apktool; original dex and all input split libraries checked |
| Portable resources / Rocket coaches | **Hash and closure checks passed** |
| Fresh Android installation/gameplay | **NOT_TESTED for this workspace migration**; no phone/app/cache changed |

Seven Python skips: Windows cannot create the symlink fixture, plus six absent
optional legacy palette/prop/effect fixtures. Synthetic palette/motion tests now
run; the NumPy/helper omission was repaired. Two Rust ignores require an external
rehearsal server. No test was removed. Existing tests/legacy remains explicitly
excluded according to main's established collection policy. Windows Python and
WSL Rust are tested; a full Linux Python runtime pass is not claimed.

Private APK: `.staging/migration-standalone-20261027/dittobuffer-family.apk`,
**201,176,002 bytes**, SHA-256
`633d3ea211a172617a346fdeb3161a96da26fe759002e27fe2a44f478df7693a`.
The APK includes the newly compiled native plugin and migrated adapters. Its
profile uses isolated M23 data/CA and contains no account credentials. The build
is not installed on either phone and must not be committed or redistributed.
M23's audio/live-AR/Celebi-teleport/claimed-button limitations remain recorded;
a source migration does not turn earlier partial acceptance into new verification.

Private evidence: final-python.log, node-*.log, rust-tests.log,
android-native-build.log, native-abi.log, standalone-build.log,
resource-restoration.log, local-resource-migration.json, private-state-copies.json,
source-migration-ledger.json and preservation/publication receipts in the migration
folder. Two newly generated test-remnant directories were hash-verified and
archived there; no old data was moved or deleted.

## Publication review

The explicit source review list contains no new APK, bundle, key, database,
cache or log payload. Gitleaks reviewed the proposed source content with values
redacted. Its one generic-api-key finding was the fixed synthetic peer label
`m23-isolated-NOT-EARNED` on the fixture registration line, not a credential.
The password is generated randomly and stored only using CurrentUser DPAPI.
Manual review confirmed no secret literal at that finding; no scanner suppression
or ignore rule was added. Sizes and actual staged blobs are checked again before
commit, and the commit range is scanned before push.

## Open VS Code and run checks

```powershell
code E:\DittoBuffer
Set-Location E:\DittoBuffer
.\.venv\Scripts\python.exe -m dittobuffer --version
.\.venv\Scripts\python.exe scripts/setup-checkout.py --check
.\scripts\test.ps1
```

For the real APK fixture check, set the private artifact paths before test.ps1:

```powershell
$env:DITTOBUFFER_TEST_APK='.staging/migration-standalone-20261027/dittobuffer-family.apk'
$env:DITTOBUFFER_TEST_BASE='.staging/client-inputs/base'
$env:DITTOBUFFER_TEST_SPLIT='.staging/client-inputs/split'
```

On a new clone create a fresh venv and install with
`python -m pip install -e '.[assets,world]' -r requirements-dev.txt`, then restore
only independently authorized resources. Existing LFS inputs need `git lfs pull`;
the Power Spot index is checked by setup-checkout. Native tests/builds use the
copied local toolchain through scripts/test-native.sh and scripts/build-native.sh
under WSL/Linux; setup-native.sh supplies the same pinned dependencies on a new
machine. No old absolute Windows workspace path is required by active code.

## Future feature and PR workflow

Start only from a clean working tree; preserve pending work before switching.
After this integration PR is reviewed and merged:

```powershell
Set-Location E:\DittoBuffer
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c feature/next-milestone origin/main
# Implement in src/dittobuffer/, client/native/, client/, scripts/, tests/.
.\scripts\test.ps1
git status --short
git diff --check
# Review and scan an explicit source/test/doc file list; never git add .
git add -- <reviewed-source-files>
git diff --cached --stat
git commit -m "Describe the completed feature"
git push --set-upstream origin HEAD
# Open a GitHub pull request, review CI, then merge through GitHub.
```

Do not merge this branch directly into main, rewrite commits or force-push.
The exact final integration commit, publication status and any remaining worktree
changes are in the private publication receipt and the completion message; a
commit cannot embed its own final SHA. The source inventory is
[WORKSPACE_MIGRATION_FILES.json](WORKSPACE_MIGRATION_FILES.json).
