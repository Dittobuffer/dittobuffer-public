# Automatic Season Generator

Use **Seasons & Events > Random Generator**, or create **Season of Beginnings** / **Season of Discovery** from Templates. Generation creates editable **drafts only**. Nothing is enabled on the normal server automatically.

## Inputs and reproducibility

The form accepts a name/theme, one to twelve weeks, start, available generations, number of distinct featured Legendaries, difficulty and seed. The declarative API also accepts event enablement, shiny policy, type themes and geographic scope. Some advanced inputs are not yet exposed in the beginner form.

A seed is reproducible only with the same complete options (especially explicit start), available local catalog, Game Master, module configuration and generator version. An omitted start intentionally means now; the same seed at a later start is a different schedule. The result stores generator version, seed/options and a document fingerprint. IDs are assigned when drafts are stored and remain stable across subsequent edits. Generation does not reuse proprietary event copy/artwork or claim official event balance.

Ordinary species are selected from the restored model catalog and requested generations. Common/less common/rare weights follow the species' stat total, with rare evolved species weighted lower. The algorithm selects distinct available featured Legendaries, lower-tier raid bosses, supported Mega branches and already released Max bosses. It creates weekly weighted egg pools when eggs are enabled. Easy difficulty lowers encounter levels; all balance remains operator-editable custom tuning.

## Generated draft bundle

A four-week default bundle includes:

- Four independently editable weekly wild and raid rotations.
- Distinct weekly featured Legendaries when requested and available.
- Original Field Research and a multistage Special storyline.
- Weekly Spotlight Hours and weekly Research Day mini-events.
- One Community Day and linked editable Research.
- Supported Mega/Max and egg entries when their existing modules/resources permit them.
- Versioned dates, priorities, bonuses, a calendar and dependency validation.

Generated child events inherit the Season's active/eligible requirement. Preview & publish reviews the entire bundle atomically. Editing a week or timed habitat rotation is possible through the editor tabs. A live Research revision does not rewrite already enrolled tasks. The current implementation can edit generated parts; it does not yet offer selective re-generation of one week while preserving all manual edits automatically. Duplicate or generate a second draft for comparison.

**Season of Discovery** uses original Routes, hatching, postcards and Buddy-candy objectives, reusing those successful gameplay counters. It does not simulate distance or physical walking. Its reward resources must be restored and its gameplay modules enabled.

## Original template library

| Template | Implemented runtime content | Important limitations |
|---|---|---|
| Community gathering | Featured species, supported shiny boost, catch XP, Field Research | Featured evolution move/grace period unavailable |
| An evening spotlight | Single species, short schedule, bonus, Field Research | Default original tuning is editable |
| Skyward raid hour | Existing three-star raid pool and schedule | Does not replace raid combat |
| Field notes day | Wild pool and earned Field Research | No Collection layout |
| Habitat celebration | Type-themed elapsed-time habitats, raids, Special Research | Global/Collection Challenges unavailable |
| Kanto/Johto field expedition | Generation-focused habitats, Legendaries, original story | Version paths/event medals unavailable |
| Shadow watch | Supported Rocket cadence setting and Rocket objectives | Lineup editor not generalized by M33 |
| Power discovery weekend | Already released Max bosses and Max Research | Requires enabled module and resources; not fresh Android-verified |
| A day of discoveries | Egg pool and actual hatch objective | No artificial walking or hatch completion |
| Friendly competition | Dependency-labelled draft | Event PvP adapter required; publication blocked |
| Wild frontiers | Dependency-labelled draft | Mighty/Safari native adapters required; publication blocked |

The template's server validation is authoritative. A nominal starter with disabled dependencies cannot be published as working content. At least four templates have real gameplay adapter tests; the acceptance report separately states how many were exercised on Android. Do not interpret a template-library card as native proof.

## Known generator gaps

The generator does not currently produce native Timed/Collection Research, end-of-Season collection grants, official habitat boundaries, ticket-specific shared raid bosses, featured attacks, GO Tour paths, Global Challenges, event medals, Mighty encounters or expiring Safari Ball rules. It does not guarantee every requested habitat/region has a local spawn if its geographic data is absent. Region/habitat refinements can be edited explicitly in the forms. Those omissions keep M33 partial rather than silently synthesizing unsupported official mechanics.

## Extending generation

`gameplay/liveops/generator.py` owns original template factories and deterministic generation. `catalog.py` supplies local model/resource and native capability evidence, `validation.py` gates publication, and `rules.py` resolves the same published declarative documents for preview/runtime. Trusted internal extensions must preserve deterministic seeds, bound inputs, expose missing dependencies, and add real gameplay tests. Imported content is data only; it cannot load Python plugins or arbitrary paths.


M25 adds six original Research templates: A New Adventure, Weekend Explorer, Kanto Discovery, Mysterious Signals, Seasonal Discovery and Field Notes Choose a Path. Their menus use the same shared task registry, validator, JSON bundle exporter and read-only feasibility preview. Imports are in `config/liveops-examples/research/`; none activate automatically. Exact level gates are read-only source references, not generated custom tasks. See [KNOWN-ISSUES.md](KNOWN-ISSUES.md).
