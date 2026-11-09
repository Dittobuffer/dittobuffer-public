# Admin panel

Material 3 / Material You, light theme, loopback only: <http://127.0.0.1:8080>

```bash
python -m dittobuffer /start admin
python -m dittobuffer /open
```

## Surfaces

| Tab | Capabilities |
|---|---|
| **Map** | Click to place, live OSM tiles with spawn, gym, Pokéstop, raid, Power Spot and player markers; street-cell download settings |
| **Spawns** | Species (PokeAPI search), level, ATK/DEF/STA IVs, shiny, shadow, count, radius, lifetime |
| **Gyms & stops** | Name and kind at the clicked point, merged into the world overlay |
| **Raids** | Gym, boss, tier, **boss HP**, **boss CP**, **shiny rate**, **shadow form**, egg delay, active window |
| **Dynamax & Power Spots** | Place a spot at exact coordinates with a chosen boss, form, mode, tier, level and lifetime; edit the released pool; end, extend or delete spots; grant particles |
| **Players** | Level, XP, last fix, centre map, read the phone's GPS over ADB |

Sprites come from PokeAPI and are cached in `data/admin/pokeapi-species.json`; offline the panel falls
back to "Pokémon #N".

## Design system

The UI implements Material 3 light tokens directly in CSS variables:

| Token | Value |
|---|---|
| `primary` | `#6750A4` |
| `surface` | `#FEF7FF` |
| `surface-container-low` | `#F7F2FA` |
| `secondary-container` | `#E8DEF8` |
| `outline-variant` | `#CAC4D0` |
| Shape scale | 28 / 20 / 14 / 9 px |
| Type | Roboto, Segoe UI Variable fallback |

Chips, pill tabs, FABs, tonal cards, elevation and state layers follow the Material 3 spec; there is no
framework dependency and no build step.

## API

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/status` | Counts, player location, config paths |
| GET | `/api/players` | Player list with level and last fix |
| GET | `/api/device-location` | Phone fused location over ADB |
| GET | `/api/species` | Cached species catalogue |
| GET/POST/DELETE | `/api/spawns` | Spawn CRUD |
| GET/POST/DELETE | `/api/pois` | Gym and Pokéstop CRUD |
| GET/POST/DELETE | `/api/raids` | Raid CRUD |
| GET/POST | `/api/max` | Max settings and released pool |
| POST | `/api/max/action` | Place, end, extend or delete a Power Spot; particles |
| GET/POST | `/api/osm` | Street-cell download toggle, radius, cached cell count |

```bash
curl -s http://127.0.0.1:8080/api/status | jq
curl -s -X POST http://127.0.0.1:8080/api/spawns \
  -H 'content-type: application/json' \
  -d '{"species":150,"count":3,"lat":60.2654,"lng":25.0218,"shiny":true,"shadow":true}'
```

## Power Spots

A hand-placed spot is written to `max_stations` with a `manual:` source id. It stays at the chosen
latitude and longitude until it expires, is ended or is deleted; the rotating candidate schedule never
retires or replaces it. The boss must already be in the released Max pool (species, form and mode), and
a Gigantamax entry additionally needs a source-allowlisted form — the shipped reference snapshot
allowlists none, so mode 2 is for raids and wild spawns, not Power Spots.

A spot that still holds stationed Pokémon can be ended but not deleted.

## Street map data

`world.osm_download` (default on in `config/family.json`) makes the game server fetch a small OSM cell
around each trainer on the map RPC. The cell under the player is downloaded before the reply (a few
seconds at most); neighbouring cells are queued by distance through one throttled worker. Cells are
cached as gzip under `data/maps/areas/` and change the client's map epoch, so new streets appear without
a restart. `/api/osm` reports the toggle, radius, cell count and cache size.

Tiles are served by this admin origin, not the TLS game port, because the phone's Lightship map loader
does not trust the private certificate chain. The game server advertises
`world.map_tiles_base` (default `http://127.0.0.1:8080/map/tiles`); this panel proxies
`/map/settings` and `/map/tiles/{z}/{x}/{y}` to the game server over loopback. If you change the admin
port, update that key. Map tiles are still unreliable on this build — see
[`KNOWN-ISSUES.md`](KNOWN-ISSUES.md).

## Storage

- Spawns and raids: `admin_spawns`, `admin_raids` tables in the game database.
- Gyms and stops: `data/world/admin-pois.json`, merged over the immutable published world.
- Power Spots: `max_stations` in the game database; hand-placed rows use a `manual:` source id.
- Street cells: `data/maps/areas/*.osm.gz`, cached OpenStreetMap extracts.
- Player fixes: `admin_locations`, also the authoritative position for the last RPC.

## Known issues

The panel shows a warning on every tab while map tiles and Power Spots are being repaired. The full
diagnosis, evidence and the fix list for the next developer are in [`KNOWN-ISSUES.md`](KNOWN-ISSUES.md).

## Safety

There is no authentication. Keep the bind address at `127.0.0.1`; if you need remote access, tunnel it
over SSH rather than exposing the port. The plain-HTTP map endpoints serve only public OSM-derived
vector data and follow the same loopback-only rule.
