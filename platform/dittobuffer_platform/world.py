"""Platform-owned world simulation.

The platform does not need the game server to publish a living map. It keeps a
small JSON world state next to the platform data:

* auto-downloaded POIs are the anchors;
* a configurable weighted/percent/uniform algorithm creates wild spawns;
* a configurable tier/boss algorithm creates raids at gym POIs;
* admins can add or delete individual spawns and raids at any time.

Spawns and raids are included in the signed room document and served by the
admin API. Everything is bounded so a long-running platform cannot grow
without limit.
"""
from __future__ import annotations

import json
import math
import random
import secrets
import threading
import time
from pathlib import Path

SPAWN_ALGORITHMS = ("weighted", "percent", "hybrid", "uniform")
RAID_ALGORITHMS = ("tier_weights", "uniform")
DEFAULT_SPECIES = [1, 4, 7, 10, 13, 16, 19, 25, 27, 37, 52, 54, 58, 60, 63,
                   66, 69, 72, 74, 77, 79, 81, 92, 95, 98, 104, 111, 113, 115,
                   118, 120, 122, 123, 129, 131, 133, 137, 143, 147, 152]
DEFAULT_BOSSES = [
    {"species": 150, "tier": 5, "weight": 1},
    {"species": 144, "tier": 5, "weight": 1},
    {"species": 145, "tier": 5, "weight": 1},
    {"species": 146, "tier": 5, "weight": 1},
    {"species": 243, "tier": 5, "weight": 1},
    {"species": 249, "tier": 5, "weight": 1},
    {"species": 250, "tier": 5, "weight": 1},
]

DEFAULTS = {
    "spawns": {
        "enabled": True,
        "algorithm": "weighted",
        "species": DEFAULT_SPECIES,
        "interval_s": 30,
        "max_active": 80,
        "lifetime_s": 1800,
        "per_cycle": 2,
        "radius_m": 300,
        "level_min": 1,
        "level_max": 35,
        "iv_min": 0,
        "iv_max": 15,
    },
    "raids": {
        "enabled": True,
        "algorithm": "tier_weights",
        "bosses": DEFAULT_BOSSES,
        "interval_s": 120,
        "max_active": 12,
        "lifetime_s": 3600,
        "egg_s": 0,
    },
}


def now_ms() -> int:
    return int(time.time() * 1000)


def _copy(value):
    return json.loads(json.dumps(value))


