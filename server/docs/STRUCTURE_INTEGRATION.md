<!--
  Copyright (C) Dittobuffer Contributors
  Copyright (C) chucny
  Copyright (C) gekkedavi

  This file is part of Dittobuffer. It is distributed under the GNU General
  Public License v3.0 (see LICENSE). The copyright notices in this file must
  be preserved in all copies, forks, and derivative works. Removing or
  altering these notices violates the license and copyright law.
-->

# New server structure integration

Date: 2026-10-26. Base: `Dittobuffer/dittobuffer` main
`c4bb197c4cb779053c90387d3f2fd6938c7767eb`.

## Working location and preservation

The incoming main reorganizes the project and contains newer Rocket, PvP and Max
code. The original workstation checkout contains newer Routes, Mateo, Postcards
and Vivillon changes. Replacing either set of whole modules would lose features.

The integration is in the separate repository
`E:\PokemonGO-libreNPL\builds\structure-audit-main-20261026`, on local branch
`integration/routes-new-layout`. Open this directory as the workspace for new
development. Its origin is `https://github.com/Dittobuffer/dittobuffer.git`.

The parent checkout remains on `feature/teamgorocket` at `09460bf`, with its
uncommitted work preserved. A new parent `AGENTS.md` points future work here.
No reset, clean, stash, history rewrite, commit or push was performed. The
original player databases, published Netherlands world, private archives,
Android installations and official resource cache were not used as writable
test inputs.

## Structure used for the port

| Old source location | Canonical destination |
|---|---|
| `server/game.py`, gameplay features | `src/dittobuffer/gameplay/` |
| database, accounts, Game Master, asset services | `src/dittobuffer/core/` |
| world modules | `src/dittobuffer/world/` |
| `server/__main__.py` | `src/dittobuffer/runtime/http.py` |
| Android launcher | `client/standalone/org/dittobuffer/launcher/` |
| maintained Rust fork | `client/native/` |
| independently supplied resource bytes | ignored `assets/pokemon/local/` |
| portable resource manifests | `config/resources/` |

No old `server/` compatibility package or duplicate Rust fork was introduced.
Every existing destination file is retained. The upstream tests, admin console,
world JSON path, native transport and existing feature modules remain present.
The complete proposed file inventory is [INTEGRATION_FILES.json](INTEGRATION_FILES.json):
392 existing paths retained, 30 modified paths and 67 added source/manifest paths,
459 paths in total. Generated test output is outside this list.

## Features integrated

- Twelve Routes, creator, graph/publication, rewards, Cells, Mateo, exploration,
  Vivillon and worldwide Postcard modules, plus examples and original artwork.
- Twenty-three additive `Game` helpers/RPC handlers and targeted hooks into
  startup, location, map, catch, inventory, settings and trainer profile. All 173
  original main `Game` methods remain.
- Research and Buddy events, consumable inventory rules, Gift/Postcard privacy,
  saved pins, and successful idempotent Gift retries with conflict rejection.
- Optional Game Master templates and separate cache epochs composed with the
  existing PvP epoch, Max, Shadow, level-50 and XL definitions.
- Runtime and admin Postcard-image endpoints, matching the plain-HTTP image
  origin used by the client.
- Opt-in Routes/Postcard client adapters, shared HUD rendering/touch ownership,
  launcher fixture notice, runtime-catalog embedding and the existing PvP flag.

The PvP epoch is composed before reserving four optional-feature bits. A PvP
template revision therefore cannot collide with enabling Routes or Vivillon.

The normal family profile is not automatically switched to a development
fixture or enabled for additional rewards. Examples retain disabled defaults.
The one-off original `m20-fixture.py` depends on dated private profiles and is
not transplanted as a portable setup command. Existing private fixture outputs
remain in the original checkout. Portable tests create their own temporary
databases; geographic administration helpers require explicit input/configuration.

## Protocol compatibility

Only the audited Routes/NPC/form/Collector corrections were applied to the
incoming schema; the later Max and PvP corrections were retained. Python
bindings were regenerated from this merged schema.

The supplied 0.429.2 ARM64 `ClientMapCellProto` codec initializer at RVA
`0x8B1AE08` independently establishes two distinct fields:

- `0x8B1B250`: tag `0x8a`, Station codec at static offset `0x50`: field **17**.
- `0x8B1B2E0`: tag `0xa2`, SharedRoute codec at static offset `0x60`: field **20**.

The old flat schema assigned Routes field 17. This integration preserves the
incoming correct Station 17 / Routes 20 schema and adds literal-byte tests,
rather than validating mutually consistent but incorrect server encoders.
The additive `scripts/normalize-routes-protocol.py` is idempotent and does not
regenerate the base schema from an older exporter.

## Build and startup integration

- Normal provisioning no longer requires a private transfer-archive manifest.
  Existing private accounts are preserved; new provisioning uses a separate
  database and Windows CurrentUser DPAPI storage.
- Patcher and transfer builds preflight their inputs and copy decoded APK trees
  into fresh ignored `.staging/` attempts. Unique APKs/reports go under ignored
  `apk/built/`; tracked profiles and decoded inputs are preserved.
- Maintained native build helpers use `client/native/`, derive the pinned Rust
  channel from its toolchain file, and keep Cargo output in its ignored `target/`.
  NDK ZIP repair validates paths, links and existing bytes before changing files.
  ABI verification writes only exclusive ignored `run/` reports.
