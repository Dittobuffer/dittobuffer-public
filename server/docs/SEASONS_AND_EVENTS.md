# Seasons, Events & Research Studio (M33)

Status: implemented foundation with real Pixel 5 gameplay evidence; **not the complete M33 definition of done**. See [KNOWN-ISSUES.md](KNOWN-ISSUES.md) for tested workflows and unsupported mechanics.

## Start and open the Studio

Use the maintained `src/dittobuffer` checkout, Python environment, Game Master and independently restored resource manifests described in [ASSETS.md](ASSETS.md). Run the existing admin and game services against the same configuration:

```powershell
.venv\Scripts\python.exe -m dittobuffer.runtime.http --config config/family.json --port 8445
.venv\Scripts\python.exe -m dittobuffer.admin.admin --config config/family.json --port 8080
```

Open `http://127.0.0.1:8080`, then **Seasons & Events**. **No separate Studio password or sign-in is required by default.** The user explicitly requested that the Studio use the existing admin website's access model. A fresh browser receives an automatic local-admin session. CSRF tokens, same-origin checks, confirmation and audit history remain active.

Optional deployments can explicitly set `liveops.admin_auth: true` to enable the separate publisher/editor/viewer mode. This is disabled in the example and normal default. In that opt-in mode only, the first password is stored privately in `.private/studio-admin.bootstrap.txt`; `scripts/manage-studio-admin.py` can prompt for administrator passwords and roles without placing them in command-line arguments. Existing private credential files are preserved but are not required in the default mode. Restart the admin service after changing that optional policy. The older admin endpoints retain their existing access model.

## Create and publish

1. Choose **New Season**, **New event**, a template, or **Generate Season**.
2. Set its name, named time zone, dates and scope. Pick species by name or number. Choose a supported form and numeric spawn weights; percentages are computed from the weights.
3. For a Season, edit each weekly tab independently. Ordinary operations use forms; JSON is only needed for optional export/import.
4. Select Field/Special Research drafts. Publishing reviews the event, linked Research and generated child events together.
5. **Save draft**, then **Preview & publish**. Review the listed publications and effects, then explicitly confirm.
6. Check **Overview** and **Calendar** at the intended coordinates/time. Preview is read-only and does not enroll players, create raids, award rewards or sample walking distance.

Draft changes do not alter the frozen live revision. Concurrent stale saves and stale publication plans fail with a reload/review message. Pause/resume/end/archive preserve owned Pokemon and all reward ledgers. History supports historical revision review and publication rollback; rollback changes the selected content revision only and does not silently roll back other publications. Import remaps identities and references, validates before saving, writes all imported drafts transactionally and never auto-publishes.

## Gameplay and scheduling

Definitions/revisions/publications/audit history/regions/entitlements are stored beside the configured trainer database in `liveops.sqlite3` (normally `data/liveops.sqlite3`). Schema creation is transactional; gameplay readers during initial creation keep ordinary defaults until the schema is committed. Isolated Database overrides do not inherit the normal trainer calendar. This uses the existing ignore rule for local SQLite state; `.gitignore` was not changed. `config/liveops.example.json` documents the opt-in path and disable flag. The existing family profile does not need editing: absent content means ordinary gameplay.

Every authenticated RPC evaluates published content into a request-local copy of the existing Game configuration. No shared global configuration is modified. The existing catch, raid, Max, egg and Research engines remain authoritative. Instance-installed PvP RPC wrappers are rebound to the projection and invoke their underlying implementation with that same object. This is necessary for catch bonuses to reach the real family client.

A schedule is a half-open interval `[start, end)`. Weekly boundaries follow the Season's local wall time, including DST. Nonexistent spring-forward local times are rejected; repeated fall-back times require an explicit earlier/later choice. Hour-offset habitat rotations are elapsed minutes after the event start. The scheduler is request-driven: an idle world changes on the next relevant request, without a background account writer or server restart.

