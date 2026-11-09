# Assets and world data

Source checkout, asset restoration, and Android acceptance are separate checks.
The asset stores verify expected lengths, SHA-256 values, and declared dependency
graphs. A manifest is compatibility metadata, not permission to acquire or
redistribute the referenced material. Use only resources you independently have
permission to use. No asset download or phone access is part of restoration.

## Portable restoration

The historical manifests in `builds/` retain their original evidence and paths.
Those paths referred to an earlier workstation layout: simply copying artwork
into `assets/pokemon/` does not satisfy them. Use the new portable manifests:

| Manifest | Restored resources | Check group |
|---|---|---|
| `config/resources/pokemon-assets.json` | The accepted M11.3 Pokémon catalog and 2,739 exact content files | `pokemon` |
| `config/resources/avatar-assets.json` | The accepted avatar catalog and 46 exact content files | `avatar` |
| `config/resources/client-runtime.json` | The accepted M20.2 catalog/hash and runtime dependencies, including the isolated Mateo overlay | `runtime` |
| `config/resources/client-runtime-m23.json` | The currently verified M23 v6 catalog and all 388 runtime resources | `runtime`, `mythical-runtime` |
| `docs/local-resources.example.json` | All destination/source paths, lengths and hashes; nine core compatibility inputs and the Explorer source pin | `compatibility`, `explorer` |

All newly restored artwork/runtime/cache bytes go beneath
`assets/pokemon/local/{pokemon,avatar,client-runtime,npc}/`, already ignored by the
unchanged `.gitignore`. The portable manifests preserve content hashes, prefab
addresses, catalog bytes, and provenance; only resource filesystem paths change.
The Explorer source remains pinned to
`dc68848c8b7b003a7a00aff17849db6b554b3898ab399bbfaa159bafcf34c5d4`.

Portable Pokemon/avatar overlays now also carry a reviewed manifest identity
hash. This accepts repairs limited to `content[*].file` and `local_catalog`,
which the overlay replaces anyway. Every other field remains pinned, including
content lengths/hashes, catalog hash, forms, costumes and dependency graphs.
The original byte hash remains accepted. Unrelated metadata edits still fail.
The user's repaired legacy manifests do not need to be overwritten or reverted.

For the current standalone APK, select the same runtime manifest on both sides:

```json
{
  "client_resources": {
    "enabled": true,
    "registry": "config/resources/client-runtime-m23.json"
  }
}
```

Use `runtime_registry: "config/resources/client-runtime-m23.json"` in the local
standalone build profile. The builder validates the resources and embeds the
catalog digest. The launcher compares that digest with `/health` before starting
Unity; a missing or mismatched server registry gives an explicit error. Known
runtime CDN addresses are routed to the local verified store; unsupported
addresses fail locally instead of downloading additional official resources.
The runtime names, paths and hashes here do not contain the asset payloads.

The default command checks the nine small core inputs and writes nothing:

```powershell
python scripts/restore-local-resources.py
```

Prepare an independently authorized input directory retaining the `source_path`
entries from `docs/local-resources.example.json`. For an existing development
tree, select that tree explicitly as the source. Preview before applying:

```powershell
python scripts/restore-local-resources.py --source D:/AuthorizedInputs --all
python scripts/restore-local-resources.py --source D:/AuthorizedInputs --all --apply
python scripts/restore-local-resources.py --all
```

`--group pokemon`, `--group avatar`, `--group runtime`, and `--group explorer`
allow separate restores; repeat `--group` to select several. `--verbose` lists
individual paths. Missing or different input produces a nonzero exit status.
Every selected input must verify before a copy begins. Existing files, including
different versions, are preserved and reported. The tool never replaces an
existing path, rewrites a database, changes a profile, or contacts a device.
Optional resource bytes are not installed merely by installing Python packages.

After a successful restore, select these paths in your own reviewed profile:

```json
{
  "pokemon_assets": {"enabled": true, "manifest": "config/resources/pokemon-assets.json"},
  "avatar_assets": {"enabled": true, "manifest": "config/resources/avatar-assets.json"},
  "client_resources": {"enabled": true, "registry": "config/resources/client-runtime.json"}
}
```

An M20.2 standalone profile must use the same
`"runtime_registry": "config/resources/client-runtime.json"` so that its embedded
catalog agrees with the server. Enabling Mateo additionally requires the
`explorer` group and `routes.mateo.npc_display_source` set to
`assets/pokemon/local/npc/game-master.cache`. Do not copy a private account profile
or another developer's credentials. The restoration tool does not enable Routes,
change player progression, or certify Android behavior after a migration.

## Existing repository content

The incoming repository already tracks Game Master/reference data, some avatar
and runtime resources, a split APK through LFS, tools, photos, and signing/TLS
material. This adaptation preserves that existing tree; it does not establish
redistribution rights or endorse reusing shared signing/private TLS keys.
Existing payloads do not form a complete asset closure: the historical avatar
and client-runtime manifests still reference omitted earlier build paths.
The portable manifests and verifier make the actual requirements explicit.

Keep newly supplied proprietary bytes in the ignored local resource directory.
Keep player databases, authentication material, and machine-specific secrets
private. No `.gitignore` exception, forced addition, broad LFS migration, or new
asset redistribution is required by this restoration process.

## World snapshot

The JSON development world (`data/world/dev-world.json`) contains forts and a
home gym, but carries no OSM map geometry. It cannot demonstrate roads, buildings
or grass rendering. A published Netherlands database must be supplied separately;
the restoration tool intentionally does not copy player or world databases.

The published snapshot is selected by `data/world/current.json`. Preserve its
database path, SHA-256, epoch, source provenance, and ODbL attribution. In your
own profile, remove `world.json` and set `world.manifest` to that reviewed
manifest; `Game` prefers `json` when both are present. Keep the world palette at
`src/dittobuffer/world/world-palette.json`. Never edit the published database in
place: `WorldStore` verifies its hash. Use a new dated database and publication
manifest for subsequent world imports. Existing player databases and the normal
published world are not migration fixtures.

OSM-derived data retains **ODbL-1.0** obligations and © OpenStreetMap contributors
attribution. POI photos have independent licenses and provenance requirements.
The reviewed Power Spot index is a derived world resource, not a trainer save.

## Existing LFS objects and licensing

The incoming `.gitattributes` selects several binary extensions for Git LFS.
An LFS pointer is metadata, not a usable resource: the verifier rejects a pointer
where actual bytes are required. Fetch only existing LFS objects you are entitled
to use. LFS does not supply ignored Pokémon resources or grant redistribution
permission. No new LFS rules are introduced here.

The root `LICENSE` and maintained native fork contain GPL terms; some incoming
README/packaging text still says MIT. Resolve that contradiction before making a
broader licensing claim. Game artwork, audio, APKs and configuration inputs keep
their rights holders' conditions regardless of the source-code license.

## Optional runtime restoration

The optional `rocket` restoration group supplies the pinned native-combat reference
into ignored `assets/pokemon/local/rocket/`; `rocket.server_combat` is explicit opt-in.
The accepted M23 runtime is locally restored at `assets/pokemon/local/mythical/registry.json`.
New developers supply their own authorized cinematic closure using `scripts/m23-encounter-resources.py`.
No private database, signing key, modified APK or resource payload belongs in a commit.
