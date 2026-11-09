<!--
  Copyright (C) Dittobuffer Contributors
  Copyright (C) chucny
  Copyright (C) gekkedavi

  This file is part of Dittobuffer. It is distributed under the GNU General
  Public License v3.0 (see LICENSE). The copyright notices in this file must
  be preserved in all copies, forks, and derivative works. Removing or
  altering these notices violates the license and copyright law.
-->

# Development storage audit - 2026-10-28

Read-only audit completed before M25.1 implementation/builds. No deletion, pruning, compression or movement was performed. The user confirmed they freed E: during this audit; the approximately 223 GiB increase is their cleanup, not an agent action. Source worktree remains the existing dirty M25 worktree; no new checkout/full copy.

All sizes below are GiB (1,073,741,824 bytes). Volume capacity/free space comes from Windows. Directory figures are logical file bytes or unique referenced bytes, not a promise of reclaimable allocated space. Sparse/compressed files, system metadata, denied paths and hard links prevent summing directory figures into Windows used space. Directory reparse points were not followed. A second project scan used os.stat NTFS identities because Windows DirEntry.stat reports zero inode/link counts.

## Drive capacity after the user cleanup

| Drive | Capacity GiB | Used GiB | Free GiB | Used |
| --- | ---: | ---: | ---: | ---: |
| C:\ | 222.53 | 218.31 | 4.22 | 98.1% |
| D:\ | 931.50 | 593.86 | 337.63 | 63.8% |
| E:\ | 953.85 | 708.67 | 245.19 | 74.3% |

At entry C: had about 4.13 GiB free, D: 337.72 GiB and E: 21.99 GiB. E: now has about 245 GiB after user cleanup. C: remains critically tight; do not put new APK/extraction/test data there or expand WSL unnecessarily. D: is not nearly full. The read-only census inspected approximately 3.37 million accessible files with bounded private metadata output (about 5.4 MB), no file-content copies.

## Project storage and hard links

| Directory | Logical GiB | Unique referenced GiB | Shared outside directory GiB |
| --- | ---: | ---: | ---: |
| `e:\dittobuffer` | 68.32 | 47.51 | 0.00 |
| `e:\dittobuffer\.private` | 43.53 | 32.08 | 9.12 |
| `e:\dittobuffer\.private\worktrees` | 35.48 | 24.03 | 9.12 |
| `e:\dittobuffer\.private\worktrees\m25-research-20261028` | 13.33 | 13.20 | 9.14 |
| `e:\dittobuffer\.private\worktrees\m331-m34-20261028` | 16.79 | 16.70 | 9.14 |
| `e:\dittobuffer\.private\worktrees\m24-gameplay-20261027` | 5.35 | 5.24 | 1.97 |
| `e:\pokemongo-librenpl` | 333.90 | 319.21 | 0.00 |
| `e:\pokemongo-librenpl\builds` | 305.92 | 291.31 | 0.00 |
| `d:\pokemongo-librenpl` | 110.09 | 110.09 | 0.00 |

The 6.57 GiB published Netherlands DB is one NTFS file with three links: primary, M33.1/M34 and M25. It is not three new 6.57 GiB copies. M25 references about 9.14 GiB also used elsewhere and owns about 4.06 GiB exclusively; even the latter includes source and evidence that must be retained. `.tools`/`.venv` junctions reuse primary installations. Deleting a linked resource name would not free the full advertised file size and is prohibited.

Large legacy generated/transfer trees (not disposable classifications):

- `e:\pokemongo-librenpl\builds\full-pc-transfer`: 15.44 GiB unique referenced.
- `e:\pokemongo-librenpl\builds\full-pc-transfer-validation-v3`: 19.49 GiB unique referenced.
- `e:\pokemongo-librenpl\builds\full-pc-transfer-validation-v2`: 18.32 GiB unique referenced.
- `e:\pokemongo-librenpl\builds\m202-routes-20261026-134913`: 23.64 GiB unique referenced.
- `e:\pokemongo-librenpl\builds\m20-routes-20261025-190211`: 16.92 GiB unique referenced.
- `e:\pokemongo-librenpl\builds\structure-audit-main-20261026`: 14.17 GiB unique referenced.
- `e:\dittobuffer\.staging`: 7.31 GiB unique referenced.
- `e:\dittobuffer\.tools`: 3.65 GiB unique referenced.
- `e:\dittobuffer\.venv`: 0.48 GiB unique referenced.

Repeated M20/Routes walking fixtures, transfer validation copies, world snapshots, extracted base/split trees, APK rebuilds and Rust targets account for substantial legacy growth. The old `builds` tree has about 173.5 GiB of recently modified logical files, which includes copies with preserved timestamps; this is evidence of recent modifications, not a trustworthy attribution of every byte to one milestone. M25 has roughly 11.3 GiB of recent unique referenced files, mostly already-linked world/resources plus generated build/test outputs. M33.1/M34 and M25 are dirty source worktrees and must not be removed wholesale.

## C: system, user caches and Codex

C: includes about 27.04 GiB `pagefile.sys` and 12.44 GiB `hiberfil.sys`; Windows manages these. No pagefile/hibernation setting change is proposed automatically. Logical root figures: Users 95.39 GiB, Windows 41.01 GiB, Program Files 29.24 GiB. Windows hard links mean those are not additive physical totals.