Rules sort by ascending numeric priority, then content ID. Later rules replace earlier wild/raid/egg/Max/Rocket pools; equal-priority ties are stable. Bonuses take the highest factor by default. Explicit replacement/multiplication are available and bounded. Existing legacy catch-calendar factors and Studio factors take the stronger resulting factor, avoiding accidental double application. Empty/disabled/no-active content preserves defaults. Research references accumulate. Child events require an active, eligible parent Season.

New raid/Max definitions receive immutable identities at rotation/publication activation boundaries. Existing battles and earned encounters keep their original snapshots. Old sessions and reward records are not deleted. Manual Power Spots are preserved. Max pause/resume/end must leave one current station per eligible candidate, using the existing materializer and retaining history.

Client map objects refresh through its normal polling. Previously displayed objects can linger until its refresh/expiration; a cold reload demonstrates the new authoritative pool without relying on those cached objects.

## Geographic regions

Global, server habitat, saved polygon and custom drawn polygon scopes are supported. **Import published OSM province** reads the preserved world snapshot in read-only mode and saves a simplified boundary in the editorial database. Its OSM relation, snapshot hash, ODbL-1.0 attribution and simplification tolerance remain attached. Multipolygon islands and holes are retained. No proprietary habitat boundaries are inferred.

The current world contains twelve province polygons. City/country outlines are not automatically invented: draw/import a validated operator-owned boundary if the source does not supply one. The custom drawing surface is a coordinate grid, not a street basemap. This limitation is deliberate and visible. Existing gameplay OSM attribution is untouched. Regions imported from OSM retain **OpenStreetMap contributors** credit and their ODbL provenance in exports.

## Current scope and extensions

Working adapters: weighted wild species/forms/levels/shinies, ordinary/Mega raid pools, released Max pools, weighted egg pools, selected Rocket cadence settings, catch XP/dust/candy, transfer candy, evolution XP, Field/Special Research and optional player entitlements. Shared Raid/Max/Rocket objects cannot be ticket-specific per-player objects; publication rejects that combination.

Not exposed as working: Shadow Raids, featured evolution attacks/grace periods, native event news/banner delivery, Mighty Pokemon, Safari Balls, global challenges, research definition version selection, event medals and cosmetic reward grants. Max/Rocket/egg rotations are not all newly Android-verified by M33. Do not infer native verification from successful publication or tests.

The internal v1 boundary is `gameplay/liveops`: `Catalog` capabilities, declarative validation, `Store` revision/publication methods, pure `rules.resolve`, `runtime.project` and `generator.template/generate`. New template strategies are trusted source additions, registered in the template library. Task extensions must register the real native quest type, validator, reliable successful-action emitter and builder capability together. Reward extensions must implement native encoding, capacity checks and transactional grant tests. No content document may execute Python/JavaScript or choose arbitrary filesystem paths.

## Isolated scheduler tests

Only a development profile with `liveops.isolated_fixture=true` and a trainer database beneath `.private/` or `builds/` can use the admin-only `/api/studio/clock` endpoint. It persists a scheduler clock in that profile's content database. It does not change OS time, native server time, global walking validation or the live trainer database. Ordinary deployments reject clock control. Do not point isolated fixtures at normal player state.

Habitat-scoped preview simulations currently need an explicit biome argument; the browser location form does not yet infer habitat from the selected coordinates. Real gameplay obtains habitat from the existing server world provider.


## M25 Research integration

Research Builder now supports Timed, Collection, branching, timed branching and Breakthrough pools alongside Field/Special. Their definitions use the existing publication/revision store and freeze at enrollment; no parallel progress engine is introduced. Timed, Collection, Field, Special, permanent path selection and Breakthrough reward flows were tested on Android. Timed branching is backend-only. See [KNOWN-ISSUES.md](KNOWN-ISSUES.md). Native level gates are immutable source definitions, available through the read-only Studio reference endpoint.
