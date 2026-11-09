# LiveOps bundle schema (version 1)

The public identifier is `dittobuffer.liveops.bundle`, with `schema_version: 1`.
The authoritative, packaged JSON Schema is
[`bundle-v1.schema.json`](../src/dittobuffer/gameplay/liveops/bundle-v1.schema.json).
Download it from **Studio → Download schema**. Original, source-only
bundles are in [`config/liveops-examples`](../config/liveops-examples/).

## Content model

The envelope contains `format`, `schema_version`, `bundle_type`, `metadata`,
`content`, optional `regions` and `dependencies`. `bundle_type` is `season`,
`event`, `research` or `collection` (a bundle of definitions, **not** a native
Collection Challenge). A Research-only bundle requires no Season.

`content` is the existing Studio document model, not a second scheduling engine.
Each document has a stable `id`, `kind`, `name`, `timezone`, `start`, `end`, and
`scope`. Seasons and Events contain `rules`, optional ordered `weeks`, and
elapsed-minute `rotations`. A child Event uses `season_id`. Research uses
`research_type`, `stages`, objectives and rewards. Refer to the schema for every
permitted property and bound; unknown properties are rejected at every model
level. Metadata generator settings are bounded opaque JSON annotations and
never executed or interpreted as paths.

Each weekly entry overrides the rules for that local-calendar week, measured
from the Season start. Time zones are IANA identifiers. Local DST gaps are
rejected; ambiguous local times require `fold`. Offset-bearing ISO times are
recommended. Intervals include start and exclude end. Rotation offsets are
elapsed minutes from start; their end is clipped to the parent interval.

Species may be numeric IDs or unambiguous names from the restored registry.
Form names resolve within the selected species. Exported imports use numeric
IDs. Item names resolve only against the supported reward registry. Do not guess
forms, item IDs, cinematic selectors or released Max bosses: use Studio's
actual catalog. All model and shiny availability checks use the local manifest.
No Master Ball reward is accepted.

Rules support wild spawns, ordinary/Mega raid pools, released Max pools, eggs,
explicit supported Rocket settings, bonuses, and Research references. Raid HP,
CP, XP and Stardust are **operator-owned configurable balance**; examples do not
claim official server tuning. Research reward types are item, XP, Stardust,
encounter, Candy, XL Candy and supported Mega Energy. Available task counters
are exposed by `/api/studio/catalog`; unsupported counters are rejected. Timed/Collection/branching definitions are now supported. No per-entry IV field is accepted because the
existing LiveOps adapter uses the server's spawn IV settings.

Geographic scopes are global, biome, named region or polygon. Coordinates use
`[longitude, latitude]`. Regions include their geometry in exported dependencies.
A referenced existing region must be present on the destination. Supported
capability declarations are `field_research`, `special_research`, `timed_research`, `collection_challenges`, `branching_research`, `research_breakthrough`, `wild_spawns`,
`bonuses`, `regions`, and enabled `raids`, `mega_raids`, `released_max`.
Other declarations reject import; nothing is silently discarded.

## Import and export

1. Open the private administrator website link. Studio shares that session.
2. Choose **Import JSON**, upload/drop a JSON file or paste its contents.
3. Choose **Preview JSON**. Review dates, habitats, normalized species/forms,
   shiny rates, spawn probabilities, raid rotations, rewards, dependencies,
   warnings and matching identifiers. This does not write to any database.
4. Choose **Import as Draft**, or Cancel. Publication remains separate and
   requires its own reviewed confirmation. Drafts open in normal Studio editors;
   **Advanced fields** edits all declarative fields while retaining identity.
5. **Export JSON** exports the selected document and required dependencies.
   Season export includes its child Events and referenced Research. The header's
   **Export selected** exports a chosen collection. Saved generated Seasons and
   template drafts use exactly the same export. API `export-template` serializes
   a template or generator options without saving first.

`POST /api/studio/import/preview` accepts `{ "json": "..." }` or `{ "bundle": ... }`.
It returns normalized `bundle`, stable SHA256 `fingerprint`, path-specific
`errors`, `warnings`, `summary`, `conflicts` and `expected_revisions`.
`POST /api/studio/import` requires that reviewed bundle, fingerprint,
`confirm: true`, and `mode` (`create`, `copy`, `update`). Updates additionally
require reviewed `expected_revisions`. No import ever publishes.

Import receipts persist in the editorial SQLite database. A retry of the same
canonical bundle and mode returns the original receipt, including after a
restart; it does not reset later edits or award player rewards. Copy mode uses
stable remapped IDs and internal references; repeated copy requests return the
same copy. For a deliberately different copy, edit the source metadata first.
Update mode creates revisions only for never-published drafts. A changed draft
rejects a stale preview. Published, paused, ended or archived content cannot be
overwritten through import. Conflicting regions must be copied with remapped
references. A failure rolls back documents, regions and receipt together.

Exports contain editorial content only: no accounts, sessions, player progress,
reward receipts, private configuration or SQLite files. Canonical object keys
and content/region ordering make fingerprints reproducible; array order within
weeks, tasks and pools is meaningful and is retained.

## Security and limits

The complete website requires a server-validated administrator session for all
privileged APIs; a player token or localhost address is insufficient. Open the
private capability link using `scripts/open-admin.py`. Its key is stored only
in `.private/admin-website-access.json`, exchanged for an HttpOnly SameSite
cookie, removed from the URL fragment, and never included in exports. Studio
uses that same session and does not request a second password. Write requests
require CSRF and matching Origin. For off-machine access, serve behind HTTPS and
an authenticated reverse proxy; the built-in website is intended for loopback.
The native map/postcard resource routes remain separate from privileged APIs.

JSON is limited to 2 MB, 32 levels, 100,000 nodes, 100 content definitions,
100 regions and bounded individual pools/stages. Duplicate object keys,
nonfinite numbers, unknown fields, unsafe/unrecognized resource fields and
unsupported mechanics fail server validation. JSON Schema validation uses
`Draft202012Validator.iter_errors`, followed by actual gameplay validation.
Errors carry JSON paths; at most 200 errors and 100 overlap warnings are returned.
Imports execute no files, SQL, shell, Python or JavaScript supplied by content.


## M25 additive schema-v1 Research categories

`research_type` accepts `field`, `special`, `timed`, `collection`, `branching`, `timed_branching`, `breakthrough`. Existing v1 bundles remain accepted; incompatible nonempty category fields are rejected rather than discarded. Stories use `stages`; Collection uses `entries` and `rewards`; branching uses two to four named `paths` with their stages/rewards; Breakthrough uses base `rewards` and a weighted `encounter_pool`. Exact bounds and filter names are in the downloadable schema. Eligibility supports levels 1-80 and completed-story prerequisites. Verified level-up gates are not editable imports.

Six validated original imports are in `config/liveops-examples/research/`. Edit their dates and pool against the destination catalog, preview, then import as Draft. Neither import nor preview publishes or changes player progression. See [Research Builder](RESEARCH_BUILDER.md) and [KNOWN-ISSUES.md](KNOWN-ISSUES.md).
