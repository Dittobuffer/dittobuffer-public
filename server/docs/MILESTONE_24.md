# M24 - Gameplay completion and remaining compatibility work

Branch: `feature/m24-gameplay-completion-20261027`.
Workspace: `E:/DittoBuffer/.private/worktrees/m24-gameplay-20261027`.
Origin/main base: `bd2ab84010b8a47cfd72d02d2f0cb275e4817e91`.
The already published source baseline `5e367f83e12261e91320cc71505c9272bebf3b87`
is an intentional prerequisite merge. Local machine-only commit `2032b56`
is excluded from this branch's ancestry. Nothing was pushed or merged into main.

## Native acceptance results

| Milestone | Android verification | Local implementation commit |
|---|---|---|
| [M24.1 Raid summary](MILESTONE_24_1.md) | Real Pidgey victory, measured Hardest Hitter card, 15 Premier Balls, earned catch, storage after restart | 296cf160a1a577a5bd21d2022b8d1ccb202f1e92 |
| [M24.2 Rocket balloons](MILESTONE_24_2.md) | Render, tap, dialogue, full Grunt battle, rewards, user-assisted Shadow Bulbasaur catch, departure and restart | f28dd05a4834b8840abbdb8689beabe80305d93d |
| [M24.3 Rewards](MILESTONE_24_3.md) | First catch1650XP, seven actual stop spins across lab dates, day7 spin6050XP, Great Throw/day7 catch6250XP, restart and no repeated daily reward | ccfae2851de177d17d6e9142d655894a8e73c7df |
| [M24.4 Gym recovery](MILESTONE_24_4.md) | Natural egg/raid/expiry sequence in one Android process; defender returns visually, Photo Disc works, real ordinary Gym battle | 20f85eb27134c27c6a70c9aebfdc86aea4d79654 |
| [M24.5 New trainer](MILESTONE_24_5.md) | Empty DB registration, native avatar selection, three starter choices, real Squirtle catch, name claim, stop tutorial/spin, bag, storage, cold restart | Separate local commit containing this report; exact SHA in the final private Git receipt |

The daily-calendar fixture changed only the server reward clock, never the
phone clock, authentication or battle timer. Middle catch-streak counters were
seeded solely to reach a declared day7 fixture; seven stop spins were genuine
native actions. No physical walking credit was fabricated. Test trainer
collections, invitations and rewards stayed in isolated private databases.

Already functional systems were reused: family accounts and encrypted saved
credentials, native transport, combat, capture/storage, rewards receipts,
wardrobe, eggs, research and Gym ownership. Per-milestone reports identify the
specific protocol, resource and lifecycle defects and exact source files.

## Checks

- Full Python suite: **705 passed, 8 skipped**, 290.73s. Six skipped checks lack
  separately supplied proprietary visual fixtures; one lacks Windows symlink
  privileges; the default APK-path check initially had no artifact there.
- Final targeted onboarding/standalone suite: **11 passed**, including the newly
  added concurrent starter-claim test and the real APK preservation check that
  initially skipped. Original game dex and native contents match the build inputs.
- Four Node suites passed: family/resource boundaries, world resume, UI/HUD/Max
  isolation, and raid routing (21 connections).
- Rust: **158 passed, 2 ignored**, followed by successful ARM64 NDK release
  build. The two ignored existing interactive/transport checks remain ignored.
  One existing unused-constant warning remains. Rust source was not changed.
- Full standalone Java/D8/assembly/sign/install succeeded for native tests.
  The Android-tested artifact is documented in M24.1. Existing ARM64 plugin
  bytes were preserved; the later successful native build was not installed.
- Source review/secret scan and final commit receipt are retained privately.
  No assets, APKs, databases, keys, screenshots or credentials are staged.

## Limitations / next concrete work

- Weekly foreground-distance reporting and exactly-once rewards pass backend
  tests, but the native weekly reward dialog and physical walking are NOT_TESTED.
  Google Fit/Health background ingestion is not implemented. Daily streaks and
  weekly Adventure Sync rewards remain distinct systems.
- Only the supported default FTUE avatar preset was saved/native-verified. All
  nine portraits load; the other eight require wardrobe eligibility/template
  validation before full support can be claimed.
- Level1 research progress was observed; its full Research screen introduction,
  hidden Pikachu starter and modern FTUE V2 remain NOT_TESTED.
- Fresh Leader/Super Radar balloon workflows and simultaneous two-device raid
  achievements remain backend-covered rather than freshly Android-verified.
- Raid achievement contrast, individual fast item-animation frames and audio
  remain presentation verification gaps. The complete native summary/catch loop
  was demonstrated; individual unobserved effects are not claimed.

Next work should finish those narrow native compatibility gaps, beginning with
weekly reward notification/ingestion and the remaining avatar preset ownership
mapping. Master Ball functionality remains outside this task.

## Preservation and runtime restoration

The main checkout remains clean on `integration/workspace-migration-20261027`,
commit29ce82e, with every preexisting Git ref and .gitignore preserved. Old
workspaces and private transfer archives were not development targets.

The original development APK and all **62 archived regular app files**, including
encrypted credentials/preferences, were restored byte for byte; UID10272 remains.
The two official APK hashes match the initial baseline. The test APK and final
fixture app data were retained separately, not discarded. Device8445 again
forwards to host8445. No official app/cache writes or persistent input changes
were made; no orphan input writer was found.

Every accepted local Pokemon bundle (2739), avatar bundle (46), and runtime
resource (388) passed its size/hash check. The published Netherlands snapshot
still hashes to `ca7568429854a23d5d46ffd218acbab5378d56d9d49779d9041d808b7dfec81e`.
Normal player, Pokemon, inventory, currency, research and distance totals match
the initial snapshot. Five transient tables differ: normal RPC receipts, recent
location metadata and the expired balloon row. No live database backup was
restored over player data. Full per-table evidence is retained privately.

The overnight interruption stopped the previously running services. Restarting
the old primary branch's default config exposed its preexisting legacy resource
paths, disabled M23 registry and JSON test-world settings. Those tracked files
were deliberately preserved. The normal server was resumed using the recorded
pre-task Netherlands/M23 profile in `.private/m24-20261027/restored-runtime-config.json`.
Two read-only published source blobs from5e367f83 restore its catalog/health
compatibility in the private `serve-preserved-runtime.py` launcher; all other
primary source is identical to that published baseline. No M24 gameplay code is
running against the normal collection. Trusted HTTPS health and /world/health
match the recorded pre-task runtime, catalog digest and Netherlands epoch.

Normal server8445 and admin8080 run again; isolated fixture8448 is stopped.
To resume the preserved server after stopping it, from E:/DittoBuffer run:
`E:/DittoBuffer/.venv/Scripts/python.exe .private/m24-20261027/serve-preserved-runtime.py`.
Do not run two listeners on8445. The old default START-SERVER profile needs the
already reviewed world/resource integration before it can replace this private
recovery launcher. Do not change primary history or overwrite its family config
merely to hide this distinction.

Private reports/backups: `E:/DittoBuffer/.private/m24-20261027/`.
Milestone screenshots, RPC logs, database evidence and test logs remain in the
worktree's ignored `.private/m24-*` directories. Generated test remainders are
retained there. No remote branch has been published for M24.
