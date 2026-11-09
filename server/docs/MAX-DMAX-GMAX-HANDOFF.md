# Max / Dynamax / Gigantamax handoff request

**To:** the ChatGPT working on my friend's Pokémon GO private server  
**From:** the developer integrating Max gameplay into a separate local server  
**Goal:** send me a focused, path-preserving bundle that is sufficient to implement Dynamax/Gigantamax (Max) gameplay in my server without copying your whole private environment.

I am **not** asking for your player database, credentials, signing keys, APK, or copyrighted game assets. I need the **source, protocol definitions, data contracts, configuration, tests, and written behavior rules** that make your implementation work.

---

## 1. What to send

Please send one zip named `max-handoff.zip` with this structure:

```
max-handoff.zip
├── README-HANDOFF.md
├── MANIFEST.sha256
├── SOURCE/
│   ├── server/
│   │   ├── max_system.py
│   │   ├── max_battle.py
│   │   ├── max_station.py
│   │   ├── max_cheer.py
│   │   ├── max_stats.py
│   │   └── max_schedule.py
│   ├── game-max-hunks.diff
│   ├── master-max-hunks.diff
│   ├── http-max-hunks.diff
│   └── client/
│       └── local-max.js
├── PROTOCOL/
│   ├── game-max.patch
│   └── max-fields.txt
├── DATA/
│   ├── max-reference.schema.json
│   ├── max-reference.example.json
│   ├── max.example.json
│   ├── max-balance.example.json
│   ├── max-research.example.json
│   ├── power-spots-schema.sql
│   └── power-spots-sample.csv
├── TESTS/
│   ├── test_max_system.py
│   ├── test_max_battle.py
│   ├── test_max_station.py
│   ├── test_max_cheer_recovery.py
│   ├── test_max_stats.py
│   ├── test_max_groups.py
│   ├── test_max_schedule.py
│   ├── test_max_schedule_integration.py
│   ├── test_max_move_catalog.py
│   ├── test_max_ball_assets.py
│   ├── test_max_catch_origin.py
│   ├── test_max_particle_bindings.py
│   └── test_max_wildfire_bindings.py
└── DOCS/
    └── max-integration-notes.md
```

If a file is unchanged from upstream, say so in `README-HANDOFF.md`; do not send a full repository dump.

---

## 2. Source files and exact integration points

### 2.1 The six Max modules

Send these in full:

| File | Why I need it |
|---|---|
| `server/max_system.py` | MP wallet, daily/capacity caps, Power Spot materialization, loot, walking quest, Max move upgrades, GM template injection |
| `server/max_battle.py` | Lobby lifecycle, shared boss state, Max meter/phase, Max Attack/Guard/Spirit, boss AI, PBS event handling, catch/reward, rejoin |
| `server/max_station.py` | Stationing, recall, assist tracking, Candy/XL rewards, station power boost |
| `server/max_cheer.py` | Sideline cheer state machine and native event 25 payloads |
| `server/max_stats.py` | Inventory stat projection (`num_bread_battles_*`) |
| `server/max_schedule.py` | Rotation/event windows, timezone/DST handling, frozen difficulty snapshots |

I have already read these files and know they are compact/one-line-heavy. Please include the exact versions that pass your tests, not a cleaned-up rewrite.

### 2.2 Shared server files: send diffs, not whole files

Send unified diffs for only the Max-related hunks in:

- `server/game.py`
  - `install_max`
  - Max RPC dispatch methods, roughly:
    - `rpc_get_mp_summary`
    - `rpc_start_mp_walk_quest`
    - `rpc_enhance_bread_move`
    - `rpc_loot_station`
    - `rpc_get_station_info`
    - `rpc_get_station_details`
    - `rpc_station_pokemon`
    - `rpc_release_stationed_pokemon`
    - `rpc_get_num_station_assists`
    - `rpc_get_bread_lobby_details`
    - `rpc_join_bread_lobby`
    - `rpc_prepare_bread_lobby`
    - `rpc_leave_bread_lobby`
    - `rpc_start_bread_battle`
    - `rpc_complete_bread_battle`
    - `rpc_rejoin_battle_check`
  - map-object Power Spot injection
  - Max inventory item 1506 injection
  - Max catch path and `catch_member` handling
  - Max stats projection in `rpc_get_holoholo_inventory`
- `server/master.py`
  - `max_system.templates(...)`
  - `max_system.merge_client_settings(...)`
  - `VNEXT_BATTLE_CLIENT_SETTINGS` merge logic
