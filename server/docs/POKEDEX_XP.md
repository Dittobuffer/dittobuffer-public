<!--
  Copyright (C) Dittobuffer Contributors
  Copyright (C) chucny
  Copyright (C) gekkedavi

  This file is part of Dittobuffer. It is distributed under the GNU General
  Public License v3.0 (see LICENSE). The copyright notices in this file must
  be preserved in all copies, forks, and derivative works. Removing or
  altering these notices violates the license and copyright law.
-->

# First Pok?dex capture XP

A successful capture of a species the trainer has never owned now grants a base **1,000 XP** registration bonus. The 0.429.2 catch response uses the actual `HoloActivityType_ActivityPokedexEntryNew` (8), allowing the native summary to display its registration line. Catch XP event multipliers apply consistently to this score row. The top-level `pokedex_entry_xp` option defaults to 1000; `config/reward-bonuses.example.json` documents it.

The check uses the existing durable `pokedex_observations`, after importing preserved legacy encounter/storage facts and before inserting the new owned Pokemon. It runs in the same SQLite transaction as the capture, Pok?dex update, XP, inventory and catch response. A repeat RPC returns the original result; another capture of that species cannot receive a second registration award. Transfers, shiny/form changes and restarts do not erase species registration. Two simultaneous successful catches serialize through the existing authoritative database transaction and award the species bonus once per trainer.

Misses, breakouts and sightings do not earn this bonus. Existing stored Pokemon are not retroactively awarded XP. No normal progression was edited for testing. Isolated tests cover native score encoding, replay, transfer/restart persistence, seen-only behavior, different accounts and concurrent catches. The M33 report records the actual Pixel acceptance evidence.
