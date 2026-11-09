# Known issues — map tiles and Power Spots (handoff note)

Status: **map tiles fixed and verified on the phone**; Power Spots outside the Netherlands are still
open. This note records what was verified, what was changed, and exactly what still needs work. It is
written for the next developer/GPT picking this up, not as user documentation.

## What the player sees

- Streets render. The in-game map draws the OSM street network for the home area, and spawns, stops
  and gyms appear with it.
- Power Spots do not appear at the test location (60.2655, 25.0218 at the time of writing).

Spawns, forts, gyms, raids and the RPC transport work. `GET_MAP_OBJECTS` answers with `status=1` and
13 map cells.

## Evidence collected

### Map tiles (resolved)

Three separate faults all had to be fixed; each one alone leaves the map empty.

1. **The advertised tile origin must be plain HTTP.** The Lightship/Unity map loader does not use the
   game RPC transport and does not trust the private certificate chain, so with
   `"map_tiles_base": "https://127.0.0.1:8445/map/tiles"` the phone requested `/map/settings` every
   minute and **never issued a single tile request**. With
   `"map_tiles_base": "http://127.0.0.1:8081/map/tiles"` — the game process' own plain-HTTP map
   origin, already forwarded by `adb reverse` — tile requests arrive immediately. `START-SERVER.bat`
   and the admin console (`world.map_tiles_port`, `rewrite_base`) both write that plain-HTTP base.
2. **The provider template must expand to z/x/y.** `settings()` used `/%d/%s`, which the client cannot
   turn into a tile address, so it asked for nothing at all. It is now `/%d/%d/%d`.
3. **The map epoch must stop churning.** `world_map.epoch()` is derived from the OSM area files, so
   every cell the ring expander downloads invalidates the client's cached area and it starts the
   visible map over again. With `"osm_expand": true` and `"osm_expand_ring_max": 0` the downloader
   added a cell every few seconds forever, the client re-requested the composition constantly, and
   the server was busy enough that RPC batches took 80–100 s (visible in `logs/server.log` as
   `RPC id=… methods=…` lines whose matching `status=` line follows a minute later). Freeze the
   expander once the home area is cached (`"osm_expand": false`); the epoch and the persisted tile
   cache then stay valid and both the map and the RPC latency recover.

Verified live on the phone (2026-10-27):

```
# 127.0.0.1:8445/map/settings now composes:
{2: 'http://127.0.0.1:8081/map/tiles', 3: '/%d/%d/%d'}
# and the phone requests and receives bundles from that origin:
logs/server.log: Plain-HTTP vector tile served: 35910 bytes path=/map/tiles/15/…?epoch=…&tileZoom=…
```

- `client/local-maps.js` rewrites the provider URL to
  `.../map/tiles/{z}/{x}/{y}?epoch=...&tileZoom=...` and decodes `LNLPMAP1`-marked bundles. It is
  included in the standalone APK build. If the Frida console does not show
  `Local map request: bundle zoom ...`, the interceptor did not install or the provider URL no longer
  matches its regex (`^(https?:\/\/[^/]+)\/map\/tiles\/?$`).
- Tile generation itself was always sound: for the test point at zoom 17 the server produced a
  1225-byte tile with 55 street features, and the Amsterdam cache test produced a 2193-byte tile from
  downloaded OSM cells. `data/maps/areas/` holds 121+ cells; `data/maps/osm.xml` holds the larger
  extract.

### Power Spots

- All Max RPC handlers are present in `Game.rpc`: `GET_STATION_INFO`, `GET_STATION_DETAILS`,
  `LOOT_STATION`, `STATION_POKEMON`, `RELEASE_STATIONED_POKEMON`, `GET_MP_SUMMARY`,
  `ENHANCE_BREAD_MOVE`, the lobby/battle methods and the walk quest.
- Hand placement works end to end: `tests/test_max_manual.py` places a spot, verifies the exact
  coordinates in `max_stations`, and asserts that `GET_MAP_OBJECTS` carries the station id in
  `ClientMapCellProto.stations`.
- The live database has **0 rows** in `max_stations` at the test time (no rotating spot materialized
  and no manual spot was successfully created).
- Rotating spots only exist where the reviewed candidate database has rows. The shipped
  `data/world/power-spots.sqlite3` covers the Netherlands only:
  lat `50.75…53.50`, lng `3.36…7.21`, 20,970 rows. The test location in Helsinki has **0** candidates,
  so `max_system.materialize()` correctly creates nothing there.
- Gigantamax Power Spots are blocked by the data: `BREAD_SHARED_SETTINGS.allowed_sourdough_pokemon`
  is empty in `data/static/max-reference.json` (0 allowlisted forms, while 19 local Gmax assets
  exist). `max_system.install` therefore rejects any mode-2 pool entry. Gmax can still be spawned as a
  wild/raid boss through the admin Max-form selector because that path does not use the station pool.