- `server/__main__.py`
  - `/max/api/get-state`
  - `/max/api/send-player-event`
  - `/max/api/get-time`
  - `nbl-battle-id` / `nbl-player-id` / `Authorization` handling
- `server/accounts.py`
  - `principal_session`
  - `authorize_digest`
  - any session-rebind behavior used by `max_battle.join` and `rejoin_status`
- `server/database.py`
  - `transaction()` semantics, `touch()`, and any `total_changes` behavior used by `measured_transaction`
- `server/management.py`
  - `owned`, `update_owned`, `add_candy`, `candy`, and the exact signature contracts used by `max_system.enhance` and `max_station`
- `server/gyms.py`
  - `battle_busy`, `public`
- `server/battle_master.py`
  - `MOVES`, `effectiveness`, `settings`
- `server/raid_transport.py`
  - `pokemon(...)` and the PBS actor/party projection helpers
- `server/progression.py`
  - `bounds`
- `server/items.py`
  - `probabilities`
- `server/eggs.py`
  - `ensure`

If any of those helpers changed for Max, include the diff. If they are unchanged upstream, list the upstream version/commit instead.

### 2.3 Client hook

Send `client/local-max.js` in full, plus:

- the exact client version it targets (`0.429.2` ARM64);
- the Frida/gadget setup used to load it;
- the address map for the hooked functions (the script contains hard-coded addresses);
- any additional client patch needed for the Max Ball, G-Max models, shaders, or UI;
- a statement of which client files are original and which are patched.

I do not need the APK or asset binaries. I need the hook source and a manifest of expected paths/GUIDs/hashes so I can check my own authorized copy.

---

## 3. Protocol changes — this is the part most likely to be missed

My local `protocol/game.proto` currently has **empty** `BreadMoveLevelProto` and `BreadPokemonAllowlist` messages, so it cannot parse upgrade costs or allowlists. Please send the exact protocol patch, not just a generated `game_pb2.py`.

At minimum, confirm/include these definitions:

```proto
message BreadMoveLevelProto {
  int32 mp_cost = 1;
  int32 candy_cost = 2;
  int32 xl_candy_cost = 3;
  float multiplier = 4;
  int32 xp_reward = 5;
  int32 stardust_cost = 6;
}

message BreadPokemonAllowlist {
  int32 pokemon_id = 1;
  repeated PokemonDisplayProto.Form form = 2;
  int32 bread_mode = 3;
}

message BreadMoveLevelSettingsProto {
  int32 group = 1;
  repeated BreadMoveLevelProto a_settings = 2;
  repeated BreadMoveLevelProto b_settings = 3;
  repeated BreadMoveLevelProto c_settings = 4;
  repeated int32 xp_reward = 5;
}

message BreadBattleMpCostPerTier {
  int32 bread_battle_catch_mp_cost = 1;
  BreadBattleLevel battle_level = 2;
  int32 bread_battle_remote_catch_mp_cost = 3;
}
```

Also confirm the fields used for:

- `BreadSharedSettingsProto`
  - `allowed_bread_pokemon`
  - `allowed_sourdough_pokemon`
  - station capacity/display fields
  - `bread_battle_availability`
  - tutorial boost fields
- `BreadMoveMappingProto`
  - `type`
  - `move`
- `SourdoughMoveMappingProto`
  - `pokemon_id`
  - `form`
  - `move`
  - optional B/C overrides
- `BreadFeatureFlagsProto`
  - every flag used by `max_system.templates`
- `BreadBattleClientSettingsProto`
  - lobby sizes, prep cutoff, remote flags
- `StationedPokemonTableSettingsProto`
  - `tier_boosts` and the power-boost table
- Max PBS messages:
  - `MaxPbsSideline`
  - `MaxPbsCinematic`
  - `MaxPbsMoveMetadata`
  - `MaxPbsConsensus`
  - `BattleResourceProto` types 6, 7, 8, 9
  - `RvnConnectionProto`

Please include a `max-fields.txt` that lists every message/field you added or corrected from the upstream 0.429.2 proto, with the reason. If you generated `game_pb2.py`, say which `protoc` version and which source proto produced it.

---

## 4. Data and reference files

### 4.1 `max-reference.json`

This is the bridge between the client Game Master and the server. Please send:

1. `max-reference.schema.json` — machine-readable schema for the file.
2. `max-reference.example.json` — a small, non-secret example with the same shape.
3. The exact generator script: `scripts/import-max-reference.py`.
4. A full `max-reference.json` **only if** it is authorized to share; otherwise send its SHA256, template count, species-group count, and a redacted sample.

