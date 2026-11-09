"""Automatic OpenStreetMap POI import for the platform.

The local game server scans around the phone that is currently playing.  A
platform process has no GPS feed, so it scans around a configured centre.  The
Overpass query, stable gym/stop assignment, deduplication and the 5,000-row
cap intentionally match ``dittobuffer.world.poi_import`` so both surfaces
produce the same places for the same coordinates.

The worker starts with the process, not with the admin page: leaving the
browser closed keeps the cache filling.  Settings and imported rows live in
the platform's data directory, next to ``platform.json``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import secrets
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("dittobuffer-platform.pois")

API = "https://overpass.openstreetmap.fr/api/interpreter"
USER_AGENT = "Dittobuffer-Platform/1.0 (private Pokemon server; automatic POI import)"
DEFAULTS = {
    # Always on: the platform starts filling its own POI world on first run.
    # Change the centre in the admin if the default city is not yours.
    "enabled": True,
    "lat": 52.3676,
    "lng": 4.9041,
    "radius_m": 1200,
    "interval_s": 60.0,
    "limit": 80,
    "gym_percent": 20.0,
}
MAX_AUTO = 5000
GUARD_S = 15.0
MAX_RADIUS_M = 5000
MAX_LIMIT = 300
MAX_QUERY = 5000

QUERY = '''[out:json][timeout:45];
(
  nwr(around:{radius},{lat},{lng})["name"]["amenity"~"^(arts_centre|community_centre|library|marketplace|place_of_worship|theatre|townhall|bus_station|ferry_terminal|fountain|clock|bench|shelter)$"];
  nwr(around:{radius},{lat},{lng})["name"]["tourism"~"^(attraction|artwork|gallery|museum|viewpoint|zoo|theme_park|picnic_site)$"];
  nwr(around:{radius},{lat},{lng})["name"]["leisure"~"^(park|garden|playground|recreation_ground|sports_centre|stadium|pitch|fitness_centre)$"];
  nwr(around:{radius},{lat},{lng})["name"]["historic"~"^(monument|memorial|castle|ruins|archaeological_site|wayside_cross|city_gate)$"];
  nwr(around:{radius},{lat},{lng})["name"]["railway"~"^(station|halt|tram_stop)$"];
  nwr(around:{radius},{lat},{lng})["name"]["highway"~"^(bus_stop|platform)$"];
);
out center {limit};'''


def now_ms() -> int:
    return int(time.time() * 1000)


def _as_float(value, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number") from None


def _point(value) -> tuple[float, float] | None:
    """Read a centre from {lat,lng}, [lat,lng] or a pair of keys."""
    if value is None:
        return None
    if isinstance(value, dict):
        lat, lng = value.get("lat"), value.get("lng")
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        lat, lng = value[0], value[1]
    else:
        return None
    if lat is None or lng is None or lat == "" or lng == "":
        return None
    lat, lng = _as_float(lat, "Latitude"), _as_float(lng, "Longitude")
    if not -90 <= lat <= 90 or not -180 <= lng <= 180:
        raise ValueError("Scan centre is outside the world")
    if abs(lat) < 1e-6 and abs(lng) < 1e-6:
        raise ValueError("Scan centre cannot be 0, 0")
    return lat, lng


def _normalise(value: dict | None, strict: bool = False) -> dict:
    raw = value if isinstance(value, dict) else {}
    out = dict(DEFAULTS)
    for key in ("lat", "lng"):
        if key in raw:
            out[key] = raw[key]

    enabled = raw.get("enabled", out["enabled"])
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in ("", "0", "false", "off", "no")
    out["enabled"] = bool(enabled)

    if "center" in raw:
        try:
            centre = _point(raw["center"])
        except ValueError:
            if strict:
                raise
            centre = None
        if centre:
            out["lat"], out["lng"] = centre
        else:
            out["lat"] = out["lng"] = None
    elif "lat" in raw or "lng" in raw:
        lat, lng = raw.get("lat"), raw.get("lng")
        if lat in (None, "") and lng in (None, ""):
            out["lat"] = out["lng"] = None
        else:
            try:
                out["lat"], out["lng"] = _as_float(lat, "Latitude"), _as_float(lng, "Longitude")
                if not -90 <= out["lat"] <= 90 or not -180 <= out["lng"] <= 180:
                    raise ValueError("Scan centre is outside the world")
                if abs(out["lat"]) < 1e-6 and abs(out["lng"]) < 1e-6:
                    raise ValueError("Scan centre cannot be 0, 0")
            except ValueError:
                if strict:
                    raise
                out["lat"] = out["lng"] = None

    try:
        out["radius_m"] = int(round(_as_float(raw.get("radius_m", out["radius_m"]), "Radius")))
        out["radius_m"] = max(200, min(MAX_RADIUS_M, out["radius_m"]))
        out["interval_s"] = max(GUARD_S, min(3600.0,
                                            _as_float(raw.get("interval_s", out["interval_s"]),
                                                      "Interval")))
        out["limit"] = int(round(_as_float(raw.get("limit", out["limit"]), "Limit")))
        out["limit"] = max(10, min(MAX_LIMIT, out["limit"]))
        out["gym_percent"] = _as_float(raw.get("gym_percent", out["gym_percent"]), "Gym chance")
        out["gym_percent"] = max(0.0, min(100.0, out["gym_percent"]))
    except ValueError:
        if strict:
            raise
        out["radius_m"] = DEFAULTS["radius_m"]
        out["interval_s"] = DEFAULTS["interval_s"]
        out["limit"] = DEFAULTS["limit"]
        out["gym_percent"] = DEFAULTS["gym_percent"]
    return out


def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dy = (lat2 - lat1) * 111320
    dx = (lng2 - lng1) * 111320 * max(0.05, math.cos(math.radians((lat1 + lat2) / 2)))
    return math.hypot(dx, dy)


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


class PoiImporter:
    """Small, thread-safe OSM cache with the same rules as the game server."""

    def __init__(self, data_dir, config: dict | None = None, fetcher=None):
        self.data_dir = Path(data_dir)
        self.settings_path = self.data_dir / "poi-import.json"
        self.data_path = self.data_dir / "pois.json"
        self._config = config if isinstance(config, dict) else {}
        self._fetcher = fetcher or self._fetch
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._generation = 0
        self._scanning = False
        self._last = {"scan_ms": 0, "error": "", "added": 0, "next_ms": 0}
        self._settings = self._read_settings()

    # ------------------------------------------------------------- settings
    def _read_settings(self) -> dict:
        value = None
        try:
            value = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = None
        if isinstance(value, dict):
            return _normalise(value)
        # First start: seed from config.json.  Once the admin saves, the
        # dedicated file is authoritative and survives a config edit.
        seed = dict(self._config)
        if "center" not in seed and ("lat" in seed or "lng" in seed):
            seed["center"] = {"lat": seed.get("lat"), "lng": seed.get("lng")}
        try:
            return _normalise(seed)
        except ValueError:
            log.warning("Invalid poi_import centre in config; waiting for the admin")
            return _normalise({})

    def save(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise ValueError("POI import settings must be an object")
        with self._lock:
            candidate = dict(self._settings)
            for key in ("enabled", "radius_m", "interval_s", "limit", "gym_percent"):
                if key in body:
                    candidate[key] = body[key]
            if "center" in body:
                candidate["center"] = body["center"]
                candidate.pop("lat", None)
                candidate.pop("lng", None)
            elif "lat" in body or "lng" in body:
                candidate["lat"], candidate["lng"] = body.get("lat"), body.get("lng")
            normalised = _normalise(candidate, strict=True)
            self._generation += 1
            self._settings = normalised
            _write_json(self.settings_path, normalised)
            if not normalised["enabled"]:
                self._clear_locked()
                self._last.update(error="", added=0)
            self._wake.set()
        return self.status()

    # ----------------------------------------------------------------- data
    def _read_data(self) -> dict:
        try:
            value = json.loads(self.data_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        rows = value.get("pois")
        if not isinstance(rows, list):
            rows = []
        return {"version": 1, "updated_ms": int(value.get("updated_ms") or 0),
                "pois": [row for row in rows if isinstance(row, dict) and row.get("id")]}

    def _write_data(self, value: dict) -> None:
        _write_json(self.data_path, value)

    def _clear_locked(self) -> None:
        self._write_data({"version": 1, "updated_ms": now_ms(), "pois": []})

    def clear(self, auto_only: bool = True) -> dict:
        """Remove generated POIs while keeping hand-placed ones by default."""
        with self._lock:
            self._generation += 1
            if auto_only:
                rows = [row for row in self._read_data()["pois"] if not row.get("auto_osm")]
                self._write_data({"version": 1, "updated_ms": now_ms(), "pois": rows})
            else:
                self._clear_locked()
            self._last.update(error="", added=0)
        return self.status()

    # -------------------------------------------------------- manual POI edits
    def all_rows(self) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._read_data()["pois"]]

    def save_row(self, body: dict) -> dict:
        """Create or edit a manual gym/stop; imported rows can be promoted."""
        if not isinstance(body, dict):
            raise ValueError("POI must be an object")
        identity = str(body.get("id") or "").strip()
        if not identity:
            identity = "manual:" + secrets.token_hex(8)
        if len(identity) > 120:
            raise ValueError("POI id is too long")
        name = str(body.get("name") or "").strip()
        if not name:
            raise ValueError("Give the place a name")
        name = name[:80]
        try:
            lat = _as_float(body.get("lat"), "Latitude")
            lng = _as_float(body.get("lng"), "Longitude")
        except (TypeError, ValueError):
            raise ValueError("Give the POI a latitude and longitude") from None
        if not -90 <= lat <= 90 or not -180 <= lng <= 180:
            raise ValueError("POI is outside the world")
        if abs(lat) < 1e-6 and abs(lng) < 1e-6:
            raise ValueError("POI cannot be at 0, 0")
        try:
            kind = int(body.get("kind", 1))
        except (TypeError, ValueError):
            raise ValueError("POI kind must be gym or stop") from None
        if kind not in (0, 1):
            raise ValueError("POI kind must be gym or stop")
        with self._lock:
            current = self._read_data()["pois"]
            existing = next((row for row in current if str(row.get("id")) == identity), None)
            row = dict(existing or {})
            row.update({
                "id": identity, "name": name, "lat": lat, "lng": lng, "kind": kind,
                "category": "gym" if kind == 0 else "stop", "score": 100,
                "status": "APPROVED", "reason": "Platform admin",
                "auto_osm": False, "updated_ms": now_ms(),
            })
            tags = body.get("tags")
            if isinstance(tags, dict):
                row["tags"] = json.dumps(tags, ensure_ascii=False)
            elif isinstance(tags, str):
                row["tags"] = tags[:2000]
            row.setdefault("tags", "")
            row.setdefault("photo_status", "PHOTO_PENDING")
            if existing is None:
                if len(current) >= MAX_AUTO:
                    raise ValueError("The POI list is full")
                current.append(row)
            else:
                current[current.index(existing)] = row
            self._write_data({"version": 1, "updated_ms": now_ms(), "pois": current})
        return row

    def delete_row(self, identity: str) -> bool:
        identity = str(identity or "").strip()
        if not identity:
            raise ValueError("Choose a POI to delete")
        with self._lock:
            current = self._read_data()["pois"]
            remaining = [row for row in current if str(row.get("id")) != identity]
            if len(remaining) == len(current):
                return False
            self._write_data({"version": 1, "updated_ms": now_ms(), "pois": remaining})
        return True

    def query(self, lat=None, lng=None, radius=None, kind=None, q="", limit: int = 200) -> dict:
        with self._lock:
            rows = [dict(row) for row in self._read_data()["pois"]]
        centre = None
        if lat not in (None, "") and lng not in (None, ""):
            centre = (_as_float(lat, "Latitude"), _as_float(lng, "Longitude"))
        elif lat not in (None, "") or lng not in (None, ""):
            raise ValueError("Give both latitude and longitude")
        if centre:
            lat0, lng0 = centre
            if not -90 <= lat0 <= 90 or not -180 <= lng0 <= 180:
                raise ValueError("Latitude or longitude is outside the world")
            try:
                radius_m = float(radius) if radius not in (None, "") else float(self._settings["radius_m"])
            except (TypeError, ValueError):
                raise ValueError("Radius must be a number") from None
            radius_m = max(1.0, radius_m)
            for row in rows:
                row["distance_m"] = round(_distance_m(lat0, lng0, float(row["lat"]), float(row["lng"])), 1)
            rows = [row for row in rows if row["distance_m"] <= radius_m]
            rows.sort(key=lambda row: row["distance_m"])
        else:
            rows.sort(key=lambda row: (str(row.get("name") or "").casefold(), str(row.get("id"))))

        kind_value = str(kind).strip().lower() if kind not in (None, "") else ""
        if kind_value in ("gym", "gyms", "0"):
            rows = [row for row in rows if int(row.get("kind", 1)) == 0]
        elif kind_value in ("stop", "stops", "pokestop", "pokestops", "1"):
            rows = [row for row in rows if int(row.get("kind", 1)) == 1]
        elif kind_value:
            raise ValueError("Kind must be gym or stop")

        needle = str(q or "").strip().casefold()
        if needle:
            rows = [row for row in rows if needle in str(row.get("name") or "").casefold()]
        total = len(rows)
        try:
            limit = max(1, min(MAX_QUERY, int(limit)))
        except (TypeError, ValueError):
            limit = 200
        return {"pois": rows[:limit], "total": total,
                "center": list(centre) if centre else None}

    # ---------------------------------------------------------------- fetch
    def _fetch(self, lat: float, lng: float, settings: dict) -> list[dict]:
        query = QUERY.format(radius=int(settings["radius_m"]), lat=f"{lat:.6f}",
                             lng=f"{lng:.6f}", limit=int(settings["limit"]))
        request = urllib.request.Request(
            API, data=urllib.parse.urlencode({"data": query}).encode(),
            headers={"User-Agent": USER_AGENT,
                     "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
        rows = []
        for element in payload.get("elements") or []:
            if not isinstance(element, dict):
                continue
            tags = element.get("tags") if isinstance(element.get("tags"), dict) else {}
            name = str(tags.get("name:en") or tags.get("name") or "").strip()
            if not name:
                continue
            centre = element.get("center") if isinstance(element.get("center"), dict) else {}
            latitude = element.get("lat", centre.get("lat"))
            longitude = element.get("lon", centre.get("lon"))
            if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
                continue
            identity = f"admin:osm:{element.get('type', 'node')}/{element.get('id')}"
            digest = hashlib.sha256(identity.encode()).digest()
            kind = 0 if int.from_bytes(digest[:4], "big") % 10000 < round(settings["gym_percent"] * 100) else 1
            category = ("amenity", "tourism", "leisure", "historic", "railway", "highway")
            label = next((tags[key] for key in category if tags.get(key)), "place")
            rows.append({
                "id": identity,
                "name": name[:80],
                "lat": float(latitude),
                "lng": float(longitude),
                "kind": kind,
                "category": "gym" if kind == 0 else "stop",
                "score": 100,
                "status": "APPROVED",
                "reason": f"OpenStreetMap {label}",
                "tags": json.dumps({"osm": label, "name": name[:80]}),
                "photo_status": "PHOTO_PENDING",
                "auto_osm": True,
            })
        return rows

    def _merge(self, rows: list[dict]) -> int:
        current = self._read_data()["pois"]
        auto = {row["id"]: row for row in current if row.get("auto_osm")}
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            existing = auto.get(row["id"])
            if existing:
                # A place keeps the role and the display name it already had;
                # refreshing the scan must not flip a stop into a gym.
                row["kind"] = existing.get("kind", row.get("kind"))
                row["category"] = existing.get("category", row.get("category"))
                row["name"] = existing.get("name", row.get("name"))
            auto[row["id"]] = row
        if len(auto) > MAX_AUTO:
            for key in list(auto)[:len(auto) - MAX_AUTO]:
                auto.pop(key, None)
        manual = [row for row in current if not row.get("auto_osm")]
        self._write_data({"version": 1, "updated_ms": now_ms(),
                          "pois": manual + list(auto.values())})
        return len(rows)

    # --------------------------------------------------------------- worker
    def scan_once(self) -> dict:
        with self._lock:
            settings = dict(self._settings)
            generation = self._generation
            if not settings["enabled"]:
                return self.status()
            if settings["lat"] is None or settings["lng"] is None:
                self._last.update(error="Set a scan centre (latitude and longitude)",
                                  next_ms=now_ms() + 30_000, added=0)
                return self.status()
            self._scanning = True
        try:
            rows = self._fetcher(float(settings["lat"]), float(settings["lng"]), settings)
            if not isinstance(rows, list):
                raise ValueError("POI source returned an invalid result")
            with self._lock:
                if generation == self._generation and self._settings["enabled"]:
                    added = self._merge(rows)
                    self._last.update(scan_ms=now_ms(), error="", added=added)
                else:
                    added = None
        except Exception as error:  # network, Overpass quota, bad JSON
            log.warning("OSM POI import failed: %s", error)
            with self._lock:
                self._last.update(scan_ms=now_ms(), error=str(error)[:200], added=0)
        finally:
            with self._lock:
                self._scanning = False
                self._last["next_ms"] = now_ms() + int(float(self._settings["interval_s"]) * 1000)
        return self.status()

    def scan_now(self) -> dict:
        if not self._thread or not self._thread.is_alive():
            self.start()
        else:
            self._wake.set()
        value = self.status()
        value["queued"] = True
        return value

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="poi-import", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                settings = dict(self._settings)
            if not settings["enabled"] or settings["lat"] is None or settings["lng"] is None:
                self._wake.wait(timeout=min(float(settings["interval_s"]), 30.0))
                self._wake.clear()
                continue
            self.scan_once()
            self._wake.wait(timeout=float(settings["interval_s"]))
            self._wake.clear()

    # --------------------------------------------------------------- status
    def status(self) -> dict:
        with self._lock:
            rows = self._read_data()["pois"]
            auto = [row for row in rows if row.get("auto_osm")]
            settings = dict(self._settings)
            return {
                **settings,
                "center": [settings["lat"], settings["lng"]]
                          if settings["lat"] is not None and settings["lng"] is not None else None,
                "auto_pois": len(auto),
                "auto_gyms": sum(1 for row in auto if int(row.get("kind", 1)) == 0),
                "auto_stops": sum(1 for row in auto if int(row.get("kind", 1)) == 1),
                "manual_pois": sum(1 for row in rows if not row.get("auto_osm")),
                "total_pois": len(rows),
                "last_scan_ms": self._last["scan_ms"],
                "last_error": self._last["error"],
                "last_added": self._last["added"],
                "next_scan_ms": self._last["next_ms"],
                "scanning": self._scanning,
                "running": bool(self._thread and self._thread.is_alive()),
                "data_bytes": self.data_path.stat().st_size if self.data_path.exists() else 0,
            }
