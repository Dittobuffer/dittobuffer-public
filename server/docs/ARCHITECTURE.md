# Architecture

Dittobuffer is a single Python package that supervises three cooperating pieces: the authoritative
game server, the local admin console, and the phone bridge (ADB reverse + gnirehtet). Everything is
loopback-only by default.

```
┌───────────────────────────── phone ─────────────────────────────┐
│ Pokemon GO 0.429.2 (patched)                                    │
│   libNianticLabsPlugin.so  ──►  Dittobuffer native runtime       │
│   Frida agent (optional)   ──►  instrumentation                  │
└───────────────┬─────────────────────────────────────────────────┘
                │  ADB reverse tcp:8445  (or gnirehtet VPN)
┌───────────────▼─────────────────────────────────────────────────┐
│ Dittobuffer process                                             │
│                                                                 │
│  runtime/http.py ── TLS ── /rpc, /get_auth_token, /map_query    │
│         │                  /map/tiles, /prelogin/*, /assets/*   │
│         ├── gameplay/game.py       authoritative transactions   │
│         ├── core/accounts.py       family accounts + sessions   │
│         ├── core/master.py         Game Master documents        │
│         ├── world/world_store.py   published world snapshot     │
│         └── core/database.py       SQLite (WAL)                 │
│                                                                 │
│  admin/admin.py ── HTTP ── /api/* + Material 3 panel (8080)     │
└─────────────────────────────────────────────────────────────────┘
```

## Request lifecycle

1. The client opens `POST /get_auth_token`. The launcher injects a family credential; the server
   exchanges it for a session token and trainer record.
2. Gameplay calls arrive as protobuf `RpcRequest` batches on `POST /rpc`. `game.rpc()` dispatches by
   method id, runs inside one SQLite transaction, and returns `RpcResponse` payloads.
3. Map queries and tiles are served from the world manifest (`data/world/current.json`), which indexes
   the Netherlands snapshot and its vector tiles.
4. Assets are served byte-exact with SHA-256 manifest validation (`core/asset_catalog.py`).
5. The admin panel reads the same SQLite file through a separate connection and writes spawns, POIs
   and raids; the game server picks them up on the next tick.

## Packages

| Package | Responsibility |
|---|---|
| `dittobuffer.cli` | Console, ASCII banner, slash commands, process supervision |
| `dittobuffer.core` | Accounts, database, TLS/network policy, Game Master, asset catalogues |
| `dittobuffer.gameplay` | Encounters, catching, gyms, raids, quests, social, items, progression |
| `dittobuffer.world` | World snapshot, vector tiles, POI photos, admin overlay, weather |
| `dittobuffer.admin` | Local admin JSON API and the Material 3 UI |
| `dittobuffer.patcher` | APK patching, Frida Gadget injection, signing |
| `dittobuffer.runtime` | Blocking HTTPS server |

`dittobuffer.paths` is the only module that knows where the repository lives; every other module
imports `ROOT`, `DATA`, `ASSETS`, `LOGS` from it.

## Data model

- `data/family-development.sqlite3` — players, pokemon, items, gyms, raids, social graph, research.
  WAL mode, one transaction per RPC.
- `data/world/` — immutable published snapshot plus `admin-pois.json`, the only writable overlay.
- `assets/` — Pokémon, avatar, Game Master and client-runtime bundles, all content-addressed.
- `config/family.json` — host, port, database path, world palette, TLS material, auth mode.

## Security posture

- The game listener binds `127.0.0.1` and requires TLS for the family profile.
- The admin panel is loopback-only and has **no authentication**; treat it as a developer tool.
- Family registration supports `open` and invite-only modes; passwords are stored as salted digests.
- The patched client refuses to talk to Niantic hosts (see `FamilyActivity` host allowlist).
- No telemetry, no outbound connections except the optional PokeAPI sprite lookup in the admin UI.

## Extending

Add a gameplay feature by:

1. Creating a module in `src/dittobuffer/gameplay/`.
2. Registering its RPC ids in `game.py`'s dispatch table.
3. Adding a table through the `SCHEMA` string pattern used by the module.
4. Adding a test under `tests/` that drives the transaction through `game.rpc()`.
