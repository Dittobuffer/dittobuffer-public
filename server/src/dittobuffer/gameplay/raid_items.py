# Copyright (C) Dittobuffer Contributors
# Copyright (C) chucny
# Copyright (C) gekkedavi
#
# This file is part of Dittobuffer. It is distributed under the GNU General
# Public License v3.0 (see LICENSE). The copyright notices in this file must
# be preserved in all copies, forks, and derivative works. Removing or
# altering these notices violates the license and copyright law.

"""Existing regular item handlers plus two source-backed reward definitions."""
import json
from pathlib import Path
from dittobuffer.gameplay.game import p
from dittobuffer.gameplay.items import POTIONS, REVIVES

DEFINITIONS = json.loads(Path(__file__).with_name('raid-items.json').read_text())
REWARD_ITEMS = {1, 2, 3, *POTIONS, *REVIVES, 701, 706, 1201, 1202, 1301, 1402}


def templates():
    return [p.GameMasterClientTemplateProto.FromString(bytes.fromhex(r['template_hex']))
            for r in DEFINITIONS['items']]
