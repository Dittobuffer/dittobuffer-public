"""Complete platform-owned live-ops document.

A room can schedule and tune every live system without a game server. The
shape intentionally matches ``dittobuffer.gameplay.liveops.legacy`` so the app
applies the same signed room JSON whether the host is this platform or a full
server.

Supported: wild spawn percentages, spawn weights and allow-list, shiny rates,
raid tier/boss weights, scheduled events, Community Days and Shadow overrides.
"""
from __future__ import annotations

import json
import math
import secrets
import time

EVENT_KINDS = ("shiny", "spawn", "raid", "spotlight")

DEFAULTS = {
    "enabled": True,
    "shiny_rates": {},
    "spawn_weights": {},
    "spawn_percent": {},
    "spawn_allow": [],
    "raid_tier_weights": {"1": 50, "3": 30, "5": 20, "6": 0},
    "raid_boss_weights": {},
    "events": [],
    "community_days": [],
    "shadow_overrides": {},
}


def now_ms() -> int:
    return int(time.time() * 1000)


def _rate(value, field: str) -> float:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number between 0 and 1") from None
    if not math.isfinite(rate) or not 0 <= rate <= 1:
        raise ValueError(f"{field} must be between 0 and 1")
    return rate


def _key(value) -> str:
    text = str(value).strip()
    if ":" in text:
        species, form = text.split(":", 1)
        try:
            return f"{int(species)}:{int(form)}"
        except ValueError:
            raise ValueError("Species keys must be id or id:form") from None
    try:
        return str(int(text))
    except ValueError:
        raise ValueError("Species keys must be numeric ids") from None


def _window(entry: dict) -> tuple[int, int]:
    starts = int(entry.get("starts_ms") or 0)
    ends = int(entry.get("ends_ms") or 0)
    if ends <= starts:
        raise ValueError("An event must end after it starts")
    return starts, ends


