# Dittobuffer 1.0.3 Android build

Built 2026-11-02 from `mergesimulwork`, package `org.dittobuffer.mobile`.

## Artifact

```
apk/built/Dittobuffer-1.0.3.xapk
bytes    7,405,283,539  (6.90 GiB)
sha256   d1a39ba32d2633d8b39b9ab52ae93dba3a0aa4a9be893e835f081e6e7e4f9b8a
version  1.0.3 (versionCode 4)
```

It is a Zip64 archive with `manifest.json`, `info.json`, `README.txt`,
`icon.png`, `base.apk` and `payload1.apk` … `payload4.apk`.

## Base and split hashes

| file | bytes | sha256 |
|---|---:|---|
| `base.apk` (`dittobuffer-mobile-base.apk`) | 217,644,334 | `5a6037993da2858f334b681b8eab2698fe5654e333dfe747c8fc464b054ef1cb` |
| `payload1.apk` | 1,800,262,044 | `c1381a8fe0a0dfd569c2f61f190ca69e6fa62cae6a644bde9ebf9cf317f8b9a5` |
| `payload2.apk` | 1,801,073,052 | `f82b8b077b54fad7c8f90db81473ca785cffc1041657d65fa25c7d33bb3e1b8f` |
| `payload3.apk` | 1,800,851,868 | `f73434369d9ca17273772800f58658e292b43037e660c9a03283f5cebdec9048` |
| `payload4.apk` | 1,661,448,604 | `7cad7b26ca8fa60a2e8f493ef5526726e36dd90cf996c535000ff1d3614bb6ba` |

All five APKs carry the same debug signer:

```
SHA-256 207b4e1e311f0fc6e2e6ad7ed39660a3d4ee6576b5600262202947906e6ac0fc
```

`aapt2 dump badging` reports `versionCode='4' versionName='1.0.3'` for the
base and for splits `payload1` … `payload4`.

## What changed in this rebuild

The previous base still bundled the 2026-11-01 client. This build bundles a
fresh standalone client:

```
client sha256  de3c8893ff98ccb37e3d97c447dfe0ac7600e2b0e7d530300314de6c5ad1ce92
agent sha256   e7facd12a8bf16b8a2dfa7b8c0277fb215d0ad617eab1e21e9e90a0ca55ccf2a
```

The packaged agent contains `LIBRE_REPAIR_RAID_RESOLVE_BACKGROUND`,
`RaidTransitionRingBackground`, and the updated auth endpoint handling. The
embedded client inside the base APK is byte-identical to
`builds/kit-m34-1.0.3/dittobuffer-family.apk`.

The first incremental Gradle build left an orphaned 198 MB copy of the old
client inside the 397 MB base APK. `gradle clean` followed by a fresh
`assembleDebug` produced the 217,644,334-byte base above; the base now has one
client entry.

## Verification performed

* `aapt2 dump badging` version/package/split checks — passed.
* `apksigner verify` on base and all four payloads — passed, same certificate.
* XAPK `zipfile.testzip()` — `None` (all CRCs good).
* XAPK member SHA-256 values equal the built base/payload files — passed.
* `manifest.json` lists package `org.dittobuffer.mobile`, version `1.0.3`,
  code `4`, and all five split entries.
* Embedded client SHA-256 inside the base equals the fresh standalone client.

Android installation and gameplay were **not** run in this pass. Do not claim
device verification from this build report.

## Install

```bash
adb install-multiple -r \
  apk/built/dittobuffer-mobile-base.apk \
  apk/built/payload1.apk apk/built/payload2.apk \
  apk/built/payload3.apk apk/built/payload4.apk
```

Or open `apk/built/Dittobuffer-1.0.3.xapk` with a Zip64/split-aware XAPK
installer. The payload is about 5 GiB; first launch needs time to unpack it.