The full file must contain at least:

```json
{
  "source": "builds/authorized/game-master.cache",
  "source_sha256": "<sha256>",
  "templates": {
    "BREAD_FEATURE_FLAGS": "<hex wire bytes>",
    "BREAD_SHARED_SETTINGS": "<hex wire bytes>",
    "MP_SHARED_SETTINGS": "<hex wire bytes>",
    "BREAD_BATTLE_CLIENT_SETTINGS": "<hex wire bytes>",
    "BREAD_MOVE_MAPPING_SETTINGS": "<hex wire bytes>",
    "SOURDOUGH_MOVE_MAPPING_SETTINGS": "<hex wire bytes>",
    "BREAD_MOVE_LEVEL_SETTINGS_GROUP_1": "<hex wire bytes>",
    "STATIONED_POKEMON_POWER_BOOST_TABLE_SETTINGS": "<hex wire bytes>",
    "STATION_REWARD_SETTINGS": "<hex wire bytes>",
    "VNEXT_BATTLE_CLIENT_SETTINGS": "<hex wire bytes>"
  },
  "species_groups": {
    "1:0": 1,
    "4:0": 1,
    "7:0": 1,
    "831:0": 1
  },
  "max_move_ids": [1, 2, 3],
  "related_battle_settings": []
}
```

Important details I need confirmed:

- `species_groups` keys must be `"species_id:form_id"`, not just `"species_id:0"`. Your importer reads the form from PokemonSettings field **28**; my current importer does not. Send the exact mapping rule.
- `max_move_ids` is required; my current extraction produces an empty list.
- `templates` must be the **settings payload**, not the outer `GameMasterClientTemplateProto` wrapper. Confirm the unwrap rule for your cache format.
- Include the SHA256 of the source cache and a note that it is an authorized 0.429.2 ARM64 input.

### 4.2 Power Spot candidates

Send:

- `power-spots-schema.sql` with `metadata` and `candidates` definitions.
- `power-spots-sample.csv` with ~20 synthetic or public rows (not your full OSM extract).
- the importer script (`scripts/power-spots-import.py`) and the expected `metadata.source` JSON, including the `ODbL-1.0` license check.
- the exact query used by `max_system.materialize` so I can match the candidate schema.

### 4.3 Configuration examples

Send these in full:

- `server/max.example.json`
- `server/max-balance.example.json`
- `server/max-research.example.json`

Also send a short note explaining:

- which values are source-backed and which are custom development tuning;
- why `battle_enabled`, `stationing.enabled`, and `schedule.enabled` default to false;
- which `pools` entries are required for G-Max;
- the difference between `mode` 1/2 and `tier` 1/3/5/7;
- the expected `reset_hour`, timezone, daily cap, capacity, and fee behavior.

### 4.4 Asset manifest

I do not want copyrighted assets. I need a manifest so I can check my own authorized resources:

- Max Ball prefab GUID(s), bundle names, expected material/shader references, hashes.
- G-Max model/form mapping for each supported species.
- Gigantamax move IDs and move-type mapping.
- any client-side prefab/serialized-setting paths changed.

Your docs mention a Max Ball GUID such as `b93823682a58e45c3892885eba17e776`; include the full manifest rather than a single GUID.

---

## 5. Behavior specification I need in writing

The code is compact; please send a concise `max-integration-notes.md` covering these rules. This is what I will implement against, and it is more useful than line-by-line comments.

### 5.1 Modes and eligibility

- `bread_mode_enum`: 1 = Dynamax, 2 = Gigantamax.
- Which species/forms are eligible for each mode.
- How the server validates a party: owned, not egg, HP > 0, not deployed, not in a gym/raid/trade/battle, valid moves.
- How `BreadFeatureFlags` and `BreadSharedSettings` allowlists are enforced.

### 5.2 MP economy

- Item ID 1506 and how it appears in inventory.
- `mp_capacity`, `mp_base_daily_limit`, reset hour/timezone.
- Loot amount formula, first-visit bonus semantics, capacity/daily cap policy (`threshold` vs `clamp`).
- Walk quest goal/reward and claim flow.
- Reservation vs charge:
  - joining reserves MP but does not spend it;
  - winning charges once;
  - losing/leaving releases the hold;
  - retries must not double-charge.
- Exact fee lookup from `battle_mp_cost_per_tier`.

### 5.3 Max move upgrades

- Three slots (`move_type` 1/2/3).
- Initial slot levels.
- Cost table lookup by species/form group and target level.
- Which resources are consumed: MP, Candy, XL Candy, Stardust, XP.
- Idempotency: target level as durable operation identity.
- Failure codes and validation order.

