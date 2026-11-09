# Android packaging notes

The APK is a shell around an in-process CPython (Chaquopy). It runs the real
`dittobuffer` server, the admin console and the plain-HTTP map origin on
`127.0.0.1`:

| Port | Service |
|------|---------|
| 8445 | authoritative game server, TLS (`config/tls/server.pem`) |
| 8080 | admin console, plain HTTP, embedded in the Admin tab |
| 8081 | map vector-tile origin, plain HTTP |

Nothing binds a public interface and the vanilla package
`com.nianticlabs.pokemongo` is never read, launched or modified. The only game
package referenced is the patched client `org.librenlp.pokemongo.dev`.

## Self-contained build

`build.sh` produces one APK that needs nothing else — no adb push, no Wi-Fi, no
separate download:

| Bundled asset | Size | Purpose |
|---------------|------|---------|
| `server-bootstrap.zip` | ~2 MB | server source, `protocol/`, `config/` (incl. TLS) |
| `payload1..5.apk` (5 splits) | ~5–8 GiB | every Pokémon/avatar bundle, every recovered model directory, map extract, client cache |
| `client/dittobuffer-family.apk` | ~190 MB | the patched 0.429.2 client itself |

AGP's packager is Zip32-only, so one APK cannot exceed 4 GiB. The payload is
packaged as four signed split APKs (`payload1.apk` … `payload4.apk`) next to
a small base APK. `install.sh` installs them atomically with
`adb install-multiple`; Android exposes every split's `assets/` under the same
path, and `Bootstrap` can also read the split APK files directly.

The payload includes all 2,739 manifest bundles. 1,749 of them resolve to
`builds/m107…m113` model directories (recovered models, official caches,
costume/shiny repairs) rather than `assets/pokemon`, so those whole trees are
packed. `assets/client-cache` is a byte-for-byte copy of the client's own
`UnityCache` and addressables cache; `builds/m151-client-runtime-v3` holds the
212 files the `client_resources` registry whitelists, hash-verified.

`data/maps` (the ODbL OpenStreetMap extract plus downloaded cells) is included
so the vector-tile origin answers with real roads immediately. The runtime
downloader keeps filling neighbouring cells while playing.

The payload deliberately excludes player data: the SQLite database and
`data/world` (walked snapshot, placed stops and photos). A fresh install has no
trainer history; spawning, catching, raids, PvP, map tiles and the admin
console all run from the bundled universal assets.

## Build (command line only)

```bash
android/make_icon.py       # regenerate launcher/wordmark icons (Pillow)
android/build.sh           # bootstrap + payload parts + base APK + splits
android/install.sh         # adb install-multiple base + every split
```

`build.sh` downloads nothing at build time except Gradle/Maven dependencies.
The JDK, Android SDK, Gradle and a private Python 3.10 (Chaquopy requires
exactly 3.10 as its build interpreter) live in `../.private/toolchain` and are
**not** part of the repository.

## First run and updates

On first start the service unpacks `server-bootstrap.zip` and then each
payload part into
`/sdcard/Android/data/org.dittobuffer.mobile/files/server/`. Version markers
(`bootstrap.version`, `payload.version`) make an APK update apply new server
files once. Payload files are never overwritten, so a tree pushed over adb
still wins.

`adb install-multiple` streams the 5+ GiB split set; expect a few minutes.
Android stores all splits as one installed app.

## Fast updates without rebuilding the payload

```bash
android/push-code.sh       # src/, protocol/, config/ — seconds
```

`push-code.sh` replaces `src/`, `protocol/` and `config/` in place; the app
imports the pushed tree first, so server changes need **no APK rebuild**. Only
Java/UI or asset changes require Gradle.

These scripts set `MSYS_NO_PATHCONV=1`; without it Git Bash rewrites
`/sdcard/...` into a Windows path and adb fails.

## Payload layout

```
/sdcard/Android/data/org.dittobuffer.mobile/files/server/
  src/            pushed source (takes precedence over the bootstrap)
  protocol/       generated protobuf modules
  config/         family.json, mobile.json (generated), tls/
  data/           static GM data and the OSM map extract
  assets/         pokemon + avatar bundles, client cache, static
  builds/         every model directory the asset manifests reference
  logs/           server.log, admin.log, mobile.log
```

## Vendored Python packages

Chaquopy cannot install native wheels, so real pure-Python packages are
vendored into `app/src/main/python/`:

- `google/protobuf` — the official pure-Python protobuf runtime. The compiled
  `_upb` extension is pruned and `mobile_server` sets
  `PROTOBUF_PYTHON_IMPLEMENTATION=python` so the Python backend is used.
- `s2sphere` — the real S2 geometry package, with only its two `future`
  (Python 2) imports removed.
- `tzdata` — the IANA time-zone database; `zoneinfo.reset_tzpath()` points at
  it because Android ships no zoneinfo tree.
- `cryptography` — the one shim: `Argon2id` is backed by stdlib
  `hashlib.scrypt` (`$scrypt$ln=14,r=8,p=1$…`). Phone-local password hashes are
  self-describing but **not interchangeable** with desktop Argon2 hashes.

`shapely` is intentionally absent: the OSM road renderer is pure Python and
does not import it. Only the optional world-snapshot renderer would.

## Limitations

- Chaquopy runs one CPython per app process; stopping the service stops both
  HTTP servers.
- First launch unpacks ~5 GiB. Keep the screen on until Home shows the server
  running; later launches reuse the unpacked tree.
- The admin console uses the system WebView (Chromium), not GeckoView.
