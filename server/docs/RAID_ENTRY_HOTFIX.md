# Raid entry hotfix ? 2026-10-28

The Pixel 5 reproduced a raid returning to the overworld and leaving a broken
raid/map transition. This is a server correction; the installed standalone APK
and its saved-account login do not need replacement.

## Cause and implementation

`GET_RAID_DETAILS` found the persisted raid but returned `ErrorNotInRange`.
The shared gym proximity helper rejected any gym absent from the published
world snapshot, including a legitimate, persisted local development gym that
`GET_MAP_OBJECTS` had just advertised. The native client then produced repeated
NullReferenceExceptions while its transition mixed raid and overworld UI.

`gyms.accessible` now follows the existing map policy: an explicit non-approved
world record remains blocked; an existing local gym does not need an OSM entry.
`gyms.near` still validates finite coordinates, geographic bounds and the same
configured physical interaction distance. Battle admission and pass spending
continue to use those checks.

A separate compatibility issue was found in `GET_SIMPLE_RAID_DETAILS` (3091).
Its actual 0.429.2 request contains gym coordinates and no player coordinates.
The handler had attempted to read nonexistent player-coordinate fields. It now
checks gym accessibility for this read-only preview; joining and starting still
validate the real player coordinates. No remote admission was added.

Rejected detailed requests now log the gym, raid seed and rejection category,
without session tokens or credentials.

## Validation

- Targeted persistent raid/gym suite: **58 passed**.
- Four existing Node client suites passed.
- Three new lifecycle tests exercise a real isolated world SQLite snapshot and
  the authoritative RPC handlers: repeat/local entry and restart, one-time pass
  consumption, malformed/distant coordinates, and rejected/retired/pending gyms.
- Pixel 5: reproduced rejection on the existing local raid, then observed its
  native Arceus raid preview, CP, timer and Local Raid button after correction.
- Opening the repaired raid legitimately awarded the daily free raid pass.
  No normal-account battle was started or pass spent during this preview check.
- Full Python suite: **718 passed, 7 skipped** (six unavailable visual fixtures
  and Windows symlink privilege). Four Node suites passed; no native/Rust source
  changed, so the previous Rust build was retained.
- Final server code: native Arceus preview opened after cold restart, closed back
  to the map, then reopened successfully. Detail replies were 133 bytes instead
  of the failing two-byte error. No raid-triggered NullReferenceException was
  recorded in these final entry windows. One unrelated startup diagnostic at
  11:34:54 remains in the private device log, before raid entry at 11:35:49.
- Nearby showed three raids. Other closely overlapping map targets often opened
  the neighboring ordinary gym; this check does not claim every boss was opened.
- Normal collection/account/avatar/Research/eggs preserved; the only item change
  was the legitimate daily free raid pass (1401). No new battle or pass-spending
  receipt was written. Official APK hashes and published-world SHA matched.

## Preservation and review

Work is on `fix/raid-entry-20261028`, retaining `f7ef82b` (M24 plus latest main
`22e4565`). Existing local configuration/manifests and admin POI changes were
left unstaged. No `.gitignore`, APK, resource payload, published Netherlands
snapshot or official installation was changed. A consistent normal DB backup,
private screenshots and logs are in `.private/raid-entry-20261028/` and are not
for publication. No main merge or push is part of this hotfix.

This validation concerns entering/exiting the raid UI. Full Android combat,
cooperative victory and bonus catch have not been revalidated in this hotfix;
existing automated battle/reward regressions remain separate evidence.
