"""Dittobuffer Platform: lightweight, signed HiveMQ rooms for Dittobuffer servers.

The platform is intentionally small: one JSON save file, one HTTP process and
one optional MQTT bridge. It never stores game assets or protocol definitions;
those already live in the Dittobuffer app.
"""

__version__ = "1.0.0"
__license__ = "Apache-2.0"
