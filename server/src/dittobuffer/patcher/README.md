# Patcher

| Path | Purpose |
| --- | --- |
| `patch.py` | Unified CLI: `server` (full server-ready injection) and `gadget` (Frida Gadget) |
| `frida_patch.py` | APK decoder, smali injector, rebuilder, zipaligner and signer |
| `gadget/arm64-v8a/libgadget.so` | Frida Gadget 17.24.3 (ARM64), SHA-256 `de0feab1...eaabc` |
| `config/` | Example gadget configurations |
| `keys/dittobuffer-patch.jks` | Signing key for patched APKs (alias `dittobuffer`, store/key password `android`) |

Run it from the kit root:

```powershell
.\scripts\patch-apk.ps1 .\apk\original\base-0.429.2.apk `
  --split .\apk\original\split-config-arm64_v8a-0.429.2.apk
```

Full behaviour, script mode and troubleshooting: `../docs/APK-PATCHER.md`.
