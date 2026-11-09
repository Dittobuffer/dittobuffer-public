"""Administrator POIs layered over the published world.

The published SQLite snapshot stays immutable. Gym/stop records written by the
local admin panel live in ``data/world/admin-pois.json`` and are merged into
both world backends at query time, so a new gym is visible on the next map
request without rebuilding a multi-gigabyte world file.
"""
import json
import math
import os
import threading
from pathlib import Path

from dittobuffer.paths import ROOT
STATE = ROOT / 'data/world/admin-pois.json'

_lock = threading.Lock()
_cache = {'mtime': None, 'rows': []}


def _normalise(raw):
    if not isinstance(raw, dict):
        raise ValueError('POI must be an object')
    identity = raw.get('id')
    name = raw.get('name')
    if not isinstance(identity, str) or not identity.startswith('admin:'):
        raise ValueError('Admin POI requires an admin: id')
    if not isinstance(name, str) or not name.strip():
        raise ValueError('Admin POI requires a name')
    kind = raw.get('kind', 0)
    if kind not in (0, 1):
        raise ValueError('POI kind must be 0 (gym) or 1 (stop)')
    lat, lng = raw.get('lat'), raw.get('lng')
    if not isinstance(lat, (int, float)) or not -90 <= lat <= 90:
        raise ValueError('Invalid latitude')
    if not isinstance(lng, (int, float)) or not -180 <= lng <= 180:
        raise ValueError('Invalid longitude')
    row = dict(raw)
    row.update(id=identity, name=name.strip(), kind=kind, lat=float(lat), lng=float(lng))
    row.setdefault('status', 'APPROVED')
    row.setdefault('score', 100)
    row.setdefault('reason', 'local administrator')
    row.setdefault('category', 'gym' if kind == 0 else 'stop')
    row.setdefault('tags', '{}')
    row.setdefault('photo_status', 'PHOTO_PENDING')
    return row


def load():
    """Return validated rows, cached by file mtime. Malformed files read empty."""
    try:
        stat = STATE.stat()
    except FileNotFoundError:
        return []
    with _lock:
        if _cache['mtime'] == stat.st_mtime_ns:
            return [dict(row) for row in _cache['rows']]
        try:
            document = json.loads(STATE.read_text(encoding='utf8'))
        except (OSError, ValueError):
            return []
        rows = []
        for raw in document.get('pois', []):
            try:
                rows.append(_normalise(raw))
            except ValueError:
                continue
        _cache['mtime'] = stat.st_mtime_ns
        _cache['rows'] = rows
        return [dict(row) for row in rows]


def save(rows):
    """Atomically replace the overlay."""
    document = {'epoch': 1, 'pois': [dict(row) for row in rows]}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_name(STATE.name + '.tmp')
    temporary.write_text(json.dumps(document, indent=2) + '\n', encoding='utf8')
    os.replace(temporary, STATE)
    with _lock:
        _cache['mtime'] = None
        _cache['rows'] = []


def near(lat, lng, radius=1500):
    dy = radius / 111320
    dx = dy / max(.1, math.cos(math.radians(lat)))
    return [dict(row) for row in load()
            if row['status'] == 'APPROVED'
            and abs(row['lat'] - lat) <= dy and abs(row['lng'] - lng) <= dx
            and (row['lat'] - lat) ** 2 + ((row['lng'] - lng) * math.cos(math.radians(lat))) ** 2
            <= (radius / 111320) ** 2]


def by_id(identity):
    for row in load():
        if row['id'] == identity:
            return dict(row)
    return None
