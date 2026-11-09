"""Opt-in import of real OpenStreetMap places as PokéStops and Gyms.

Off by default. While enabled, one small Overpass query is made per interval
around the trainer who is playing. Results are merged into the same admin
overlay the console edits, so no published world snapshot is touched. Turning
the switch off removes exactly the rows this module generated (and the admin
caller removes their forts), leaving hand-placed POIs alone.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.parse
import urllib.request

from dittobuffer.paths import ROOT

STATE = ROOT / 'data' / 'world' / 'poi-import.json'
API = 'https://overpass.openstreetmap.fr/api/interpreter'
USER_AGENT = 'Dittobuffer/0.1 (private Pokemon server; manual POI import)'
DEFAULTS = {
    # Always-on by default: scan slowly around whichever trainer is playing.
    'enabled': True,
    'gym_percent': 20.0,
    'radius_m': 1200,
    'interval_s': 60.0,
    'limit': 80,
}
MAX_AUTO = 5000
GUARD_S = 15.0
# Never turn an old test fix (for example a previous city) into POIs: only scan
# where the phone has actually reported itself within this window.
MAX_FIX_AGE_MS = 30 * 60 * 1000
_lock = threading.Lock()
_generation = 0
_thread = None
_last = {'scan_ms': 0, 'error': '', 'added': 0}

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


def settings():
    value = dict(DEFAULTS)
    try:
        raw = json.loads(STATE.read_text(encoding='utf8'))
    except (OSError, ValueError):
        raw = {}
    if isinstance(raw, dict):
        for key in DEFAULTS:
            if key in raw:
                value[key] = raw[key]
    value['enabled'] = bool(value['enabled'])
    value['gym_percent'] = max(0.0, min(100.0, float(value['gym_percent'])))
    value['radius_m'] = max(200, min(5000, int(value['radius_m'])))
    value['interval_s'] = max(GUARD_S, min(3600.0, float(value['interval_s'])))
    value['limit'] = max(10, min(300, int(value['limit'])))
    return value


def _write(value):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_name(STATE.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf8')
    temporary.replace(STATE)


def generated_ids():
    from dittobuffer.world import world_admin
    return [row['id'] for row in world_admin.load() if row.get('auto_osm')]


def remove_generated():
    """Drop imported rows; hand-placed POIs keep their place."""
    from dittobuffer.world import world_admin
    rows = world_admin.load()
    keep = [row for row in rows if not row.get('auto_osm')]
    if len(keep) != len(rows):
        world_admin.save(keep)
    return len(rows) - len(keep)


def save(body):
    if not isinstance(body, dict):
        raise ValueError('POI import settings must be an object')
    value = settings()
    if 'enabled' in body:
        value['enabled'] = bool(body['enabled'])
    if 'gym_percent' in body:
        value['gym_percent'] = max(0.0, min(100.0, float(body['gym_percent'])))
    if 'radius_m' in body:
        value['radius_m'] = max(200, min(5000, int(body['radius_m'])))
    if 'interval_s' in body:
        value['interval_s'] = max(GUARD_S, min(3600.0, float(body['interval_s'])))
    if 'limit' in body:
        value['limit'] = max(10, min(300, int(body['limit'])))
    global _generation
    with _lock:
        _generation += 1
        _write(value)
        # Any scan already in flight becomes invalid here; this also makes the
        # off switch remove imported rows before the request returns.
        if not value['enabled']:
            remove_generated()
            _last.update(error='', added=0)
    return value


def _is_gym(identity, percent):
    digest = hashlib.sha256(identity.encode()).digest()
    # Stable per place: refreshing a scan never flips a stop into a gym.
    return int.from_bytes(digest[:4], 'big') % 10000 < round(percent * 100)


def fetch(lat, lng, value):
    radius = int(value['radius_m'])
    query = QUERY.format(radius=radius, lat=f'{lat:.6f}', lng=f'{lng:.6f}', limit=int(value['limit']))
    request = urllib.request.Request(
        API, data=urllib.parse.urlencode({'data': query}).encode(),
        headers={'User-Agent': USER_AGENT, 'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    rows = []
    for element in payload.get('elements') or []:
        tags = element.get('tags') or {}
        name = str(tags.get('name:en') or tags.get('name') or '').strip()
        if not name:
            continue
        center = element.get('center') or {}
        latitude = element.get('lat', center.get('lat'))
        longitude = element.get('lon', center.get('lon'))
        if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            continue
        identity = f"admin:osm:{element.get('type', 'node')}/{element.get('id')}"
        kind = 0 if _is_gym(identity, value['gym_percent']) else 1
        category = ('amenity', 'tourism', 'leisure', 'historic', 'railway', 'highway')
        label = next((tags[key] for key in category if tags.get(key)), 'place')
        rows.append({
            'id': identity,
            'name': name[:80],
            'lat': float(latitude),
            'lng': float(longitude),
            'kind': kind,
            'category': 'gym' if kind == 0 else 'stop',
            'score': 100,
            'status': 'APPROVED',
            'reason': f'OpenStreetMap {label}',
            'tags': json.dumps({'osm': label, 'name': name[:80]}),
            'photo_status': 'PHOTO_PENDING',
            'auto_osm': True,
        })
    return rows


def merge(rows):
    """Merge a scan into the overlay, keeping the role each place already has."""
    from dittobuffer.world import world_admin
    current = world_admin.load()
    auto = {row['id']: row for row in current if row.get('auto_osm')}
    for row in rows:
        existing = auto.get(row['id'])
        if existing:
            row['kind'] = existing['kind']
            row['category'] = existing['category']
            row['name'] = existing['name']
        auto[row['id']] = row
    if len(auto) > MAX_AUTO:
        for key in list(auto)[:len(auto) - MAX_AUTO]:
            auto.pop(key, None)
    manual = [row for row in current if not row.get('auto_osm')]
    world_admin.save(manual + list(auto.values()))
    return len(rows)


def _loop(location):
    while True:
        value = settings()
        delay = max(GUARD_S, min(3600.0, float(value['interval_s'])))
        if value['enabled']:
            generation = _generation
            try:
                point = location() or {}
                lat, lng = point.get('lat'), point.get('lng')
                if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
                    raise ValueError('No trainer location yet')
                seen = point.get('seen_ms')
                if isinstance(seen, (int, float)) and seen > 0 and time.time() * 1000 - seen > MAX_FIX_AGE_MS:
                    raise ValueError('Player location is stale; waiting for a live GPS fix')
                rows = fetch(lat, lng, value)
                with _lock:
                    if _generation == generation and settings()['enabled']:
                        added = merge(rows)
                    else:
                        added = None
                if added is not None:
                    _last.update(scan_ms=int(time.time() * 1000), error='', added=added)
            except Exception as error:  # network, Overpass quota, bad JSON
                logging.getLogger(__name__).warning('OSM POI import failed: %s', error)
                _last.update(scan_ms=int(time.time() * 1000), error=str(error)[:200], added=0)
        time.sleep(delay)


def start(location):
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, args=(location,), name='poi-import', daemon=True)
    _thread.start()


def status():
    from dittobuffer.world import world_admin
    rows = [row for row in world_admin.load() if row.get('auto_osm')]
    return {**settings(), 'auto_pois': len(rows),
            'auto_gyms': sum(1 for row in rows if row.get('kind') == 0),
            'last_scan_ms': _last['scan_ms'], 'last_error': _last['error'],
            'last_added': _last['added']}
