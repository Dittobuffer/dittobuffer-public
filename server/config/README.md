# Configuration

Profiles are JSON documents resolved against the repository root.

| File | Use |
|---|---|
| `server.json` | Clean local profile: loopback listener, local accounts, generated server database, automatic OSM tiles. |
| `rocket.json` | Team GO Rocket line-ups and shadow rules. |
| `rocket-leaders.json`, `rocket-moves.json`, `rocket-teams.json` | Rocket reference data. |
| `max*.example.json` | Optional Max/Dynamax feature examples. |
| `pvp.example.json` | Optional PvP season example. |
| `special-research.example.json` | Optional research example. |
| `resources/` | Portable asset manifests; they contain metadata and hashes, not asset payloads. |

## Default profile

`server.json` uses:

```json
{
  "host": "127.0.0.1",
  "port": 8445,
  "network": {"mode": "loopback"},
  "authentication": {"mode": "local", "registration": "open"},
  "database": "data/server.sqlite3",
  "world": {
    "enabled": true,
    "osm_download": true,
    "map_tiles_base": "http://127.0.0.1:8081/map/tiles",
    "map_tiles_port": 8081
  }
}
```

OSM cells download automatically around the configured `osm_expand_focus`.
Change that coordinate in the admin before playing, or set it in your own
profile. The checked-in coordinate is a neutral city-center default, not a
user's home.

## Secrets

Do not add TLS keys, signing keystores, tokens, machine-specific paths,
player databases or world snapshots to tracked JSON. Put local profiles under
`.private/`, which is ignored. `config/tls/` and local public profiles are
intentionally absent from this branch.