### 5.4 Lobby and room lifecycle

- `join` → `prepare` → `start` → `complete`.
- Lobby duration, prep cutoff, early-start behavior.
- Party size 1–3, duplicate rejection, ownership.
- Whether a player can rejoin an existing room and what state is preserved.
- Room capacity: 4 for Dynamax, up to 20 for G-Max, subgroup size 4.
- How `max_players_per_bread_lobby` / `max_players_per_bread_dough_lobby` cap the room.
- What is frozen at room creation: boss, rules, station snapshot, difficulty multipliers, fee.

### 5.5 Combat

- Shared boss HP and per-player damage tracking.
- Group meter gain per normal attack; `meter_goal`; when Max phase begins.
- Max phase: 3 turns, 5-second cinematic/selection timing, Max Attack/Guard/Spirit.
- Guard: per-active-Pokémon shield, max stacks 3, lost on switch.
- Spirit: heal fraction of max HP, group scope.
- Boss AI interval, targeting, damage multiplier, dodging, substitutions, fainting.
- Normal attacks: move validation, energy, cooldown, STAB, effectiveness, player damage multiplier, station assist multiplier.
- Win/lose conditions and battle-end event payloads.
- How disconnected players are auto-selected and how they rejoin.

### 5.6 Rewards and catch

- Victory fee charge and `max_victories` row.
- Reward spawn creation, encounters row, Premier Balls count.
- `CompleteBreadBattle` replay semantics (once-only receipt).
- How `catch_member` identifies a Max reward.
- Catch probability, ball type, throws remaining, candy/XL, origin detail (`loot_battle_detail` / lobby size).
- Post-battle recovery with station ID only.
- Failure/loss reward behavior and whether a loss creates an encounter.

### 5.7 Stationing

- Capacity: per station, per player, display count.
- One active placement per Pokémon.
- Eligibility and lock checks.
- Assist counting: one room = one assist even with multiple winners.
- Candy/XL reward caps and one-time grant.
- Recall/expiry behavior and preserving canonical HP/identity.
- Power boost table lookup and combat multiplier.

### 5.8 Cheering

- Eligible state: own party fully fainted, active room, 3-star-or-higher.
- Tap count, interval, meter gain; provenance label.
- Charge state persistence.
- Event 25 payload and dispatch scope.
- Anti-replay / rate-limit rules.
- What happens when the target group is transforming or already in Max phase.

### 5.9 Scheduling

- Regular rotation vs event windows.
- Weekday/date filters, absolute timestamps, DST fold/gap policies.
- Priority and tie-breaking.
- Stable segment identity and candidate identity.
- How old stations are retired without deleting rooms/victories.
- How difficulty multipliers freeze into a room.

### 5.10 Failure and concurrency rules

- Transaction boundaries.
- Idempotency keys for loot, walk claims, upgrades, battle fees, catches, station rewards.
- What happens on duplicate RPCs, stale sessions, expired rooms, and concurrent joins.
- Which errors are safe to retry.
- Any known remaining bugs or “do not port this” sections.

---

## 6. Tests and evidence

Send all 13 `test_max_*.py` files, plus any helper module they import. Do not send private fixture databases; the tests should construct synthetic fixtures (your `test_max_system.py` already does this).

In `README-HANDOFF.md`, include:

1. Exact command(s) used to run the Max suite.
2. Python version and dependency lock (`requirements*.txt` or `pyproject.toml`).
3. The last passing output, with counts.
4. Which tests require proprietary inputs and which run standalone.
5. Any skipped/xfail tests and why.
6. A minimal smoke test that does not need the native client.

I already confirmed your pure schedule suite passes 33/33 in my environment. I could not import the other Max tests because `protocol/game_pb2.py` is missing from the source-only zip; please include the generated file or the exact generation command.

---

## 7. Packaging and safety rules

- Do **not** include player databases, `.private` directories, cookies, tokens, API keys, signing keys, or personal data.
- Do **not** include APKs, Unity bundles, textures, models, audio, or other copyrighted game assets.
- Replace proprietary inputs with hashes + generation instructions unless you have explicit permission to share them.
- If you include `max-reference.json`, confirm it is authorized to share; otherwise send the schema, generator, counts, and hash.
- Include `MANIFEST.sha256` for every file in the zip.
- Include the source commit hash (`git rev-parse HEAD`) and the client version (`0.429.2` ARM64).
- State the license/authorization boundary for any game data included.

