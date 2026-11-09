# Android app

The canonical Android project is **`../server/android/`**. It is kept inside
the server root because `make_bootstrap.py` and `build.sh` package the sibling
`server/src`, `server/config`, `server/assets`, and `server/client` trees into
the phone APK.

Included source:

- `server/android/app/src/main/java` — launcher, bootstrap, server service
- `server/android/app/src/main/python/mobile_server.py` — phone server bridge
  and room-POI importer
- `server/android/app/src/main/res` — UI resources
- `server/android/app/src/main/AndroidManifest.xml`
- `server/android/app/build.gradle`, root Gradle files and packaging scripts

Not included:

- `server/android/app/src/main/assets/` (client APK, server-bootstrap, payloads)
- APKs, split payloads, the Android SDK, Chaquopy Python 3.10, or the patched
  family client

Build from `../server/android/` after supplying an authorized client APK and
payload set. See `../server/android/README.md` for the packaging flow.
