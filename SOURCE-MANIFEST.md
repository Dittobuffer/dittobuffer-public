# Source manifest

## Included

| Path | Contents |
|---|---|
| `server/src/` | Python game server, gameplay, world, runtime, admin, patcher |
| `server/config/` | Loopback profile, examples, resource manifests |
| `server/protocol/` | `.proto` definitions and generated Python modules |
| `server/tests/` | Server test suite |
| `server/docs/` | Architecture, phone setup, admin, asset policy |
| `server/scripts/` | TLS, build, import, and maintenance scripts |
| `server/android/` | Android app project and packaging scripts |
| `server/client/` | Frida agent JavaScript and native Rust client source |
| `server/data/static/` | Game Master binaries and Max/PvP references |
| `server/assets/game-master/` | Game Master binaries and manifest |
| `platform/` | Platform/admin source, tests, Docker files, launchers |
| `game-master/` | Top-level Game Master copy and notes |
| `docs/screenshots/` | README screenshots from the Android build |
| `app/README.md` | Android app entry point and build notes |
| `client/README.md` | Native client/agent entry point |

Game Master data is the only game data included. Small app UI PNGs under
`server/android/app/src/main/res` are retained so the Android project can be
built. The screenshots under `docs/screenshots/` are documentation captures,
not game data.

## Excluded

- APKs, XAPKs, split payloads, server-bootstrap archives, patched clients.
- Android build outputs, `.gradle`, payload parts, `.splits`, local properties.
- Pokémon/Unity asset bundles, textures, models, audio, world data, map tiles,
  camera/device photos, caches, and generated artwork.
- TLS certificates/private keys, signing keystores, admin tokens, saved
  platform state, player databases, and device data.
- Prebuilt Frida gadgets, SDK jars, native libraries, and other binaries.
- Python bytecode and build logs.

## Build notes

`server/android` must remain inside `server/` so its packaging scripts can find
`server/src`, `server/config`, `server/assets`, and `server/client` at the
expected relative paths. The app source release intentionally omits the client
APK, payload splits, and server-bootstrap archive; supply your own authorized
copies when building.