def _bounds(value, low, high, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a whole number") from None
    return max(low, min(high, number))


def _species(value) -> list[int]:
    if not isinstance(value, list):
        raise ValueError("Species pool must be a list of ids")
    out = []
    for item in value:
        try:
            number = int(item)
        except (TypeError, ValueError):
            raise ValueError("Species pool must contain numeric ids") from None
        if number > 0 and number not in out:
            out.append(number)
    return out[:500]


def _bosses(value) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError("Raid bosses must be a list")
    out = []
    for row in value[:200]:
        if not isinstance(row, dict):
            raise ValueError("Every raid boss must be an object")
        try:
            species = int(row.get("species"))
            tier = int(row.get("tier", 5))
            weight = float(row.get("weight", 1))
        except (TypeError, ValueError):
            raise ValueError("Raid boss species, tier and weight must be numeric") from None
        if species <= 0 or tier not in (1, 3, 5, 6) or not math.isfinite(weight) or weight <= 0:
            raise ValueError("Raid bosses need a positive species and weight and tier 1/3/5/6")
        out.append({"species": species, "tier": tier, "weight": round(weight, 3)})
    return out


def _normalise(value) -> dict:
    out = _copy(DEFAULTS)
    if not isinstance(value, dict):
        return out
    for section in ("spawns", "raids"):
        raw = value.get(section)
        if isinstance(raw, dict):
            out[section].update(raw)
    spawn = out["spawns"]
    spawn["enabled"] = bool(spawn.get("enabled", True))
    spawn["algorithm"] = str(spawn.get("algorithm") or "weighted")
    if spawn["algorithm"] not in SPAWN_ALGORITHMS:
        spawn["algorithm"] = "weighted"
    spawn["species"] = _species(spawn.get("species") or DEFAULT_SPECIES)
    spawn["interval_s"] = _bounds(spawn.get("interval_s", 30), 5, 3600, "Spawn interval")
    spawn["max_active"] = _bounds(spawn.get("max_active", 80), 1, 1000, "Spawn cap")
    spawn["lifetime_s"] = _bounds(spawn.get("lifetime_s", 1800), 60, 604800, "Spawn lifetime")
    spawn["per_cycle"] = _bounds(spawn.get("per_cycle", 2), 1, 50, "Spawns per cycle")
    spawn["radius_m"] = _bounds(spawn.get("radius_m", 300), 10, 5000, "Spawn radius")
    spawn["level_min"] = _bounds(spawn.get("level_min", 1), 1, 50, "Minimum level")
    spawn["level_max"] = _bounds(spawn.get("level_max", 35), 1, 50, "Maximum level")
    if spawn["level_min"] > spawn["level_max"]:
        spawn["level_min"], spawn["level_max"] = spawn["level_max"], spawn["level_min"]
    spawn["iv_min"] = _bounds(spawn.get("iv_min", 0), 0, 15, "Minimum IV")
    spawn["iv_max"] = _bounds(spawn.get("iv_max", 15), 0, 15, "Maximum IV")
    if spawn["iv_min"] > spawn["iv_max"]:
        spawn["iv_min"], spawn["iv_max"] = spawn["iv_max"], spawn["iv_min"]
    raid = out["raids"]
    raid["enabled"] = bool(raid.get("enabled", True))
    raid["algorithm"] = str(raid.get("algorithm") or "tier_weights")
    if raid["algorithm"] not in RAID_ALGORITHMS:
        raid["algorithm"] = "tier_weights"
    raid["bosses"] = _bosses(raid.get("bosses") or DEFAULT_BOSSES)
    raid["interval_s"] = _bounds(raid.get("interval_s", 120), 10, 86400, "Raid interval")
    raid["max_active"] = _bounds(raid.get("max_active", 12), 1, 200, "Raid cap")
    raid["lifetime_s"] = _bounds(raid.get("lifetime_s", 3600), 60, 604800, "Raid lifetime")
    raid["egg_s"] = _bounds(raid.get("egg_s", 0), 0, 3600, "Raid egg time")
    return out


def _pick(rng: random.Random, rows: list[dict], weights: list[float]) -> dict | None:
    total = sum(max(0.0, float(weight)) for weight in weights)
    if not rows or total <= 0:
        return None
    pick = rng.random() * total
    for row, weight in zip(rows, weights):
        pick -= max(0.0, float(weight))
        if pick <= 0:
            return row
    return rows[-1]


class WorldSimulator:
    def __init__(self, data_dir, poi_importer, liveops):
        self.path = Path(data_dir) / "world.json"
        self.poi_importer = poi_importer
        self.liveops = liveops
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._data = self._read()
        self._settings = _normalise(self._data.get("settings"))
        self._spawns = [row for row in self._data.get("spawns", []) if isinstance(row, dict)]
        self._raids = [row for row in self._data.get("raids", []) if isinstance(row, dict)]
        self._last = {"spawn_ms": 0, "raid_ms": 0, "error": ""}

    # ------------------------------------------------------------------ state
    def _read(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = {}
        return value if isinstance(value, dict) else {}

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        stamp = now_ms()
        temporary.write_text(json.dumps({
            "version": 1, "updated_ms": stamp, "settings": self._settings,
            "spawns": self._spawns, "raids": self._raids,
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def _active(self, rows, key="expires_ms") -> list[dict]:
        stamp = now_ms()
        return [row for row in rows if int(row.get(key, 0)) > stamp]

    def settings(self) -> dict:
        with self._lock:
            return _copy(self._settings)

    def save_settings(self, value: dict) -> dict:
        with self._lock:
            self._settings = _normalise(value if isinstance(value, dict) else {})
            self._last.update(spawn_ms=0, raid_ms=0, error="")
            self._write()
            return _copy(self._settings)

    def status(self) -> dict:
        with self._lock:
            specs = self._liveops()
            return {
                "settings": _copy(self._settings),
                "active_spawns": len(self._active(self._spawns)),
                "active_raids": len(self._active(self._raids)),
                "spawns_total": len(self._spawns),
                "raids_total": len(self._raids),
                "last_spawn_ms": self._last["spawn_ms"],
                "last_raid_ms": self._last["raid_ms"],
                "last_error": self._last["error"],
                "pois": len(self._pois()),
                "spawn_percent": specs.get("spawn_percent") or {},
                "raid_tier_weights": specs.get("raid_tier_weights") or {},
                "running": bool(self._thread and self._thread.is_alive()),
            }

    def _liveops(self) -> dict:
        try:
            return self.liveops() if callable(self.liveops) else (self.liveops or {})
        except Exception:
            return {}

    # ------------------------------------------------------------------ POIs
    def _pois(self) -> list[dict]:
        if self.poi_importer is None:
            return []
        try:
            return [row for row in self.poi_importer.all_rows()
                    if isinstance(row, dict) and row.get("lat") is not None and row.get("lng") is not None]
        except Exception:
            return []

    def _gyms(self) -> list[dict]:
        return [row for row in self._pois() if int(row.get("kind", 1)) == 0]

    def _pick_poi(self, rng: random.Random, gyms: bool = False) -> dict | None:
        rows = self._gyms() if gyms else self._pois()
        if not rows:
            return None
        return rows[rng.randrange(len(rows))]

    def _offset(self, poi: dict, radius_m: int, rng: random.Random) -> tuple[float, float]:
        distance = rng.random() * max(0, radius_m)
        angle = rng.random() * math.tau
        dy = math.sin(angle) * distance / 111320.0
        dx = math.cos(angle) * distance / (111320.0 * max(0.05, math.cos(math.radians(float(poi["lat"])))))
        return float(poi["lat"]) + dy, float(poi["lng"]) + dx

    # --------------------------------------------------------------- spawns
    def _spawn_species(self) -> tuple[list[int], list[float]]:
        specs = self._liveops()
        pool = list(self._settings["spawns"]["species"])
        allow = {int(item) for item in (specs.get("spawn_allow") or []) if str(item).isdigit()}
        if allow:
            pool = [species for species in pool if species in allow] or list(allow)
        if not pool:
            pool = list(DEFAULT_SPECIES)
        mode = self._settings["spawns"]["algorithm"]
        percent = specs.get("spawn_percent") or {}
        weights = specs.get("spawn_weights") or {}
        result = []
        for species in pool:
            if mode == "uniform":
                result.append(1.0)
            elif mode == "percent":
                result.append(float(percent.get(str(species), 1.0)))
            elif mode == "hybrid":
                result.append(max(0.0, float(percent.get(str(species), 1.0)))
                              * max(0.0, float(weights.get(str(species), 1.0))))
            else:
                result.append(max(0.0, float(weights.get(str(species), 1.0))))
        return pool, result

    def _new_spawn(self, rng: random.Random, poi: dict | None = None, body: dict | None = None,
                   manual: bool = True) -> dict:
        body = body or {}
        if body.get("poi_id"):
            poi = next((row for row in self._pois() if row.get("id") == body["poi_id"]), None)
        config = self._settings["spawns"]
        pool, weights = self._spawn_species()
        species = int(body.get("species") or (_pick(rng, pool, weights) or pool[0]))
        if species <= 0:
            raise ValueError("Species must be a positive id")
        if body.get("lat") is not None and body.get("lng") is not None:
            lat, lng = float(body["lat"]), float(body["lng"])
            if not -90 <= lat <= 90 or not -180 <= lng <= 180:
                raise ValueError("Spawn coordinates are outside the world")
        else:
            poi = poi or self._pick_poi(rng)
            if poi is None:
                raise ValueError("Add or import a POI first, or enter coordinates")
            lat, lng = self._offset(poi, config["radius_m"], rng)
        level = _bounds(body.get("level", rng.randint(config["level_min"], config["level_max"])),
                        config["level_min"], config["level_max"], "Level")
        ivs = [_bounds(item, 0, 15, "IV") for item in (body.get("ivs") or [rng.randint(config["iv_min"], config["iv_max"]) for _ in range(3)])][:3]
        while len(ivs) < 3:
            ivs.append(rng.randint(config["iv_min"], config["iv_max"]))
        return {
            "id": "spawn:" + secrets.token_hex(8), "species": species, "lat": lat, "lng": lng,
            "level": level, "ivs": ivs, "poi_id": (poi or {}).get("id", ""),
            "spawned_ms": now_ms(), "expires_ms": now_ms() + config["lifetime_s"] * 1000,
            "manual": bool(body.get("manual", manual)),
        }

    def add_spawn(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Spawn must be an object")
        with self._lock:
            rng = random.Random()
            row = self._new_spawn(rng, body=body)
            self._spawns.append(row)
            self._spawns = self._active(self._spawns)[-self._settings["spawns"]["max_active"]:]
            self._write()
            return _copy(row)

    def delete_spawn(self, identity: str) -> bool:
        with self._lock:
            before = len(self._spawns)
            self._spawns = [row for row in self._spawns if row.get("id") != identity]
            if len(self._spawns) != before:
                self._write()
                return True
            return False

    def clear_spawns(self, auto_only: bool = True) -> dict:
        with self._lock:
            self._spawns = [] if not auto_only else [row for row in self._spawns if row.get("manual")]
            self._write()
            return self.status()

    def spawns(self) -> list[dict]:
        with self._lock:
            return _copy(self._active(self._spawns))

    # ---------------------------------------------------------------- raids
    def _raid_boss(self) -> tuple[list[dict], list[float]]:
        specs = self._liveops()
        bosses = list(self._settings["raids"]["bosses"])
        if self._settings["raids"]["algorithm"] == "uniform":
            return bosses, [1.0 for _ in bosses]
        tiers = specs.get("raid_tier_weights") or {}
        weights = specs.get("raid_boss_weights") or {}
        result = []
        for boss in bosses:
            tier = float(tiers.get(str(boss["tier"]), 1.0))
            multiplier = float(weights.get(str(boss["species"]), 1.0))
            result.append(max(0.0, float(boss["weight"]) * tier * multiplier))
        return bosses, result

    def _new_raid(self, rng: random.Random, poi: dict | None = None, body: dict | None = None,
                  manual: bool = True) -> dict:
        body = body or {}
        if body.get("poi_id"):
            poi = next((row for row in self._pois() if row.get("id") == body["poi_id"]), None)
        poi = poi or self._pick_poi(rng, gyms=True)
        if poi is None:
            raise ValueError("Add or import a gym first")
        config = self._settings["raids"]
        bosses, weights = self._raid_boss()
        boss = _pick(rng, bosses, weights) or bosses[0]
        if body.get("species"):
            boss = {**boss, "species": int(body["species"])}
        if body.get("tier"):
            tier = int(body["tier"])
            if tier not in (1, 3, 5, 6):
                raise ValueError("Raid tier must be 1, 3, 5 or 6")
            boss = {**boss, "tier": tier}
        starts = now_ms() + int(config["egg_s"]) * 1000
        lifetime = _bounds(body.get("lifetime_s", config["lifetime_s"]), 60, 604800, "Raid lifetime")
        lat, lng = float(poi["lat"]), float(poi["lng"])
        return {"id": "raid:" + secrets.token_hex(8), "species": int(boss["species"]),
                "tier": int(boss["tier"]), "lat": lat, "lng": lng, "poi_id": poi.get("id", ""),
                "starts_ms": starts, "expires_ms": starts + lifetime * 1000,
                "manual": bool(body.get("manual", manual))}

    def add_raid(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Raid must be an object")
        with self._lock:
            row = self._new_raid(random.Random(), body=body)
            self._raids.append(row)
            self._raids = self._active(self._raids)[-self._settings["raids"]["max_active"]:]
            self._write()
            return _copy(row)

    def delete_raid(self, identity: str) -> bool:
        with self._lock:
            before = len(self._raids)
            self._raids = [row for row in self._raids if row.get("id") != identity]
            if len(self._raids) != before:
                self._write()
                return True
            return False

    def clear_raids(self, auto_only: bool = True) -> dict:
        with self._lock:
            self._raids = [] if not auto_only else [row for row in self._raids if row.get("manual")]
            self._write()
            return self.status()

    def raids(self) -> list[dict]:
        with self._lock:
            return _copy(self._active(self._raids))

    # ------------------------------------------------------------------ auto
    def tick(self, force: bool = False) -> dict:
        with self._lock:
            stamp = now_ms()
            self._spawns = self._active(self._spawns)
            self._raids = self._active(self._raids)
            pois = self._pois()
            if not pois:
                self._last["error"] = "Waiting for automatic POI download"
            else:
                self._last["error"] = ""
            spawn = self._settings["spawns"]
            if spawn["enabled"] and (force or stamp >= self._last["spawn_ms"]):
                for _ in range(min(spawn["per_cycle"], max(0, spawn["max_active"] - len(self._spawns)))):
                    try:
                        self._spawns.append(self._new_spawn(random.Random(), manual=False))
                    except ValueError:
                        break
                self._last["spawn_ms"] = stamp + spawn["interval_s"] * 1000
            raid = self._settings["raids"]
            if raid["enabled"] and (force or stamp >= self._last["raid_ms"]):
                for _ in range(max(0, min(raid["max_active"] - len(self._raids), 1))):
                    try:
                        self._raids.append(self._new_raid(random.Random(), manual=False))
                    except ValueError:
                        break
                self._last["raid_ms"] = stamp + raid["interval_s"] * 1000
            self._write()
            return self.status()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="platform-world", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as error:
                with self._lock:
                    self._last["error"] = str(error)[:200]
            self._stop.wait(3.0)
