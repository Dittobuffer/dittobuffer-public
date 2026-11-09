# Copyright (C) Dittobuffer Contributors
# Copyright (C) chucny
# Copyright (C) gekkedavi
#
# This file is part of Dittobuffer. It is distributed under the GNU General
# Public License v3.0 (see LICENSE). The copyright notices in this file must
# be preserved in all copies, forks, and derivative works. Removing or
# altering these notices violates the license and copyright law.

"""Dittobuffer Platform: lightweight, signed HiveMQ rooms for Dittobuffer servers.

The platform is intentionally small: one JSON save file, one HTTP process and
one optional MQTT bridge. It never stores game assets or protocol definitions;
those already live in the Dittobuffer app.
"""

__version__ = "1.0.0"
__license__ = "Apache-2.0"
