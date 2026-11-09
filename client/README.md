<!--
  Copyright (C) Dittobuffer Contributors
  Copyright (C) chucny
  Copyright (C) gekkedavi

  This file is part of Dittobuffer. It is distributed under the GNU General
  Public License v3.0 (see LICENSE). The copyright notices in this file must
  be preserved in all copies, forks, and derivative works. Removing or
  altering these notices violates the license and copyright law.
-->

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
