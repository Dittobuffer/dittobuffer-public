# DITTOBUFFER — The Complete Explanation, Runbook and Handoff

> **Audience:** a brand-new developer or a new AI coding session that has never seen this
> repository. Read this file top to bottom once and you will know what the project is, how the
> pieces fit together, how to run it, where every important file lives, what was recently changed,
> and which traps will waste your time.
>
> **Working copy described here:** `Dittobuffer-dirty`
> (called “the dirty folder” throughout). The sibling `Dittobuffer` folder is the clean/production
> copy: same source/config/tests, no multi-gigabyte world data and no live runtime database.
>
> **Project version:** 1.0.0. **Client target:** Pokémon GO 0.429.2 (arm64-v8a).
> **Current date of this snapshot:** 2026-10-24.
> **Status of this working copy:** live family server, 14 registered players, 74 stored Pokémon,
> 14 forts, 43 active spawns, 1 active raid at the time of writing.

---

## Table of contents

0. [If you are a new AI session, read this first](#0-if-you-are-a-new-ai-session-read-this-first)
1. [What this project is](#1-what-this-project-is)
2. [What is in this working copy right now](#2-what-is-in-this-working-copy-right-now)
3. [One-click start: `START-SERVER.bat`](#3-one-click-start-start-serverbat)
4. [High-level architecture](#4-high-level-architecture)
5. [Repository map](#5-repository-map)
6. [The runtime HTTP server](#6-the-runtime-http-server)
7. [Accounts, authentication and sessions](#7-accounts-authentication-and-sessions)
8. [The RPC protocol](#8-the-rpc-protocol)
9. [Gameplay subsystems](#9-gameplay-subsystems)
10. [The world subsystem](#10-the-world-subsystem)
11. [The database](#11-the-database)
12. [The Game Master](#12-the-game-master)
13. [The admin panel](#13-the-admin-panel)
14. [The Pokéstop loot system](#14-the-pokéstop-loot-system)
15. [Mega evolution and Mega raids](#15-mega-evolution-and-mega-raids)
16. [Master Ball](#16-master-ball)
17. [Level 50, XL Candy and power-ups](#17-level-50-xl-candy-and-power-ups)
18. [APK patching and the client](#18-apk-patching-and-the-client)
19. [Phone networking: ADB reverse and gnirehtet](#19-phone-networking-adb-reverse-and-gnirehtet)
20. [Configuration reference](#20-configuration-reference)
21. [The console and service manager](#21-the-console-and-service-manager)
22. [Development guide](#22-development-guide)
23. [Testing](#23-testing)
24. [Troubleshooting and runbook](#24-troubleshooting-and-runbook)
25. [Change log of this dirty tree](#25-change-log-of-this-dirty-tree)
26. [Security, privacy and legal posture](#26-security-privacy-and-legal-posture)
27. [Known limitations and risks](#27-known-limitations-and-risks)
28. [Glossary](#28-glossary)
- [Appendix A — Module inventory](#appendix-a--module-inventory)
- [Appendix B — RPC handler names](#appendix-b--rpc-handler-names)
- [Appendix C — Every protobuf `Method` enum](#appendix-c--every-protobuf-method-enum)
- [Appendix D — Every SQLite table](#appendix-d--every-sqlite-table)
- [Appendix E — Admin API endpoints](#appendix-e--admin-api-endpoints)
- [Appendix F — Configuration keys](#appendix-f--configuration-keys)
- [Appendix G — Ports, processes, logs and PID files](#appendix-g--ports-processes-logs-and-pid-files)
- [Appendix H — Useful SQL queries](#appendix-h--useful-sql-queries)
- [Appendix I — First ten minutes checklist](#appendix-i--first-ten-minutes-checklist)
- [Appendix J — The current loot table verbatim](#appendix-j--the-current-loot-table-verbatim)
- [Appendix K — Item catalog](#appendix-k--item-catalog)
- [Appendix L — Mega species and forms](#appendix-l--mega-species-and-forms)

---

## 0. If you are a new AI session, read this first

You do **not** need to re-derive the project from scratch. These are the load-bearing facts:

1. **This is a private, self-hosted Pokémon GO server** for one family/phone. It is not a
   production service and it is not affiliated with Niantic. It targets client **0.429.2**.
2. **It is one Python package**, `dittobuffer`, run from `src/` with `PYTHONPATH=src`. Python is
   **3.13.9** in `.venv`. It uses SQLite, protobuf, `cryptography`, and `s2sphere`.
3. **Three local processes** make up the live system:
   - game server: HTTPS on **127.0.0.1:8445**
   - admin panel: HTTP on **127.0.0.1:8080**, **no authentication**, loopback only
   - gnirehtet relay: **127.0.0.1:31416**, reverse-tethers the phone so it has internet
4. **The phone reaches the PC through `adb reverse`**, not through the LAN. The phone dials
   `https://127.0.0.1:8445`; ADB forwards that to the PC. gnirehtet is for general phone internet;
   the reverse rules work independently.
5. **The one-click launcher is `START-SERVER.bat`** in the repository root. It kills stale
   server/admin/gnirehtet processes, starts everything, recreates the tunnels, starts gnirehtet,
   relaunches `org.librenlp.pokemongo.dev/org.dittobuffer.launcher.FamilyActivity`, and opens the
   admin panel. Run it with the phone plugged in and USB debugging authorised.
6. **The game database is `data/family-development.sqlite3`** (about 450 MB, WAL mode, 98 tables,
   schema version 19). The world snapshot is `data/world/netherlands-261020-v2.sqlite3` (about
   7.06 GB). Never rewrite the published world snapshot from admin code; admin POIs live in
   `data/world/admin-pois.json`.
7. **The protocol is protobuf.** `/rpc` receives an `RpcRequest` batch; `Game.rpc()` dispatches each
   item by numeric method ID to a `rpc_<lowercase_name>` handler inside one SQLite transaction and
   returns `RpcResponse` payloads. There are **444** enum methods in `protocol/game.proto` and about
   **129** implemented handlers.
8. **RPCs are idempotent by receipt.** `rpc_receipts` is keyed by `(ticket, session_run, rpc_id)` and
   stores the request hash and serialized response. Reusing an RPC id with a different body is an
   error.
9. **The Game Master is generated**, not hand-edited: `data/static/gmts.binpb.gz` is served from
   `/prelogin/gmts.binpb.gz`, and `core/master.py` injects local templates (Master Ball, sizes,
   genders, level rewards, level-50 upgrade settings, raid items, PvP moves). If you change it, the
   client may keep its cached copy until the app is fully restarted.
10. **The Pokéstop loot table is data, not code**: `data/admin/stop-loot.json`, edited from the
    admin **Pokéstop loot** tab. The live game reads it on the next spin and caches by mtime.
11. **The admin backend must be restarted after backend code changes.** The UI is read from disk on
    every request, so a new HTML tab can appear while the old Python process still returns
    `{"error":"Unknown endpoint"}`. That exact mismatch happened in this project.
12. **A down game server looks like broken content.** When the server process is not listening,
    Pokéstops show blank images and cannot be spun; this is not a loot-table bug. Check
    `https://127.0.0.1:8445/health` first.
13. **Do not edit `data/world/netherlands-261020-v2.sqlite3`** for gameplay changes. Use the admin
    overlay or the game DB. The snapshot is immutable, content-addressed, ODbL-licensed OSM data.
14. **The code style is intentionally dense**: one-line functions, semicolons, short names. Match the
    surrounding file rather than reformatting it. Tests are the authority; `pytest` is the usual
    verification.
15. **One test is a known pre-existing failure**: `tests/test_shiny_palette.py` imports
    `scripts/retarget-same-species-animation.py`, which is absent in this tree. It is unrelated to
    gameplay. Report it as a limitation, not as a regression you caused.

If you only need to do one thing, run:

```bat
cd Dittobuffer-dirty
START-SERVER.bat
```

Then check:

```bash
curl -k https://127.0.0.1:8445/health
curl -s http://127.0.0.1:8080/api/status
```

---

## 1. What this project is

Dittobuffer is a **private, self-hosted Pokémon GO family server**. It reimplements enough of the
0.429.2 client/server protocol to let a patched client log in, see a map, catch Pokémon, spin
Pokéstops, fight gyms, do raids, hatch eggs, complete research, manage a buddy, trade, and use an
inventory — all against a local SQLite database and a local world snapshot.

It is delivered as one repository containing:

| Piece | What it does |
|---|---|
| **Authoritative game server** | TLS protobuf backend on `https://127.0.0.1:8445` |
| **Game Master generator** | Builds `gmts.binpb.gz` and settings from `assets/game-master/` plus local rules |
| **World** | 7.06 GB published Netherlands OSM snapshot, vector map tiles, POI photos, weather |
| **Admin console** | Material 3 panel on `http://127.0.0.1:8080` for spawns, gyms, stops, raids, players, rewards and loot |
| **Console** | `python -m dittobuffer` with an ASCII banner and slash commands |
| **APK patcher** | Frida Gadget injection, signing, and a full standalone family build |
| **Client runtime** | Frida agent bundles, a native plugin, a launcher and local JS modules |
| **gnirehtet** | Reverse-tethered phone networking in `tools/gnirehtet/` |
| **Docker** | `docker compose up` for server + admin, persistent volumes |

### What it is not

- It is **not** a Niantic service, and it contains no Pokémon GO server code from Niantic.
- It is **not** an official product. Running a modified client violates the Pokémon GO Terms of
  Service and can get an account banned. Use accounts you are willing to lose.
- It is **not** a public server. The game listener binds loopback; the admin panel is unauthenticated
  and must never be exposed to a LAN or the internet.
- It is **not** a complete reproduction of every live-game feature. See
  [Known limitations](#27-known-limitations-and-risks).
- It is **not** legal advice. The code is MIT; the game assets and client are not yours to
  redistribute.

### Design principles visible in the code

1. **Loopback only.** `host` is `127.0.0.1`; TLS is required for the family profile; the admin
   panel is a developer tool with no auth.
2. **Server-authoritative.** Clients send intentions; damage, catch success, rewards, raid
   participation, trades and purchases are decided server-side inside one transaction.
3. **Verified assets only.** Asset manifests carry byte counts and SHA-256; files are served only if
   they match. The world snapshot is content-addressed and never mutated by gameplay.
4. **Explicit local balance.** Raid HP/CP, loot weights, catch probability and Mega rewards are
   local configuration, not claims about official values. Some config strings say so explicitly.
5. **Proof-oriented tests.** Tests exercise wire bytes, TLS, transactions and failure paths rather
   than only testing helper functions.
6. **No telemetry.** The only outbound calls are optional: PokeAPI species names/sprites in the
   admin panel, OpenStreetMap tiles in the browser, and Open-Meteo weather if enabled.

---

## 2. What is in this working copy right now

### Paths

| Path | Meaning |
|---|---|
| `Dittobuffer-dirty` | Live development copy; world data, DB, logs, builds |
| `Dittobuffer` | Clean production copy; source/config/tests, no world/DB |
| `src/dittobuffer/` | The Python package (69 files, 11,069 lines) |
| `data/family-development.sqlite3` | Live game database (450,596,864 bytes) |
| `data/world/netherlands-261020-v2.sqlite3` | Published world (7,056,125,952 bytes) |
| `data/admin/stop-loot.json` | Live editable Pokéstop loot table |
| `config/family.json` | Live profile: family auth, TLS, world, raids, level 50 |
| `config/tls/` | `server.pem` and `server-key.pem` for the family profile |
| `tools/gnirehtet/` | `gnirehtet.exe`, `gnirehtet.apk`, `gnirehtet-run.cmd` |
| `logs/` | `server.log`, `server.err.log`, `admin.log`, `gnirehtet.log`, test logs |
| `run/` | `server.pid`, `admin.pid` (managed by the console) |

### Live counts at snapshot time

| Fact | Value |
|---|---|
| Registered players/accounts | 14 |
| Stored Pokémon | 74 |
| Forts (gyms + stops) | 14 |
| Active spawns | 43 |
| Admin-created spawns | 35 |
| Active raids | 1 |
| Database tables | 98 (schema versions 1–19) |
| Protobuf `Method` enum values | 444 |
| Implemented RPC handlers | 129 |
| Protocol item enum values | 291 (290 usable catalog entries) |
| Mega-capable species | 56 species, 59 forms |
| World photos on disk | 10 (the rest use a verified placeholder) |
| Test functions in the tree | 353 |

### Recent feature work present in this tree

The following changes are already in the dirty folder (and were mirrored into the clean folder in
the previous session). They are described in detail later:

- **Editable Pokéstop loot table** with guaranteed drops, weighted drops, per-row level/count bounds,
  and independent Premium/Remote Raid Pass chances.
- **Arbitrary item rewards** in the admin panel: all 290 protocol items, multiple item rows, XL Candy,
  Pokémon/bag storage, and Mega Energy.
- **Mega raids** reward the Mega-evolved boss encounter, grant Mega Energy by completion speed, and
  repair legacy 0CP raid cards.
- **Master Ball** as a real item template and a legal catch ball, plus default loot weight.
- **Level 50 schedule** and XL Candy power-up costs.
- **One-click launcher** `START-SERVER.bat` (the file this document accompanies).

### Dirty vs clean

The dirty folder is the one to run: it has the 7 GB world snapshot, the live 450 MB database, build
artifacts, logs and the phone tooling. The clean folder is a source/config/test copy meant for
production packaging; it does not include the world snapshot or live DB. If you copy code between
them, copy `src/`, `config/`, `tests/`, `scripts/`, `client/` and `docs/` — never copy the world or
runtime database over a published deployment.

---

## 3. One-click start: `START-SERVER.bat`

### What it is for

Double-click it after connecting the phone over USB. It is deliberately destructive only to
Dittobuffer-related processes: it stops old server/admin/gnirehtet instances, then starts a clean
set. It does not touch unrelated Python programs or ports, but it does restart the local ADB server
and clear the connected device’s reverse rules.

### Preconditions

- Windows, with this repository at the path above (or any path).
- `.venv\Scripts\python.exe` exists, or `python` on `PATH`, with `requirements.txt` installed.
- `config\family.json` exists.
- `adb.exe` on `PATH`, or at `%LOCALAPPDATA%\Android\Sdk\platform-tools`,
  `%USERPROFILE%\adb\platform-tools`, or `C:\adb\platform-tools`.
- Phone connected, **Developer options → USB debugging** enabled, RSA prompt accepted.
- The phone has the patched client installed as `org.librenlp.pokemongo.dev`.

### What the script does, step by step

1. Sets UTF-8 console, title, working directory to the repo, `PYTHONPATH` to `src`.
2. Validates config and source presence.
3. Finds Python (`.venv` first) and verifies `google.protobuf`, `cryptography` and `s2sphere`.
4. Finds `adb`, adding common SDK directories to `PATH`.
5. **Stops old things**:
   - `taskkill /F /IM gnirehtet.exe`
   - force-stops `com.genymobile.gnirehtet` on the phone (tears down the old VPN)
   - kills only `python*.exe` processes whose command line contains `dittobuffer`
   - kills whatever still listens on 8445, 8080 or 31416
   - deletes stale `run\*.pid`
   - `adb reverse --remove-all`, `adb kill-server`, `adb start-server`
6. Starts the game server with `python -m dittobuffer --no-autostart /start server` and waits for
   port 8445.
7. Starts the admin panel the same way and waits for port 8080.
8. Waits up to 45 seconds for `adb get-state` to return `device`.
9. Creates `adb reverse tcp:8445 tcp:8445` and `adb reverse tcp:8080 tcp:8080`.
10. Starts gnirehtet via the service manager and waits up to 15 seconds for port 31416.
11. Force-stops and relaunches the game activity.
12. Prints a summary, lists reverse tunnels, opens `http://127.0.0.1:8080`, and waits for a keypress.
    Services are detached and keep running after the window closes.

### If the phone is not connected

The server and admin still start. The script prints a checklist (cable, developer options, RSA
prompt) and exits without starting gnirehtet or the tunnels. Connect the phone and run it again. You
do not need to stop the server first; the script is idempotent.

### Manual equivalent

```bat
cd /d Dittobuffer-dirty
set PYTHONPATH=%CD%\src
.venv\Scripts\python.exe -m dittobuffer --no-autostart /start server
.venv\Scripts\python.exe -m dittobuffer --no-autostart /start admin
adb reverse tcp:8445 tcp:8445
adb reverse tcp:8080 tcp:8080
.venv\Scripts\python.exe -m dittobuffer --no-autostart /start gnirehtet
adb shell am force-stop org.librenlp.pokemongo.dev
adb shell am start -n org.librenlp.pokemongo.dev/org.dittobuffer.launcher.FamilyActivity
```

Stop:

```bat
.venv\Scripts\python.exe -m dittobuffer /stop all
```

or kill the three services via Task Manager if the console is unavailable.

### Why the script kills by port as well as by name

A Python process can be orphaned, renamed in the command line, or started by a different interpreter
(venv redirector vs base Python). Port ownership is the ground truth: if 8445, 8080 or 31416 is
listening, something is holding the service. The script kills that PID after the name-based pass.

### Why it kills gnirehtet first

gnirehtet runs an Android VPN. Killing only the Windows relay can leave the phone VPN up, which can
confuse the next start. The script force-stops `com.genymobile.gnirehtet` while ADB is still alive,
then restarts the ADB server. On the next run, gnirehtet reinstalls/activates its VPN cleanly.

### What to look at after it finishes

| Check | Command |
|---|---|
| Server health | `curl -k https://127.0.0.1:8445/health` |
| Admin status | `curl -s http://127.0.0.1:8080/api/status` |
| Phone visible | `adb devices` |
| Tunnels | `adb reverse --list` |
| Relay port | `netstat -ano \| findstr :31416` |
| Server log tail | `powershell -c "Get-Content logs\server.log -Tail 40"` |
| Admin log tail | `powershell -c "Get-Content logs\admin.log -Tail 40"` |
| Relay log tail | `powershell -c "Get-Content logs\gnirehtet.log -Tail 40"` |

---

## 4. High-level architecture

```
┌────────────────────────────── phone ──────────────────────────────┐
│ Pokémon GO 0.429.2 (patched)                                      │
│   libNianticLabsPlugin.so ──► Dittobuffer native runtime           │
│   Frida agent (optional)  ──► instrumentation / local UI overrides │
└───────────────┬───────────────────────────────────────────────────┘
                │ adb reverse tcp:8445  (phone 127.0.0.1 → PC 127.0.0.1)
                │ adb reverse tcp:8080
                │ gnirehtet VPN (general internet)
┌───────────────▼───────────────────────────────────────────────────┐
│ Dittobuffer processes                                             │
│                                                                   │
│  runtime/http.py ── TLS ── /rpc, /get_auth_token, /map_query      │
│         │                  /map/tiles, /prelogin/*, /assets/*     │
│         ├── gameplay/game.py       authoritative transactions     │
│         ├── core/accounts.py       family accounts + sessions     │
│         ├── core/master.py         Game Master documents          │
│         ├── world/world_store.py   published world snapshot       │
│         └── core/database.py       SQLite (WAL)                   │
│                                                                   │
│  admin/admin.py ── HTTP ── /api/* + Material 3 UI (8080)          │
│  tools/gnirehtet/gnirehtet.exe ── relay 31416 + Android VPN       │
└───────────────────────────────────────────────────────────────────┘
```

### Process table

| Process | Command | Port | Log | PID file | Lifetime |
|---|---|---|---|---|---|
| Game server | `python -m dittobuffer.runtime.http --config config/family.json --port 8445` | 8445/TLS | `logs/server.log` | `run/server.pid` | detached |
| Admin | `python -m dittobuffer.admin.admin --config config/family.json --port 8080` | 8080 | `logs/admin.log` | `run/admin.pid` | detached |
| Relay | `tools/gnirehtet/gnirehtet.exe run` | 31416 | `logs/gnirehtet.log` | — | detached |
| Console | `python -m dittobuffer` | — | stdout | — | interactive |

On Windows, a venv `python.exe` may spawn the base interpreter as a child with the same command line.
That is why you may see two `python.exe` entries for one service; they are a launcher/worker pair.
The service manager's PID file points at the launcher, and `taskkill /T` handles the tree. The
one-click script kills both by command-line matching.

### Request lifecycle

1. The patched launcher obtains a family credential and posts it to `/get_auth_token`.
2. The server validates the credential, creates a session, and returns an auth ticket.
3. Gameplay calls arrive as protobuf `RpcRequest` batches on `POST /rpc`, with `Authorization: Bearer`
   and optional `x-dittobuffer-*` location headers.
4. `Game.rpc()` checks the family session, opens one SQLite transaction, expires timed state (Mega,
   gym battles, raids), and dispatches each item by method ID.
5. The response is serialized and stored in `rpc_receipts`; retries return the stored response.
6. Map queries and tiles come from the world manifest; assets are served byte-exact with SHA-256
   validation; prelogin documents come from `core/master.py`.
7. The admin panel opens its own SQLite connection, writes spawns/POIs/raids/loot/rewards, and the
   game server sees them on the next query/spin.

### Storage layers

| Layer | Location | Mutability |
|---|---|---|
| Game state | `data/family-development.sqlite3` | read/write, WAL |
| Published world | `data/world/netherlands-261020-v2.sqlite3` | immutable, content-addressed |
| Admin world overlay | `data/world/admin-pois.json` | writable, merged over world |
| Weather cache | `data/world/weather.sqlite3` | writable cache |
| Static Game Master | `data/static/gmts.binpb.gz`, `settings.binpb.gz` | generated, served read-only |
| Loot | `data/admin/stop-loot.json` | writable |
| Logs/PIDs | `logs/`, `run/` | runtime |

### Security boundaries

- The game server binds `127.0.0.1` and terminates TLS with a certificate containing an IP SAN for
  `127.0.0.1`. `peer_allowed()` optionally restricts by `network.allowed_networks`; an empty list
  fails closed when configured.
- The admin panel has **no authentication**. It is a local developer tool. Never change its bind
  address to `0.0.0.0`.
- Accounts use Argon2id password hashes, opaque bearer tokens, hashed session storage, rate limits,
  and audit rows. See section 7.
- The patched client refuses Niantic hosts via the launcher allowlist. See section 18.

---

## 5. Repository map

### Top level

| Path | Purpose |
|---|---|
| `src/dittobuffer/` | Python package: CLI, core, gameplay, world, admin, patcher, runtime |
| `protocol/` | `game.proto`, `game_pb2.py`, `devrpc.proto`, `opaque.py` |
| `assets/` | Pokémon, avatar, Game Master, client-runtime, world source assets |
| `data/` | Live DB, static GM, world snapshot, admin loot/names, maps |
| `client/` | Frida agent JS, native plugin (Rust), launchers, inspect scripts |
| `apk/` | Official 0.429.2 base APK and arm64 config split (build inputs) |
| `tools/` | gnirehtet, Frida Gadget, apktool, Android build-tools, transfer runtime |
| `config/` | `family.json`, `development.json`, TLS material, profiles |
| `docs/` | Architecture, phone setup, admin panel, patcher, troubleshooting, assets |
| `scripts/` | Standalone build and TLS certificate helpers |
| `tests/` | 353 test functions across unit, HTTP, TLS and gameplay tests |
| `builds/` | Generated manifests, asset catalogs, temporary test dirs |
| `logs/` | Runtime logs |
| `run/` | PID files |
| `.venv/` | Python 3.13.9 environment |
| `.tools/` | Scratch patch/verify scripts from development (not part of runtime) |
| `.private/` | Private TLS/test material; do not commit |
| `.staging/` | Scratch staging |
| `START-SERVER.bat` | One-click launcher (this document’s companion) |
| `README.md` | User-facing overview and quick start |
| `pyproject.toml` | Package metadata, dependencies, pytest/ruff config |
| `Makefile` | `install`, `dev`, `test`, `lint`, `run`, `admin`, `docker`, `clean` |
| `Dockerfile`, `docker-compose.yml` | Container deployment of server + admin |

### `src/dittobuffer` packages

| Package | Files | Responsibility |
|---|---|---|
| `cli` | `ascii.py`, `console.py`, `services.py` | Banner, slash commands, detached process supervision, ADB helpers |
| `core` | `accounts.py`, `asset_catalog.py`, `client_resources.py`, `database.py`, `family_network.py`, `management.py`, `master.py`, `preferences.py` | Identity, DB, TLS policy, GM, assets, ownership rules |
| `gameplay` | 40 modules | Encounters, catch, inventory, gyms, raids, Mega, eggs, research, buddy, social, trading, shop, medals, avatar, progression |
| `world` | 9 modules | World store, admin overlay, vector tiles, POI photos, weather, loot rewards, JSON dev world |
| `admin` | `admin.py`, `ui/admin_ui.html` | Local JSON API and Material 3 panel |
| `patcher` | `frida_patch.py`, `patch.py` | APK decode/inject/sign; standalone family build |
| `runtime` | `http.py` | Blocking HTTPS server and route handling |
| root | `paths.py`, `__main__.py`, `__init__.py` | Path anchors, console entry point |

`paths.py` is the only module that decides where the repository is. Every other module imports
`ROOT`, `DATA`, `ASSETS`, `CONFIG`, `LOGS`, `BUILDS`, `PROTOCOL` from it. It also inserts `protocol/`
into `sys.path` so generated protobuf modules can import each other by top-level name.

---

## 6. The runtime HTTP server

`src/dittobuffer/runtime/http.py` is a `ThreadingHTTPServer` with a request queue of 128 (the client
opens dozens of parallel asset connections; the default backlog of 5 drops TLS handshakes under
load) and daemon threads. It is wrapped in TLS when `network.mode == "tls"`; otherwise plain HTTP for
development.

### GET routes

| Path | Response | Notes |
|---|---|---|
| `/health` | JSON status | `status`, `target` (`0.429.2`), `database`, `profile`, asset booleans, auth mode |
| `/world/health` | JSON | World enabled, epoch, weather, snapshot name |
| `/map/settings` | protobuf | Lightship map composition settings |
| `/map/tiles/{z}/{x}/{y}` | protobuf | Vector tile; query params `epoch`, `tileZoom` |
| `/prelogin/settings.binpb.gz` | gzip protobuf | Settings document, timestamp 0 for reproducibility |
| `/prelogin/gmts.binpb.gz` | gzip protobuf | Game Master templates |
| `/version`, `/plfe/version` | protobuf | `ClientVersionProto(min_version="0.429.2")` |
| `/assets/client/<name>` | bytes | Opt-in exact runtime resources; 404 unless registry enabled and hash matches |
| `/assets/pokemon/...`, `/assets/avatar/...` | image/bundle | SHA-verified local assets, ETag, immutable cache for bundles |
| `/assets/dittobuffer-stop.png` | PNG | Local Pokéstop image |
| `/assets/dittobuffer-original-background.bundle` | bundle | Only if `pokemon_backgrounds.enabled` |
| `/world/photos/<64hex>.jpg` | JPEG | Verified POI photo; filename must equal SHA-256 |
| `/world/photo-unavailable.png` | PNG | Placeholder |
| `/world/placeholders/<sha>.png` | PNG | Content-addressed placeholder |
| anything else | 404 | Logged as “Unknown local GET path” |

### POST routes

| Path | Body | Response | Notes |
|---|---|---|---|
| `/accounts/register` | JSON username/password/trainer/invitation | JSON account | Family mode only; 8 KB limit |
| `/accounts/login` | JSON username/password/device_label | JSON token | Family mode only; no-store |
| `/accounts/logout` | JSON all_sessions | JSON | Revokes bearer |
| `/get_auth_token` | protobuf `AuthRequest` | protobuf `AuthResponse` | Exchanges a family credential for an RPC ticket |
| `/rpc` | protobuf `RpcRequest` | protobuf `RpcResponse` | Main gameplay entry; 8 MB limit |
| `/map_query` | protobuf `MapQueryRequest` | `MapQueryResponse(status=0)` | Callback delivery is not implemented |
| `/raid/api/get-state` | protobuf/HTTP | raid state | Nebula HTTP adapter; 250 ms pacing; GET and POST |
| `/raid/api/send-player-event` | protobuf | raid event | Adapter over the authoritative PvE clock |
| `/raid/api/get-time` | — | time | Adapter |

### Behavior and limits

- `peer_allowed()` runs before every request. If `network.allowed_networks` is present, only those
  peers are accepted; an empty list denies all.
- Bodies are limited to 8 KB for `/accounts/*` and 8 MB otherwise; oversized requests get 413.
- `/accounts/*` and `/get_auth_token` send `Cache-Control: no-store`.
- Account errors return a generic 401 JSON body; detailed errors are intentionally suppressed to avoid
  disclosing whether an account exists. Credentials/tokens are never logged.
- RPC logging includes method IDs, names and payload sizes, not bodies (unless a fixture trace is
  explicitly enabled for a temp build DB).
- `RpcError`, protobuf `DecodeError` and `ValueError` become 400; unexpected exceptions become 500 and
  are logged with a traceback.
- Unknown GET/POST paths return 404 and are logged as unknown local paths.
- The server prints “Listening on https://127.0.0.1:8445 (open family accounts)” at startup.

### TLS

`core/family_network.py` builds the SSL context from `network.certificate` and `network.private_key`
(paths resolved under the repo). The family profile uses `config/tls/server.pem` and
`config/tls/server-key.pem`. `scripts/create-family-tls.py` issues a private CA and a leaf certificate
with an **IP SAN** for the exact host the client dials (`127.0.0.1` for ADB reverse). It refuses
non-private IPs and refuses to overwrite an existing directory; it does not modify OS trust.

---

## 7. Accounts, authentication and sessions

`src/dittobuffer/core/accounts.py` implements family identity on top of the existing player storage.
It is enabled only when `authentication.mode == "family"`. Legacy localhost development databases are
never migrated automatically.

### Tables

| Table | Purpose |
|---|---|
| `family_accounts` | `id`, unique `username`, unique `player_id`, Argon2id `password_hash`, capacities |
| `family_invites` | SHA-256 digest of invitation token, expiry, consumed/revoked state |
| `family_sessions` | SHA-256 digest of bearer token, account, kind (`rpc`/credential), parent, expiry, revocation |
| `family_auth_limits` | Per-account and per-peer attempt counters in a 5-minute window |
| `family_audit` | Last 10,000 registration/login/invite/logout events |
| `family_battle_sessions` | Binds battle generation to an authenticated session |

### Rules

- **Usernames**: `[A-Za-z0-9][A-Za-z0-9_.-]{2,31}`, lowercased.
- **Passwords**: 12–128 characters, hashed with Argon2id (16-byte salt, 32-byte output, 2 iterations,
  1 lane, 19,456 KiB memory). A dummy hash is computed for unknown usernames so timing does not
  disclose account existence.
- **Trainer names**: 2–20 characters, letters/digits/underscore/space/hyphen, NFC-normalised, must
  contain non-space.
- **Registration** is `open` in `config/family.json`; set `authentication.registration` to `invite`
  to require an invitation. Invitations are generated by `Accounts.invite()` and stored as digests.
- **Rate limits**: 12 attempts per account digest and 40 per peer digest per 5 minutes. Failed auth
  still commits the limit counter.
- **Credentials vs RPC sessions**: a login credential has a longer lifetime
  (`credential_seconds`, default 604,800 s) and is exchanged at `/get_auth_token` for a short RPC
  ticket (`session_seconds`, default 3,600 s). The RPC ticket is stored in `sessions` as
  `family:<sha256>`.
- **No raw tokens at rest**: only SHA-256 digests of bearer tokens are stored. Tokens are returned
  once at issuance.
- **Logout** revokes the current session, or all sessions when `all_sessions` is true.
- **No-store headers** on account endpoints and `/get_auth_token`.
- The admin panel can list `family_accounts` and capacities, but never passwords or tokens.

### Auth flow on the wire

```
POST /accounts/register   {username, password, trainer_name, invitation}
POST /accounts/login      {username, password, device_label}  -> {token, expires_ms}
POST /get_auth_token      AuthRequest{auth_id, token}          -> AuthResponse{ticket}
POST /rpc                 Authorization: Bearer <ticket>
```

The launcher stores the credential/ticket; the server maps it to a player. If the family session is
invalid or expired, `/rpc` returns 400 with “Family session invalid or expired”. A missing local
session returns “A local session is required”.

---

## 8. The RPC protocol

### Messages

- `protocol/game.proto` is the supplied 0.429.2 schema. `protocol/game_pb2.py` is generated from it.
- `RpcRequest { rpc_id, requests[] { method, body } }`
- `RpcResponse { rpc_id, status, payloads[] { method, payload } }`
- `AuthRequest` / `AuthResponse` for `/get_auth_token`.
- `MapQueryRequest` / `MapQueryResponse` for `/map_query`.
- `devrpc.proto` is the developer/tooling schema; `opaque.py` preserves opaque model bytes.

### Dispatch

`Game.methods` is built from the `Method` enum: `{number: name_without_Method_}`. The handler name is
`rpc_` + lowercased method name. For example:

| Method | ID | Handler |
|---|---|---|
| `GET_PLAYER` | 2 | `rpc_get_player` |
| `GET_HOLOHOLO_INVENTORY` | 4 | `rpc_get_holoholo_inventory` |
| `DOWNLOAD_SETTINGS` | 5 | `rpc_download_settings` |
| `GET_MAP_OBJECTS` | 106 | `rpc_get_map_objects` |
| `ENCOUNTER` | 102 | `rpc_encounter` |
| `CATCH_POKEMON` | 103 | `rpc_catch_pokemon` |
| `FORT_SEARCH` | 104 | `rpc_fort_search` |
| `GET_PLAYER_PROFILE` | 108 | `rpc_get_player_profile` |
| `GET_RAID_DETAILS` | 110 | `rpc_get_raid_details` |

There are **444** enum values and **129** handlers. Unsupported methods raise
`RpcError("Unsupported method <id> <NAME>")`; the whole HTTP request becomes 400. The client sometimes
sends `610001`/`UNKNOWN_610001`; that is expected to fail in this build.

### Transaction and idempotency

`Game.rpc()`:

1. Computes SHA-256 of the serialized request.
2. Opens one `Database.transaction()`.
3. Resolves the family session (if enabled) to a player, hashing the bearer into `family:<sha256>`.
4. Looks up `rpc_receipts` by `(ticket, session_run, rpc_id)`.
   - Same request hash → returns the stored serialized response.
   - Different hash → `RpcError("RPC identifier reused with a different body")`.
5. Expires timed state: Mega evolutions, gym battles, raids, retired world entities.
6. Iterates the batch:
   - Resolves the handler; missing → error.
   - Social method IDs (`950–974`, `1909–1913`, `2600–2608`) require `social.enabled`.
   - Reads location headers and updates walking/egg sampling once.
   - Runs the handler, then records level-up transitions for stats.
7. Inserts the receipt with the response, returns.

Because it is one transaction, a failure in any item rolls back the whole batch. This is deliberate:
the client batches independent calls, but the server treats them atomically.

### Location headers

The patched client (or launcher) sends:

| Header | Meaning |
|---|---|
| `x-dittobuffer-lat` | latitude |
| `x-dittobuffer-lng` | longitude |
| `x-dittobuffer-acc` | accuracy in metres |
| `x-dittobuffer-fix-time` | fix timestamp |
| `x-dittobuffer-provider` | provider label |
| `x-dittobuffer-source` | location source |

`Game.rpc()` passes these to `friends.observe()` and `eggs.sample()` once per batch. The admin panel
can also read the phone’s fused location via `adb shell dumpsys location`.

### Error behavior

- Invalid protobuf → 400, logged.
- Unknown method → 400, `Unsupported method`.
- Out-of-range encounter, wrong owner, full storage, no balls, invalid trade, etc. → `RpcError` with a
  short human-readable message; the client sees a failed RPC.
- Catches, purchases, trades and raid events are idempotent by their own tables as well; the receipt
  layer protects retries at the transport level.

### Method categories

Rather than listing every one here, the full handler list is in
[Appendix B](#appendix-b--rpc-handler-names) and every enum value is in
[Appendix C](#appendix-c--every-protobuf-method-enum). The implemented surface covers:

- **Player/inventory**: get player, profile, inventory, settings, server time, player day, warnings,
  tutorial completion, preferences, tags, medals, journal.
- **World**: map objects, encounter, catch, fort details/search, spawns, raids.
- **Gyms**: info, deploy, feed, start session, attack, badges, notifications.
- **Raids**: details, lobby, battle, attack, complete, encounter, passes, free ticket, mega.
- **Items**: potion, revive, bulk heal, recycle, berry, incubator, TM reroll, rare candy.
- **Progression**: evolve, power up, level rewards, unlock second charge move, Mega level-up.
- **Social**: friends, gifts, trading, remote trading, postcards, snapshots, social proxy.
- **Economy**: shop SKUs, purchase, balances.
- **Research**: quests, stamps, story stages, dialogs, encounters.
- **Eggs/buddy**: inventory, hatch, incubators, buddy stats/map/feed/pet/gift/history.
- **Cosmetics**: avatar store/appearance, neutral avatar, wardrobe, costumes, backgrounds.

---

## 9. Gameplay subsystems

The gameplay package is split by domain. Each module owns its tables, validation and RPC handlers;
`game.py` is the dispatcher and the transaction boundary. If you add a feature, add a module and a
handler rather than growing `game.py`.

### 9.1 Players, progression and level 50

| Concern | Where |
|---|---|
| Player row, session binding, capacity | `gameplay/game.py`, `core/database.py` |
| Level schedule and bounds | `gameplay/progression.py` |
| XP/stardust/catch rewards | `gameplay/game.py` |
| Level-up reward bundles | `gameplay/trainer_rewards.py`, `config.level_up_rewards` |
| Medals | `gameplay/medals.py` |
| Journal | `gameplay/journal.py` |
| Preferences | `core/preferences.py` |

Key facts:

- `players` stores `id`, `name`, `xp`, `stardust`, `revision`, `captures`, `throws`, `stops` and
  `created_ms`. Km walked and hatches come from the joined `walking` row; team lives in
  `trainer_teams`; appearance/cosmetics live in their own tables.
- Level is derived from XP, never stored as the source of truth. `progression.bounds(config, xp)`
  returns `(level, current_threshold, next_threshold)`.
- `config.level_requirements` is a list of **individual XP costs** to reach each level. It starts with
  `0` and currently has **50 entries**, so the cap is 50. The last costs are 130,000 / 150,000 /
  175,000.
- `progression.cap(config)` returns `len(requirements)` in local mode; `pinned-modern` mode reads
  `trainer-reference.json` instead.
- Level-up rewards are configured per level in `level_up_rewards` and claimed once per level via
  `level_reward_claims`. The Game Master also gets `LEVEL_UP_REWARDS_LEVEL_<n>` templates because the
  level-up GUI reads them independently of the reward RPC.
- `players.revision` increments on mutations; the client uses it for inventory/player sync.
- `walking` stores `total_m`, `hatches`, last fix, accuracy, provider and timestamps. Distance is
  sampled from location headers and feeds eggs/buddy.
- `player_stats_history` records before/after snapshots when a level boundary is crossed, so the
  journal can show level-ups.
- Level 50 details are in section 17.

### 9.2 Spawns, encounters and catching

#### Spawn storage

| Table | Meaning |
|---|---|
| `spawns` | `id`, `spawnpoint` (S2 cell token), `species`, `lat`, `lng`, `expires_ms` |
| `spawn_attributes` | serialized `PokemonProto`, level, IVs, form, costume, shiny/shadow/lucky/mega |
| `spawn_generations` | spawn generation marker used by world reconciliation |
| `admin_spawns` | marks admin-created spawns and stores a label |
| `encounters` | per-player encounter state (`active`, `caught`, `fled`) and final result |
| `encounter_deadlines` | per-player encounter expiry independent of spawn expiry |
| `encounter_items` | active berry per player/spawn |
| `pokedex_observations` | seen/caught observations keyed by source |

Spawn points are S2 cell tokens. The admin picks depth 20, 19 or 18 to avoid overlapping active
spawns at the same point. Wild spawns are generated per tile (`spawn_points_per_tile`, default 8),
respecting `spawn_pools`, `spawn_level_range`, `spawn_iv_range`, shiny rate and biome weights.

#### Encounter flow

1. Map-object calls return nearby wild Pokémon and forts within range.
2. `ENCOUNTER` validates ownership/range (`interaction_range_meters`, default 100), creates or keeps
   an `encounters` row, sets a deadline (`encounter_duration_seconds`, default 300), records the
   Pokédex observation, and returns the wild `PokemonProto` plus capture probabilities.
3. `CATCH_POKEMON` validates the encounter, ball, storage capacity and expiry.
4. It consumes one ball (raid Premier Balls use `raid_members.balls`, not inventory), increments
   throws, and if the throw hit, rolls against the capture probability.
5. On success it inserts a `pokemon` row with a storage id, writes `pokemon_levels`, records the
   Pokédex, applies candy/stardust/XP, and returns `CatchPokemonOutProto`.
6. On failure it returns status 2/3 (breakout / final raid ball) without consuming more than the one
   ball.

#### Capture probability

`gameplay/items.py::probabilities(game, berry=0)` builds:

```
base = config.catch_probability            # 0.8 in family.json
ball multipliers = [1, 1.5, 2, 255]        # Poke, Great, Ultra, Master
berry multiplier = 1.5 if Razz Berry (701) else 1
probability = min(1, base * ball * berry)
```

The response includes `pokeball_type = [1,2,3,4]` and four probabilities. Catch uses
`probability[pokeball - 1]` for wild encounters and index 0 for raid encounters (Premier Ball ID 5).
Master Ball therefore always succeeds unless another constraint fails (storage full, expired
encounter, wrong owner).

#### Balls and berries

| Item ID | Item | Notes |
|---|---|---|
| 1 | Poké Ball | default |
| 2 | Great Ball | 1.5× |
| 3 | Ultra Ball | 2× |
| 4 | Master Ball | 255×, added locally |
| 5 | Premier Ball | raid-only, not inventory |
| 701 | Razz Berry | 1.5× capture, consumed on use |
| 703 | Nanab Berry | slows target; GM food template injected |
| 705 | Pinap Berry | candy bonus; GM food template injected |

Berries are applied with `USE_ITEM_ENCOUNTER`; only one berry can be active per encounter. The active
berry is cleared when a catch is attempted.

#### Catch rewards

`config.catch_xp` (100), `catch_stardust` (100), `catch_candy` (3), plus species/family candy and XL
candy rolls. A level-31+ player can receive XL Candy at the rates in `xl_catch_rates`
(`[1,0]`, `[11,0.1]`, `[21,0.25]`, `[31,0.5]`, `[36,0.75]`, `[41,0.9]`). Mega catches have their own
bonus in `gameplay/mega.py`.

### 9.3 Inventory and items

| Table | Meaning |
|---|---|
| `items` | `(player_id, item, count)` |
| `candy` | per-family candy |
| `mega_catch_xl` | per-family XL candy (despite the name) |
| `shop_offers` | durable purchased offers |
| `raid_daily_passes` | daily free raid pass bookkeeping |
| `raid_pass_receipts` | paid raid pass receipts |

Item behavior lives in `gameplay/items.py`:

- **Potions** (101/102/103/104): heal flat + percent of max stamina.
- **Revives** (201/202): restore 50% / 100% of max stamina, only if fainted.
- **Recycle** (`RECYCLE_INVENTORY_ITEM`): only balls, berries, potions and revives; returns candy.
- **Bulk heal**: heals a list of Pokémon, returns per-Pokémon results and remaining count.
- **TMs**: `USE_ITEM_MOVE_REROLL` and `UNLOCK_SPECIAL_MOVE` in `pokemon_moves.py`.
- **Evolution items**: defined in `core/master.py` item templates and used by `evolutions.py`.
- **Raid passes**: Free (1401), Premium (1402), Remote (1408), Premier (5). Free passes are
  `ignore_inventory_space`; paid passes are consumed by raid entry.
- **Storage**: `pokemon_capacity`/`item_capacity` are per-account in `family_accounts`, defaulting to
  config values. Admin rewards can increase them.

Inventory is returned by `GET_HOLOHOLO_INVENTORY`. The client is sensitive to exact protobuf shape;
use existing helpers rather than inventing new fields.

### 9.4 Gyms and gym battles

| Table | Meaning |
|---|---|
| `forts` | `id`, `kind` (0 gym, 1 stop), name, lat, lng |
| `gyms` | team, prestige, modified time |
| `gym_defenders` | deployed Pokémon, owner, deploy time, motivation |
| `gym_badges` | per-player badge progress |
| `gym_battles` | timed PvE battle sessions |
| `gym_returns` | returned defenders |
| `gym_notifications` | durable inbox records for returns |

Behavior:

- `gyms.info` builds the client-facing gym state (team, defenders, raid, badges).
- `GYM_DEPLOY` places an owned Pokémon; `GYM_FEED_POKEMON` restores motivation; `GYM_GET_INFO` reads.
- `GYM_START_SESSION` creates a timed `gym_battles` row for a defender; `GYM_BATTLE_ATTACK` applies
  server-authoritative damage; expiry settles abandoned battles.
- Defenders cannot be deployed to multiple forts, cannot be eggs/components/fusions, and cannot be in
  another active battle.
- `battle_busy` prevents using a Pokémon that is defending or fighting.
- Gym returns are written to `gym_returns`/`gym_notifications`; delivery never grants rewards.
- Admin-created gyms are `admin:gym-*` identities written to `admin-pois.json` and `forts`; the world
  overlay merges them over OSM POIs.

### 9.5 Raids

Raids are local, persistent, solo-first PvE. There is co-op plumbing (`gameplay/cooperative.py`,
`raid_members`) but the family profile is configured for a small participation limit.

#### Configuration

`config.raids` in `family.json`:

| Key | Current | Meaning |
|---|---|---|
| `enabled` | true | schedule raids at all |
| `development_only` | true | refuses to run in a non-development environment |
| `staggered` | true | geographic phase offsets |
| `slot_probability` | 0.5 | chance a gym gets a raid slot |
| `start_hour` / `end_hour` | 6 / 22 | local raid window |
| `egg_seconds` | 900 | egg duration |
| `active_seconds` | 2700 | raid duration |
| `rotation_seconds` | 14400 | schedule rotation |
| `lobby_seconds` | 20 | lobby |
| `battle_seconds` | 300 | battle |
| `multiplayer` | true | enables multi-participant validation |
| `participation_limit` | 5 | max participants |
| `pools` | 5 entries | tier 1/3/5/6 bosses |

Pool entries carry `species`, `tier`, `hp`, `cp`, `weight`, `xp`, `dust`, optional `shiny_rate`,
`shadow`, `level`, `form`, `costume`, `items`, and for tier 6 `mega_evolution`,
`mega_energy_rewards`, `reward_policy`.

#### Lifecycle

1. `raids.schedule()` picks a gym and a boss from the weighted pool when the schedule says so.
2. An egg is written with `spawn_ms` (now), `battle_ms` (hatch), `end_ms`.
3. `GET_RAID_DETAILS` / `GET_SIMPLE_RAID_DETAILS` expose the egg or active boss.
4. `JOIN_LOBBY` admits the player, allocates Premier Balls (`premier_balls`, default 12), and creates
   `raid_members`.
5. `START_RAID_BATTLE` creates a `raid_battles` row; `ATTACK_RAID` applies server-authoritative damage
   with the shared PvE engine.
6. `COMPLETE_RAID_BATTLE` settles victory/defeat, grants XP/dust/items, writes participation, and
   creates the bonus encounter.
7. The bonus encounter uses Premier Balls; the final ball is persisted so a restart cannot refill it.
8. `GET_RAID_ENCOUNTER_DETAILS` / `CATCH_POKEMON` handle the bonus catch. Mega bosses are
   Mega-evolved on catch with boosted CP.

#### Raid HTTP adapter

`gameplay/raid_transport.py` implements the 0.429.2 Nebula HTTP adapter (`/raid/api/get-state`,
`send-player-event`, `get-time`) over the same authoritative PvE clock. It authorizes the family
session from headers and returns protobuf. It adds a 250 ms delay on `get-state` to pace the client.

#### Mega raids

Tier 6 is special: it requires a species with a supported Mega branch, a `mega_evolution` value, and
an explicit `mega_energy_rewards` policy. The boss Pokémon carries temporary-evolution fields and
`temp_evo_cp`, so the raid card shows real CP. Victory grants Mega Energy by completion-speed bands
(200 at ≤150 s, 100 at ≤300 s in the shipped pool) and the catch encounter is Mega-evolved.

### 9.6 Mega evolution

`gameplay/mega.py` is source-bound temporary evolution. Owned identity/stats remain ordinary; only
the temporary form is projected.

- `SPECIES` is loaded from a bundled data file and contains **56 species / 59 forms** (some species
  have X and Y branches).
- `mega_energy` stores per-player/per-species/per-form energy.
- `mega_progress` stores per-Pokémon/per-form Mega level, points, daily count and evolution count.
- `mega_ledger` records grants/evolutions for idempotency and audit.
- `MEGA_EVOLVE_POKEMON` validates species/form, energy cost, cooldown and level settings, deducts
  energy, sets `current_temp_evolution`, finish time, locked flag and boosted CP/stamina.
- `MEGA_LEVEL_UP` and `UNLOCK_TEMPORARY_EVOLUTION_LEVEL` spend energy on permanent Mega levels.
- `GET_MEGA_LEVEL_UP_PREVIEW` returns bulk cost tables.
- `expire()` clears active temporary evolutions when their finish time passes.
- `catch_bonus()` applies catch bonuses from the active Mega level.
- Admin rewards can grant energy directly via `POST /api/rewards` (`mega_energy` list).

Temporary evolution fields that matter:

| Field | Meaning |
|---|---|
| `pokemon_display.current_temp_evolution` | active Mega form ID |
| `pokemon_display.temporary_evolution_finish_ms` | expiry |
| `pokemon_display.temp_evolution_is_locked` | locked (raid boss) vs player-activated |
| `pokemon_display.locked_temp_evolution` | locked form |
| `pokemon.temp_evo_cp` | CP shown on the Mega card |
| `pokemon.temp_evo_cp_multiplier` | CP multiplier |
| `pokemon.temp_evo_stamina_modifier` | stamina scale |

Legacy/admin rows with a Mega form but `temp_evo_cp == 0` are repaired in `raids.info()` by copying
`pokemon.cp` and `cp_multiplier`. This is why old Mega raids no longer show 0CP.

### 9.7 Eggs and incubators

`gameplay/eggs.py` owns `eggs`, `incubators`, `initial_incubator_grants`, `hatches`.

- Eggs are stored by player and distance (`2`, `5`, `10` km pools in config).
- Stops can drop eggs with `eggs.stop_drop_probability` (0.5); capacity is `eggs.capacity` (9).
- `USE_ITEM_EGG_INCUBATOR` starts incubation; walking samples from location headers advance distance.
- Hatching inserts a Pokémon, records the Pokédex, grants XP/stardust/candy, and returns hatch results.
- The first limited incubator is granted once per player via `initial_incubator_grants`.
- Egg storage IDs are distinct from caught Pokémon storage IDs.

### 9.8 Research, quests and stories

`gameplay/research.py` and `gameplay/stories.py` own:

| Table | Purpose |
|---|---|
| `research_tasks` | active tasks per player |
| `research_visits` | stop visits for field research |
| `research_encounters` | encounter rewards |
| `research_cards` | daily card/cycle |
| `research_stamps` | stamp progress |
| `research_stories` | special research stages |
| `research_stage_claims` | claimed stages |
| `research_dialog_reads` | dialog state |

- Tasks are generated/validated from config and client quest prototypes.
- `GET_NEW_QUESTS`, `GET_QUEST_DETAILS`, `COMPLETE_QUEST`, `REMOVE_QUEST`, `QUEST_ENCOUNTER`,
  `COMPLETE_QUEST_STAMP_CARD`, `GET_QUEST_UI`, `READ_QUEST_DIALOG` are implemented.
- Special research is multistage with explicit rewards and once-only claims.
- `research.enabled` toggles the subsystem.
- Catch/throw/walk/stop events call `research.progress()` from the relevant gameplay paths.

### 9.9 Buddy

`gameplay/buddy.py` and `buddy_perks.py` own buddy state on top of the egg walking samples:

- `set_buddy_pokemon`, `get_buddy_stats`, `get_buddy_map`, `get_buddy_walked`, `feed_buddy`,
  `pet_buddy`, `open_buddy_gift`, `get_buddy_history`.
- Buddy distance uses the same validated `walking` samples as eggs.
- Hunger/affection/heart state is persistent.
- Perks (gifts, souvenirs, ribbon, catch assist) are gated by config and source rules.
- `OPEN_BUDDY_GIFT` grants a bounded reward and records it.

### 9.10 Friends, gifts, trading and social

This is the most protocol-sensitive area. `gameplay/friends.py`, `gifts.py`, `trading.py`,
`remote_trading.py` and `cooperative.py` implement it.

- `social.enabled` must be true for social method IDs. If false, those methods raise.
- Friends are installed into the existing player DB; friend codes and pairing are deterministic.
- Gifts are attached to published stops; `SEND_GIFT`, `OPEN_GIFT`, `GIFT_DETAILS`, `CHECK_SEND_GIFT`,
  `DELETE_GIFT_FROM_INVENTORY` are implemented.
- Trading uses two-consent negotiation with durable locks and receipts: `OPEN_TRADING`,
  `CONFIRM_TRADING`, `UPDATE_TRADING`, `CANCEL_TRADING`, `GET_TRADING`.
- Remote trading is fail-closed: mark/list/propose/pending/respond/cancel, with native final-consent
  binding. Do not weaken these checks.
- `PROXY_SOCIAL_ACTION`, `PROXY_SOCIAL_SIDE_CHANNEL_ACTION`, `GAR_PROXY_ACTION` are the native social
  envelopes. Device push tokens must never be logged; the fixture trace only logs action numbers and
  byte lengths.
- Postcards/snapshots: `CREATE_POSTCARD`, `UPDATE_POSTCARD`, `DELETE_POSTCARDS`,
  `COMPLETE_SNAPSHOT_SESSION`, `SAVE_PLAYER_SNAPSHOT`.

### 9.11 Shop and economy

`gameplay/shop.py` owns a local coin-only catalog:

- `GET_AVAILABLE_SKUS_AND_BALANCES`, `IS_SKU_AVAILABLE`, `PURCHASE_SKU`.
- `trainer_teams` stores team and coins.
- `shop_offers` is the durable, owned offer and the purchase retry key. A purchase is idempotent by
  offer ID.
- No real-money integration; coins are local. Do not add payment SDKs without an explicit request.

### 9.12 Collection, cosmetics and identity

| Subsystem | Module | Notes |
|---|---|---|
| Pokédex | `pokedex.py` | Persistent seen/caught observations, independent of transfers |
| Medals | `medals.py` | Derived from committed facts; cosmetic rewards |
| Avatar | `avatar.py` | Default avatar from client catalog |
| Wardrobe | `wardrobe.py` | Owned neutral-avatar articles from dependency-checked registry |
| Costumes | `costumes.py` | Catalog-derived legacy costumes; availability ≠ gameplay proof |
| Backgrounds | `backgrounds.py` | Opt-in original collectible cards |
| Tags | `pokemon_tags.py` | Owned storage tags serialized as native tag records |
| Sizes | `pokemon_sizes.py` | Pinned height boundaries and custom distribution |
| Trainer | `trainer.py` | Additive records and once-only events |
| Preferences | `core/preferences.py` | Bounded server-owned settings |

Cosmetics are intentionally conservative: a costume or avatar article is only selectable if the
local asset registry says it has complete dependencies. Do not grant arbitrary costume IDs just
because the protocol accepts an integer.

### 9.13 Journal, stats and weather

- `journal.read()` projects the native action log; it never disguises unsupported event kinds.
- `trainer.py` stores once-only gameplay events and increments counters.
- `medals.py` refreshes medal counters from durable facts.
- `world/world_weather.py` caches coarse-cell weather (Open-Meteo) with a daylight/condition/math
  level model; it is shared across players, not a per-player trail. `weather.enabled` and refresh
  limits are in config.
- `GET_SERVER_TIME` returns server time; the client uses it for cooldowns.

---

## 10. The world subsystem

The world is a **published, immutable Netherlands snapshot** plus writable overlays. It is separate
from the game database so gameplay never rewrites geographic source data.

### Manifest

`data/world/current.json` is the switchboard:

```json
{
  "database": "data/world/netherlands-261020-v2.sqlite3",
  "sha256": "ca7568429854a23d5d46ffd218acbab5378d56d9d49779d9041d808b7dfec81e",
  "epoch": 1249208389,
  "source": {
    "source": "https://download.geofabrik.de/europe/netherlands-261020.osm.pbf",
    "source_sha256": "466113224994119a050a215d150c0b772fde024502de041137063f994e8e55a3",
    "bytes": 1407494620,
    "license": "ODbL-1.0",
    "attribution": "OpenStreetMap contributors",
    "region": "European Netherlands; Caribbean Netherlands excluded",
    "importer": "world-import-v1",
    "osmium": "4.3.2",
    "shapely": "2.1.2"
  },
  "published": "2026-10-22T12:25:46.248375+00:00",
  "validation": "builds/m14-world-coverage-release.json"
}
```

| Fact | Value |
|---|---|
| Snapshot size | 7,056,125,952 bytes (about 7.06 GB) |
| Region | European Netherlands; Caribbean Netherlands excluded |
| License | ODbL-1.0, attribution required |
| Photos on disk | 10 verified POI JPEGs; everything else uses the placeholder |
| Overlay | `data/world/admin-pois.json` (gyms/stops created from the admin panel) |
| Weather cache | `data/world/weather.sqlite3` |
| Control DB | `data/world/control.sqlite3` (overrides/audit) |

### World snapshot schema (`world_store.py`)

| Table | Purpose |
|---|---|
| `metadata` | key/value manifest, ETL metadata |
| `features` | OSM features with layer, geometry and bounds |
| `feature_bounds` | R-tree-like bounding boxes for spatial queries |
| `pois` | named points of interest with score, category, lat/lng |
| `overrides` | controlled edits with reason and timestamp |
| `photos` | photo records keyed by POI |
| `audit` | append-only change log |

The world is queried by bounding box: `WorldStore.near()` joins `feature_bounds` and `pois`, applies
overrides, and returns only published/approved rows. Admin writes go to the JSON overlay or the
separate `forts` table, never into the snapshot.

### Map rendering

`world/world_map.py` composes Lightship “vector bundles” from the cached ODbL OSM extract:

- `/map/settings` returns the composition settings (palette, epoch, layer defaults).
- `/map/tiles/{z}/{x}/{y}?epoch=...&tileZoom=...` returns a vector tile in the native triangle/polyline
  wire format (`world_tiles.py`).
- The palette lives in `src/dittobuffer/world/world-palette.json`.
- The world epoch is used by the client to invalidate tile caches.
- `world/world_weather.py` provides coarse-cell weather; `world_images.py` provides the one verified
  placeholder image so a missing photo never breaks a stop.

### Admin overlay

`world/world_admin.py` loads/saves `data/world/admin-pois.json`. Each row has `id`, `name`, `lat`,
`lng`, `kind` (0 gym, 1 stop), `status`, `category`. Admin-created IDs are `admin:gym-<hex>` or
`admin:stop-<hex>`. `world_admin.near()` merges these rows into map responses and `forts` rows are
created/removed in the game DB when the POI is created/deleted. Deletion keeps history if a defender,
active battle or active raid still references the fort.

### JSON development world

`world/world_json.py` is a lightweight `JsonWorld` used by tests and development profiles. It reads a
JSON world file and projects the same POI shape. `data/world/dev-world.json` is the development fixture.

### Important limit

The snapshot covers the Netherlands only. At other coordinates the map has no roads/buildings; gyms,
stops, spawns and raids still render. A black map outside the Netherlands is expected, not a bug.

---

## 11. The database

### Location and shape

| Fact | Value |
|---|---|
| Path | `data/family-development.sqlite3` |
| Size | 450,596,864 bytes |
| Mode | SQLite WAL (`-wal`, `-shm` alongside) |
| Tables | 98 |
| Schema versions | 1 through 19 |
| Busy timeout | 10–15 seconds |
| Transaction model | one transaction per RPC batch |

The database is opened by `core/database.py`. `Database.transaction()` is a context manager that
begins a transaction, yields a connection with `sqlite3.Row`, commits on success, rolls back on
exception. Handlers never commit individually; `Game.rpc()` owns the boundary. The admin panel opens
its own connection and commits its own writes.

### Table groups

| Group | Tables |
|---|---|
| Core | `schema_version`, `players`, `sessions`, `rpc_receipts`, `tutorials`, `player_stats_history` |
| Inventory | `items`, `candy`, `mega_catch_xl`, `shop_offers`, `level_reward_claims` |
| Pokémon | `pokemon`, `pokemon_levels`, `pokemon_tombstones`, `pokemon_tags`, `pokedex_observations`, `pokedex_imports` |
| World/spawns | `spawns`, `spawn_attributes`, `spawn_generations`, `encounters`, `encounter_deadlines`, `encounter_items`, `forts`, `fort_visits`, `admin_spawns`, `admin_raids`, `admin_locations` |
| Gyms | `gyms`, `gym_defenders`, `gym_badges`, `gym_battles`, `gym_returns`, `gym_notifications` |
| Raids | `raids`, `raid_members`, `raid_battles`, `raid_combat_sessions`, `raid_daily_passes`, `raid_tokens`, `raid_pass_receipts`, `raid_event_receipts` |
| Eggs | `eggs`, `incubators`, `initial_incubator_grants`, `hatches` |
| Research | `research_tasks`, `research_visits`, `research_encounters`, `research_cards`, `research_stamps`, `research_stories`, `research_stage_claims`, `research_dialog_reads` |
| Social | `trainer_teams`, `family_*`, social tables installed by `friends.py`, `trading.py`, `remote_trading.py` |
| Mega | `mega_energy`, `mega_progress`, `mega_ledger` |
| Cosmetics | `player_appearance`, `wardrobe` tables, `pokemon_tags` |
| Settings | `trainer_settings`, `walking` |

The exact table list is in [Appendix D](#appendix-d--every-sqlite-table). Migrations are additive: the
`Database` constructor checks `schema_version` and applies numbered steps, including table rebuilds
for old `spawns`, `gym_battles`, `raid_battles`, `raid_event_receipts` and Pokédex imports. Never
change an existing migration in place; add a new version.

### Operational rules

- **Back up before schema work**: stop the server, then `sqlite3 data/family-development.sqlite3
  ".backup data/family-development.backup.sqlite3"`. Copying a WAL database while it is live can
  produce a torn backup.
- **Do not run `VACUUM` while the server/admin are live.** It takes an exclusive lock and can stall
  RPCs.
- **Use the admin API** for spawns, POIs, raids, loot and rewards. Direct SQL is fine for inspection
  and for targeted repairs, but it bypasses receipts/validation.
- **Receipts**: `rpc_receipts` grows one row per RPC batch. It can be pruned by ticket/session age if
  it becomes large, but deleting a receipt makes a retry re-execute. Do not prune during an active
  session.
- **Player deletion**: no cascade across every table. If you delete a player manually, clean
  `pokemon`, `items`, `candy`, encounters, raid members, gym defenders, sessions and receipts too.
- **Timestamps are milliseconds** (`now_ms()`), not seconds.
- **S2 cell IDs** are signed 64-bit; some are stored adjusted for protobuf int64. Follow the existing
  helpers.

---

## 12. The Game Master

The Game Master (GM) is the client’s data-driven rulebook: species stats, moves, items, forms, levels,
upgrade costs, raid settings, UI templates. Dittobuffer serves a generated GM rather than hand-editing
a binary blob.

### Files

| Path | Role |
|---|---|
| `assets/game-master/` | Source templates/fixtures |
| `data/static/gmts.binpb.gz` | Served GM (313,566 bytes) |
| `data/static/settings.binpb.gz` | Served settings (1,431 bytes) |
| `data/static/manifest.json` | Metadata |
| `src/dittobuffer/core/master.py` | Builds and injects local templates |
| `/prelogin/gmts.binpb.gz`, `/prelogin/settings.binpb.gz` | HTTP endpoints |

### What `master.py` injects

- **Pokémon size settings** from the pinned reference, including height boundaries.
- **Spawn gender distributions** (the compact snapshot omits them). A gender symbol alone is not
  model validation.
- **Master Ball** item template: `unique_id=4`, `item_type=1`, `category=1`,
  `drop_trainer_level=1`, `pokeball.capture_multi=255`.
- **PvP moves** from the pinned reference.
- **Level-up reward templates** (`LEVEL_UP_REWARDS_LEVEL_<n>`) so the GUI shows the right items.
- **Pokémon upgrade settings**: 2 upgrades per level, allowed levels above player, max normal upgrade
  level 50, candy/stardust cost arrays for 50 levels, XL candy min player level 31, XL min Pokémon
  level 40, XL cost 10 for 20 levels.
- **Player level settings**: cumulative required XP from `progression.requirements()`, CP multipliers
  through 50, rank numbers, default level cap, milestone levels, max encounter/egg player levels.
- **Item templates** for categories (balls, berries, medicine, tools, evolution items, TMs, candy,
  raid passes), including the local food definitions for Nanab/Pinap/Razz.
- **Raid item templates**: Free/Premium/Remote passes, Premier Ball.
- **PvE battle settings** from `battle_master.py` so the client and server use the same effectiveness
  and move data.

### Client caching

The client caches the GM. After changing `master.py`, `config.level_up_rewards`, item templates or
level settings:

1. Restart the game server (`/restart server` or `START-SERVER.bat`).
2. Force-stop and relaunch the app.
3. If the client still shows old data, clear the app cache or reinstall the patched APK.

A Master Ball added to the inventory may not appear in the encounter ball selector until the client
downloads the updated GM templates after a full app restart. This is a client cache behavior, not a
server bug.

### Reproducibility

`prelogin()` compresses with `mtime=0` so the gzip bytes are deterministic. The GM is generated from
local sources plus config; the world snapshot is not involved. `settings_bytes()` is served from the
game object and includes raid/level settings.

---

## 13. The admin panel

The admin panel is a **local-only developer console** at `http://127.0.0.1:8080`. It has no
authentication. It reads/writes the same SQLite database as the game server through a separate
connection, and writes the world overlay JSON.

### UI tabs

| Tab | What it does |
|---|---|
| **Map & spawns** | Click-to-place spawns, live OSM tiles, spawn/gym/stop/raid/player markers |
| **Gyms & stops** | Create a gym or Pokéstop at a clicked point, name it, delete it |
| **Raids** | Create a raid: gym, boss, tier, HP, CP, shiny rate, shadow, egg delay, active window, Mega form |
| **Players** | Level, XP, last fix, centre map, read phone GPS over ADB |
| **Rewards** | Grant XP, stardust, arbitrary items, candy, XL candy, storage, Mega Energy |
| **Pokéstop loot** | Edit items per spin, guaranteed drops, weighted drops, level/count bounds, pass rates |

The UI is `src/dittobuffer/admin/ui/admin_ui.html` (1,185 lines, Material 3 light theme, no build step).
It is read from disk on every request, so HTML changes appear on refresh even if the backend process is
old. That is exactly how “Unknown endpoint” happens: new tab, old Python process.

### API summary

The full table is in [Appendix E](#appendix-e--admin-api-endpoints). The core routes:

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/status` | Counts, player location, config |
| GET | `/api/device-location` | Fused location over ADB (15 s cache) |
| GET | `/api/players` | Players with level, XP, last fix, capacities |
| GET | `/api/forms?species=` | Forms, costumes and Mega options |
| GET | `/api/rewards` | Players + 290 items + 56 Mega species |
| POST | `/api/rewards` | Grant XP/stardust/items/candy/XL/storage/Mega Energy |
| GET | `/api/items` | 290-item catalog |
| GET | `/api/mega-species` | Mega species/forms |
| GET | `/api/loot` | Current table + defaults + item catalog |
| POST | `/api/loot` | Validate and save table |
| GET | `/api/species` | Species names/catalog |
| GET/POST/DELETE | `/api/spawns` | Spawn CRUD |
| GET/POST/DELETE | `/api/pois` | Gym/stop CRUD |
| GET/POST/DELETE | `/api/raids` | Raid CRUD |

### Reward grants

`POST /api/rewards` accepts:

```json
{
  "player_id": "…",
  "xp": 1000,
  "stardust": 500,
  "items": {"1": 50, "4": 1, "1402": 2},
  "candy": {"1": 25},
  "xl_candy": {"1": 10},
  "pokemon_storage": 50,
  "item_storage": 50,
  "mega_energy": [{"species": 3, "evo": 1, "amount": 200}]
}
```

Bounds: XP/stardust ≤ 50,000,000; items ≤ 9,999 each; candy/XL ≤ 99,999 each; storage ≤ 5,000; up to
20 Mega grants; Mega amount ≤ 50,000. It updates `players`, `items`, `candy`, `mega_catch_xl`,
`family_accounts` capacities, and writes Mega energy through `mega.grant()` with a unique
`admin:<hex>` source for idempotency. It returns the updated totals and inventory.

### Spawn and raid validation

- Spawn: species must be in the GM catalog; level 1–50; IVs 0–15; count 1–25; radius 0–500 m;
  duration 60 s–7 days; form/costume/Mega must be supported; coordinates valid.
- Raid: gym must exist; tier 1/3/5/6; tier 6 requires a supported Mega form; HP/CP bounded; shiny
  0–1; egg 0–3600 s; active 60–86400 s; boss form/costume validated.
- POI: kind 0/1, name required, coordinates valid; creates `world_admin` row, `forts`, and `gyms` for
  gyms.
- Deletion: spawn deletes attributes/generations/admin marker; POI deletion preserves history if
  defenders/battles/raids reference it; raid deletion removes members/tokens/battles.

### Device location

`GET /api/device-location` runs `adb shell dumpsys location`, regexes the last fused location, and
caches it for 15 seconds. It needs a connected, authorised phone. It returns `{}` if unavailable.

### Safety

No auth, no CSRF token, no TLS, loopback only. Any local process can grant itself items. That is
acceptable for a private developer tool, not for a shared machine. Never bind to `0.0.0.0`; use an SSH
tunnel if you need remote access.

---

## 14. The Pokéstop loot system

### Where it lives

| Item | Path |
|---|---|
| Table | `data/admin/stop-loot.json` |
| Code | `src/dittobuffer/world/world_rewards.py` |
| Item rules (minimum levels, default weights, food templates) | `src/dittobuffer/world/world-item-rules.json` |
| Admin endpoints | `GET/POST /api/loot` |
| Tests | `tests/test_stop_loot.py` |

The table is data, not code. It is loaded on every spin; `load()` caches by file `st_mtime_ns`, so a
save is visible immediately. `save()` validates, writes a temp file and `os.replace()`s it atomically.

### Schema

```json
{
  "count": 19,
  "guaranteed": [{"item": 1, "count": 1}],
  "drops": [
    {"item": 2, "weight": 20, "min_level": 12, "min_count": 1, "max_count": 1}
  ],
  "passes": {"premium": 1, "remote": 1}
}
```

| Field | Bounds | Meaning |
|---|---|---|
| `count` | 0–20 | number of weighted picks per spin |
| `guaranteed` | ≤20 rows | always granted; item 1–100000, count 1–999 |
| `drops` | 1–200 rows | weighted pool; weight 0–100000; min level 1–50; min/max count 1–999 |
| `passes.premium` | 0–1 | independent Premium Battle Pass (1402) chance |
| `passes.remote` | 0–1 | independent Remote Raid Pass (1408) chance |

A drop row is only eligible if `min_level <= player level` and `weight > 0`. `max_awards()` computes
the upper bound used by tests/UI: guaranteed count + `count × max(max_count)` + number of positive
pass rates.

### Exact algorithm

```python
rng = random.Random(sha256(f"{player}:{fort}:{stamp}").digest())
result = Counter()
for row in guaranteed:
    result[row.item] += row.count
pool = [row for row in drops if row.min_level <= level and row.weight > 0]
if pool and count:
    for row in rng.choices(pool, weights=[row.weight for row in pool], k=count):
        result[row.item] += rng.randint(row.min_count, row.max_count)
for key, item in PASS_ITEMS.items():
    if rng.random() < passes[key]:
        result[item] += 1
```

- The RNG seed is deterministic for `(player, fort, timestamp)`. The same spin cannot be re-rolled by
  retrying; the `fort_visits` cooldown also prevents immediate re-spins.
- A single item can appear multiple times (multiple picks + guaranteed), and counts are summed.
- Premium/Remote passes are independent of the weighted pool and of each other.
- `PASS_ITEMS = {"premium": 1402, "remote": 1408}`.

### Current table (summary)

The live table in `data/admin/stop-loot.json` has `count: 19`, guaranteed **Poké Ball ×1**, and these
weighted rows:

| Item | Weight | Min level | Count |
|---|---|---|---|
| Great Ball (2) | 20 | 12 | 1 |
| Nanab Berry (703) | 5 | 4 | 1 |
| Pinap Berry (705) | 5 | 18 | 1 |
| Poké Ball (1) | 100 | 1 | 1 |
| Potion (101) | 25 | 5 | 1 |
| Razz Berry (701) | 8 | 8 | 1 |
| Revive (201) | 10 | 5 | 1 |
| Ultra Ball (3) | 5 | 20 | 1 |
| Master Ball (4) | 100 | 1 | 1 |

Both pass chances are `1.0` (100%) in this working copy. This is a deliberate local tuning, not the
default. The built-in default is `count: 2`, one guaranteed Poké Ball, the nine GM items with their
default weights, and 2.5% each for Premium/Remote. `POST /api/loot` with the defaults or deleting the
file restores the default behavior.

> The current table is extremely generous (19 weighted picks, two 100-weight ball rows, 100% passes).
> If spins feel absurd, that is why. Change the table, not the code.

### Default weights and minimum levels

`world-item-rules.json` defines nine items: Great Ball (12), Nanab (4), Pinap (18), Poké Ball (1),
Potion (5), Razz (8), Revive (5), Ultra Ball (20), plus the config extra Master Ball. `default()`
reads `config.world.spin_weights` so config can tune weights without touching the rules file. Any
weight key not in the rules becomes an extra default row (this is how Master Ball entered the default
table).

### Adding a new item to loot

1. Confirm the item ID exists in the protocol (`Item` enum) and has a GM template.
2. Add a row in the admin UI, or add a weight to `config.world.spin_weights`.
3. Save. The game reads it on the next spin.
4. If the item has no GM template, the client may show it as unknown. Add a template in
   `core/master.py` (as done for Master Ball).

### Tests

`tests/test_stop_loot.py` covers default generation, normalisation bounds, pass items, deterministic
awards, max awards, save/load, and augmentation of templates. Run it with:

```bash
PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_stop_loot.py -q
```

---

## 15. Mega evolution and Mega raids

### Data

`gameplay/mega.py` loads `SPECIES` from a bundled source. At snapshot time: **56 species, 59 forms**
(Charizard X/Y, Mewtwo X/Y, etc.). Each entry has a base species, ordinary form, branches
(`temp_evo` 1/2/3), stats, and level settings.

### Energy and levels

| Table | Purpose |
|---|---|
| `mega_energy` | per-player, species, form energy |
| `mega_progress` | per-Pokémon/form Mega level, points, daily count, evolutions |
| `mega_ledger` | append-only grant/evolve ledger |

- `grant(game, db, player, species, evo, amount, source)` is idempotent by `(player, source)` and
  rejects a changed source identity. Admin grants use `admin:<hex>`.
- `MEGA_EVOLVE_POKEMON` costs energy, validates cooldown, sets the temporary form and returns the
  projected Pokémon.
- `MEGA_LEVEL_UP` spends energy to raise permanent Mega level; `GET_MEGA_LEVEL_UP_PREVIEW` shows bulk
  costs; `UNLOCK_TEMPORARY_EVOLUTION_LEVEL` unlocks a level.
- `expire()` runs at the start of every RPC batch and clears finished temporary evolutions.
- `catch_bonus()` applies the active Mega level’s catch bonus.
- `project()` builds the client-visible Mega state (`has_mega_evolved`, forms, boosted CP/stamina).

### Mega raids

Tier 6 raid bosses are configured with `mega_evolution` and `mega_energy_rewards`. Validation in
`raids.validate()` enforces:

- species has a supported Mega branch,
- `mega_evolution` matches the branch,
- the boss uses the ordinary form (not a costume),
- an explicit `reward_policy` string exists,
- `mega_energy_rewards` thresholds increase, amounts do not increase for slower completion, and the
  last threshold covers the battle duration.

Victory grants energy by the fastest satisfied band, plus the usual XP/dust/items. The bonus encounter
is the Mega-evolved boss with locked temporary evolution and boosted CP.

### The 0CP repair

New Mega raid rows set:

```python
pokemon.temp_evo_cp = cp
pokemon.temp_evo_cp_multiplier = pokemon.cp_multiplier
pokemon.temp_evo_stamina_modifier = 1
```

Older rows may have `temp_evo_cp = 0`. `raids.info()` repairs them at read time:

```python
if pokemon.pokemon_display.current_temp_evolution and not pokemon.temp_evo_cp and pokemon.cp:
    pokemon.temp_evo_cp = pokemon.cp
    pokemon.temp_evo_cp_multiplier = pokemon.cp_multiplier
    pokemon.temp_evo_stamina_modifier = 1
```

This is a read-time repair, not a destructive migration. It makes existing Mega raid cards show real
CP without rewriting rows.

### Admin flow for a Mega raid

1. Pick a gym and a species with Mega forms.
2. Choose the Mega form (e.g. Venusaur → Mega Venusaur).
3. Tier becomes 6 automatically; HP/CP defaults are 3000/30000 but can be overridden.
4. Set shiny rate, shadow, egg delay and active window.
5. Create. The server stores the definition, the boss Pokémon with temporary-evolution fields, and an
   admin marker.

### Constraints

- Mega cannot be applied to a costume or an ineligible species.
- Mega energy is per species/form; granting the wrong form is rejected.
- `config.mega` can override cooldown; otherwise the level settings from the GM are used.
- The client may cache Mega form assets; if a form renders as a normal Pokémon, check the asset
  registry and app restart.

---

## 16. Master Ball

Master Ball is protocol item **4** (`Item_MasterBall`), but the supplied GM snapshot did not include
its item template. Dittobuffer adds it locally.

### GM template

`core/master.py`:

```python
master = p.GameMasterClientTemplateProto(template_id='ITEM_MASTER_BALL')
master.item.unique_id = 4
master.item.item_type = 1
master.item.category = 1
master.item.drop_trainer_level = 1
master.item.pokeball.capture_multi = 255
values.append(master)
```

### Catch handling

`gameplay/game.py` accepts balls `(1,2,3,4)` for wild encounters and `(5,)` for raids:

```python
if request.pokeball not in ((5,) if raid else (1,2,3,4)):
    raise RpcError('Unsupported ball')
```

`gameplay/items.py` returns probabilities for balls `[1, 1.5, 2, 255]`, so Master Ball is `min(1,
0.8 * 255) = 1.0` at the default catch probability. It consumes one Master Ball from `items` like any
other ball; if the player has none, the catch fails with “No balls available”.

### Loot

`config.world.spin_weights` includes `"4": 0.5` (default 0.5 weight, about 0.6% of a spin with the
default pool). The current live table has Master Ball weight **100**, so it is common. The default
`world_rewards.default()` adds any config weight not present in `world-item-rules.json` as an extra
row, which is how the default table gets a Master Ball without editing the rules file.

### Client visibility

The client must have the Master Ball GM template and inventory item. If it is missing from the
encounter ball selector:

1. Confirm the server is running the new code (`/health`, restart).
2. Force-stop and relaunch the app so it re-downloads `/prelogin/gmts.binpb.gz`.
3. Clear app cache/reinstall if the client persists an old GM.
4. Confirm the player actually has item 4 in `items` (admin Rewards or SQL).

---

## 17. Level 50, XL Candy and power-ups

### Level schedule

`config/family.json` has `level_requirements` with **50 entries**. Index 0 is `0`; each subsequent
value is the XP cost to reach the next level. The tail is:

```
... 96000, 112000, 130000, 150000, 175000
```

`progression.requirements()` validates 2–50 entries, first 0, all positive ints. `cap()` returns 50.
`bounds()` uses cumulative thresholds and clamps to the cap. The default fallback is 40 levels, so a
missing config keeps the old behavior; this profile explicitly extends to 50.

### Game Master

`core/master.py` writes:

- `pokemon_upgrades.max_normal_upgrade_level = 50`
- `candy_cost` and `stardust_cost` arrays for 50 levels
- `xl_candy_min_player_level = 31`
- `xl_candy_min_pokemon_level = 40`
- `xl_candy_cost` array of 20 entries (level 40→50)
- `player_level.required_exp` cumulative thresholds
- `player_level.cp_multiplier` through 50
- `player_level.default_level_cap = 50`

### XL Candy

| Concern | Where |
|---|---|
| Storage table | `mega_catch_xl` (per family) |
| Catch chance | `config.xl_catch_rates`, applied from level 31 |
| Power-up cost | `power_up_xl_candy_cost` (10) |
| Min player level | `xl_candy_min_player_level` (31) |
| Min Pokémon level | 40 |
| Admin grant | `POST /api/rewards` `xl_candy` map |

### Power-ups

`UPGRADE_POKEMON` uses:

- candy cost 1 and stardust cost 100 per level (config),
- allowed up to 10 levels above the player,
- XL candy from level 40 to 50,
- CP multiplier from the GM/reference table,
- shadow/purified multipliers set to 1 in the local GM (deliberate local balance).

### Level-up rewards

`config.level_up_rewards` maps level → item → count (e.g. level 2 gives 10 Poké Balls; level 3 gives
13 Poké Balls, 2 Potions, 1 Revive; …). `trainer_rewards.claim()` grants a bundle once per level and
records `level_reward_claims`. The GM also exposes the same bundles to the level-up GUI.

### Testing

`tests/test_mega.py` covers Mega data/energy/raid behavior. `tests/test_trainer_rewards.py` covers
level-up bundles. `tests/test_progression_modern.py` covers the level schedule and bounds.

---

## 18. APK patching and the client

Dittobuffer ships two client paths: a **complete standalone family build** (the normal path) and a
**Frida Gadget injection** (instrumentation/debugging). Both use the official 0.429.2 APK inputs in
`apk/` and never modify those inputs.

### Inputs

| File | Size | Role |
|---|---|---|
| `apk/PokemonGO-0.429.2-base.apk` | 107,831,025 bytes | Base APK |
| `apk/PokemonGO-0.429.2-config-arm64_v8a.apk` | 82,885,009 bytes | arm64 config split |
| `tools/frida-gadget/arm64-v8a/libgadget.so` | — | Frida Gadget 17.24.3, arm64 |
| `src/dittobuffer/patcher/keys/dittobuffer-patch.jks` | — | Signing key |
| `client/plugin/libNianticLabsPlugin.so` | — | Rebuilt native plugin for the standalone build |

### Patcher commands

```bash
# Full standalone family build (the normal path)
python -m dittobuffer.patcher.patch server --host 192.168.1.20

# Reuse existing provisioning/TLS
python -m dittobuffer.patcher.patch server --no-provision

# Frida Gadget only
python -m dittobuffer.patcher.patch gadget app.apk
python -m dittobuffer --patcher app.apk
```

The console exposes `/patch <apk>` for the gadget path. The unified patcher has two subcommands:

| Subcommand | What it does |
|---|---|
| `server` | Provisions TLS/profile, copies `builds/transfer-public-profile.json`, rebuilds the standalone APK, injects native plugin/CA/agent/runtime/launcher, signs, writes `apk/built/dittobuffer-family-<host>.apk` and a `.inject.json` report |
| `gadget` | Decodes an existing APK, injects `libgadget.so` + config, adds `System.loadLibrary("gadget")`, rebuilds, zipaligns, signs; can re-sign splits with the same key |

### Standalone build pipeline (`scripts/build-standalone.py`)

1. Validates the public profile is project-local and HTTPS.
2. Verifies the pinned Frida Gadget SHA-256:
   `de0feab1ae960aa6cb24440bcb02dc3149ef84c4672a4f19ff4405aba63eaabc`.
3. Copies `builds/client-base` and `builds/client-split` into `builds/<output>/`.
4. Patches `AndroidManifest.xml`:
   - removes split requirements and Play split metadata,
   - disables backup/debuggable, allows cleartext (local HTTP admin only),
   - labels the app “Dittobuffer Family 0.429.2”,
   - removes the original launcher intent,
   - adds `org.dittobuffer.launcher.FamilyActivity` as the exported launcher,
   - marks the Unity main activity non-exported.
5. Copies the CA certificate into `assets/dittobuffer/family-ca.pem` and writes `profile.json`.
6. Compiles `client/standalone/**/*.java` with `javac --release 8` against the bundled Android SDK,
   dexes with `d8 --min-api 28`, and adds `classes4.dex`.
7. Builds the Frida agent by concatenating local JS modules into `standalone-bootstrap.js`:
   `local-auth.js`, `local-ui.js`, `local-maps.js`, `local-world.js`, `local-gameplay.js`,
   `local-raids.js`, `local-backgrounds.js`, `local-assets.js`, `standalone-render-diagnostics.js`,
   plus optional map diagnostics and fixture location.
8. Embeds the runtime as `assets/libdittobuffer_runtime.so` (text masquerading as a native lib because
   Unity probes the native directory), patches the manifest, zipaligns and signs.
9. Emits `build.json` and an injection report. Android install/gameplay is explicitly marked
   `NOT_TESTED` by the patcher; it does not claim device verification.

### App identity

| Fact | Value |
|---|---|
| Package | `org.librenlp.pokemongo.dev` |
| Launcher activity | `org.dittobuffer.launcher.FamilyActivity` |
| Target client | 0.429.2 |
| Signing | bundled `dittobuffer-patch.jks` |
| Frida listen socket | `127.0.0.1:27042` (gadget path) |
| Profile | `config/public-profile.json` / `builds/transfer-public-profile.json` |
| CA | `config/tls/ca.pem` copied into assets |

### Local JS modules

| Module | Purpose |
|---|---|
| `local-auth.js` | Family sign-in/registration UI and token flow |
| `local-ui.js` | Launcher/panel UI overrides |
| `local-maps.js` | Local map composition/tiles |
| `local-world.js` | World/POI data |
| `local-gameplay.js` | Gameplay UI hooks |
| `local-raids.js` | Raid UI/transport |
| `local-backgrounds.js` | Collectible card backgrounds |
| `local-assets.js` | Asset URL routing |
| `standalone-bootstrap.js` | Integration entry point |
| `inspect-*.js` | Instrumentation/debug probes; not part of normal gameplay |

The `inspect-*` scripts are development tools. They can log protocol payloads and UI state; do not
ship them in a public build unless you understand the privacy impact.

### Host allowlist

The launcher (`client/standalone/org/dittobuffer/launcher/FamilyActivity.java`) restricts the client
to the configured local origin and refuses Niantic hosts. The profile’s `origin` must be HTTPS for a
standalone remote profile. This is a safety boundary: do not weaken it to “make it work”; fix the
origin/TLS instead.

### Requirements and failure modes

- **Java 17+** on `PATH` or `JAVA_HOME`; the patcher fails with a clear message if missing.
- Bundled `apktool`, `zipalign`, `apksigner`, Android SDK classes and `d8`.
- Re-sign every split with the same key or Android refuses the install set.
- If apktool cannot decode, the APK is protected or split-only; use the official base.
- If signature verification fails, delete the output and rebuild; do not mix keys.

### Legal/ethical

You must supply your own legally obtained APK. Patching a client violates the Pokémon GO Terms of
Service and can get the account banned. Keep the server loopback-only and use accounts you are willing
to lose.

---

## 19. Phone networking: ADB reverse and gnirehtet

### The two mechanisms

| Mechanism | What it does | When it is needed |
|---|---|---|
| `adb reverse tcp:8445 tcp:8445` | Phone’s `127.0.0.1:8445` connects to the PC’s `127.0.0.1:8445` over USB | Always, for game traffic |
| `adb reverse tcp:8080 tcp:8080` | Phone can open the admin panel over USB | Optional, useful for debugging |
| gnirehtet | Reverse-tethers the phone’s internet through the PC’s connection | When the phone has no Wi-Fi/data |

ADB reverse rules belong to the ADB server session and the current USB configuration. They disappear
when:

- the USB mode changes (charging ↔ file transfer ↔ tethering),
- the cable is unplugged,
- `adb kill-server` / `adb start-server` runs,
- some OEM “USB debugging” toggles reset.

The game server keeps running when the tunnel drops. The client then reports “failed to connect to
https://127.0.0.1:8445” even though `/status` shows the server up.

### Exact reconnect sequence

```bash
adb start-server
adb devices                       # accept the RSA prompt
adb reverse tcp:8445 tcp:8445
adb reverse tcp:8080 tcp:8080
adb reverse --list                # verify
```

Then, if needed:

```bash
python -m dittobuffer /start gnirehtet
adb shell am force-stop org.librenlp.pokemongo.dev
adb shell am start -n org.librenlp.pokemongo.dev/org.dittobuffer.launcher.FamilyActivity
```

`python -m dittobuffer /reconnect` performs the reverse rules, starts gnirehtet if 31416 is closed,
and relaunches the app. It is idempotent; run it after every USB change.

### Verify from the phone

```bash
adb shell 'echo | nc -w 5 127.0.0.1 8445; echo rc=$?'
```

`rc=0` means the TCP path works. If it fails, the tunnel is missing. If it succeeds but the client
still fails, the problem is TLS (certificate/hostname) or credentials.

### TLS certificate

The family profile uses `config/tls/server.pem` + `config/tls/server-key.pem`. The certificate must
contain an **IP SAN for the exact address the client dials** — `127.0.0.1` for ADB reverse. Regenerate
with:

```bash
python scripts/create-family-tls.py --host 127.0.0.1 --directory config/tls-new
```

The helper refuses public IPs, refuses to overwrite an existing directory, and does not modify OS
trust. Copy the new PEMs into `config/tls/`, restart the server, and reinstall the client if the CA is
embedded in the APK.

### gnirehtet

`tools/gnirehtet/` ships:

- `gnirehtet.exe` (Windows relay, listens on 31416),
- `gnirehtet.apk` (Android VPN client),
- `gnirehtet-run.cmd`.

`gnirehtet run` starts the relay, installs the APK if needed, and starts the VPN on the phone. The
first launch may show an Android VPN permission dialog. The relay log is `logs/gnirehtet.log`.

The ADB reverse rules and gnirehtet are independent: the game tunnel works even if the relay is down,
and gnirehtet can provide internet even if the reverse rules are missing. Start both for the best
experience.

### Ports

| Port | Owner | Exposure |
|---|---|---|
| 8445 | game server | TLS, loopback |
| 8080 | admin panel | HTTP, loopback, no auth |
| 31416 | gnirehtet relay | loopback |
| 5037 | ADB server | loopback |
| 27042 | Frida Gadget (gadget build only) | loopback |

### Why the one-click script restarts ADB

A fresh ADB server guarantees a clean reverse-rule table. The script removes all rules, kills the
server, starts it again, waits for the device, then adds the two rules. This is more reliable than
“add another reverse rule” on top of stale state.

---

## 20. Configuration reference

The live profile is `config/family.json`. `config/development.json` is a local-development profile
(no family auth, port 8080, small spawn pool). `config/boot-test.json` is a boot fixture.
`config/public-profile.json` is used by the APK provisioning path.

### Top-level keys

| Key | Current | Meaning |
|---|---|---|
| `host` | `127.0.0.1` | Game listener bind address |
| `port` | `8445` | Game listener port |
| `database` | `data/family-development.sqlite3` | SQLite path relative to root |
| `player_name` | `Developer` | Default/local player name |
| `pokemon_capacity` | `300` | Default Pokémon storage |
| `item_capacity` | `350` | Default item storage |
| `spawn_duration_seconds` | `1800` | Wild spawn lifetime |
| `encounter_duration_seconds` | `300` | Encounter window |
| `catch_probability` | `0.8` | Base catch probability |
| `catch_xp` | `100` | XP per catch |
| `catch_stardust` | `100` | Stardust per catch |
| `catch_candy` | `3` | Candy per catch |
| `interaction_range_meters` | `100` | Encounter/fort range |
| `fort_cooldown_seconds` | `300` | Stop spin cooldown |
| `spawn_species` | `[1]` | Legacy fallback species |
| `spawn_pools` | garden-mewtwo | Weighted species pools, biome tags, shiny rate |
| `spawn_points_per_tile` | `8` | Spawn density |
| `spawn_level_range` | `[20,20]` | Wild level range |
| `spawn_iv_range` | `[0,15]` | IV range |
| `shiny_rate` | `0.001953125` | Base shiny rate (1/512) |
| `spawn_generation` | `15001` | Spawn generation marker |
| `power_up_candy_cost` | `1` | Candy per power-up |
| `power_up_stardust_cost` | `100` | Stardust per power-up |
| `power_up_levels_above_player` | `10` | Power-up level allowance |
| `power_up_xl_candy_cost` | `10` | XL candy per level 40+ |
| `xl_candy_min_player_level` | `31` | XL candy unlock level |
| `xl_catch_rates` | level→rate pairs | XL candy catch chance |
| `evolution_xp` | `500` | XP per evolution |
| `level_up_rewards` | map | Items per level |
| `initial_items` | map | Starting inventory |
| `level_requirements` | 50 entries | XP cost per level |
| `fort_xp` | `50` | XP per stop/gym interaction |
| `spawn_neighbor_tiles` | `1` | Neighbor tile radius |
| `eggs` | object | Capacity, stop drop rate, pools, rewards, incubators |
| `walking` | object | Accuracy/speed limits and debug fixture |
| `research` | `{enabled: true}` | Research subsystem |
| `pokemon_assets` | `{enabled: false, manifest: ...}` | Local Pokémon asset serving |
| `avatar_assets` | `{enabled: false, manifest: ...}` | Local avatar asset serving |
| `buddy_perks` | object | Gifts/souvenirs/ribbon/catch assist |
| `profile` | `private-transfer-family` | Profile label |
| `world` | object | World enabled, manifest, palette, spin weights, raid pass rate |
| `weather` | object | Enabled, refresh/stale limits, daily limit |
| `raids` | object | Enabled, tiers, pools, Mega, participation |
| `authentication` | object | Mode, registration |
| `network` | object | TLS mode, certificate, private key |
| `client_resources` | `{enabled:false, registry:...}` | Exact opt-in client runtime resources |
| `social` | `{enabled:true}` | Social method gate |
| `map_trace` | `false` | Log catch throws |
| `rpc_trace_methods` | `[]` | Fixture wire tracing (only for temp build DBs) |
| `shiny_eligible_forms` | `[{species:150}]` | Explicit shiny eligibility |

### Important nested config

#### `world`

```json
{
  "enabled": true,
  "manifest": "data/world/current.json",
  "palette": "src/dittobuffer/world/world-palette.json",
  "spin_weights": {"1": 100, "2": 20, "3": 5, "4": 0.5, "101": 25, "201": 10, "701": 8, "703": 5, "705": 5},
  "raid_pass_rate": 0.05
}
```

`spin_weights` feeds the default loot table. `raid_pass_rate` is a legacy/alternate pass rate; the
live table uses `passes` instead.

#### `raids`

See section 9.5. The current pools are Spearow (tier 1), Pikachu (tier 3), Articuno (tier 5),
Venusaur Mega (tier 6), Dialga (tier 5). Tier 6 requires `mega_evolution: 1` and
`mega_energy_rewards`.

#### `authentication`

```json
{"mode": "family", "registration": "open"}
```

- `mode: family` enables accounts/sessions.
- `registration: open` allows self-registration; `invite` requires an invitation.
- Credential/session lifetimes can be set with `credential_seconds` and `session_seconds`.

#### `network`

```json
{"mode": "tls", "certificate": "config/tls/server.pem", "private_key": "config/tls/server-key.pem"}
```

`mode: tls` wraps the listener. `allowed_networks` optionally restricts peers; an empty list fails
closed. `mode` other than `tls` is plain HTTP and should only be used in tests/development.

#### `level_up_rewards`

Maps level → item ID → count. Example: level 3 gives 13 Poké Balls, 2 Potions, 1 Revive. These are
also injected into the GM for the level-up GUI.

#### `eggs`

`capacity` 9, `stop_drop_probability` 0.5, pools for 2/5/10 km with species and weights, hatch XP/
stardust/candy, `initial_limited_incubators` 1.

#### `weather`

`enabled`, `refresh_seconds` 900, `stale_seconds` 7200, `daily_limit` 9000. Weather is coarse-cell,
shared, cached in `data/world/weather.sqlite3`.

### Profiles

| Profile | Use |
|---|---|
| `family.json` | Live family server: TLS, family auth, world, raids, level 50 |
| `development.json` | Local development: port 8080, no family auth, small spawn pool, assets off |
| `boot-test.json` | Boot fixture used by tests |
| `public-profile.json` | APK provisioning profile (origin/CA) |

### Paths and validation

`runtime/http.py` resolves `--config` and refuses a path outside the project. `paths.py` anchors all
other paths. The database path may be relative to the root; the admin resolves it the same way. Keep
TLS material under `config/` and never commit `.private/`.

---

## 21. The console and service manager

`python -m dittobuffer` starts the banner and an interactive slash-command console. It autostarts
`server` and `admin` unless `--no-autostart` is passed. Services are detached OS processes, so they
survive the console.

### Commands

| Command | Action |
|---|---|
| `/help` | Full command list |
| `/status` | Services, ports, PIDs, device and tunnels |
| `/start [all\|server\|admin\|relay]` | Start background services |
| `/stop [all\|server\|admin\|relay]` | Stop background services |
| `/restart [all\|server\|admin\|relay]` | Restart one or all |
| `/reconnect` | Rebuild ADB reverse, relay and relaunch app |
| `/devices` | Attached devices, model, Android version |
| `/doctor` | Python/config/adb/gnirehtet/gadget/world/assets/APK checks |
| `/logs <service> [lines]` | Tail a service log |
| `/players` | Online trainers from the admin API |
| `/spawns` | Active spawns near the player |
| `/raids` | Active and upcoming raids |
| `/pois` | Admin gyms and Pokéstops |
| `/open` | Open the admin panel |
| `/patch <apk>` | Inject Frida Gadget into an APK |
| `/config` | Resolved endpoints and paths |
| `/clear` | Clear the screen |
| `/quit` | Leave the console (services keep running) |

Aliases: `relay`/`tunnel` → `gnirehtet`, `game` → `server`, `panel` → `admin`, `all` → all.

### Non-interactive usage

```bash
python -m dittobuffer /status
python -m dittobuffer --no-autostart /start server
python -m dittobuffer /logs server 80
python -m dittobuffer --patcher app.apk
```

`_normalise()` undoes MSYS path rewriting of `/status` in Git Bash. On Windows, prefer `start server`
without the slash if a shell mangles it; the console adds the slash.

### Service manager internals

`cli/services.py`:

- `Service` holds name, command, port, log, health URL and PID file.
- `start()` checks the port/PID, opens the log in append mode, spawns with `DETACHED_PROCESS |
  CREATE_NEW_PROCESS_GROUP` on Windows (or `start_new_session` on POSIX), writes the PID, and waits
  for the port up to `wait` seconds.
- `stop()` reads the PID, `taskkill /F /T` on Windows or `kill -TERM` on POSIX, waits up to 8 s.
- `status()` reports port/PID state; `tail()` reads the last N lines.
- `reverse()` waits for a device (0/2/5 s retries), adds reverse rules for 8445 and 8080, lists them.
- `reconnect()` starts ADB if needed, reverses, starts gnirehtet if 31416 is closed, relaunches the
  app.
- `doctor()` checks Python ≥3.11, config, adb, gnirehtet, Frida gadget, world manifest, Pokémon
  assets and APK presence.

### Logs and PIDs

| File | Content |
|---|---|
| `logs/server.log` | RPC methods, map/asset requests, listener startup |
| `logs/server.err.log` | Tracebacks/warnings from a previous redirect |
| `logs/admin.log` | Admin startup |
| `logs/gnirehtet.log` | Relay/VPN output |
| `run/server.pid`, `run/admin.pid` | Launcher PIDs |

The console opens logs in append mode; `START-SERVER.bat` does not truncate them. If you need a clean
log, stop the service and rename/remove the file first.

---

## 22. Development guide

### Environment

```bash
cd Dittobuffer-dirty
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install -r requirements-dev.txt
```

`requirements.txt` includes protobuf, s2sphere, cryptography and tzdata. Dev requirements add pytest,
grpcio-tools, pyelftools, androguard, frida and capstone. The `world` extra adds osmium/shapely/astral;
the `assets` extra adds UnityPy.

### Run from source

```bash
set PYTHONPATH=%CD%\src
.venv\Scripts\python.exe -m dittobuffer
```

Or use the one-click batch file. The package is not required to be pip-installed; `PYTHONPATH=src` is
enough.

### Code style

The codebase is deliberately compact:

- one-line functions where clear (`def now_ms(): return int(time.time()*1000)`),
- semicolons to keep related statements together,
- short local names (`db`, `p`, `v`, `row`),
- no framework, no build step for the admin UI,
- protobuf types are imported as `p` from `game_pb2`,
- `ROOT` and paths come from `paths.py`,
- SQL is inline with explicit columns; no ORM.

Match the file you are editing. Do not reformat a dense module into PEP-8 vertical style “while you
are there”; it makes diffs unreviewable. Do not add a dependency without checking `pyproject.toml` and
the existing stack.

### Adding an RPC method

1. Add the method to `protocol/game.proto` (if it is not already in the 444-value enum) and regenerate
   `game_pb2.py`.
2. Add a `rpc_<lowercase_name>` method to `gameplay/game.py` or a domain module imported into it.
3. Validate ownership/state inside the existing transaction; never commit yourself.
4. Return the matching `*OutProto`; keep field numbers/shape compatible with the client.
5. Add a test that drives it through `Game.rpc()` with real wire bytes.
6. If it needs timed expiry, call it from `Game.rpc()`’s expiry block.

### Adding an admin endpoint

1. Add the method to `Admin` in `src/dittobuffer/admin/admin.py`.
2. Add a route in `Handler.route()` (GET/POST/DELETE) and a JSON error path.
3. Add UI in `admin_ui.html` if needed.
4. Restart the admin process. HTML is read from disk, Python is not.
5. Add a test if the endpoint is non-trivial.

### Adding a gameplay module

1. Create `src/dittobuffer/gameplay/<feature>.py` with a module docstring and a `SCHEMA` string if it
   needs tables.
2. Install the schema from `Game.__init__` or the module’s `install()` called there.
3. Add handlers to `Game` or import a mixin; register nothing else — dispatch is by method name.
4. Add a focused test under `tests/`.
5. Keep rewards idempotent with a receipt/once-only table.

### Adding a DB migration

1. Append a new version number to `Database`’s migration chain.
2. Use additive DDL; if you must rebuild a table, create `*_vN`, copy, drop, rename, and recreate
   indexes in the same transaction.
3. Never edit a migration that has already run in a live DB; add version N+1.
4. Test against a copy of the live DB, not only an empty one.

### Changing loot

Edit `data/admin/stop-loot.json` or use the admin tab. No code change. If you add a new item, ensure a
GM template exists. If you change the default behavior, edit `world_rewards.default()` and
`world-item-rules.json`, then update `tests/test_stop_loot.py`.

### Changing the Game Master

Edit `core/master.py` (templates/injections) or the source assets, regenerate the GM if needed, restart
the server, and relaunch the client. Never hand-edit `data/static/gmts.binpb.gz` in place; it is a
generated artifact.

### Changing the world

Do not edit the published snapshot. Use the admin overlay for gyms/stops, `data/world/admin-pois.json`
for bulk edits, or rebuild a new snapshot with a new manifest (out of scope for this repo’s scripts).
Admin POIs are merged at read time.

### Debugging tips

- Add `logging.info` in the handler; server log is line-buffered to file/stderr.
- Use `map_trace: true` only for catch throw tracing; it logs throw details.
- `rpc_trace_methods` is restricted to temp build DBs to avoid logging sensitive social payloads.
- Inspect `rpc_receipts` if a client retry behaves differently from the first call.
- Use `sqlite3` with `.headers on` and `.mode column` for manual queries.

---

## 23. Testing

### Scale

The tree contains **353 test functions** across 30 test files plus a `legacy/` folder. The suite covers
unit logic, wire-format checks, TLS, HTTP, database transactions and failure paths.

### Test files

| File | Area |
|---|---|
| `test_game.py` | Core RPC/catch/inventory behavior |
| `test_family_accounts.py` | Registration/login/session/rate limits |
| `test_family_http.py` | Real loopback TLS listener, protobuf auth, logout, no credential logging |
| `test_family_raids.py` | Family raid lifecycle |
| `test_family_world.py` | World integration |
| `test_raids.py` | Raid scheduling, bosses, rewards |
| `test_mega.py` | Mega species/energy/raids/catch/0CP |
| `test_stop_loot.py` | Loot table defaults, bounds, passes, awards, save/load |
| `test_trainer_rewards.py` | Level-up bundles |
| `test_progression_modern.py` | Level schedule/cap/bounds |
| `test_eggs.py` | Eggs/incubators/hatches |
| `test_gyms.py` | Gyms/defenders/battles |
| `test_buddy.py`, `test_buddy_perks.py` | Buddy state/perks |
| `test_friends.py`, `test_social_gameplay.py` | Social/gifts/trading |
| `test_research.py` | Quests/stories |
| `test_shop.py` | SKUs/purchases |
| `test_medals.py` | Medal counters |
| `test_wardrobe.py` | Avatar articles |
| `test_asset_catalog.py` | Asset manifest/hash validation |
| `test_standalone.py` | TLS allowlist, cert hostname, APK preservation |
| `test_world_map.py` | Map composition/tiles |
| `test_spawn_stability.py` | Spawn determinism |
| `test_shiny_palette.py` | **Known pre-existing failure** (missing script import) |

### Run

```bash
set PYTHONPATH=%CD%\src
.venv\Scripts\python.exe -m pytest -q
```

Targeted:

```bash
.venv\Scripts\python.exe -m pytest tests/test_stop_loot.py tests/test_mega.py tests/test_trainer_rewards.py -q
```

### Known failure

`tests/test_shiny_palette.py` imports `scripts/retarget-same-species-animation.py`, which is not in
this tree. It is a pre-existing packaging gap, not a gameplay regression. Report it explicitly when
you run the full suite; do not “fix” it by deleting the test unless the user asks.

### Test design notes

- `test_family_http.py` starts a real TLS listener on a loopback port with a generated certificate,
  registers/logs in, exchanges a ticket, calls RPC, logs out, and asserts no raw credentials are
  logged. It uses a temp DB under `builds/`.
- `test_standalone.py` checks runtime-resource opt-in, TLS peer allowlist, certificate hostname
  validation, and that APK patching preserves the original game DEX/native content.
- Tests create temporary directories under `builds/`; they do not touch the live DB or world.
- The suite is proof-oriented: it asserts serialized bytes/status codes, not only Python return values.
- A passing suite does not prove Android gameplay. The patcher explicitly marks device install as
  `NOT_TESTED`; verify on the phone when the change affects the client.

---

## 24. Troubleshooting and runbook

### First response to any “it broke”

```bash
curl -k https://127.0.0.1:8445/health
curl -s http://127.0.0.1:8080/api/status
adb devices
adb reverse --list
netstat -ano | findstr ":8445 :8080 :31416"
```

If the game server is not listening, nothing else matters. Start it before debugging content.

### Symptom → cause → fix

| Symptom | Likely cause | Fix |
|---|---|---|
| Blank Pokéstop images, spins do nothing | Game server process is down | `START-SERVER.bat`; check `/health` |
| “Unknown endpoint” when saving loot/rewards | Admin process is older than the HTML/backend | Restart admin (`/restart admin`); refresh page |
| Only “Poke Ball” selectable in loot | Old admin backend without the 290-item catalog | Restart admin; verify `GET /api/items` count 290 |
| “saved account unavailable: failed to connect to https://127.0.0.1:8445” | ADB reverse tunnel lost | `adb reverse tcp:8445 tcp:8445`; `/reconnect` |
| Client connects but TLS fails | Cert missing/wrong IP SAN | Regenerate with `create-family-tls.py --host 127.0.0.1`; reinstall client CA |
| Admin unreachable | Admin service stopped/port conflict | `/start admin`; check 8080 |
| Phone has no internet | gnirehtet relay/VPN down | `/restart gnirehtet`; accept VPN prompt |
| Mega raid card shows 0CP | Legacy row lacks `temp_evo_cp` | `raids.info()` repairs on read; restart server |
| Master Ball missing from encounter selector | Client cached old GM | Restart server, force-stop/relaunch app, clear cache |
| Map black outside Netherlands | World snapshot has no roads there | Expected; gyms/stops/spawns still render |
| Spawns/gyms not visible in game | Server hasn't reloaded/admin wrote wrong DB | Restart server; check `/api/spawns`, `/api/pois` |
| `Address already in use` on 8445/8080 | Orphan process | `START-SERVER.bat` or `taskkill` the PID |
| Server exits on startup | Missing `data/static/gmts.binpb.gz`, config outside root, world missing | Read `logs/server.log` |
| Patcher: “Java runtime not found” | No JDK 17+ | Install JDK, set `JAVA_HOME`/PATH |
| Patcher: missing zipalign/apktool | Tools folder incomplete | Restore `tools/android-build-tools`, `tools/apktool` |
| Patcher: signature mismatch | Split not re-signed with same key | Rebuild/re-sign the whole set |
| Admin JSON 400 | Validation error (species/form/level/coords) | Read the `error` field; fix input |
| Admin JSON 500 | Unhandled server error | Check `logs/admin.log`/console; restart |
| DB locked/timeout | Long write or external sqlite session | Close external transaction; retry; avoid VACUUM |
| Test fails on `test_shiny_palette` | Missing `scripts/retarget-same-species-animation.py` | Known pre-existing; report, don't mask |

### Specific incidents from this project

1. **“Pokéstops are broken — blank image, can't spin.”** The game server had been stopped to apply
   the Mega changes and the session ended before restart. The client was talking to a closed port.
   Once the server was restarted, stops and spins worked. The loot code was never at fault.
2. **“Saving the loot table says Unknown endpoint.”** The admin process had been started before the
   `/api/loot` endpoints existed. It served the new HTML from disk but ran old Python. Restarting the
   admin fixed it. Same root cause for “only Poke Ball is selectable”: the backend catalog was old.
3. **“Mega raid shows 0CP.”** Legacy/admin raid rows had `temp_evo_cp = 0`. New rows set it, and
   `raids.info()` repairs old rows at read time.
4. **“Master Ball doesn't appear.”** The item needed a GM template (the snapshot omitted it) and catch
   handling for ball ID 4. Both were added; the client may still cache the old GM until a full app
   restart.

### Log reading

```powershell
Get-Content logs\server.log -Tail 80
Get-Content logs\server.log -Wait
Get-Content logs\admin.log -Tail 40
Get-Content logs\gnirehtet.log -Tail 40
Select-String -Path logs\server.log -Pattern "Traceback|ERROR|Unsupported"
```

RPC log lines show method IDs and names; a burst of `UNKNOWN_610001` is a client probe, not a crash.

### Database sanity

```bash
sqlite3 data/family-development.sqlite3 "PRAGMA integrity_check;"
sqlite3 data/family-development.sqlite3 "SELECT COUNT(*) FROM players;"
sqlite3 data/family-development.sqlite3 "SELECT item,count FROM items WHERE player_id='...';"
sqlite3 data/family-development.sqlite3 "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
```

Do not run these while a long RPC is writing without expecting a busy wait.

### Reset paths

| Goal | Action |
|---|---|
| Restart everything | `START-SERVER.bat` |
| Stop everything | `python -m dittobuffer /stop all` |
| Rebuild phone tunnels only | `python -m dittobuffer /reconnect` |
| Reset loot to defaults | Delete `data/admin/stop-loot.json` or POST the defaults |
| Reset admin UI cache | Refresh; the HTML is no-store |
| Reset client GM cache | Force-stop/relaunch app; clear cache/reinstall |
| Reset world overlay | Restore `data/world/admin-pois.json` from backup |
| Reset live DB | Stop server, restore a backup; never delete while running |

---

## 25. Change log of this dirty tree

This is the state of the working copy, not a git history. It records the changes that matter to a new
session and the files they touch.

### 25.1 Editable Pokéstop loot

| File | Change |
|---|---|
| `src/dittobuffer/world/world_rewards.py` | Rewritten around a JSON table with `count`, `guaranteed`, `drops`, `passes`; normalisation, atomic save, mtime cache, deterministic awards, template augmentation |
| `src/dittobuffer/admin/admin.py` | `Admin.loot()`, `Admin.save_loot()`, `GET/POST /api/loot` |
| `src/dittobuffer/admin/ui/admin_ui.html` | “Pokéstop loot” tab: items per spin, guaranteed rows, weighted rows with item/weight/min level/min-max count, pass probabilities, reset-to-defaults |
| `data/admin/stop-loot.json` | Live table (currently 19 picks, guaranteed ball, Master Ball row, 100% passes) |
| `tests/test_stop_loot.py` | Defaults, bounds, passes, deterministic awards, save/load, template augmentation |

Behavior: saving returns 200 and persists the file; the game reads it on the next spin. The published
world manifest is never rewritten.

### 25.2 Arbitrary item rewards and the 290-item catalog

| File | Change |
|---|---|
| `src/dittobuffer/admin/admin.py` | `item_catalog()` exposes every protocol item (290); `reward_catalog()`; `grant_rewards()` with XP/stardust/items/candy/XL/storage/Mega Energy; `GET/POST /api/rewards`, `GET /api/items`, `GET /api/mega-species` |
| `src/dittobuffer/admin/ui/admin_ui.html` | Rewards tab: player picker, XP/stardust, multiple item rows with quantity and autocomplete, candy/XL, storage, Mega Energy |
| `tests/test_trainer_rewards.py` | Reward bundle coverage |

The catalog is derived from `p.Item.items()`; duplicate aliases are collapsed by numeric value. This
is why the UI can grant Master Ball, passes, berries, TMs and every other protocol item without a
hard-coded shop list.

### 25.3 Mega raids and 0CP repair

| File | Change |
|---|---|
| `src/dittobuffer/gameplay/raids.py` | Tier 6 validation (`mega_evolution`, `mega_energy_rewards`, `reward_policy`), boss temporary-evolution fields, `temp_evo_cp`, `raids.info()` repair, Mega catch projection |
| `src/dittobuffer/gameplay/mega.py` | Energy/grant/level/project/catch-bonus paths used by raids |
| `src/dittobuffer/admin/admin.py` | Admin raid creation supports Mega forms, forces tier 6, sets temp-evo fields and `temp_evo_cp` |
| `config/family.json` | Venusaur tier 6 pool entry with energy bands |
| `tests/test_mega.py` | Mega raid/reward/0CP coverage |

Victory now rewards the Mega-evolved boss encounter in addition to Mega Energy. Old rows with
`temp_evo_cp = 0` are repaired when read.

### 25.4 Master Ball

| File | Change |
|---|---|
| `src/dittobuffer/core/master.py` | GM item template for `ITEM_MASTER_BALL` (ID 4, capture multiplier 255) |
| `src/dittobuffer/gameplay/game.py` | Wild catch accepts balls 1–4; raid remains ball 5 |
| `src/dittobuffer/gameplay/items.py` | Capture probabilities include the 255× Master Ball multiplier |
| `src/dittobuffer/world/world_rewards.py` | Default table adds config-only weights such as Master Ball |
| `config/family.json` | `world.spin_weights["4"] = 0.5` |
| `data/admin/stop-loot.json` | User-tuned Master Ball row (weight 100 in the live table) |
| `tests/test_stop_loot.py` | Master Ball default/weight coverage |

### 25.5 Level 50 and XL Candy

| File | Change |
|---|---|
| `config/family.json` | `level_requirements` extended to 50 entries; `xl_candy_min_player_level`, `power_up_xl_candy_cost`, `xl_catch_rates` |
| `src/dittobuffer/core/master.py` | Max normal upgrade 50, 50-entry cost arrays, XL candy settings, level cap 50, CP multipliers through 50 |
| `src/dittobuffer/gameplay/progression.py` | Local schedule validation up to 50; cap from config |
| `src/dittobuffer/core/management.py` | XL candy helpers |
| `src/dittobuffer/gameplay/game.py` | XL candy catch rolls/power-up handling |
| `tests/test_progression_modern.py` | Schedule/cap/bounds |

### 25.6 One-click launcher

`START-SERVER.bat` was added at the repository root. It is described in section 3. It is the only
Windows-specific launcher in the tree; the console remains the portable path.

### 25.7 What was not changed

- The published world snapshot and its manifest.
- The protocol schema (`protocol/game.proto`, `game_pb2.py`).
- The client APK inputs.
- The clean/production copy’s world/DB (it has none).
- Payment, analytics or telemetry integrations (there are none).

---

## 26. Security, privacy and legal posture

### Network

- Game listener: loopback only, TLS in the family profile.
- Admin: loopback only, **no authentication**. Treat it as a developer tool.
- gnirehtet/ADB: loopback; USB-tethered.
- No port forwarding, UPnP, public listener, or cloud dependency is part of the design.

### Identity

- Argon2id password hashes with per-password salts.
- Opaque bearer tokens; only SHA-256 digests stored.
- Separate credential and RPC session lifetimes.
- Rate limits per account and peer.
- Audit log capped at 10,000 rows.
- Generic account errors to avoid account enumeration.
- No raw credentials/tokens in logs.

### Data

- Player location is stored in `walking`/`admin_locations` for gameplay and admin display. It stays in
  the local DB.
- Social envelopes may contain device push tokens; do not log them. The fixture trace logs only action
  numbers/lengths.
- The admin panel calls PokeAPI for species names/sprites and the browser loads OSM tiles. Those are
  the only routine outbound requests; weather uses Open-Meteo when enabled.
- Backups contain account hashes, locations and gameplay history. Treat them as private.

### Legal

- This repository contains no Niantic server code. The patcher operates on an APK you supply.
- Running a modified client violates the Pokémon GO Terms of Service and can get the account banned.
- Assets retain their original rights; the code is MIT.
- Keep the server private, use accounts you are willing to lose, and do not redistribute assets you do
  not have rights to.

### Operational safety

- Do not bind the admin to `0.0.0.0`.
- Do not commit `.private/`, `config/tls/*-key.pem`, `data/family-development.sqlite3`, logs or APKs
  with embedded profiles.
- Do not run untrusted code with the admin API open; it can grant items and rewrite spawns.
- Do not “fix” TLS errors by disabling verification in the client; fix the certificate/IP SAN.

---

## 27. Known limitations and risks

| Area | Limitation |
|---|---|
| World | Netherlands snapshot only; other regions have no roads/buildings |
| Map query | `/map_query` callback delivery is not implemented; it returns status 0 |
| Unsupported RPCs | Some client probes (e.g. 610001) have no handler and return 400 |
| Raids | Local solo-first balance; co-op is limited/experimental; no production matchmaking |
| Social | Native social/trading is partial; some flows are fail-closed rather than fully featured |
| Assets | Pokémon/avatar asset serving is disabled by default; missing assets 404 |
| Client resources | `client_resources.enabled` is false; exact runtime registry is opt-in |
| Devices | Patcher does not test Android install/gameplay; device verification is manual |
| Tests | `test_shiny_palette` fails due to a missing script; full suite has one known error |
| Admin | No auth; loopback only; a stale process can serve old endpoints |
| GM cache | Client may keep old templates until a full app restart/clear cache |
| Windows | `START-SERVER.bat` is Windows-specific; other platforms use the console |
| MySQL/Postgres | Not supported; SQLite only |
| Payments | No real-money purchases; coins are local |
| Weather | Coarse-cell cached data, not live per-second weather |
| World editing | No importer UI; snapshot rebuild is out of scope |

Risks:

- Running a modified client can get the account banned.
- Exposing the admin port lets any local/network process grant itself items.
- Editing the live DB while the server is writing can corrupt state; stop and back up first.
- Copying a WAL database with a file copy can produce a torn backup; use `.backup`.
- Deleting `rpc_receipts` during a session can make retries re-execute.
- Replacing `gmts.binpb.gz` by hand can break the client; regenerate from source.

---

## 28. Glossary

| Term | Meaning |
|---|---|
| **ADB reverse** | `adb reverse tcp:8445 tcp:8445` maps the phone’s loopback port to the PC |
| **Admin panel** | Local Material 3 UI on 8080 for world/rewards/loot |
| **APK** | Android package; the patched client |
| **Argon2id** | Password hashing function used for family accounts |
| **Boss** | Raid Pokémon with configured HP/CP/tier |
| **CP** | Combat Power; derived from stats/level |
| **Ditto** | The project name; also a Pokémon |
| **Egg** | Raid egg (timer before boss) or incubating Pokémon egg; context distinguishes |
| **Fort** | A gym or Pokéstop (`forts.kind` 0/1) |
| **GM / GMTS** | Game Master templates (`gmts.binpb.gz`) |
| **gnirehtet** | Reverse-tethering VPN relay for the phone |
| **Loot table** | `data/admin/stop-loot.json`; weights/guarantees/passes |
| **Mega** | Temporary evolution with energy, levels and boosted CP |
| **PoI** | Point of interest; gym/stop/photo |
| **Premier Ball** | Raid-only ball (ID 5), tracked in `raid_members.balls` |
| **Protobuf** | Binary protocol used by the client/server |
| **RPC** | Batched gameplay request (`/rpc`) |
| **S2** | Geospatial cell system used for spawn points/map cells |
| **Snapshot** | Immutable published world SQLite file |
| **Ticket** | Bearer token exchanged for a family RPC session |
| **WAL** | SQLite write-ahead log mode used by the game DB |
| **XL Candy** | Candy used from level 40 to 50 |

---

## Appendix A — Module inventory
Every Python module under `src/dittobuffer`, with line count and the first line of its module
docstring. Use this as the map when a new session asks “where does X live?”

| Module | Lines | Purpose |
|---|---:|---|
| `src/dittobuffer/__init__.py` | 3 | Dittobuffer: a private, self-hosted Pokemon GO family server. |
| `src/dittobuffer/__main__.py` | 6 | ``python -m dittobuffer`` entry point. |
| `src/dittobuffer/admin/__init__.py` | 1 | Admin package for the Dittobuffer server. |
| `src/dittobuffer/admin/admin.py` | 780 | Local-only administrator console for the family development server. |
| `src/dittobuffer/cli/__init__.py` | 1 | Cli package for the Dittobuffer server. |
| `src/dittobuffer/cli/ascii.py` | 62 | ANSI banner art for the Dittobuffer console. |
| `src/dittobuffer/cli/console.py` | 333 | Interactive Dittobuffer console with slash commands. |
| `src/dittobuffer/cli/services.py` | 283 | Process and device management for the Dittobuffer console. |
| `src/dittobuffer/core/__init__.py` | 1 | Core package for the Dittobuffer server. |
| `src/dittobuffer/core/accounts.py` | 183 | Invite-only family identities over the existing player storage. |
| `src/dittobuffer/core/asset_catalog.py` | 310 | Offline Unity compact-catalog decoding and verified local bundle serving. |
| `src/dittobuffer/core/client_resources.py` | 22 | Exact, opt-in runtime resource registry. No fetching or arbitrary file serving. |
| `src/dittobuffer/core/database.py` | 318 | (no module docstring) |
| `src/dittobuffer/core/family_network.py` | 32 | Explicit private listener/TLS configuration; never opens firewall or VPN routes. |
| `src/dittobuffer/core/management.py` | 208 | Basic ownership and resource rules inside the existing RPC transaction. |
| `src/dittobuffer/core/master.py` | 205 | Local gameplay rules with losslessly preserved model and form definitions. |
| `src/dittobuffer/core/preferences.py` | 64 | Bounded server-owned player preferences; device toggles stay on the device. |
| `src/dittobuffer/gameplay/__init__.py` | 1 | Gameplay package for the Dittobuffer server. |
| `src/dittobuffer/gameplay/avatar.py` | 71 | A local appearance using default articles in the client's avatar catalog. |
| `src/dittobuffer/gameplay/backgrounds.py` | 44 | Opt-in original collectible cards, separate from ordinary type backdrops. |
| `src/dittobuffer/gameplay/battle_master.py` | 52 | Pinned compatible PvE balance, delivered identically to Android and combat. |
| `src/dittobuffer/gameplay/buddy.py` | 296 | Persistent buddies sharing the egg system's validated walking samples. |
| `src/dittobuffer/gameplay/buddy_perks.py` | 65 | Source-gated buddy gifts with explicit local scheduling and reward rules. |
| `src/dittobuffer/gameplay/combat.py` | 14 | Shared authoritative PvE damage, independent of gym/raid persistence. |
| `src/dittobuffer/gameplay/cooperative.py` | 202 | Durable family raid rooms over the existing PvE move/damage engine. |
| `src/dittobuffer/gameplay/costumes.py` | 23 | Catalog-derived legacy costumes; availability and gameplay proof are separate. |
| `src/dittobuffer/gameplay/eggs.py` | 182 | Persistent egg/incubator lifecycle in the existing gameplay transaction. |
| `src/dittobuffer/gameplay/evolutions.py` | 33 | Evaluate verified permanent evolution conditions before consuming anything. |
| `src/dittobuffer/gameplay/friends.py` | 292 | Native social proxy, authorized by Game.rpc's principal and transaction. |
| `src/dittobuffer/gameplay/game.py` | 1276 | Authoritative transactions. Wire messages always come from the supplied schema. |
| `src/dittobuffer/gameplay/gifts.py` | 156 | Published-stop Gifts; mutations run in the authenticated RPC transaction. |
| `src/dittobuffer/gameplay/gym_battle.py` | 291 | Timed, persisted PvE combat. Client actions are intentions, never damage claims. |
| `src/dittobuffer/gameplay/gym_notifications.py` | 44 | Durable gym-return inbox records. Delivery never grants rewards. |
| `src/dittobuffer/gameplay/gyms.py` | 248 | Persistent ordinary gyms. Every mutation shares the existing RPC transaction. |
| `src/dittobuffer/gameplay/items.py` | 88 | Validated item mutations, called inside the existing RPC transaction. |
| `src/dittobuffer/gameplay/journal.py` | 39 | Native action-log projection; never disguise unsupported event kinds. |
| `src/dittobuffer/gameplay/medals.py` | 112 | Source-backed medals projected from durable, committed gameplay facts. |
| `src/dittobuffer/gameplay/mega.py` | 424 | Source-bound temporary evolution; owned identity/stats remain ordinary. |
| `src/dittobuffer/gameplay/pokedex.py` | 62 | Persistent registration independent of transfers and current storage. |
| `src/dittobuffer/gameplay/pokemon_moves.py` | 58 | Form-aware TMs and second Charged Attacks in the existing RPC transaction. |
| `src/dittobuffer/gameplay/pokemon_sizes.py` | 65 | Pinned height boundaries; explicitly configurable custom size distribution. |
| `src/dittobuffer/gameplay/pokemon_tags.py` | 73 | Owned storage tags, serialized in the client's native tag records. |
| `src/dittobuffer/gameplay/progression.py` | 35 | One configurable level schedule shared by inventory and Game Master. |
| `src/dittobuffer/gameplay/raid_items.py` | 13 | Existing regular item handlers plus two source-backed reward definitions. |
| `src/dittobuffer/gameplay/raid_transport.py` | 138 | 0.429.2 Nebula HTTP adapter over the existing authoritative PvE clock. |
| `src/dittobuffer/gameplay/raids.py` | 671 | Local, persistent solo raids. No production services or synthetic participants. |
| `src/dittobuffer/gameplay/remote_trading.py` | 93 | Durable remote negotiation; native final-consent binding remains fail-closed. |
| `src/dittobuffer/gameplay/research.py` | 274 | Persistent local Research, inside the existing gameplay transactions. |
| `src/dittobuffer/gameplay/shop.py` | 98 | Local coin-only catalog. A durable, owned offer is the purchase retry key. |
| `src/dittobuffer/gameplay/species.py` | 183 | Read verified gameplay fields without reserializing opaque model templates. |
| `src/dittobuffer/gameplay/stories.py` | 123 | Frozen, multistage Special Research using the shared objective/reward engine. |
| `src/dittobuffer/gameplay/trading.py` | 210 | Two-consent native trading. SQLite owns locks, finalization and receipts. |
| `src/dittobuffer/gameplay/trainer.py` | 41 | Additive trainer records and once-only committed gameplay events. |
| `src/dittobuffer/gameplay/trainer_rewards.py` | 42 | Configurable cosmetic bundles for level, research and future event callers. |
| `src/dittobuffer/gameplay/wardrobe.py` | 171 | Owned neutral-avatar articles from the dependency-checked local registry. |
| `src/dittobuffer/patcher/frida_patch.py` | 463 | Inject Frida Gadget into an Android APK for non-rooted devices. |
| `src/dittobuffer/patcher/patch.py` | 180 | Unified APK patcher for the Dittobuffer kit. |
| `src/dittobuffer/paths.py` | 38 | Filesystem anchors for the Dittobuffer server. |
| `src/dittobuffer/runtime/__init__.py` | 1 | Runtime package for the Dittobuffer server. |
| `src/dittobuffer/runtime/http.py` | 226 | (no module docstring) |
| `src/dittobuffer/world/__init__.py` | 1 | World package for the Dittobuffer server. |
| `src/dittobuffer/world/world_admin.py` | 99 | Administrator POIs layered over the published world. |
| `src/dittobuffer/world/world_images.py` | 17 | One byte-preserved local placeholder shared by every photo-pending POI. |
| `src/dittobuffer/world/world_json.py` | 153 | JSON-backed development world. |
| `src/dittobuffer/world/world_map.py` | 109 | Local Lightship vector bundles derived from a cached ODbL OSM extract. |
| `src/dittobuffer/world/world_rewards.py` | 163 | Local, admin-editable Pokéstop loot table. |
| `src/dittobuffer/world/world_store.py` | 310 | Indexed, versioned public geographic content; trainer state stays in its own DB. |
| `src/dittobuffer/world/world_tiles.py` | 74 | OSM geometry to the native Lightship triangle/polyline wire format. |
| `src/dittobuffer/world/world_weather.py` | 120 | Shared coarse-cell weather cache; no per-player trails or blocking network RPCs. |

**Total:** 11,069 lines across 69 modules.

---

## Appendix B — RPC handler names

Handlers are discovered by name: `rpc_` + lowercased protobuf method name. This list is extracted
from the source, grouped by file. It is the practical inventory of implemented gameplay.

| Module | Handlers |
|---|---|
| `src/dittobuffer/gameplay/game.py` | `get_player`, `get_rocket_balloon`, `get_vs_seeker_status`, `get_holoholo_inventory`, `download_settings`, `download_remote_config_version`, `download_game_config_version`, `get_server_time`, `get_player_day`, `get_player_profile`, `sfida_action_log`, `process_player_inbox`, `get_new_quests`, `get_quest_details`, `complete_quest`, `remove_quest`, `quest_encounter`, `complete_quest_stamp_card`, `get_quest_ui`, `read_quest_dialog`, `check_awarded_badges`, `list_avatar_store_items`, `list_avatar_appearance_items`, `set_neutral_avatar`, `neutral_avatar_badge_reward`, `get_contests_unclaimed_rewards`, `get_party`, `get_daily_encounter`, `get_photobomb`, `remote_gift_ping`, `get_image_gallery_settings`, `get_weekly_challenge_info`, `get_pokemon_tags`, `create_pokemon_tag`, `edit_pokemon_tag`, `delete_pokemon_tag`, `set_pokemon_tags_for_pokemon`, `get_fitness_report`, `get_web_token_action`, `get_available_skus_and_balances`, `purchase_sku`, `is_sku_available`, `fetch_newsfeed_action`, `get_incense_recap`, `get_buddy_walked`, `set_buddy_pokemon`, `get_buddy_stats`, `get_buddy_map`, `feed_buddy`, `pet_buddy`, `open_buddy_gift`, `get_buddy_history`, `complete_snapshot_session`, `save_player_preferences`, `save_social_player_settings`, `save_combat_player_preferences`, `set_contact_settings`, `mark_tutorial_complete`, `download_game_master_templates`, `get_outstanding_warnings`, `mark_remote_tradable`, `get_remote_tradable_pokemon_from_other_player`, `propose_remote_trade`, `get_pending_remote_trade`, `cancel_remote_trade`, `respond_remote_trade`, `open_trading`, `get_trading`, `update_trading`, `confirm_trading`, `cancel_trading`, `send_gift`, `save_player_snapshot`, `get_friendship_milestone_rewards`, `open_gift`, `gift_details`, `check_send_gift`, `delete_gift_from_inventory`, `create_postcard`, `get_memento_list`, `update_postcard`, `delete_postcards`, `proxy_social_side_channel_action`, `proxy_social_action`, `get_inbox`, `gar_proxy_action`, `fort_details`, `get_map_objects`, `encounter`, `catch_pokemon`, `release_pokemon`, `use_item_move_reroll`, `unlock_special_move`, `set_favorite_pokemon`, `nickname_pokemon`, `recycle_inventory_item`, `use_item_encounter`, `use_item_potion`, `use_item_egg_incubator`, `get_hatched_eggs`, `use_item_revive`, `use_item_bulk_heal`, `upgrade_pokemon`, `mega_evolve_pokemon`, `get_mega_level_up_preview`, `mega_level_up`, `unlock_temporary_evolution_level`, `evolve_pokemon`, `level_up_rewards`, `fort_search`, `set_player_team`, `gym_get_info`, `gym_deploy`, `gym_feed_pokemon`, `list_gym_badges`, `get_gym_badge_details`, `gym_start_session`, `gym_battle_attack`, `get_raid_details`, `get_simple_raid_details`, `join_lobby`, `leave_lobby`, `get_raid_lobby_details`, `award_free_raid_ticket`, `start_raid_battle`, `attack_raid`, `complete_raid_battle`, `sync_battle_inventory`, `get_raid_encounter_details` |

**Total handlers:** 129 across 1 module.

---

## Appendix C — Every protobuf `Method` enum

The full 0.429.2 method surface. Only names with an `rpc_` handler are implemented (Appendix B).
Any other value is rejected with `Unsupported method`.

| ID | Method |
|---:|---|
| 0 | `Method_METHOD_UNSET` |
| 2 | `Method_GET_PLAYER` |
| 4 | `Method_GET_HOLOHOLO_INVENTORY` |
| 5 | `Method_DOWNLOAD_SETTINGS` |
| 6 | `Method_DOWNLOAD_ITEM_TEMPLATES` |
| 7 | `Method_DOWNLOAD_REMOTE_CONFIG_VERSION` |
| 8 | `Method_REGISTER_BACKGROUND_DEVICE` |
| 9 | `Method_GET_PLAYER_DAY` |
| 10 | `Method_ACKNOWLEDGE_PUNISHMENT` |
| 11 | `Method_GET_SERVER_TIME` |
| 12 | `Method_GET_LOCAL_TIME` |
| 20 | `Method_SET_PLAYER_STATUS` |
| 21 | `Method_DOWNLOAD_GAME_CONFIG_VERSION` |
| 22 | `Method_GET_GPS_BOOKMARKS` |
| 23 | `Method_UPDATE_GPS_BOOKMARKS` |
| 101 | `Method_FORT_SEARCH` |
| 102 | `Method_ENCOUNTER` |
| 103 | `Method_CATCH_POKEMON` |
| 104 | `Method_FORT_DETAILS` |
| 106 | `Method_GET_MAP_OBJECTS` |
| 110 | `Method_FORT_DEPLOY_POKEMON` |
| 111 | `Method_FORT_RECALL_POKEMON` |
| 112 | `Method_RELEASE_POKEMON` |
| 113 | `Method_USE_ITEM_POTION` |
| 114 | `Method_USE_ITEM_CAPTURE` |
| 115 | `Method_USE_ITEM_FLEE` |
| 116 | `Method_USE_ITEM_REVIVE` |
| 121 | `Method_GET_PLAYER_PROFILE` |
| 125 | `Method_EVOLVE_POKEMON` |
| 126 | `Method_GET_HATCHED_EGGS` |
| 127 | `Method_ENCOUNTER_TUTORIAL_COMPLETE` |
| 128 | `Method_LEVEL_UP_REWARDS` |
| 129 | `Method_CHECK_AWARDED_BADGES` |
| 137 | `Method_RECYCLE_INVENTORY_ITEM` |
| 138 | `Method_COLLECT_DAILY_BONUS` |
| 139 | `Method_USE_ITEM_XP_BOOST` |
| 140 | `Method_USE_ITEM_EGG_INCUBATOR` |
| 141 | `Method_USE_INCENSE` |
| 142 | `Method_GET_INCENSE_POKEMON` |
| 143 | `Method_INCENSE_ENCOUNTER` |
| 144 | `Method_ADD_FORT_MODIFIER` |
| 145 | `Method_DISK_ENCOUNTER` |
| 147 | `Method_UPGRADE_POKEMON` |
| 148 | `Method_SET_FAVORITE_POKEMON` |
| 149 | `Method_NICKNAME_POKEMON` |
| 150 | `Method_EQUIP_BADGE` |
| 151 | `Method_SET_CONTACT_SETTINGS` |
| 152 | `Method_SET_BUDDY_POKEMON` |
| 153 | `Method_GET_BUDDY_WALKED` |
| 154 | `Method_USE_ITEM_ENCOUNTER` |
| 155 | `Method_GYM_DEPLOY` |
| 156 | `Method_GYM_GET_INFO` |
| 157 | `Method_GYM_START_SESSION` |
| 158 | `Method_GYM_BATTLE_ATTACK` |
| 159 | `Method_JOIN_LOBBY` |
| 160 | `Method_LEAVE_LOBBY` |
| 161 | `Method_SET_LOBBY_VISIBILITY` |
| 162 | `Method_SET_LOBBY_POKEMON` |
| 163 | `Method_GET_RAID_DETAILS` |
| 164 | `Method_GYM_FEED_POKEMON` |
| 165 | `Method_START_RAID_BATTLE` |
| 166 | `Method_ATTACK_RAID` |
| 167 | `Method_AWARD_POKECOIN` |
| 168 | `Method_USE_ITEM_STARDUST_BOOST` |
| 169 | `Method_REASSIGN_PLAYER` |
| 170 | `Method_REDEEM_POI_PASSCODE` |
| 171 | `Method_CONVERT_CANDY_TO_XL_CANDY` |
| 172 | `Method_IS_SKU_AVAILABLE` |
| 173 | `Method_USE_ITEM_BULK_HEAL` |
| 174 | `Method_USE_ITEM_BATTLE_BOOST` |
| 175 | `Method_USE_ITEM_LUCKY_FRIEND_APPLICATOR` |
| 176 | `Method_USE_ITEM_STAT_INCREASE` |
| 177 | `Method_GET_PLAYER_STATUS_PROXY` |
| 300 | `Method_GET_ASSET_DIGEST` |
| 301 | `Method_GET_DOWNLOAD_URLS` |
| 302 | `Method_GET_ASSET_VERSION` |
| 403 | `Method_CLAIM_CODENAME` |
| 404 | `Method_SET_AVATAR` |
| 405 | `Method_SET_PLAYER_TEAM` |
| 406 | `Method_MARK_TUTORIAL_COMPLETE` |
| 407 | `Method_UPDATE_PERFORMANCE_METRICS` |
| 408 | `Method_SET_NEUTRAL_AVATAR` |
| 409 | `Method_LIST_AVATAR_STORE_ITEMS` |
| 410 | `Method_LIST_AVATAR_APPEARANCE_ITEMS` |
| 450 | `Method_NEUTRAL_AVATAR_BADGE_REWARD` |
| 600 | `Method_CHECK_CHALLENGE` |
| 601 | `Method_VERIFY_CHALLENGE` |
| 666 | `Method_ECHO` |
| 800 | `Method_SFIDA_REGISTRATION` |
| 801 | `Method_SFIDA_ACTION_LOG` |
| 802 | `Method_SFIDA_CERTIFICATION` |
| 803 | `Method_SFIDA_UPDATE` |
| 804 | `Method_SFIDA_ACTION` |
| 805 | `Method_SFIDA_DOWSER` |
| 806 | `Method_SFIDA_CAPTURE` |
| 807 | `Method_LIST_AVATAR_CUSTOMIZATIONS` |
| 808 | `Method_SET_AVATAR_ITEM_AS_VIEWED` |
| 809 | `Method_GET_INBOX` |
| 811 | `Method_LIST_GYM_BADGES` |
| 812 | `Method_GET_GYM_BADGE_DETAILS` |
| 813 | `Method_USE_ITEM_MOVE_REROLL` |
| 814 | `Method_USE_ITEM_RARE_CANDY` |
| 815 | `Method_AWARD_FREE_RAID_TICKET` |
| 816 | `Method_FETCH_ALL_NEWS` |
| 817 | `Method_MARK_READ_NEWS_ARTICLE` |
| 818 | `Method_GET_PLAYER_DISPLAY_INFO` |
| 819 | `Method_BELUGA_TRANSACTION_START` |
| 820 | `Method_BELUGA_TRANSACTION_COMPLETE` |
| 822 | `Method_SFIDA_ASSOCIATE` |
| 823 | `Method_SFIDA_CHECK_PAIRING` |
| 824 | `Method_SFIDA_DISASSOCIATE` |
| 825 | `Method_WAINA_GET_REWARDS` |
| 826 | `Method_WAINA_SUBMIT_SLEEP_DATA` |
| 827 | `Method_SATURDAY_TRANSACTION_START` |
| 828 | `Method_SATURDAY_TRANSACTION_COMPLETE` |
| 829 | `Method_REIMBURSE_ITEM` |
| 830 | `Method_LIFT_USER_AGE_CONFIRMATION` |
| 831 | `Method_SOFT_SFIDA_START` |
| 832 | `Method_SOFT_SFIDA_PAUSE` |
| 833 | `Method_SOFT_SFIDA_CAPTURE` |
| 834 | `Method_SOFT_SFIDA_LOCATION_UPDATE` |
| 835 | `Method_SOFT_SFIDA_RECAP` |
| 900 | `Method_GET_NEW_QUESTS` |
| 901 | `Method_GET_QUEST_DETAILS` |
| 902 | `Method_COMPLETE_QUEST` |
| 903 | `Method_REMOVE_QUEST` |
| 904 | `Method_QUEST_ENCOUNTER` |
| 905 | `Method_COMPLETE_QUEST_STAMP_CARD` |
| 906 | `Method_PROGRESS_QUEST` |
| 907 | `Method_START_QUEST_INCIDENT` |
| 908 | `Method_READ_QUEST_DIALOG` |
| 909 | `Method_DEQUEUE_QUEST_DIALOGUE` |
| 950 | `Method_SEND_GIFT` |
| 951 | `Method_OPEN_GIFT` |
| 952 | `Method_GIFT_DETAILS` |
| 953 | `Method_DELETE_GIFT` |
| 954 | `Method_SAVE_PLAYER_SNAPSHOT` |
| 955 | `Method_GET_FRIENDSHIP_MILESTONE_REWARDS` |
| 956 | `Method_CHECK_SEND_GIFT` |
| 957 | `Method_SET_FRIEND_NICKNAME` |
| 958 | `Method_DELETE_GIFT_FROM_INVENTORY` |
| 959 | `Method_SAVE_SOCIAL_PLAYER_SETTINGS` |
| 963 | `Method_SET_FRIEND_PREMIUM_GIFT_PREFERENCE` |
| 970 | `Method_OPEN_TRADING` |
| 971 | `Method_UPDATE_TRADING` |
| 972 | `Method_CONFIRM_TRADING` |
| 973 | `Method_CANCEL_TRADING` |
| 974 | `Method_GET_TRADING` |
| 980 | `Method_GET_FITNESS_REWARDS` |
| 990 | `Method_GET_COMBAT_PLAYER_PROFILE` |
| 991 | `Method_GENERATE_COMBAT_CHALLENGE_ID` |
| 992 | `Method_CREATE_COMBAT_CHALLENGE` |
| 993 | `Method_OPEN_COMBAT_CHALLENGE` |
| 994 | `Method_GET_COMBAT_CHALLENGE` |
| 995 | `Method_ACCEPT_COMBAT_CHALLENGE` |
| 996 | `Method_DECLINE_COMBAT_CHALLENGE` |
| 997 | `Method_CANCEL_COMBAT_CHALLENGE` |
| 998 | `Method_SUBMIT_COMBAT_CHALLENGE_POKEMONS` |
| 999 | `Method_SAVE_COMBAT_PLAYER_PREFERENCES` |
| 1000 | `Method_OPEN_COMBAT_SESSION` |
| 1001 | `Method_UPDATE_COMBAT` |
| 1002 | `Method_QUIT_COMBAT` |
| 1003 | `Method_GET_COMBAT_RESULTS` |
| 1004 | `Method_UNLOCK_SPECIAL_MOVE` |
| 1005 | `Method_GET_NPC_COMBAT_REWARDS` |
| 1006 | `Method_COMBAT_FRIEND_REQUEST` |
| 1007 | `Method_OPEN_NPC_COMBAT_SESSION` |
| 1008 | `Method_START_TUTORIAL_ACTION` |
| 1009 | `Method_GET_TUTORIAL_EGG_ACTION` |
| 1020 | `Method_SEND_PROBE` |
| 1021 | `Method_PROBE_DATA` |
| 1022 | `Method_COMBAT_DATA` |
| 1023 | `Method_COMBAT_CHALLENGE_DATA` |
| 1101 | `Method_CHECK_PHOTOBOMB` |
| 1102 | `Method_CONFIRM_PHOTOBOMB` |
| 1103 | `Method_GET_PHOTOBOMB` |
| 1104 | `Method_ENCOUNTER_PHOTOBOMB` |
| 1105 | `Method_GET_SIGNED_GMAP_URL_DEPRECATED` |
| 1106 | `Method_CHANGE_TEAM` |
| 1107 | `Method_GET_WEB_TOKEN` |
| 1110 | `Method_COMPLETE_SNAPSHOT_SESSION` |
| 1111 | `Method_COMPLETE_WILD_SNAPSHOT_SESSION` |
| 1200 | `Method_START_INCIDENT` |
| 1201 | `Method_INVASION_COMPLETE_DIALOGUE` |
| 1202 | `Method_INVASION_OPEN_COMBAT_SESSION` |
| 1203 | `Method_INVASION_BATTLE_UPDATE` |
| 1204 | `Method_INVASION_ENCOUNTER` |
| 1205 | `Method_PURIFY_POKEMON` |
| 1206 | `Method_GET_ROCKET_BALLOON` |
| 1207 | `Method_START_ROCKET_BALLOON_INCIDENT` |
| 1300 | `Method_VS_SEEKER_START_MATCHMAKING` |
| 1301 | `Method_CANCEL_MATCHMAKING` |
| 1302 | `Method_GET_MATCHMAKING_STATUS` |
| 1303 | `Method_COMPLETE_VS_SEEKER_AND_RESTART_CHARGING` |
| 1304 | `Method_GET_VS_SEEKER_STATUS` |
| 1305 | `Method_COMPLETE_COMBAT_COMPETITIVE_SEASON_ACTION` |
| 1306 | `Method_CLAIM_VS_SEEKER_REWARDS` |
| 1307 | `Method_VS_SEEKER_REWARD_ENCOUNTER` |
| 1308 | `Method_ACTIVATE_VS_SEEKER` |
| 1350 | `Method_GET_BUDDY_MAP` |
| 1351 | `Method_GET_BUDDY_STATS` |
| 1352 | `Method_FEED_BUDDY` |
| 1353 | `Method_OPEN_BUDDY_GIFT` |
| 1354 | `Method_PET_BUDDY` |
| 1355 | `Method_GET_BUDDY_HISTORY` |
| 1400 | `Method_UPDATE_ROUTE_DRAFT` |
| 1401 | `Method_GET_MAP_FORTS` |
| 1402 | `Method_SUBMIT_ROUTE_DRAFT` |
| 1403 | `Method_GET_PUBLISHED_ROUTES` |
| 1404 | `Method_START_ROUTE` |
| 1405 | `Method_GET_ROUTES` |
| 1406 | `Method_PROGRESS_ROUTE` |
| 1408 | `Method_PROCESS_TAPPABLE` |
| 1409 | `Method_LIST_ROUTE_BADGES` |
| 1410 | `Method_CANCEL_ROUTE` |
| 1411 | `Method_LIST_ROUTE_STAMPS` |
| 1412 | `Method_RATE_ROUTE` |
| 1413 | `Method_CREATE_ROUTE_DRAFT` |
| 1414 | `Method_DELETE_ROUTE_DRAFT` |
| 1415 | `Method_REPORT_ROUTE` |
| 1416 | `Method_SPAWN_TAPPABLE` |
| 1417 | `Method_ROUTE_ENCOUNTER` |
| 1418 | `Method_CAN_REPORT_ROUTE` |
| 1420 | `Method_ROUTE_UPTATE_SEEN` |
| 1421 | `Method_RECALL_ROUTE_DRAFT` |
| 1422 | `Method_ROUTES_NEARBY_NOTIF_SHOWN` |
| 1423 | `Method_NPC_ROUTE_GIFT` |
| 1424 | `Method_GET_ROUTE_CREATIONS` |
| 1425 | `Method_APPEAL_ROUTE` |
| 1426 | `Method_GET_ROUTE_DRAFT` |
| 1427 | `Method_FAVORITE_ROUTE` |
| 1428 | `Method_CREATE_ROUTE_SHORTCODE` |
| 1429 | `Method_GET_ROUTE_BY_SHORTCODE` |
| 1456 | `Method_CREATE_BUDDY_MUTLIPLAYER_SESSION` |
| 1457 | `Method_JOIN_BUDDY_MULTIPLAYER_SESSION` |
| 1458 | `Method_LEAVE_BUDDY_MULTIPLAYER_SESSION` |
| 1502 | `Method_MEGA_EVOLVE_POKEMON` |
| 1503 | `Method_REMOTE_GIFT_PING` |
| 1504 | `Method_SEND_RAID_INVITATION` |
| 1505 | `Method_SEND_BREAD_BATTLE_INVITATION` |
| 1506 | `Method_UNLOCK_TEMPORARY_EVOLUTION_LEVEL` |
| 1601 | `Method_GET_DAILY_ENCOUNTER` |
| 1602 | `Method_DAILY_ENCOUNTER` |
| 1650 | `Method_OPEN_SPONSORED_GIFT` |
| 1651 | `Method_SPONSORED_GIFT_REPORT_INTERACTION` |
| 1652 | `Method_SAVE_PLAYER_PREFERENCES` |
| 1653 | `Method_PROFANITY_CHECK` |
| 1700 | `Method_GET_TIMED_GROUP_CHALLENGE` |
| 1710 | `Method_GET_NINTENDO_ACCOUNT` |
| 1711 | `Method_UNLINK_NINTENDO_ACCOUNT` |
| 1712 | `Method_GET_NINTENDO_OAUTH2_URL` |
| 1713 | `Method_TRANSFER_TO_POKEMON_HOME` |
| 1716 | `Method_REPORT_AD_FEEDBACK` |
| 1717 | `Method_CREATE_POKEMON_TAG` |
| 1718 | `Method_DELETE_POKEMON_TAG` |
| 1719 | `Method_EDIT_POKEMON_TAG` |
| 1720 | `Method_SET_POKEMON_TAGS_FOR_POKEMON` |
| 1721 | `Method_GET_POKEMON_TAGS` |
| 1722 | `Method_CHANGE_POKEMON_FORM` |
| 1723 | `Method_CHOOSE_EVENT_VARIANT` |
| 1724 | `Method_BUTTERFLY_COLLECTOR_REWARD_ENCOUNTER` |
| 1725 | `Method_GET_ADDITIONAL_POKEMON_DETAILS` |
| 1726 | `Method_CREATE_ROUTE_PIN` |
| 1727 | `Method_LIKE_ROUTE_PIN` |
| 1728 | `Method_VIEW_ROUTE_PIN` |
| 1800 | `Method_GET_REFERRAL_CODE` |
| 1801 | `Method_ADD_REFERRER` |
| 1802 | `Method_SEND_FRIEND_INVITE_VIA_REFERRAL_CODE` |
| 1803 | `Method_GET_MILESTONES` |
| 1804 | `Method_MARK_MILESTONES_AS_VIEWED` |
| 1805 | `Method_GET_MILESTONES_PREVIEW` |
| 1806 | `Method_COMPLETE_MILESTONE` |
| 1820 | `Method_GET_GEOFENCED_AD` |
| 1900 | `Method_POWER_UP_POKESTOP_ENCOUNTER` |
| 1901 | `Method_GET_PLAYER_STAMP_COLLECTIONS` |
| 1902 | `Method_SAVE_STAMP` |
| 1904 | `Method_CLAIM_STAMP_COLLECTION_REWARD` |
| 1905 | `Method_CHANGE_STAMP_COLLECTION_PLAYER_DATA` |
| 1906 | `Method_CHECK_STAMP_GIFTABILITY` |
| 1909 | `Method_DELETE_POSTCARDS` |
| 1910 | `Method_CREATE_POSTCARD` |
| 1911 | `Method_UPDATE_POSTCARD` |
| 1913 | `Method_GET_MEMENTO_LIST` |
| 1914 | `Method_UPLOAD_RAID_CLIENT_LOG` |
| 1915 | `Method_SKIP_ENTER_REFERRAL_CODE` |
| 1916 | `Method_UPLOAD_COMBAT_CLIENT_LOG` |
| 1917 | `Method_COMBAT_SYNC_SERVER_OFFSET` |
| 2000 | `Method_CHECK_GIFTING_ELIGIBILITY` |
| 2001 | `Method_REDEEM_TICKET_GIFT_FOR_FRIEND` |
| 2002 | `Method_GET_INCENSE_RECAP` |
| 2003 | `Method_ACKNOWLEDGE_INCENSE_RECAP` |
| 2004 | `Method_BOOT_RAID` |
| 2005 | `Method_GET_POKESTOP_ENCOUNTER` |
| 2006 | `Method_ENCOUNTER_POKESTOP_ENCOUNTER` |
| 2007 | `Method_POLL_PLAYER_SPAWNABLE_POKEMON` |
| 2008 | `Method_GET_QUEST_UI` |
| 2009 | `Method_GET_ELIGIBLE_COMBAT_LEAGUES` |
| 2010 | `Method_SEND_FRIEND_REQUEST_VIA_PLAYER_IDS` |
| 2011 | `Method_GET_RAID_LOBBY_COUNTER` |
| 2014 | `Method_USE_NON_COMBAT_MOVE` |
| 2100 | `Method_CHECK_POKEMON_SIZE_CONTEST_ELIGIBILITY` |
| 2101 | `Method_UPDATE_POKEMON_SIZE_CONTEST_ENTRY` |
| 2102 | `Method_TRANSFER_POKEMON_SIZE_CONTEST_ENTRY` |
| 2103 | `Method_REMOVE_POKEMON_SIZE_CONTEST_ENTRY` |
| 2104 | `Method_GET_POKEMON_SIZE_CONTEST_ENTRY` |
| 2105 | `Method_GET_CONTEST_DATA` |
| 2106 | `Method_GET_CONTESTS_UNCLAIMED_REWARDS` |
| 2107 | `Method_CLAIM_CONTESTS_REWARDS` |
| 2108 | `Method_GET_ENTERED_CONTEST` |
| 2109 | `Method_GET_POKEMON_SIZE_CONTEST_FRIEND_ENTRY` |
| 2150 | `Method_CHECK_CONTEST_ELIGIBILITY` |
| 2151 | `Method_UPDATE_CONTEST_ENTRY` |
| 2152 | `Method_TRANSFER_CONTEST_ENTRY` |
| 2153 | `Method_GET_CONTEST_FRIEND_ENTRY` |
| 2154 | `Method_GET_CONTEST_ENTRY` |
| 2300 | `Method_CREATE_PARTY` |
| 2301 | `Method_JOIN_PARTY` |
| 2302 | `Method_START_PARTY` |
| 2303 | `Method_LEAVE_PARTY` |
| 2304 | `Method_GET_PARTY` |
| 2305 | `Method_UPDATE_PARTY_LOCATION` |
| 2306 | `Method_SEND_PARTY_DARK_LAUNCH_LOG` |
| 2308 | `Method_START_PARTY_QUEST` |
| 2309 | `Method_COMPLETE_PARTY_QUEST` |
| 2310 | `Method_SEND_PARTY_INVITE` |
| 2312 | `Method_CANCEL_PARTY_INVITE` |
| 2350 | `Method_GET_BONUS_ATTRACTED_POKEMON` |
| 2352 | `Method_GET_BONUSES` |
| 2360 | `Method_BADGE_REWARD_ENCOUNTER` |
| 2400 | `Method_NPC_UPDATE_STATE` |
| 2401 | `Method_NPC_SEND_GIFT` |
| 2402 | `Method_NPC_OPEN_GIFT` |
| 2450 | `Method_JOIN_BREAD_LOBBY` |
| 2452 | `Method_SET_BREAD_LOBBY_PUBLIC` |
| 2453 | `Method_PREPARE_BREAD_LOBBY` |
| 2455 | `Method_LEAVE_BREAD_LOBBY` |
| 2456 | `Method_START_BREAD_BATTLE` |
| 2457 | `Method_GET_BREAD_LOBBY_DETAILS` |
| 2458 | `Method_START_MP_WALK_QUEST` |
| 2459 | `Method_ENHANCE_BREAD_MOVE` |
| 2460 | `Method_STATION_POKEMON` |
| 2461 | `Method_LOOT_STATION` |
| 2462 | `Method_GET_STATION_DETAILS` |
| 2463 | `Method_MARK_SAVE_FOR_LATER` |
| 2464 | `Method_USE_SAVE_FOR_LATER` |
| 2465 | `Method_REMOVE_SAVE_FOR_LATER` |
| 2466 | `Method_GET_SAVE_FOR_LATER_ENTRIES` |
| 2467 | `Method_GET_MP_SUMMARY` |
| 2468 | `Method_REPLENISH_MP` |
| 2470 | `Method_REPORT_STATION` |
| 2471 | `Method_DEBUG_RESET_DAILY_MP` |
| 2472 | `Method_RELEASE_STATIONED_POKEMON` |
| 2473 | `Method_COMPLETE_BREAD_BATTLE` |
| 2475 | `Method_ENCOUNTER_STATION_SPAWN` |
| 2476 | `Method_GET_NUM_STATION_ASSISTS` |
| 2501 | `Method_PT_TWO` |
| 2600 | `Method_PROPOSE_REMOTE_TRADE` |
| 2601 | `Method_CANCEL_REMOTE_TRADE` |
| 2602 | `Method_MARK_REMOTE_TRADABLE` |
| 2603 | `Method_GET_REMOTE_TRADABLE_FROM_OTHER_PLAYER` |
| 2604 | `Method_GET_NON_REMOTE_TRADABLE_POKEMON` |
| 2605 | `Method_GET_PENDING_REMOTE_TRADE` |
| 2606 | `Method_RESPOND_REMOTE_TRADE` |
| 2607 | `Method_GET_POKEMON_TRADING_DETAILS` |
| 2608 | `Method_GET_POKEMON_TRADING_COST` |
| 3000 | `Method_GET_VPS_EVENTS` |
| 3001 | `Method_UPDATE_VPS_EVENTS` |
| 3002 | `Method_ADD_PTC_LOGIN_ACTION` |
| 3003 | `Method_CLAIM_PTC_LINKING_REWARD` |
| 3004 | `Method_CAN_CLAIM_PTC_REWARD_ACTION` |
| 3005 | `Method_CONTRIBUTE_PARTY_ITEMS` |
| 3006 | `Method_CONSUME_PARTY_ITEMS` |
| 3007 | `Method_REMOVE_PTC_LOGIN` |
| 3008 | `Method_SEND_PARTY_PLAY_INVITE` |
| 3009 | `Method_CONSUME_STICKERS` |
| 3010 | `Method_COMPLETE_RAID_BATTLE` |
| 3011 | `Method_SYNC_BATTLE_INVENTORY` |
| 3015 | `Method_PREVIEW_CONTRIBUTE_PARTY_ITEMS` |
| 3016 | `Method_KICK_FROM_PARTY` |
| 3017 | `Method_FUSE_POKEMON` |
| 3018 | `Method_UNFUSE_POKEMON` |
| 3019 | `Method_GET_IRIS_SOCIAL_SCENE` |
| 3020 | `Method_UPDATE_IRIS_SOCIAL_SCENE` |
| 3021 | `Method_GET_CHANGE_POKEMON_FORM_PREVIEW` |
| 3022 | `Method_GET_FUSE_POKEMON_PREVIEW` |
| 3023 | `Method_GET_UNFUSE_POKEMON_PREVIEW` |
| 3024 | `Method_PROCESS_PLAYER_INBOX` |
| 3025 | `Method_GET_SURVEY_ELIGIBILITY` |
| 3026 | `Method_UPDATE_SURVEY_ELIGIBILITY` |
| 3027 | `Method_SMART_GLASSES_SYNC_SETTINGS` |
| 3030 | `Method_COMPLETE_VISIT_PAGE_QUEST` |
| 3031 | `Method_GET_EVENT_RSVPS` |
| 3032 | `Method_CREATE_EVENT_RSVP` |
| 3033 | `Method_CANCEL_EVENT_RSVP` |
| 3034 | `Method_CLAIM_EVENT_PASS_REWARDS` |
| 3035 | `Method_CLAIM_ALL_EVENT_PASS_REWARDS` |
| 3036 | `Method_GET_EVENT_RSVP_COUNT` |
| 3039 | `Method_SEND_RSVP_INVITATION` |
| 3040 | `Method_UPDATE_EVENT_RSVP_SELECTION` |
| 3041 | `Method_GET_WEEKLY_CHALLENGE_INFO` |
| 3047 | `Method_START_WEEKLY_CHALLENGE_GROUP_MATCHMAKING` |
| 3048 | `Method_SYNC_WEEKLY_CHALLENGE_MATCHMAKING_STATUS` |
| 3051 | `Method_GET_STATION_INFO` |
| 3052 | `Method_AGE_CONFIRMATION` |
| 3053 | `Method_CHANGE_STAT_INCREASE_GOAL` |
| 3054 | `Method_END_POKEMON_TRAINING` |
| 3055 | `Method_GET_SUGGESTED_PLAYERS_SOCIAL` |
| 3056 | `Method_START_TGR_BATTLE` |
| 3057 | `Method_GRANT_EXPIRED_ITEM_CONSOLATION` |
| 3058 | `Method_COMPLETE_TGR_BATTLE` |
| 3059 | `Method_START_TEAM_LEADER_BATTLE` |
| 3060 | `Method_COMPLETE_TEAM_LEADER_BATTLE` |
| 3061 | `Method_GET_DEBUG_ENCOUNTER_STATISTICS_ACTION` |
| 3062 | `Method_REJOIN_BATTLE_CHECK` |
| 3063 | `Method_COMPLETE_ALL_QUEST` |
| 3064 | `Method_LEAVE_WEEKLY_CHALLENGE_MATCHMAKING` |
| 3065 | `Method_GET_PLAYER_FIELD_BOOK` |
| 3066 | `Method_GET_DAILY_BONUS_SPAWN` |
| 3067 | `Method_DAILY_BONUS_SPAWN_ENCOUNTER` |
| 3068 | `Method_GET_SUPPLY_BALLOON` |
| 3069 | `Method_OPEN_SUPPLY_BALLOON` |
| 3070 | `Method_NATURAL_ART_POI_ENCOUNTER` |
| 3071 | `Method_START_PVP_BATTLE` |
| 3072 | `Method_COMPLETE_PVP_BATTLE` |
| 3074 | `Method_AR_PHOTO_REWARD` |
| 3075 | `Method_UPDATE_FIELD_BOOK_POST_CATCH_POKEMON` |
| 3076 | `Method_GET_TIME_TRAVEL_INFORMATION` |
| 3077 | `Method_DAY_NIGHT_POI_ENCOUNTER` |
| 3078 | `Method_MARK_FIELDBOOK_SEEN` |
| 3079 | `Method_MEGA_LEVEL_UP` |
| 3080 | `Method_GET_MEGA_LEVEL_UP_PREVIEW` |
| 3083 | `Method_GET_DEBUG_EGG_STATISTICS_ACTION` |
| 3084 | `Method_FLEE_BATTLE_ENCOUNTER` |
| 3085 | `Method_DEBUG_TEST_SETUP` |
| 3086 | `Method_DEBUG_POKEMON_ENCOUNTER` |
| 3087 | `Method_REMOVE_DEBUG_POKEMON` |
| 3088 | `Method_CREATE_BUG_REPORT` |
| 3089 | `Method_REDEEM_PREMIUM_GIFT_FOR_USER` |
| 3090 | `Method_GET_COMBAT_DOWNSCALED_STATS` |
| 3091 | `Method_GET_SIMPLE_RAID_DETAILS` |
| 3092 | `Method_GET_INCOMPLETE_BATTLES` |
| 3093 | `Method_GET_RAID_LOBBY_DETAILS` |
| 3094 | `Method_GET_RAID_ENCOUNTER_DETAILS` |
| 3095 | `Method_ROTATING_SPAWN_ENCOUNTER` |

**Total:** 444 enum values.

---

## Appendix D — Every SQLite table

Table names and live row counts from `data/family-development.sqlite3`. Counts are a snapshot,
not a contract; use `COUNT(*)` when you need current values.

| Table | Rows |
|---|---:|
| `admin_locations` | 2 |
| `admin_raids` | 15 |
| `admin_spawns` | 37 |
| `buddy_action_receipts` | 0 |
| `buddy_active` | 0 |
| `buddy_daily` | 0 |
| `buddy_state` | 0 |
| `candy` | 22 |
| `cosmetic_ownership` | 271 |
| `eggs` | 12 |
| `encounter_deadlines` | 71 |
| `encounter_items` | 5 |
| `encounters` | 79 |
| `family_accounts` | 14 |
| `family_audit` | 30 |
| `family_auth_limits` | 2 |
| `family_battle_sessions` | 7 |
| `family_invites` | 2 |
| `family_sessions` | 72 |
| `fort_visits` | 14 |
| `forts` | 14 |
| `gym_badges` | 7 |
| `gym_battles` | 0 |
| `gym_defenders` | 1 |
| `gym_notifications` | 0 |
| `gym_returns` | 0 |
| `gyms` | 6 |
| `hatches` | 0 |
| `incubators` | 14 |
| `initial_incubator_grants` | 7 |
| `items` | 90 |
| `level_reward_claims` | 52 |
| `mega_active` | 0 |
| `mega_catch_xl` | 4 |
| `mega_energy` | 4 |
| `mega_ledger` | 4 |
| `mega_progress` | 0 |
| `player_appearance` | 7 |
| `player_stats_history` | 10 |
| `players` | 14 |
| `pokedex_imports` | 7 |
| `pokedex_observations` | 78 |
| `pokemon` | 74 |
| `pokemon_levels` | 74 |
| `pokemon_tags` | 0 |
| `pokemon_tombstones` | 0 |
| `raid_battles` | 7 |
| `raid_combat_sessions` | 7 |
| `raid_daily_passes` | 1 |
| `raid_event_receipts` | 107 |
| `raid_members` | 7 |
| `raid_pass_receipts` | 7 |
| `raid_room_events` | 146 |
| `raid_room_members` | 7 |
| `raid_rooms` | 7 |
| `raid_tokens` | 7 |
| `raids` | 17 |
| `research_cards` | 7 |
| `research_dialog_reads` | 3 |
| `research_encounters` | 1 |
| `research_stage_claims` | 1 |
| `research_stamps` | 1 |
| `research_stories` | 7 |
| `research_tasks` | 31 |
| `research_visits` | 7 |
| `rpc_receipts` | 5229 |
| `schema_version` | 19 |
| `sessions` | 58 |
| `shop_offers` | 16 |
| `social_codes` | 2 |
| `social_gifts` | 19 |
| `social_history` | 0 |
| `social_locations` | 1 |
| `social_milestones` | 0 |
| `social_nicknames` | 0 |
| `social_pairs` | 0 |
| `social_postcards` | 0 |
| `social_remote_sessions` | 0 |
| `social_requests` | 0 |
| `social_settings` | 0 |
| `social_stickers` | 0 |
| `social_trade_history` | 0 |
| `social_trade_locks` | 0 |
| `social_trades` | 0 |
| `spawn_attributes` | 45 |
| `spawn_generations` | 37 |
| `spawns` | 45 |
| `sqlite_sequence` | 2 |
| `trainer_counter_imports` | 7 |
| `trainer_counters` | 0 |
| `trainer_events` | 116 |
| `trainer_medals` | 224 |
| `trainer_preferences` | 2 |
| `trainer_settings` | 0 |
| `trainer_teams` | 14 |
| `trainer_unlock_claims` | 0 |
| `tutorials` | 17 |
| `walking` | 7 |

**Total:** 98 tables.

---

## Appendix E — Admin API endpoints

All routes are on `http://127.0.0.1:8080`, JSON, no authentication, no TLS. Unknown routes return
`404 {"error":"Unknown endpoint"}`. Validation errors return 400 with a message; unexpected errors
return 500 with the exception type/message.

| Method | Endpoint | Body / query | Returns |
|---|---|---|---|
| GET | `/` | — | `admin_ui.html` |
| GET | `/favicon.ico` | — | 204 |
| GET | `/api/status` | — | Counts, location, config |
| GET | `/api/device-location` | — | `{lat,lng}` or `{}` |
| GET | `/api/players` | — | Player list |
| GET | `/api/forms` | `?species=<id>` | Forms, costumes, Mega options |
| GET | `/api/rewards` | — | Players, 290 items, Mega species |
| POST | `/api/rewards` | `{player_id,xp,stardust,items,candy,xl_candy,pokemon_storage,item_storage,mega_energy}` | Updated totals/inventory |
| GET | `/api/items` | — | 290-item catalog |
| GET | `/api/mega-species` | — | Mega species/forms |
| GET | `/api/loot` | — | `{table,defaults,items}` |
| POST | `/api/loot` | `{table:{count,guaranteed,drops,passes}}` or the table itself | `{table}` |
| GET | `/api/species` | — | Species names/IDs |
| GET | `/api/spawns` | `?limit&lat&lng&radius` | Spawns |
| POST | `/api/spawns` | `{species,level,iv_attack,iv_defense,iv_stamina,shiny,shadow,lucky,mega,form,costume,count,radius_m,duration_s,label,lat,lng}` | Created spawns |
| DELETE | `/api/spawns/<id>` | — | `{deleted}` |
| GET | `/api/pois` | — | Admin POIs |
| POST | `/api/pois` | `{kind,name,lat,lng}` | Created POI |
| DELETE | `/api/pois/<id>` | — | `{deleted,kept_history}` |
| GET | `/api/gyms` | — | Gym forts |
| GET | `/api/raids` | — | Active/upcoming raids |
| POST | `/api/raids` | `{species,tier,mega,fort_id,hp,cp,level,shiny_rate,shadow,egg_seconds,active_seconds,form,costume,xp,dust,label}` | Created raid |
| DELETE | `/api/raids/<seed>` | — | `{deleted}` |

### Notes

- `/api/spawns` without `lat`/`lng` returns the newest 300 active spawns; with coordinates it filters
  by a bounding box derived from `radius`.
- `/api/device-location` shells out to `adb shell dumpsys location`; it is cached for 15 seconds.
- `/api/rewards` returns 201 on success; `/api/loot` returns 200.
- POI IDs are `admin:gym-<hex>` / `admin:stop-<hex>`; the API accepts the full ID in DELETE.
- Admin UI uses only these endpoints; there is no hidden RPC.

---

## Appendix F — Configuration keys

The live profile is `config/family.json`. Values below are the current ones; see section 20 for
meaning and section 9/10/15 for subsystem behavior.

| Key | Current value |
|---|---|
| `host` | `127.0.0.1` |
| `port` | `8445` |
| `database` | `data/family-development.sqlite3` |
| `player_name` | `Developer` |
| `pokemon_capacity` | `300` |
| `item_capacity` | `350` |
| `spawn_duration_seconds` | `1800` |
| `encounter_duration_seconds` | `300` |
| `catch_probability` | `0.8` |
| `catch_xp` | `100` |
| `catch_stardust` | `100` |
| `catch_candy` | `3` |
| `interaction_range_meters` | `100` |
| `fort_cooldown_seconds` | `300` |
| `spawn_species` | `[1]` |
| `spawn_pools` | `garden-mewtwo` (Mewtwo, shiny rate 1.0) |
| `spawn_points_per_tile` | `8` |
| `spawn_level_range` | `[20,20]` |
| `spawn_iv_range` | `[0,15]` |
| `shiny_rate` | `0.001953125` |
| `spawn_generation` | `15001` |
| `power_up_candy_cost` | `1` |
| `power_up_stardust_cost` | `100` |
| `power_up_levels_above_player` | `10` |
| `power_up_xl_candy_cost` | `10` |
| `xl_candy_min_player_level` | `31` |
| `xl_catch_rates` | `[[1,0],[11,0.1],[21,0.25],[31,0.5],[36,0.75],[41,0.9]]` |
| `evolution_xp` | `500` |
| `level_up_rewards` | map (levels 2–40, items) |
| `initial_items` | `{1:100,2:30,3:10,101:10,201:5,701:10}` |
| `level_requirements` | 50 entries, `0 … 175000` |
| `fort_xp` | `50` |
| `spawn_neighbor_tiles` | `1` |
| `eggs` | capacity 9, drop 0.5, 2/5/10 km pools, XP 200, dust 100, candy 5, 1 incubator |
| `walking` | max accuracy 25 m, max speed 2.8 m/s, debug fixture false |
| `research` | `{enabled:true}` |
| `pokemon_assets` | disabled, manifest `builds/m113-reviewed-assets.json` |
| `avatar_assets` | disabled, manifest `builds/m10-avatar-assets.json` |
| `buddy_perks` | gifts/souvenirs/ribbon/catch assist true |
| `profile` | `private-transfer-family` |
| `world` | enabled, manifest `data/world/current.json`, palette, spin weights, raid pass 0.05 |
| `world.spin_weights` | `1:100, 2:20, 3:5, 4:0.5, 101:25, 201:10, 701:8, 703:5, 705:5` |
| `weather` | enabled, refresh 900, stale 7200, daily 9000 |
| `raids` | enabled, development-only, 5 pools, tier 6 Venusaur, participation 5 |
| `authentication` | `{mode:family, registration:open}` |
| `network` | `{mode:tls, certificate:config/tls/server.pem, private_key:config/tls/server-key.pem}` |
| `client_resources` | disabled, registry `builds/m16-client-runtime-03/registry.json` |
| `social` | `{enabled:true}` |
| `map_trace` | `false` |
| `rpc_trace_methods` | `[]` |
| `shiny_eligible_forms` | `[{species:150}]` |

### Profile differences

- `config/development.json`: port 8080, local test player, no family auth, catch 0.8, 40-level
  schedule, small spawn pool, world palette, no raids/weather config.
- `config/boot-test.json`: fixture paths and a small DB for boot tests.
- `config/public-profile.json`: origin, CA file, asset options for the standalone APK.
- `config/family.json`: the live profile described above.

---

## Appendix G — Ports, processes, logs and PID files

| Service | Port | Bind | Protocol | Log | PID |
|---|---|---|---|---|---|
| Game server | 8445 | 127.0.0.1 | HTTPS + protobuf | `logs/server.log` | `run/server.pid` |
| Admin | 8080 | 127.0.0.1 | HTTP + JSON | `logs/admin.log` | `run/admin.pid` |
| gnirehtet relay | 31416 | 127.0.0.1 | TCP relay | `logs/gnirehtet.log` | — |
| ADB server | 5037 | 127.0.0.1 | ADB | — | — |
| Frida Gadget | 27042 | 127.0.0.1 | Frida (gadget build only) | — | — |

### Commands

```bash
# status
python -m dittobuffer /status
netstat -ano | findstr ":8445 :8080 :31416"
adb reverse --list

# start / stop
python -m dittobuffer /start all
python -m dittobuffer /stop all
python -m dittobuffer /restart server

# logs
python -m dittobuffer /logs server 80
python -m dittobuffer /logs admin 40
python -m dittobuffer /logs gnirehtet 40

# one-click
START-SERVER.bat
```

### Process names

- `python.exe` running `-m dittobuffer.runtime.http`
- `python.exe` running `-m dittobuffer.admin.admin`
- `gnirehtet.exe run`
- `adb.exe` server
- venv redirector may add a second `python.exe` with the same command line

### Health endpoints

- `GET https://127.0.0.1:8445/health` (TLS, self-signed)
- `GET http://127.0.0.1:8080/api/status` (plain HTTP)

Use `curl -k` for the game server. A browser will warn about the private CA.

---

## Appendix H — Useful SQL queries

Open the live DB read-only for inspection:

```bash
sqlite3 data/family-development.sqlite3
.headers on
.mode column
```

### Players and accounts

```sql
SELECT id, name, xp, stardust, revision, created_ms FROM players ORDER BY xp DESC;
SELECT username, player_id, pokemon_capacity, item_capacity, disabled FROM family_accounts;
SELECT account_id, kind, created_ms, expires_ms, revoked_ms FROM family_sessions ORDER BY created_ms DESC LIMIT 20;
SELECT at_ms, account_id, event FROM family_audit ORDER BY id DESC LIMIT 50;
```

### Inventory and Pokémon

```sql
SELECT item, count FROM items WHERE player_id = '<player-id>' ORDER BY item;
SELECT family, count FROM candy WHERE player_id = '<player-id>' ORDER BY family;
SELECT family, count FROM mega_catch_xl WHERE player_id = '<player-id>' ORDER BY family;
SELECT id, species, spawn_id, length(data) AS bytes FROM pokemon WHERE player_id = '<player-id>' ORDER BY id DESC LIMIT 20;
SELECT pokemon_id, level FROM pokemon_levels WHERE pokemon_id IN (SELECT id FROM pokemon WHERE player_id='<player-id>');
```

### Spawns and encounters

```sql
SELECT id, species, lat, lng, expires_ms FROM spawns WHERE expires_ms > (unixepoch()*1000) ORDER BY expires_ms DESC LIMIT 30;
SELECT s.id, s.species, a.level, length(a.data) FROM spawns s LEFT JOIN spawn_attributes a ON a.spawn_id=s.id WHERE s.expires_ms > (unixepoch()*1000) LIMIT 30;
SELECT * FROM admin_spawns ORDER BY created_ms DESC LIMIT 20;
SELECT player_id, spawn_id, state FROM encounters ORDER BY rowid DESC LIMIT 20;
```

### Forts, gyms and raids

```sql
SELECT id, kind, name, lat, lng FROM forts ORDER BY kind, name;
SELECT fort_id, team, prestige FROM gyms;
SELECT pokemon_id, fort_id, owner_id, deploy_ms FROM gym_defenders;
SELECT seed, fort_id, spawn_ms, battle_ms, end_ms, json_extract(definition,'$.species') AS species, json_extract(definition,'$.tier') AS tier FROM raids WHERE end_ms > (unixepoch()*1000);
SELECT seed, player_id, balls FROM raid_members;
```

### Mega

```sql
SELECT player_id, species, temp_evo, amount FROM mega_energy ORDER BY amount DESC;
SELECT pokemon_id, temp_evo, level, points, evolutions FROM mega_progress ORDER BY level DESC;
SELECT * FROM mega_ledger ORDER BY id DESC LIMIT 30;
```

### Receipts and stats

```sql
SELECT COUNT(*) FROM rpc_receipts;
SELECT ticket, session_run, rpc_id, length(response) FROM rpc_receipts ORDER BY rowid DESC LIMIT 10;
SELECT * FROM player_stats_history ORDER BY rowid DESC LIMIT 20;
```

### World/weather

```sql
-- on data/world/weather.sqlite3
SELECT * FROM weather ORDER BY updated_ms DESC LIMIT 10;
```

### Maintenance

```sql
PRAGMA integrity_check;
PRAGMA journal_mode;
PRAGMA busy_timeout;
-- backup from the shell, not while writing:
-- sqlite3 data/family-development.sqlite3 ".backup data/backup.sqlite3"
```

Do not run `VACUUM` or `PRAGMA journal_mode=DELETE` while the server/admin are live.

---

## Appendix I — First ten minutes checklist

Use this when you are dropped into a fresh session with this repository.

1. **Read the top of this file** and section 0. Do not re-architect anything yet.
2. **Check what is running:**
   ```bash
   curl -k https://127.0.0.1:8445/health
   curl -s http://127.0.0.1:8080/api/status
   adb devices
   ```
3. **If the server is down**, run `START-SERVER.bat` (phone connected) or
   `python -m dittobuffer --no-autostart /start server` and `/start admin`.
4. **If the phone is disconnected**, run `adb reverse tcp:8445 tcp:8445` and re-run the launcher.
5. **Open the admin panel** at `http://127.0.0.1:8080`; confirm players/spawns/raids load.
6. **Confirm loot**: `curl -s http://127.0.0.1:8080/api/loot` should show the current table and 290
   items; if it returns “Unknown endpoint”, restart the admin process.
7. **Confirm the world**: `curl -k https://127.0.0.1:8445/world/health` and
   `curl -s http://127.0.0.1:8080/api/pois`.
8. **Check recent logs** for tracebacks: `logs/server.log`, `logs/admin.log`, `logs/gnirehtet.log`.
9. **Run the targeted tests** for whatever you are about to change:
   ```bash
   PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_stop_loot.py tests/test_mega.py -q
   ```
10. **Do not edit** the world snapshot, protocol schema or client APK unless the request requires it.
    Use the admin overlay, config, and module boundaries.

### If the user reports a gameplay bug

1. Reproduce or inspect the relevant RPC in `logs/server.log` (method IDs/names).
2. Check the DB state for that player/encounter/raid.
3. Check whether the process is running the code you think it is (restart after edits).
4. Fix the cause, add/execute a focused test, then verify through the actual path (RPC or admin API).
5. Report the result and any checks that could not run (e.g. device-only behavior).

### If the user asks for a new feature

1. Identify the domain module and existing tables/handlers.
2. Decide whether it is gameplay (protobuf RPC), admin (JSON API + UI), world (overlay), or config.
3. Implement the smallest change that fits the existing architecture.
4. Add a test that exercises the transaction/wire path.
5. Restart the affected service and verify via HTTP/DB, not only unit tests.

---

## Appendix J — The current loot table verbatim

`data/admin/stop-loot.json` as of this snapshot:

```json
{
  "count": 19,
  "guaranteed": [
    {
      "item": 1,
      "count": 1
    }
  ],
  "drops": [
    {
      "item": 2,
      "weight": 20,
      "min_level": 12,
      "min_count": 1,
      "max_count": 1
    },
    {
      "item": 703,
      "weight": 5,
      "min_level": 4,
      "min_count": 1,
      "max_count": 1
    },
    {
      "item": 705,
      "weight": 5,
      "min_level": 18,
      "min_count": 1,
      "max_count": 1
    },
    {
      "item": 1,
      "weight": 100,
      "min_level": 1,
      "min_count": 1,
      "max_count": 1
    },
    {
      "item": 101,
      "weight": 25,
      "min_level": 5,
      "min_count": 1,
      "max_count": 1
    },
    {
      "item": 701,
      "weight": 8,
      "min_level": 8,
      "min_count": 1,
      "max_count": 1
    },
    {
      "item": 201,
      "weight": 10,
      "min_level": 5,
      "min_count": 1,
      "max_count": 1
    },
    {
      "item": 3,
      "weight": 5,
      "min_level": 20,
      "min_count": 1,
      "max_count": 1
    },
    {
      "item": 4,
      "weight": 100,
      "min_level": 1,
      "min_count": 1,
      "max_count": 1
    }
  ],
  "passes": {
    "premium": 1,
    "remote": 1
  }
}
```

This is intentionally generous local tuning. The built-in default is `count: 2`, the nine GM items
with their default weights, one guaranteed Poké Ball, and 2.5% Premium/Remote. See section 14.

---

## Appendix K — Item catalog

Every item ID the admin reward/loot picker exposes (290 entries). Names are derived from the
protocol enum; the numeric ID is what `items`, loot rows and `mega_energy` use.

| ID | Item |
|---:|---|
| 1 | Poke Ball |
| 2 | Great Ball |
| 3 | Ultra Ball |
| 4 | Master Ball |
| 5 | Premier Ball |
| 6 | Beast Ball |
| 7 | Wild Ball |
| 8 | Wild Ball Premier |
| 101 | Potion |
| 102 | Super Potion |
| 103 | Hyper Potion |
| 104 | Max Potion |
| 201 | Revive |
| 202 | Max Revive |
| 301 | Lucky Egg |
| 302 | Max Boost |
| 303 | Lucky Friend Applicator |
| 304 | Single Stat Increase |
| 305 | Triple Stat Increase |
| 401 | Incense Ordinary |
| 402 | Incense Spicy |
| 403 | Incense Cool |
| 404 | Incense Floral |
| 405 | Incense Beluga Box |
| 406 | Incense Daily Adventure |
| 407 | Incense Sparkly |
| 408 | Incense Day Bonus |
| 409 | Incense Night Bonus |
| 501 | Troy Disk |
| 502 | Troy Disk Glacial |
| 503 | Troy Disk Mossy |
| 504 | Troy Disk Magnetic |
| 505 | Troy Disk Rainy |
| 506 | Troy Disk Sparkly |
| 602 | XAttack |
| 603 | XDefense |
| 604 | XMiracle |
| 650 | Beans |
| 651 | Breakfast |
| 701 | Razz Berry |
| 702 | Bluk Berry |
| 703 | Nanab Berry |
| 704 | Wepar Berry |
| 705 | Pinap Berry |
| 706 | Golden Razz Berry |
| 707 | Golden Nanab Berry |
| 708 | Golden Pinap Berry |
| 709 | Poffin |
| 801 | Special Camera |
| 802 | Sticker Inventory |
| 803 | Postcard Inventory |
| 804 | Soft Sfida |
| 805 | Soft Sfida Limit Upgrade Earned |
| 806 | Pending Premium Gift Inventory |
| 901 | Incubator Basic Unlimited |
| 902 | Incubator Basic |
| 903 | Incubator Super |
| 904 | Incubator Timed |
| 905 | Incubator Special |
| 1001 | Pokemon Storage Upgrade |
| 1002 | Item Storage Upgrade |
| 1003 | Postcard Storage Upgrade |
| 1101 | Sun Stone |
| 1102 | Kings Rock |
| 1103 | Metal Coat |
| 1104 | Dragon Scale |
| 1105 | Up Grade |
| 1106 | Gen4 Evolution Stone |
| 1107 | Gen5 Evolution Stone |
| 1150 | Other Evolution Stone A |
| 1151 | Other Evolution Stone Maple A |
| 1152 | Other Evolution Stone Maple B |
| 1153 | Resource Crowned Zacian |
| 1154 | Resource Crowned Zamazenta |
| 1155 | Other Evolution Stone Maple C |
| 1201 | Move Reroll Fast Attack |
| 1202 | Move Reroll Special Attack |
| 1203 | Move Reroll Elite Fast Attack |
| 1204 | Move Reroll Elite Special Attack |
| 1250 | Move Reroll Other Special Attack A |
| 1301 | Rare Candy |
| 1302 | Xl Rare Candy |
| 1350 | Fusion Resource Dawnwings Necrozma |
| 1351 | Fusion Resource Duskmane Necrozma |
| 1352 | Fusion Resource Black Kyurem |
| 1353 | Fusion Resource White Kyurem |
| 1354 | Fusion Resource Icerider Calyrex |
| 1355 | Fusion Resource Spectralrider Calyrex |
| 1401 | Free Raid Ticket |
| 1402 | Paid Raid Ticket |
| 1404 | Star Piece |
| 1405 | Friend Gift Box |
| 1406 | Team Change |
| 1408 | Remote Raid Ticket |
| 1409 | SRaid Ticket |
| 1410 | Enhanced Currency |
| 1411 | Enhanced Currency Holder |
| 1501 | Leader Map Fragment |
| 1502 | Leader Map |
| 1503 | Giovanni Map |
| 1504 | Shadow Gem Fragment |
| 1505 | Shadow Gem |
| 1506 | Mp |
| 1507 | Mp Replenish |
| 1600 | Global Event Ticket |
| 1601 | Event Ticket Pink |
| 1602 | Event Ticket Gray |
| 1603 | Global Event Ticket To Gift |
| 1604 | Event Ticket Pink To Gift |
| 1605 | Event Ticket Gray To Gift |
| 1606 | Battle Pass Ticket |
| 1607 | Evergreen Ticket |
| 1608 | Evergreen Ticket To Gift |
| 1609 | Deprecated1 |
| 1610 | Ticket City Safari00 |
| 1611 | Ticket City Safari01 |
| 1612 | Ticket City Safari02 |
| 1613 | Ticket City Safari03 |
| 1614 | Ticket City Safari04 |
| 1615 | Event Ticket01 |
| 1616 | Event Ticket02 |
| 1617 | Event Ticket03 |
| 1618 | Event Ticket04 |
| 1619 | Event Ticket05 |
| 1620 | Event Ticket06 |
| 1621 | Event Ticket07 |
| 1622 | Event Ticket08 |
| 1623 | Event Ticket09 |
| 1624 | Event Ticket10 |
| 1625 | Event Ticket01 To Gift |
| 1626 | Event Ticket02 To Gift |
| 1627 | Event Ticket03 To Gift |
| 1628 | Event Ticket04 To Gift |
| 1629 | Event Ticket05 To Gift |
| 1630 | Event Ticket06 To Gift |
| 1631 | Event Ticket07 To Gift |
| 1632 | Event Ticket08 To Gift |
| 1633 | Event Ticket09 To Gift |
| 1634 | Event Ticket10 To Gift |
| 2001 | Event Pass Point Go Tour01 |
| 2002 | Event Pass Point Go Tour02 |
| 2003 | Event Pass Point Go Tour03 |
| 2004 | Event Pass Point Go Tour04 |
| 2021 | Event Pass Point Go Fest01 |
| 2022 | Event Pass Point Go Fest02 |
| 2023 | Event Pass Point Go Fest03 |
| 2024 | Event Pass Point Go Fest04 |
| 2041 | Event Pass Point Go Wild Area01 |
| 2042 | Event Pass Point Go Wild Area02 |
| 2043 | Event Pass Point Go Wild Area03 |
| 2044 | Event Pass Point Go Wild Area04 |
| 2101 | Event Pass Point Live Ops01 |
| 2102 | Event Pass Point Live Ops02 |
| 2103 | Event Pass Point Live Ops03 |
| 2104 | Event Pass Point Live Ops04 |
| 2105 | Event Pass Point Live Ops05 |
| 2106 | Event Pass Point Live Ops06 |
| 2107 | Event Pass Point Live Ops07 |
| 2108 | Event Pass Point Live Ops08 |
| 2109 | Event Pass Point Live Ops09 |
| 2110 | Event Pass Point Live Ops10 |
| 2111 | Event Pass Point Live Ops11 |
| 2112 | Event Pass Point Live Ops12 |
| 2113 | Event Pass Point Live Ops13 |
| 2114 | Event Pass Point Live Ops14 |
| 2115 | Event Pass Point Live Ops15 |
| 2116 | Event Pass Point Live Ops16 |
| 2117 | Event Pass Point Live Ops17 |
| 2118 | Event Pass Point Live Ops18 |
| 2119 | Event Pass Point Live Ops19 |
| 2120 | Event Pass Point Live Ops20 |
| 2151 | Event Pass Point Monthly01 |
| 2152 | Event Pass Point Monthly02 |
| 2153 | Event Pass Point Monthly03 |
| 2154 | Event Pass Point Monthly04 |
| 2155 | Event Pass Point Monthly05 |
| 2156 | Event Pass Point Monthly06 |
| 2157 | Event Pass Point Monthly07 |
| 2158 | Event Pass Point Monthly08 |
| 2159 | Event Pass Point Monthly09 |
| 2160 | Event Pass Point Monthly10 |
| 2161 | Event Pass Point Monthly11 |
| 2162 | Event Pass Point Monthly12 |
| 2200 | Giftbox Storage Upgrade |
| 2201 | Pokemon Storage Upgrade Earned |
| 2202 | Item Storage Upgrade Earned |
| 2203 | Postcard Storage Upgrade Earned |
| 2204 | Giftbox Storage Upgrade Earned |
| 2205 | Event Ticket S23202601 |
| 2206 | Event Ticket S23202602 |
| 2207 | Event Ticket S23202603 |
| 2208 | Event Ticket S23202604 |
| 2209 | Event Ticket S23202605 |
| 2210 | Event Ticket S23202606 |
| 2211 | Event Ticket S23202607 |
| 2212 | Event Ticket S23202608 |
| 2213 | Event Ticket S23202609 |
| 2214 | Event Ticket S23202610 |
| 2215 | Event Ticket S23202611 |
| 2216 | Event Ticket S23202612 |
| 2217 | Event Ticket S23202613 |
| 2218 | Event Ticket S23202614 |
| 2219 | Event Ticket S23202615 |
| 2220 | Event Ticket S23202616 |
| 2221 | Event Ticket S23202617 |
| 2222 | Event Ticket S23202618 |
| 2223 | Event Ticket S23202619 |
| 2224 | Event Ticket S23202620 |
| 2225 | Event Ticket S23202621 |
| 2226 | Event Ticket S23202622 |
| 2227 | Event Ticket S23202623 |
| 2228 | Event Ticket S23202624 |
| 2229 | Event Ticket S23202625 |
| 2230 | Event Ticket S23202626 |
| 2231 | Event Ticket S23202627 |
| 2232 | Event Ticket S23202628 |
| 2233 | Event Ticket S23202629 |
| 2234 | Event Ticket S23202630 |
| 2235 | Event Ticket S23202631 |
| 2236 | Event Ticket S23202632 |
| 2237 | Event Ticket S23202601 To Gift |
| 2238 | Event Ticket S23202602 To Gift |
| 2239 | Event Ticket S23202603 To Gift |
| 2240 | Event Ticket S23202604 To Gift |
| 2241 | Event Ticket S23202605 To Gift |
| 2242 | Event Ticket S23202606 To Gift |
| 2243 | Event Ticket S23202607 To Gift |
| 2244 | Event Ticket S23202608 To Gift |
| 2245 | Event Ticket S23202609 To Gift |
| 2246 | Event Ticket S23202610 To Gift |
| 2247 | Event Ticket S23202611 To Gift |
| 2248 | Event Ticket S23202612 To Gift |
| 2249 | Event Ticket S23202613 To Gift |
| 2250 | Event Ticket S23202614 To Gift |
| 2251 | Event Ticket S23202615 To Gift |
| 2252 | Event Ticket S23202616 To Gift |
| 2253 | Event Ticket S24 Sep12 Community |
| 2254 | Event Ticket S24 Sep12 Community To Gift |
| 2255 | Event Ticket S24 Sep19 Megaraid |
| 2256 | Event Ticket S24 Sep19 Megaraid To Gift |
| 2257 | Event Ticket S24 Sep26 Catch |
| 2258 | Event Ticket S24 Sep26 Catch To Gift |
| 2259 | Event Ticket S24 Oct3 Maxbattle |
| 2260 | Event Ticket S24 Oct3 Maxbattle To Gift |
| 2261 | Event Ticket S24 Oct10 Community |
| 2262 | Event Ticket S24 Oct10 Community To Gift |
| 2263 | Event Ticket S24 Oct17 Hatch |
| 2264 | Event Ticket S24 Oct17 Hatch To Gift |
| 2265 | Event Ticket S24 Oct24 Maxbattle |
| 2266 | Event Ticket S24 Oct24 Maxbattle To Gift |
| 2267 | Event Ticket S24 Oct31 Megaraid |
| 2268 | Event Ticket S24 Oct31 Megaraid To Gift |
| 2269 | Event Ticket S24 Nov9 Gowaol |
| 2270 | Event Ticket S24 Nov9 Gowaol To Gift |
| 2271 | Event Ticket S24 Nov21 Community |
| 2272 | Event Ticket S24 Nov21 Community To Gift |
| 2273 | Event Ticket S24 Nov28 Megaraid |
| 2274 | Event Ticket S24 Nov28 Megaraid To Gift |
| 2275 | Event Ticket S25202601 |
| 2276 | Event Ticket S25202602 |
| 2277 | Event Ticket S25202603 |
| 2278 | Event Ticket S25202604 |
| 2279 | Event Ticket S25202605 |
| 2280 | Event Ticket S25202606 |
| 2281 | Event Ticket S25202607 |
| 2282 | Event Ticket S25202608 |
| 2283 | Event Ticket S25202609 |
| 2284 | Event Ticket S25202610 |
| 2285 | Event Ticket S25202611 |
| 2286 | Event Ticket S25202612 |
| 2287 | Event Ticket S25202613 |
| 2288 | Event Ticket S25202614 |
| 2289 | Event Ticket S25202615 |
| 2290 | Event Ticket S25202616 |
| 2291 | Event Ticket S25202601 To Gift |
| 2292 | Event Ticket S25202602 To Gift |
| 2293 | Event Ticket S25202603 To Gift |
| 2294 | Event Ticket S25202604 To Gift |
| 2295 | Event Ticket S25202605 To Gift |
| 2296 | Event Ticket S25202606 To Gift |
| 2297 | Event Ticket S25202607 To Gift |
| 2298 | Event Ticket S25202608 To Gift |
| 2299 | Event Ticket S25202609 To Gift |
| 2300 | Event Ticket S25202610 To Gift |
| 2301 | Event Ticket S25202611 To Gift |
| 2302 | Event Ticket S25202612 To Gift |
| 2303 | Event Ticket S25202613 To Gift |
| 2304 | Event Ticket S25202614 To Gift |
| 2305 | Event Ticket S25202615 To Gift |
| 2306 | Event Ticket S25202616 To Gift |

**Total:** 290 items.

---

## Appendix L — Mega species and forms

The 56 species with at least one supported temporary evolution. `evo` is the value stored in
`mega_energy.temp_evo`, raid `mega_evolution`, and admin Mega grants.

| Species | Name | Forms |
|---:|---|---|
| 3 | Venusaur | 1 (Mega) |
| 6 | Charizard | 2 (Mega X), 3 (Mega Y) |
| 9 | Blastoise | 1 (Mega) |
| 15 | Beedrill | 1 (Mega) |
| 18 | Pidgeot | 1 (Mega) |
| 26 | Raichu | 2 (Mega X), 3 (Mega Y) |
| 65 | Alakazam | 1 (Mega) |
| 71 | Victreebel | 1 (Mega) |
| 80 | Slowbro | 1 (Mega) |
| 94 | Gengar | 1 (Mega) |
| 115 | Kangaskhan | 1 (Mega) |
| 121 | Starmie | 1 (Mega) |
| 127 | Pinsir | 1 (Mega) |
| 130 | Gyarados | 1 (Mega) |
| 142 | Aerodactyl | 1 (Mega) |
| 149 | Dragonite | 1 (Mega) |
| 150 | Mewtwo | 2 (Mega X), 3 (Mega Y) |
| 181 | Ampharos | 1 (Mega) |
| 208 | Steelix | 1 (Mega) |
| 212 | Scizor | 1 (Mega) |
| 214 | Heracross | 1 (Mega) |
| 227 | Skarmory | 1 (Mega) |
| 229 | Houndoom | 1 (Mega) |
| 248 | Tyranitar | 1 (Mega) |
| 254 | Sceptile | 1 (Mega) |
| 257 | Blaziken | 1 (Mega) |
| 260 | Swampert | 1 (Mega) |
| 282 | Gardevoir | 1 (Mega) |
| 302 | Sableye | 1 (Mega) |
| 303 | Mawile | 1 (Mega) |
| 306 | Aggron | 1 (Mega) |
| 308 | Medicham | 1 (Mega) |
| 310 | Manectric | 1 (Mega) |
| 319 | Sharpedo | 1 (Mega) |
| 323 | Camerupt | 1 (Mega) |
| 334 | Altaria | 1 (Mega) |
| 354 | Banette | 1 (Mega) |
| 359 | Absol | 1 (Mega) |
| 362 | Glalie | 1 (Mega) |
| 373 | Salamence | 1 (Mega) |
| 376 | Metagross | 1 (Mega) |
| 380 | Latias | 1 (Mega) |
| 381 | Latios | 1 (Mega) |
| 384 | Rayquaza | 1 (Mega) |
| 428 | Lopunny | 1 (Mega) |
| 445 | Garchomp | 1 (Mega) |
| 448 | Lucario | 1 (Mega) |
| 460 | Abomasnow | 1 (Mega) |
| 475 | Gallade | 1 (Mega) |
| 531 | Audino | 1 (Mega) |
| 652 | Chesnaught | 1 (Mega) |
| 655 | Delphox | 1 (Mega) |
| 658 | Greninja | 1 (Mega) |
| 687 | Malamar | 1 (Mega) |
| 719 | Diancie | 1 (Mega) |
| 870 | Falinks | 1 (Mega) |

**Total:** 56 species, 59 forms.

---

*End of document.*