- `c:\users\davi2\.codex\sessions`: 7.32 GiB logical/referenced (0.00 means absent or below reporting precision).
- `c:\users\davi2\.codex\plugins`: 0.48 GiB logical/referenced (0.00 means absent or below reporting precision).
- `c:\users\davi2\.codex\.tmp`: 0.11 GiB logical/referenced (0.00 means absent or below reporting precision).
- `c:\users\davi2\appdata\local\temp`: 1.66 GiB logical/referenced (0.00 means absent or below reporting precision).
- `c:\users\davi2\appdata\local\npm-cache`: 0.37 GiB logical/referenced (0.00 means absent or below reporting precision).
- `c:\users\davi2\appdata\local\pip`: 0.46 GiB logical/referenced (0.00 means absent or below reporting precision).
- `c:\users\davi2\.gradle`: 6.74 GiB logical/referenced (0.00 means absent or below reporting precision).
- `c:\users\davi2\.cargo`: 0.00 GiB logical/referenced (0.00 means absent or below reporting precision).
- `c:\users\davi2\appdata\local\android`: 0.00 GiB logical/referenced (0.00 means absent or below reporting precision).
- `c:\users\davi2\appdata\local\wsl`: 4.40 GiB logical/referenced (0.00 means absent or below reporting precision).
- `c:\users\davi2\appdata\local\docker`: 1.64 GiB logical/referenced (0.00 means absent or below reporting precision).

One Codex session JSONL is about 5.97 GiB, modified this week; the sessions tree is 7.33 GiB. These are development history, not source or safe-to-delete cache. Long tool transcripts/image evidence can contribute, but no content or credential dump was performed. The Ubuntu-24.04 VHDX is 4.40 GiB, recently modified by builds; Docker disks total about 1.64 GiB and were last modified in September, so recent Rocket/Research work is not evidence that Docker caused this week's growth. Default Ubuntu points to a missing old disk. Do not unregister WSL or compact virtual disks automatically.

## Other drives and unrelated data

D: contains the 110.10 GiB older project copy, plus large unrelated Steam/Fortnite/Minecraft/application trees. E: Downloads is about 138.07 GiB logically, SteamLibrary 57.03 GiB and Minecraft 27.60 GiB. Their presence is not authorization for cleanup. Recycle Bin figures were about 7.78 GiB on C: and 28.05 GiB on E: during enumeration; those can change with the user's cleanup and are not included in project reclaim estimates.

## Cleanup candidates requiring separate explicit approval

No candidate is approved or removed. Estimates below are upper bounds and overlap; do not add parent and child totals.

| Candidate | Estimated scope | Conditions before approval |
| --- | --- | --- |
| Superseded unsigned/signed APK outputs and per-build decoded output trees | Several GiB within primary 7.31 GiB staging plus M25 2.76 GiB staging | Identify final APK/input closure and keep original backups, signing data and required decoded inputs. Review exact files first. |
| Rebuildable Rust target directories | Roughly 1 GiB per active target tree | Preserve source/toolchain/lockfiles and current working library; determine shared links. No cargo clean now. |
| Old isolated walking/test DB copies | Several GiB per legacy fixture, potentially tens of GiB across old builds | Review exact fixture ownership and retained evidence; never live/family/world DBs. Do not remove source worktrees. |
| Redundant transfer validation outputs | Large upper bound across 10-20 GiB validation trees | Preserve private transfer archives and required restoration artifacts; user must identify which outputs are redundant. Not currently safe for bulk deletion. |
| Windows TEMP | About 1.66 GiB | Select inactive, task-owned files only; shared/unrelated caches are not purge targets. |
| Codex old history/log management | One 5.97 GiB session dominates | Preserve history; use application-supported retention/export only after user review. No manual editing/deletion in this task. |

No defensible single total reclaimable number exists without selecting exact files and excluding protected restoration/source/asset data. The smallest low-risk review scope is superseded build outputs, a few GiB; larger legacy validation/fixture candidates require a file-level retention decision. No protected directory is marked disposable.

## Must not touch

All Git branches/stashes/index/source including dirty M25 and M33.1/M34; `.gitignore`; normal player/family databases, XP/dust/items/Pokemon/avatar data; published Netherlands data; private Unity/Pokemon resources/manifests/cache; original development/official APK backups; private transfer archives; signing keys/configuration; shared user caches and WSL/Docker data. The entire old E: project and D: copy remain preservation sources.

## Resource limits for M25.1

Reuse the existing M25 worktree, Python/NDK/Cargo installations, decoded APK input links, prior built test APK and original APK backup when its hash matches. Use one `.private/m251` evidence directory, bounded screenshots and small logs. Back up changed Android settings as a delta against the existing validated tar, not another full resource copy. Keep new temporary tests/builds on E: and check capacity before a build. Prefer server-field fixes so a new APK is not required. No large build is currently necessary; free E: is sufficient after user cleanup, C: is still constrained. Record final drive capacity and artifact sizes in the M25.1 report.


## M25.1 final capacity and artifact outcome

At 29 October 2026, approximately 13:08 local: C: 20,072,808,448 bytes free (18.69 GiB); D: 362,521,858,048 (337.62 GiB); E: 292,499,484,672 (272.41 GiB). Free-space increases came from the user/external activity, not agent cleanup. New `.private/m251` evidence occupied about 311 MiB at the checkpoint (324 files, 326,292,051 unique logical bytes), plus small subsequent receipts/screenshots; final exact count is in `storage-final.json`. Existing source-only secret-scan inputs (about 6 MB) and task-owned test caches were reused. No new APK build/copy, worktree/full checkout, full asset archive or full normal-database backup. No deletion, pruning, compression or moving of existing files occurred. The existing original APK and base Android TAR were reused with a changed-settings delta.
