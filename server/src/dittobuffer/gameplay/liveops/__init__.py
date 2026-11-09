# Copyright (C) Dittobuffer Contributors
# Copyright (C) chucny
# Copyright (C) gekkedavi
#
# This file is part of Dittobuffer. It is distributed under the GNU General
# Public License v3.0 (see LICENSE). The copyright notices in this file must
# be preserved in all copies, forks, and derivative works. Removing or
# altering these notices violates the license and copyright law.

"""Versioned Studio content and the existing Platform live-ops API.

Studio publications are request-local. Existing JSON room settings remain valid
when no Studio pool overrides that subsystem.
"""
from .legacy import (
    DEFAULTS, FILE, active_events, allowed, community_day, install, load,
    normalise, now_ms, save, shadow_rule, snapshot, spawn_percent, spawn_weight,
)
from . import legacy

API_VERSION = 1


def shiny_rate(game, base, species, form, source='spawns'):
    effective = getattr(game, 'liveops_effective', {})
    if source in effective:
        return float(base)
    return legacy.shiny_rate(game, base, species, form)


def raid_pool(game):
    if 'raids' in getattr(game, 'liveops_effective', {}):
        return game.raids['pools']
    return legacy.raid_pool(game)