- Interaction requires proximity through `walking`/`social_locations` (`max_system.near`), not just an
  admin map click. A player moved only through the admin map may see a spot but fail to loot it with
  status 6 until a real walking fix arrives.
- The native Max feature flags are read from `BREAD_FEATURE_FLAGS`; `client/local-max.js` logs
  `Native Max enabled/stations-enabled/mp-enabled` and lifecycle events.

## Changes made in this pass

1. **Plain-HTTP tile bridge.** The admin server (default `127.0.0.1:8080`, already forwarded
   by `adb reverse`) proxies `GET /map/settings` and `GET /map/tiles/{z}/{x}/{y}?epoch=&tileZoom=`;
   the game server serves the same two paths on its own plain-HTTP origin
   (`world.map_tiles_port`, default `8081`) so the map does not depend on the admin panel being open.
   Both origins advertise a plain-HTTP `world.map_tiles_base`; the private TLS origin
   (`https://127.0.0.1:8445/map/tiles`) is never advertised to the map loader.
2. **Tile address template.** The provider template is `/%d/%d/%d`; the loader can expand it.
3. **Quiet epoch.** `world_map.epoch()` still follows the OSM data, but the ring expander is meant to
   be frozen on a live profile (`osm_expand: false`) once the home area is cached, and the generated
   bundles are persisted under `data/maps/tile-cache/<epoch>/…` so a restarted server or a second
   trainer is served from disk.
4. **Faster OSM fallback.** `world_map.roads()` used to parse every cached cell for every tile (20 s
   for the first tile). It now reads only the cells intersecting that tile, with a per-file parse
   cache: the Helsinki test tile takes 1.9 s cold and 0 ms warm.
5. **Diagnostics.** The game server logs the advertised tile base, every served tile path, and
   rejected tile URLs with the reason; the admin logs failed proxy requests too.
6. **Admin warning.** Every admin tab shows the known-issues banner.
7. **Manual Power Spots.** Admin placement, end/extend/delete, Gmax availability warning, released-pool
   editor and the OSM status card.
8. **Max form validation.** The stat catalog's default form (for example Pikachu `25:598`) is not in
   the Max reference, which only carries `species:0` groups. The admin Max form selector now offers only
   reference-backed forms, an omitted form resolves to the reference base form, and pool saves are
   rejected with a clear error when the species/form/mode cannot be served. `config/family.json` had
   been saved with `25:598`, which made `max_system.install` fail on the next server start; it is now
   `25:0`.

## What still needs work

1. Power Spots at non-Netherlands locations need candidate data or a deliberate manual spot. Options:
   generate reviewed candidates from the OSM cache for the player's cell, or define a documented
   fallback (for example, use hand-placed spots only outside candidate coverage). Do not silently
   fabricate candidates as if they were reviewed POIs.
2. Gigantamax spots need a Max reference whose `BREAD_SHARED_SETTINGS.allowed_sourdough_pokemon`
   contains the intended species/forms with `bread_mode=2`, and matching local assets.
3. Verify the native Max UI end to end: station appears on the map, `GET_STATION_INFO` returns
   `result=1`, looting succeeds, lobby/battle opens and the catch/reward flow completes. Watch for
   `Native Max exception:` lines from `client/local-max.js`.
4. If a future profile must advertise the TLS origin, expect no tile requests at all and check
   `logs/server.log` for the base URL printed by `Local map composition requested: … base=…` before
   looking anywhere else.

## Reproduction

```bash
# tile bytes and feature count for a point (works):
python - <<'PY'
import sys; sys.path.insert(0,'src')
from dittobuffer.world.world_map import configure, raw_tile, xy
configure(None, {'port': 8445})
lat, lng, z = 60.2654832, 25.0217887, 17
x, y = map(int, xy(lat, lng, z))
print(len(raw_tile(z, x, y)))
PY

# what the phone is told to fetch (must be plain HTTP, template /%d/%d/%d):
curl -sk https://127.0.0.1:8445/map/settings | strings | grep map/tiles

# candidate coverage (0 outside the Netherlands):
sqlite3 data/world/power-spots.sqlite3 \
  "select count(*) from candidates where lat between 60.2 and 60.3;"

# live Power Spots:
sqlite3 data/family-development.sqlite3 "select count(*) from max_stations;"
```

Files most relevant: `src/dittobuffer/world/world_map.py`, `src/dittobuffer/runtime/http.py`,
`src/dittobuffer/admin/admin.py`, `client/local-maps.js`, `client/local-max.js`,
`src/dittobuffer/gameplay/max_system.py`, `src/dittobuffer/gameplay/game.py`.
