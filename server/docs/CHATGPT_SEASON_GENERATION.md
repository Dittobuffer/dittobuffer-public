# Generate content with ChatGPT

Download Studio's **JSON schema** and inspect its **Pokemon Availability** and
Research capability catalog. Supply the schema and an exported original example
to your content generator. Resource availability is specific to your server.

Reusable prompt:

> Generate one valid `dittobuffer.liveops.bundle` version 1 JSON file using the
> attached authoritative schema and my attached server catalog. Include an
> original four-week Season, weekly wild pools and one-/three-/five-star raid
> rotations, geographic or biome habitats, at least two child Events, Field
> Research and a multi-step Special Research story. Use stable unique IDs and
> correct internal Research/Season references. Use the IANA time zone
> Europe/Amsterdam and the start date I provide. Make all descriptions original.
> Use only available species/forms/shinies, supported items/tasks, and explicitly
> released Max bosses from my catalog. Do not add Master Balls, fake capabilities, arbitrary file paths or executable
> code. Identify custom balance clearly in metadata.notes. Preserve all dates,
> scopes, pool weights and dependencies. Return JSON only, suitable for the
> DittoBuffer Studio preview/importer. Do not publish content or change players.

Specify your start date and intended balance separately. The samples are
original format-inspired scenarios, not proprietary historical event tables.
The Routes/Mateo example uses supported Route, Gift and Postcard counters and
does not claim a Mateo-specific Research counter exists.

Import through **Import JSON → Preview JSON**. Read all errors and warnings.
Correct the file or ask the generator to correct exact reported paths, then
preview again. Import as Draft, edit through Studio, preview publication and
activate only when ready. Export the final Season to retain your edited version.
Never provide the generator with private accounts, session cookies, database
files or the private website access key.


For M25, request `research_type` from the installed schema/catalog: Field, Special, Timed, Collection, branching, timed branching or Breakthrough. Use `entries` for Collection, `paths` for branching, and `encounter_pool` for Breakthrough. Do not invent level-gate requirements: use Studio's read-only supplied 71-80 reference. Sample original imports are in `config/liveops-examples/research/`. Ask for an expiration policy and feasible spawn/method prerequisites. Native Timed branching and additional Collection acquisition methods still need device verification; successful validation is not that proof.
