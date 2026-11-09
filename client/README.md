# Native client and Frida agent source

The canonical source is **`../server/client/`**:

- `server/client/*.js` — Frida agent modules for auth, map, encounters, raids,
  routes, social, Max, PvP, and UI.
- `server/client/native/` — Rust native client bridge source and Cargo files.
- `server/client/plugin/` — agent build inputs where present.

This source release does **not** include prebuilt `libgadget.so` libraries,
patched clients, APKs, or signing material. Supply the Frida gadget and other
authorized tool binaries from your own toolchain; see
`../server/docs/APK-PATCHER.md` and `../server/docs/PHONE-SETUP.md`.
