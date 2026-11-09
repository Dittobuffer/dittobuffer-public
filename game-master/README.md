<!--
  Copyright (C) Dittobuffer Contributors
  Copyright (C) chucny
  Copyright (C) gekkedavi

  This file is part of Dittobuffer. It is distributed under the GNU General
  Public License v3.0 (see LICENSE). The copyright notices in this file must
  be preserved in all copies, forks, and derivative works. Removing or
  altering these notices violates the license and copyright law.
-->

# Game Master data

Game Master data is the only game data included in this source release.

| File | Purpose |
|---|---|
| `gmts.binpb.gz` | Generated Game Master templates |
| `settings.binpb.gz` | Game Master settings |
| `manifest.json` | Game Master manifest |
| `max-reference.json` | Max battle reference data |
| `pvp-reference.json` | PvP reference data |

The server expects the same files under:

- `../server/assets/game-master/`
- `../server/data/static/`

Those copies are included. Do not add Pokémon/Unity asset bundles, world data,
map extracts, photos, or other third-party content to this release.