- Startup pins an explicit or sole physical device and uses `adb -s SERIAL`.
  It refuses unknown occupied ports and unverified PID records, stops only a
  verified process lifetime owned by this checkout, and does not reset global ADB.
- The unified test runner runs pytest and all four Node suites, preserves the
  caller's environment, and stores results in ignored `.private/test-runs/`.
  CI keeps its existing jobs and adds the Node suites and clean-checkout setup.

## Resource restoration and existing repository contents

See [ASSETS.md](ASSETS.md). The restoration plan contains 3,074 hash-pinned records.
Its read-only source preview verified nine existing compatibility inputs and
3,065 independently restorable resources totaling 1,919,822,910 bytes. **No game
resource payloads were copied.** Compact path overlays preserve the historical
catalog/evidence documents, prefab identities, dependency graphs and costumes.

All new payload destinations are already ignored. No ignore exceptions or
force-added assets are used. Historical manifests are retained. The source
preview is evidence of local compatibility, not redistribution permission.

Incoming main already tracks a split official APK, third-party asset inputs,
TLS material and a signing keystore. They were not added, deleted or replaced
by this integration. Their presence is a publication/security review issue;
do not assume they are private identities suitable for deployment or that
their redistribution is licensed. The README and package metadata now agree
with the repository's existing GPLv3 `LICENSE`; the license file itself and
third-party rights are unchanged.

Immutable SHA-256 values:

| File | SHA-256 |
|---|---|
| Original checkout `.gitignore` | `31533f97085c97fb6719be67d61e79d1ef297a698b275ff4c67df9d0cd99f008` |
| Incoming main `.gitignore` | `fdd6c09dd04f61f1180a17fd37a29ee2fb56cba154bfc9d0cdca1f92efd112d8` |
| Incoming `client/native/.gitignore` | `0cc531f39408e6d1b77f6d5db1f05b5e7570336baa4b3a40c1c248e5480fa223` |

## Setup and validation

Follow the corrected [README quick start](../README.md#quick-start). The package
must be installed with `pip install -e .`, and the tracked OSM Power Spot LFS
object must be restored before running Max tests. `scripts/setup-checkout.py`
checks that object and creates only writable runtime directories. It neither
downloads assets nor edits profiles/databases; protocol regeneration is explicit.

Verification used Python 3.13 and the destination's pinned pytest 8.4.2:

| Check | Observed result |
|---|---|
| Complete collected regression run | **573 passed, 16 skipped**, 589 tests, 559.59 seconds |
| Four Node suites after that run | **All passed**: UI, raids, family, world |
| Final source-freeze checks | **49 passed**; covers all five subsequently added/renamed test nodes, including cache revision isolation and transfer output preservation |
| Game Master checks after the cache fix | **3 passed**: bootstrap/settings, original model/form preservation, unknown-field preservation |
| Combined feature GM | **2,512 unique templates** with Routes/Cells/Vivillon and incoming Rocket/PvP/Max enabled |
| Java launcher and assembled-agent checks | Compilation and Node syntax passed; no APK build |
| Python 3.11 grammar | All **195** Python source/helper/test files parsed; not a Linux runtime test |
| Resource restoration | Nine core pins verified; full authorized-source preview wrote **zero** payloads |
| Original preservation | All **651** pre-existing source hashes match; original HEAD/branch/index/stash match |
| Destination preservation | All **392** pre-existing files retained; no native-source or ignore changes |
| Size and new-source scan | No tracked/added file above 100 MiB; only key-pattern match was an intentional rejection-test string |

The final collection has **593** tests. The full run preceded the last small
fixes; all five new or renamed nodes are included in the passing 49-test final
check. Do not describe the 593-test collection as another full execution.
Sixteen skips cover unavailable optional Unity/Explorer/NumPy/APK fixtures and
Windows symlink creation. The existing legacy collection exclusion is unchanged.

Local evidence (ignored, not included in the source manifest):

- `.private/test-runs/20261026-205718-66c77137/results.json` and its Python/four Node logs.
- `run/final-source-freeze-checks.log`, `run/final-compatibility-checks.log`,
  `run/remaining-route-tests.log`, `run/final-collection.txt`.
- `run/native-map-codecs.txt`, recording the local binary's tag evidence.
- Three new temporary test-database directories from interrupted/failed checks
  were retained under `.private/test-remnants/20261026-structure-integration/`.
  No original source data was moved or deleted.

Python tests, Node/Java source checks and native gameplay verification are separate.
No Android client was installed or launched for this structural integration.
Fresh Android gameplay verification is **NOT_TESTED**. The Rust source is retained
unchanged; prior Android/Rust milestone evidence is not a fresh test of this port.
Native Cargo execution, actual NDK build, fresh APK build and Linux execution are
**NOT_TESTED**. An executable `protoc` and the reviewed toolchain are still required
for a native build; setup fails clearly rather than inventing or downloading a
replacement compiler automatically.

The existing `tests/legacy` collection exclusion is unchanged. Optional Unity
resource tests require independently supplied fixtures and report explicit skips.

## Publication workflow

Use this checkout and the canonical package paths for subsequent work. Inspect
`git status --short` and `git diff` here; the parent repository's ignore policy
deliberately hides this separate checkout. The parent branch must not be pushed
in place of this structure.

No publication is automatic. Review the complete changed/new file list, existing
key material and third-party inputs before approving a commit or push. Preserve
all existing destination files and keep independently restored resource bytes
ignored. No force-push or history rewrite is needed.