def _event(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Every event must be an object")
    kind = str(value.get("kind") or "spotlight")
    if kind not in EVENT_KINDS and kind != "community_day":
        raise ValueError("Unknown event kind")
    entry = {
        "id": str(value.get("id") or "event-" + secrets.token_hex(4))[:40],
        "name": str(value.get("name") or "Event")[:80],
        "kind": kind,
        "starts_ms": int(value.get("starts_ms") or 0),
        "ends_ms": int(value.get("ends_ms") or 0),
        "enabled": bool(value.get("enabled", True)),
        "species": [],
        "shiny_rate": None,
        "spawn_multiplier": 1.0,
        "raid_tier_weights": {},
        "shiny_rates": {},
        "spawn_weights": {},
    }
    _window(entry)
    species = value.get("species") or []
    if not isinstance(species, list):
        raise ValueError("Event species must be a list")
    try:
        entry["species"] = [int(item) for item in species]
    except (TypeError, ValueError):
        raise ValueError("Event species must be numeric ids") from None
    if value.get("shiny_rate") is not None:
        entry["shiny_rate"] = _rate(value["shiny_rate"], "Event shiny rate")
    if value.get("spawn_multiplier") is not None:
        try:
            entry["spawn_multiplier"] = max(0.0, min(100.0, float(value["spawn_multiplier"])))
        except (TypeError, ValueError):
            raise ValueError("Event spawn multiplier must be a number") from None
    for key, weight in (value.get("raid_tier_weights") or {}).items():
        try:
            tier = int(key)
        except (TypeError, ValueError):
            raise ValueError("Event raid tiers must be 1, 3, 5 or 6") from None
        if tier not in (1, 3, 5, 6):
            raise ValueError("Event raid tiers must be 1, 3, 5 or 6")
        entry["raid_tier_weights"][str(tier)] = max(0.0, min(1e6, float(weight)))
    for key, rate in (value.get("shiny_rates") or {}).items():
        entry["shiny_rates"][_key(key)] = _rate(rate, "Event shiny rate")
    for key, weight in (value.get("spawn_weights") or {}).items():
        try:
            entry["spawn_weights"][_key(key)] = max(0.0, min(100.0, float(weight)))
        except (TypeError, ValueError):
            raise ValueError("Event spawn weights must be numbers") from None
    return entry


def _community_day(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Every Community Day must be an object")
    entry = {
        "id": str(value.get("id") or "community-day-" + secrets.token_hex(4))[:40],
        "name": str(value.get("name") or "Community Day")[:80],
        "species": int(value.get("species") or 0),
        "form": int(value.get("form") or 0),
        "shiny_rate": _rate(value.get("shiny_rate", 0.04), "Community Day shiny rate"),
        "starts_ms": int(value.get("starts_ms") or 0),
        "ends_ms": int(value.get("ends_ms") or 0),
        "enabled": bool(value.get("enabled", True)),
        "spawn_share": max(0.0, min(1.0, float(value.get("spawn_share", 1.0)))),
        "raid_tier_weights": {},
    }
    _window(entry)
    if entry["species"] <= 0:
        raise ValueError("Choose a Community Day species")
    for key, weight in (value.get("raid_tier_weights") or {}).items():
        try:
            tier = int(key)
        except (TypeError, ValueError):
            raise ValueError("Community Day raid tiers must be 1, 3, 5 or 6") from None
        if tier not in (1, 3, 5, 6):
            raise ValueError("Community Day raid tiers must be 1, 3, 5 or 6")
        entry["raid_tier_weights"][str(tier)] = max(0.0, min(1e6, float(weight)))
    return entry


def normalise(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Live-ops settings must be an object")
    out = json.loads(json.dumps(DEFAULTS))
    out["enabled"] = bool(value.get("enabled", True))
    for key, rate in (value.get("shiny_rates") or {}).items():
        out["shiny_rates"][_key(key)] = _rate(rate, "Shiny rate")
    for key, weight in (value.get("spawn_weights") or {}).items():
        try:
            out["spawn_weights"][_key(key)] = max(0.0, min(100.0, float(weight)))
        except (TypeError, ValueError):
            raise ValueError("Spawn weights must be numbers") from None
    percent = value.get("spawn_percent") or {}
    if not isinstance(percent, dict):
        raise ValueError("spawn_percent must be an object of species=percent")
    total = 0.0
    for key, share in percent.items():
        try:
            share = float(share)
        except (TypeError, ValueError):
            raise ValueError("Spawn percentages must be numbers from 0 to 100") from None
        if not math.isfinite(share) or not 0 <= share <= 100:
            raise ValueError("Spawn percentages must be between 0 and 100")
        out["spawn_percent"][_key(key)] = round(share, 3)
        total += share
    if total > 100.001:
        raise ValueError("Spawn percentages add up to more than 100%")
    allow = value.get("spawn_allow") or []
    if not isinstance(allow, list):
        raise ValueError("spawn_allow must be a list of species")
    try:
        out["spawn_allow"] = [int(item) for item in allow]
    except (TypeError, ValueError):
        raise ValueError("spawn_allow must contain numeric species ids") from None
    for key, weight in (value.get("raid_tier_weights") or {}).items():
        try:
            tier = int(key)
        except (TypeError, ValueError):
            raise ValueError("Raid tiers are 1, 3, 5 and 6") from None
        if tier not in (1, 3, 5, 6):
            raise ValueError("Raid tiers are 1, 3, 5 and 6")
        out["raid_tier_weights"][str(tier)] = max(0.0, min(1e6, float(weight)))
    for key, weight in (value.get("raid_boss_weights") or {}).items():
        try:
            out["raid_boss_weights"][_key(key)] = max(0.0, min(100.0, float(weight)))
        except (TypeError, ValueError):
            raise ValueError("Raid boss weights must be numbers") from None
    events = value.get("events") or []
    if not isinstance(events, list) or len(events) > 100:
        raise ValueError("At most 100 events")
    out["events"] = [_event(item) for item in events]
    days = value.get("community_days") or []
    if not isinstance(days, list) or len(days) > 60:
        raise ValueError("At most 60 Community Days")
    out["community_days"] = [_community_day(item) for item in days]
    for key, override in (value.get("shadow_overrides") or {}).items():
        if not isinstance(override, dict):
            raise ValueError("Shadow overrides must be objects")
        out["shadow_overrides"][_key(key)] = {
            "enabled": bool(override.get("enabled", True)),
            "dust": max(0, min(100000, int(override.get("dust", 1000)))),
            "candy": max(0, min(1000, int(override.get("candy", 25)))),
        }
    return out


def defaults() -> dict:
    return json.loads(json.dumps(DEFAULTS))


def summary(value: dict | None = None) -> dict:
    value = normalise(value or {})
    return {
        "species_percent": len(value["spawn_percent"]),
        "species_weights": len(value["spawn_weights"]),
        "allow_list": len(value["spawn_allow"]),
        "raid_bosses": len(value["raid_boss_weights"]),
        "events": len(value["events"]),
        "community_days": len(value["community_days"]),
        "shadow_overrides": len(value["shadow_overrides"]),
    }
