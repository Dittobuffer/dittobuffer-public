# Changelog

All notable changes are recorded here. The project follows [Semantic Versioning](https://semver.org/).

## [1.0.0] — 2026-10-24

### Added
- `Dittobuffer` repository: game server, Game Master, world snapshot, admin console, APK patcher.
- `python -m dittobuffer` console with an ASCII wordmark and slash commands (`/status`, `/reconnect`,
  `/doctor`, `/logs`, `/players`, `/spawns`, `/raids`, `/pois`, `/patch`, `/config`, `/quit`).
- `/reconnect` rebuilds ADB reverse tunnels, restarts gnirehtet and relaunches the client in one step.
- `dittobuffer.patcher` with `--patcher <apk>` Frida Gadget injection and the full family build.
- Dockerfile and compose stack for the server and admin panel.
- Material 3 light admin panel with PokeAPI sprites and a live map.

### Changed
- Python sources moved from the flat `server/` package into `src/dittobuffer/{core,gameplay,world,
  admin,patcher,runtime,cli}` with absolute package imports.
- All branding, paths, documentation and generated assets renamed from `LibreNLP` to `Dittobuffer`.
- Configuration split into `config/family.json` (TLS family profile) and `config/development.json`.

### Fixed
- ADB tunnels are re-established explicitly instead of relying on a long-lived session.
- Zip-slip and manifest-drift checks when serving client resources.
