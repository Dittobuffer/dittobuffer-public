# APK patcher

Two entry points, one toolchain.

```bash
python -m dittobuffer --patcher /path/to/app.apk      # Frida Gadget injection
python -m dittobuffer.patcher.patch server --host 127.0.0.1   # full family build
```

## One-shot injection

`--patcher` decodes the APK with apktool, injects the bundled Frida Gadget, wires
`System.loadLibrary("gadget")` into the application class, bundles the requested agent, zipaligns and
signs the result.

```bash
python -m dittobuffer --patcher apk/PokemonGO-0.429.2-base.apk
# -> apk/patched/PokemonGO-0.429.2-base-gadget.apk
```

| Flag | Default | Meaning |
|---|---|---|
| `--output`, `-o` | `apk/patched/<name>-gadget.apk` | Result path |
| `--script` | listen mode | Bundle an agent JS (`client/local-auth.js` etc.) |
| `--listen-address` / `--listen-port` | `127.0.0.1:27042` | Gadget interaction socket |
| `--abi` | `arm64-v8a` | Target ABI directory |
| `--split` | – | Split APKs to re-sign with the same key |
| `--keystore` / `--alias` | `src/dittobuffer/patcher/keys/dittobuffer-patch.jks` / `dittobuffer` | Signing identity |
| `--verbose` | off | Show every tool invocation |

Connect to the gadget after launching:

```bash
adb forward tcp:27042 tcp:27042
frida -H 127.0.0.1:27042 -l client/local-auth.js
```

## Full family build

The server build combines the maintained native plugin, family CA, launcher and embedded runtime into
one standalone ARM64 APK. Supply your own legally obtained 0.429.2 base and ARM64 split inputs and any
independently required resources described in [ASSETS.md](ASSETS.md). Repository access or a matching
checksum does not grant redistribution permission.

The build expects independently decoded inputs at `builds/client-base/` and `builds/client-split/`.
These are local build inputs, not source contributions; the current immutable `.gitignore` does not
blanket-ignore `builds/`, so keep those decoded trees out of commits. Missing inputs or unresolved
Git LFS pointers stop the command before provisioning.

```bash
python -m dittobuffer.patcher.patch server --host 192.168.1.20 --port 8445
```

The host must be the private or loopback IPv4 address the phone will use. Provisioning runs through
`tools/transfer-runtime.py provision` and does not require a private transfer archive. To reuse an
already provisioned matching endpoint, pass `--no-provision`; `--rotate-tls` explicitly requests a
new TLS certificate.

The plugin is selected in this order:

1. `client/native/target/aarch64-linux-android/release/libNianticLabsPlugin.so` from the maintained Rust source and native build helpers.
2. `client/plugin/libNianticLabsPlugin.so` from the reviewed kit inputs.
3. An existing `builds/libNianticLabsPlugin.so` as a legacy read-only fallback. New native helpers do not write here.

Select a particular rebuilt library with `--plugin`:

```bash
python -m dittobuffer.patcher.patch server --host 192.168.1.20 --port 8445 --plugin client/native/target/aarch64-linux-android/release/libNianticLabsPlugin.so
```

The selected library must be an actual little-endian ARM64 ELF file. A missing file, an LFS pointer
or a library for another architecture produces an actionable error.

Each build then:

1. Validates the provisioned endpoint, public profile and public CA certificate. Profiles containing
   account credentials or private signing material are rejected.
2. Creates a fresh ignored `.staging/patcher-<id>/` directory and copies the decoded base/split into it.
3. Copies `builds/transfer-public-profile.json` into that working directory and injects the plugin
   into both working copies, including when the original split has no plugin file.
4. Runs `scripts/build-standalone.py` against those disposable copies to merge the split and embed
   the launcher, agent, public CA and runtime. `config/public-profile.json` and the original decoded
   inputs remain unchanged.
5. Exports a unique `apk/built/dittobuffer-family-<host>-<id>.apk` with matching `.build.json` and
   `.inject.json` reports. Previous outputs remain in place. The working directory is retained for
   inspection, including after a failed build.

Both `.staging/` and `apk/built/` are already ignored; no ignore-rule changes are required.
The injection report records paths, hashes and `android_install_and_gameplay: NOT_TESTED`.
Eight [synthetic integration tests](../tests/test_patcher_integration.py) cover orchestration, input
preservation, retries and validation without invoking APK tools. They do not demonstrate an APK
build, installation or Android gameplay from this new package structure.

## Bundled toolchain

| Tool | Path |
|---|---|
| apktool | `tools/apktool/apktool.jar` (+ framework `1.apk`) |
| zipalign / apksigner | `tools/android-build-tools/` |
| Frida Gadget 17.24.3 (arm64) | `tools/frida-gadget/arm64-v8a/libgadget.so` |
| Signing key | `src/dittobuffer/patcher/keys/dittobuffer-patch.jks` |
| Build tools runner | `tools/transfer-runtime.py`, `scripts/build-standalone.py` |

Java 17+ must be on `PATH` (or `JAVA_HOME`).

## Safety rules

- The full family build preserves its decoded inputs and tracked public profile; each attempt uses fresh working copies and unique final outputs.
- The family launcher rejects Niantic hosts, so a patched build cannot accidentally talk to production.
- Keep `apk/built/` and `apk/patched/` out of version control (already ignored).
- Use the patched build only with accounts you are prepared to lose.
