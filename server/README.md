# Dittobuffer

Private, self-hosted Pokémon GO server source for local research and family play.
This branch is a cleaned source snapshot of `main`; it is not affiliated with,
endorsed by, or connected to Niantic or The Pokémon Company.

## What this branch contains

- The game server, protocol definitions, admin console, client tooling and tests.
- The platform room/registry service in `platform/`.
- Both requested main-line fixes:
  - external addressables catalog support so team-leader and medal art resolves;
  - automatic OSM tile downloading with a stable map epoch and no per-tile
    placeholder stops/gyms.
- A safe local profile at `config/server.json`.

## What this branch deliberately does not contain

- Patched or base APKs, built artifacts or release archives.
- TLS private keys, APK signing keystores, admin tokens or local profiles.
- Player databases, world snapshots, map extracts, photo caches or device data.
- Bundled Pokémon/Unity artwork or other third-party assets that cannot be
  redistributed here. Game Master data is included in this source release.

Supply only client and game resources you are independently authorized to use.
See [docs/ASSETS.md](docs/ASSETS.md) for the verifier workflow. The server source
and platform can run without those payloads; rendering and some gameplay
content need your own legally obtained resources and APK.

## Quick start

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m dittobuffer
```

On Windows use `.venv\Scripts\python.exe` and the matching executable path.

The default profile binds to `127.0.0.1`, uses local accounts and the OSM
downloader. It does not use TLS and does not contain a player database.

## Platform

```bash
cd platform
python run.py
```

The first run asks for a room address, generates its signing key and state, and
hosts the room. See [platform/README.md](platform/README.md) for recovery,
resetting platform JSON, OSM POIs and Docker.

## Docker

```bash
docker compose up --build
```

The compose file runs the server and admin console. The platform has its own
`platform/docker-compose.yml`; the packaged release includes one combined
compose file that mounts the server workspace into the platform.

## License and assets

Source code is GPL-3.0-only. Third-party names and assets remain the property
of their owners. No asset payloads are included or licensed by this branch.
