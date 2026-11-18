# Configuration

Profiles are JSON documents resolved against the repository root. Paths inside a profile are
relative to that root, never to the working directory.

| File | Use |
|---|---|
| `family.json` | The live profile: HTTPS host/port, family authentication, assets, world, weather, raids, social, Max, PvP. This is what `python -m dittobuffer` loads. |
| `development.json` | Local development against `data/development.sqlite3`, JSON world, no TLS. |
| `boot-test.json` | Throwaway profile used by the boot tests. |
| `public-profile.json` | Client build profile (origin + embedded CA path) written by provisioning; the APK builder reads it. |
| `max.example.json` | Dynamax/Gigantamax and Max battle block with every key. |
| `max-balance.example.json` | Max battle balance and reward tuning. |
| `max-research.example.json` | Max research/mission definitions. |
| `pvp.example.json` | GO Battle League seasons, sets, rewards and AI backfill. |
| `rocket.json` | Team GO Rocket line-ups, grunt pools and shadow rules. |
| `tls/` | Local certificate material for the loopback HTTPS listener. |

## World

`family.json` ships pointed at the JSON development world so a fresh clone boots with nothing
downloaded:

```json
"world": { "enabled": true, "json": "data/world/dev-world.json", ... }
```

`Game` prefers `json` over `manifest`. To serve an imported OSM snapshot instead, drop the `json`
key and set `"manifest": "data/world/current.json"` — see [`docs/ASSETS.md`](../docs/ASSETS.md).

## Map tiles

The in-game map loader only fetches vector tiles from a **plain-HTTP** origin: it does not use the
game RPC transport and does not trust the loopback TLS chain. Keep the profile pointed at the game
server's own plain-HTTP map port, which `START-SERVER.bat` forwards with `adb reverse`:

```json
"world": { "map_tiles_base": "http://127.0.0.1:8081/map/tiles", "map_tiles_port": 8081, ... }
```

Advertising `https://127.0.0.1:8445/map/tiles` produces no tile request at all — the client asks for
`/map/settings` forever and the map stays empty. Once the home area is cached, set
`"osm_expand": false` so the OSM ring expander stops changing the map epoch on every downloaded
cell (a moving epoch invalidates the client's cached area and the persisted tile cache).
See [`docs/KNOWN-ISSUES.md`](../docs/KNOWN-ISSUES.md).

## Secrets

TLS keys, the DPAPI credential blob and every machine-specific profile belong in `.private/`
(gitignored). Never commit a profile that contains a token, a password or an absolute path from your
own machine: `config/*.json` is tracked, and CI fails on a tracked file above 100 MiB or on a
tracked save database.