---

## 8. Integration mapping for my server

My local server is a refactored layout, not your `server/` monolith. Map your files as follows:

| Your path | My likely target |
|---|---|
| `server/max_system.py` | `src/dittobuffer/gameplay/max_system.py` |
| `server/max_battle.py` | `src/dittobuffer/gameplay/max_battle.py` |
| `server/max_station.py` | `src/dittobuffer/gameplay/max_station.py` |
| `server/max_cheer.py` | `src/dittobuffer/gameplay/max_cheer.py` |
| `server/max_stats.py` | `src/dittobuffer/gameplay/max_stats.py` |
| `server/max_schedule.py` | `src/dittobuffer/gameplay/max_schedule.py` |
| `server/game.py` Max hunks | `src/dittobuffer/gameplay/game.py` and/or `src/dittobuffer/runtime/http.py` |
| `server/master.py` Max hunks | `src/dittobuffer/core/master.py` |
| `server/__main__.py` `/max/*` hunks | `src/dittobuffer/runtime/http.py` |
| `protocol/game.proto` | `protocol/game.proto`, then regenerate `protocol/game_pb2.py` |
| `client/local-max.js` | my client hook layer; send addresses and manifest |
| `tests/test_max_*.py` | `tests/` with import-path adaptations |

Please also tell me:

- which imports/helpers differ between your `server/` package and a `src/dittobuffer/gameplay/` package;
- whether your modules assume `game.config`, `game.db`, `game.player`, `game.touch`, `game.appearance`, and `game.forms` exactly as named;
- any circular imports or initialization-order requirements (`install_max` before/after raids, gyms, accounts);
- the exact SQLite schema and migration order;
- whether `max_system.reference()` is cached and how tests reset it.

My current local state:

- `src/dittobuffer/gameplay/` has no `max_*` runtime modules yet.
- `scripts/import-max-reference.py` exists but is incomplete.
- `data/static/max-reference.json` exists (18 templates, 1,023 species groups, 0 move IDs) and is **not** trustworthy yet.
- `protocol/game.proto` has the Max RPC IDs but empty `BreadMoveLevelProto` / `BreadPokemonAllowlist`, so costs/allowlists parse as empty.
- Admin panel can set `bread_mode` on spawns/raid bosses, but there is no Max battle/MP/station runtime.

So the most useful thing you can send is the **protocol patch + reference generator + six modules + tests + behavior notes**. I can adapt table names, imports, and RPC dispatch on my side.

---

## 9. Acceptance checklist for the handoff

The bundle is sufficient when I can:

- [ ] Regenerate `protocol/game_pb2.py` from the patched proto.
- [ ] Generate a `max-reference.json` with non-empty `BreadMoveLevelProto` costs, allowlists, and `max_move_ids`.
- [ ] Start my server with Max enabled, `battle_enabled` false, and see Power Spots/MP/upgrades.
- [ ] Enable battles and complete a solo Dynamax battle through join → prepare → start → attacks → Max phase → win → catch.
- [ ] Run a G-Max room with the configured mode/tier and correct boss/move/asset mapping.
- [ ] Station a caught Max Pokémon and recall it without duplicating rewards or corrupting HP.
- [ ] Run the Max test suite (or the standalone subset) with a documented pass count.
- [ ] Reconnect/replay a battle without double-charging MP or duplicating catches/rewards.

If any item cannot be sent, say why and send the closest safe substitute (schema, hash, diff, or written spec).

---

## 10. Short message to paste to the friend's ChatGPT

> I need to implement Dynamax/Gigantamax on my own Pokémon GO server. Please prepare `max-handoff.zip` following `MAX-DMAX-GMAX-HANDOFF.md`. Send the six `max_*` server modules in full, Max-related diffs for `game.py`, `master.py`, `__main__.py`, `accounts.py`, `database.py`, `management.py`, `gyms.py`, `battle_master.py`, `raid_transport.py`, `progression.py`, `items.py`, and `eggs.py`, the `protocol/game.proto` Max patch, the Max reference generator/schema/example, the three config examples, all 13 `test_max_*.py` files, `client/local-max.js` with its address/manifest notes, and a written behavior spec covering MP, upgrades, lobby, combat, Max moves, rewards, catch, stationing, cheering, scheduling, reconnect, and idempotency. Do not send player data, credentials, APKs, game assets, or any proprietary file you are not authorized to share; use hashes and generation instructions instead. Include `MANIFEST.sha256`, the source commit, client version, Python/dependency versions, and the exact test command/output.
