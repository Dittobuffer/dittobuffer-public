"""Signed room presence for phone and desktop clients.

The app joins a room by subscribing to ``dittobuffer/v1/<room>/room``. A room
therefore only exists for players once a retained document is on that topic.
Creating a row in the platform's JSON file is not enough.

This module builds that self-signed document from the hosted platform room and
the active server profile. It is deliberately independent of the optional
standalone server publisher: the platform key is the room key, so every app
pins the same key for the lifetime of the room.
"""

from __future__ import annotations

import json
import secrets
import time

from . import liveops as liveops_mod
from . import rsa_min

DEFAULT_LIVEOPS = liveops_mod.DEFAULTS


def _profile(workspace) -> dict:
    """Read the active server profile without making the platform depend on it."""
    if workspace is None or not workspace.available:
        return {}
    try:
        return dict(workspace.profile().get("data") or {})
    except Exception:
        return {}


def _liveops(platform, workspace) -> dict:
    """Platform-owned live-ops, with the old spawn defaults as a fallback."""
    try:
        value = platform.liveops()
    except Exception:
        value = liveops_mod.defaults()
    defaults = (platform.defaults().get("spawn_percent") or {})
    if defaults and not value.get("spawn_percent"):
        value["spawn_percent"] = dict(defaults)
    return value


def room_document(platform, workspace=None, poi_importer=None, world=None) -> dict | None:
    """Build and sign the current hosted room document.

    Returns ``None`` when no room is hosted. Raises ``ValueError`` when the
    hosted name is already bound to a different key: republishing in that case
    would make clients reject the room, so the caller must reset the room or
    use the original key.
    """
    room = platform.hosted()
    if room is None:
        return None

    host = platform.host_key
    public = rsa_min.public(host)
    fingerprint = rsa_min.fingerprint(public)
    previous = room.get("host_public")
    if previous and rsa_min.fingerprint(previous) != fingerprint:
        raise ValueError(
            "The hosted room is bound to a different signing key. "
            "Use Recovery to create a fresh room and key."
        )

    stamp = int(time.time() * 1000)
    profile = _profile(workspace)
    liveops = _liveops(platform, workspace)
    title = str(room.get("title") or room.get("display_name") or room["domain"])
    description = str(room.get("description") or "")
    body = {
        "version": stamp,
        "logical_ms": stamp,
        "players": 0,
        "liveops": liveops,
        "poi_count": 0,
        "pois": [],
        "server": {
            "title": title,
            "description": description,
            "profile": str(profile.get("profile") or "platform"),
            "target": "0.429.2",
        },
    }
    if poi_importer is not None:
        try:
            rows = poi_importer.all_rows()
            body["poi_count"] = len(rows)
            body["pois"] = [{key: row.get(key) for key in ("id", "name", "lat", "lng", "kind")
                            if row.get(key) is not None} for row in rows[:5000]]
        except Exception:
            body["poi_count"] = 0
            body["pois"] = []
    if world is not None:
        try:
            body["spawns"] = world.spawns()
            body["raids"] = world.raids()
            body["world"] = world.settings()
        except Exception:
            body["spawns"] = []
            body["raids"] = []

    document = {
        "v": 1,
        "kind": "room",
        "room": room["domain"],
        "title": title,
        "description": description,
        "host_public": public,
        "fingerprint": fingerprint,
        "state": {"body": body, "issued_ms": stamp},
        "defaults": {"spawn_percent": liveops.get("spawn_percent") or {}},
        "liveops": liveops,
        "issued_ms": stamp,
        "nonce": secrets.token_hex(12),
    }
    document["sig"] = rsa_min.sign(host, rsa_min.canonical(document))
    return document


def encoded(document: dict) -> bytes:
    """Stable wire encoding for an MQTT retained document."""
    return json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
