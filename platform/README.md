# Dittobuffer Platform

Self-hosted rooms for a Dittobuffer server, plus a browser admin for the release
workspace. The platform uses the Python standard library only.

* **Signed rooms:** the platform signs every hosted room document with its RSA
  key; clients pin the key and reject anything else. Room names are first-come,
  first-served.
* **Joinable by name:** while HiveMQ is connected, the platform publishes a
  retained, signed document at `dittobuffer/v1/<room>/room`. That single
  document is what the phone discovers when the player types the room name.
* **HiveMQ bridge:** verified host state is relayed between the platform and
  `dittobuffer/v1/<room>/...`; the admin and HTTP snapshots keep working with
  MQTT off, but app room discovery needs the broker.
* **Admin interface:** light/dark responsive UI, server profile/file/database
  editing, logs, server start/stop and room management.
* **Platform-owned live world:** complete live-ops rules, automatic OSM POIs,
  a configurable wild-spawn simulator and a configurable raid simulator. All
  of it is published in the signed room document; no game server is required.
* **Manual and automatic:** spawns and raids can be generated on a timer or
  placed and removed by an admin from the World simulator page.
* **Quick Jump:** `Ctrl+K` or `Cmd+K` searches sections, actions and files.
* **Recovery:** reset the platform signature and host a new room from one card.

## Run

```bash
python3 run.py   # Linux / macOS
python run.py    # Windows
```

Open `http://127.0.0.1:8780`. The first interactive start asks for one **room
address** — the name players type, not a DNS domain. It then creates
`config.json`, `data/platform-key.json` and `data/platform.json` and hosts that
room automatically and starts publishing its signed retained document.

If the platform JSON or signing key is missing, the next start regenerates it
and recreates the saved room. If `config.json` is also missing, the platform
asks for a room address again.

The server workspace is found automatically in both supported layouts:
`release/platform` + `release/server`, and a source clone's `platform/` plus the
clone root. The path is stored relative to the platform, so the folder can be
moved without breaking the admin.

## Joining from the app

1. Keep the platform running and wait for **HiveMQ connected** in the admin.
2. In the app, open **Settings -> Dittobuffer Platform**.
3. Type the room name exactly as shown by the host, for example `fairview`.

Room names are names, not DNS domains: `Koppis Room!` becomes `koppis-room`.
There is no port forward, website or DNS record to configure.

If the app says **No room online**, check that the platform is running, HiveMQ is
connected, and the name matches. If it says the room is signed by a different
key, use **Platform & rooms -> Recovery** to create a fresh room and key.

Useful arguments:

```bash
python3 run.py --host 0.0.0.0 --port 8780
python3 run.py --room-address fairview --public-url https://example.test
python3 run.py --setup
```

## Signature and room recovery

Open **Platform & rooms -> Recovery**, enter the new room address, and press
**Reset signature and room**. The platform:

1. backs up `config.json`, `data/platform.json` and the current key under
   `data/recovery-backups/`;
2. generates a new RSA signing key;
3. clears every room binding;
4. creates and hosts the new room.

Existing app installs must rejoin because the pinned platform key changed.
The same operation is available through `POST /api/admin/recovery` and is
protected by the admin token when one is set.

## Full platform reset

```bash
python3 run.py --reset
```

This backs up and removes only the platform's `config.json`, signing key and
JSON state (`platform.json`, `pois.json`), then asks for a new room address.
It does not touch server profiles, player databases, world data or backups.
Add `--yes` for a non-interactive reset.

You can also delete `config.json` and `data/*.json` by hand and start the
platform again; it will regenerate the files and prompt for a room address.

## Docker

```bash
docker compose up --build
```

The platform is exposed on `http://127.0.0.1:8780`. State is stored in
`./docker-data`; `../server` is mounted as the editable workspace. Set
`ROOM_ADDRESS` and optionally `PUBLIC_URL` in `.env`:

```dotenv
ROOM_ADDRESS=fairview
PUBLIC_URL=http://127.0.0.1:8780
```

The image contains the platform only. Starting the game server from the admin
requires the server's Python dependencies in the same runtime; use a combined
release image or run the platform natively for server process control.

## Admin interface

The platform detects the server workspace automatically. In a release it is
`../server`; in a source clone it is `..`. The detected path is stored relative
to the platform folder, so no machine-specific absolute path is written or
printed.

The admin can:

* edit every key of the active server profile with validated forms or raw JSON;
* browse and edit UTF-8 files, inspect SQLite schemas/rows and run guarded SQL;
* inspect Game Master manifests and reference data, logs and server state;
* start, stop and restart the server when its runtime is available;
* host/delete rooms, edit room defaults and configure the MQTT/OSM importers;
* edit spawn percentages, weights, allow-lists, shiny rates, Shadow overrides,
  raid tiers, boss multipliers, events and Community Days;
* run the platform-owned world simulator, place manual spawns and raids, and
  clear only automatic rows when needed.

Admin routes live under `/api/admin/*`. When `admin_token` is set, send it as
`X-Platform-Token`. Backups are written to `data/admin-backups/`.

The app-facing endpoints (`/api/status`, `/api/rooms`, `/api/defaults`,
`/api/poi-import`, `/api/pois`) keep their JSON contracts. The service worker
caches only the static shell; `/api/*` is never cached.

## Automatic OSM POIs

POI importing is **on by default and needs no opt-in**. The platform starts
scanning with the process and writes results to `data/pois.json`; the admin can
stay closed. With no configured centre it uses central Amsterdam
(`52.3676, 4.9041`) so a fresh install still gets a world. Change the centre in
**Platform & rooms**, or in `config.json`:

```json
"poi_import": {
  "enabled": true,
  "lat": 51.5074,
  "lng": -0.1278,
  "radius_m": 1200,
  "interval_s": 60,
  "limit": 80,
  "gym_percent": 20
}
```

An explicitly saved empty centre pauses scans and reports that a centre is
needed. Manual POIs survive every refresh and the **Clear generated** action.

## World simulator

The **World simulator** page owns the platform's live world:

* wild spawns are generated from platform POIs on a configurable interval,
  with weighted, percent, hybrid or uniform selection, a species pool, level/IV
  ranges, radius, lifetime and active cap;
* raids are generated at gyms with configurable interval, cap, lifetime, egg
  time, boss pool, tier weights and the live-ops boss multipliers;
* manual Pokémon and raids can be created, selected by POI or gym, and deleted;
* automatic rows can be cleared without touching manual rows.

State is stored in `data/world.json`. Active spawns, raids and the simulator
rules are added to the signed room body on every publication. These admin
routes work without a server workspace:

```text
GET  /api/admin/world
POST /api/admin/world
GET  /api/admin/spawns
POST /api/admin/spawns
POST /api/admin/spawns/delete
POST /api/admin/spawns/clear
GET  /api/admin/raids
POST /api/admin/raids
POST /api/admin/raids/delete
POST /api/admin/raids/clear
POST /api/admin/world/tick
```

## Security model

| Threat | Defence |
| --- | --- |
| Forged room publishes | RSA signature verified against the pinned host key |
| Name takeover | First signed publish binds the name to the host key |
| Forged bootstrap | Signed by the platform key and pinned by the app |
| Replay | Monotonic version, timestamp window and nonce history |
| Broker outage | Admin and HTTP snapshots remain available; app room discovery waits for HiveMQ |

The admin API can edit the complete server workspace. Keep the platform bound
to loopback or a trusted private network and set `admin_token`.

## Tests

```bash
python -m unittest discover -s tests -v
```

## License

Apache License 2.0. See `LICENSE`.
