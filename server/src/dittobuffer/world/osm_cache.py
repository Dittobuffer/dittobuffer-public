"""Slow, per-area OpenStreetMap downloads for the local game map.

The public OSM API is deliberately used one small cell at a time.  The cell
around a trainer is fetched before the map response is returned; surrounding
cells are queued by distance to a single background worker so a login never
starts a burst of API requests.  A second trainer elsewhere only jumps ahead
for their own centre cell, never for a whole surrounding area.
"""
from __future__ import annotations

import gzip
import itertools
import json
import logging
import math
import queue
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from dittobuffer.paths import ROOT

AREA_DIR = ROOT / 'data' / 'maps' / 'areas'
CELL_DEG = 0.006
DEFAULT_RADIUS_M = 1000
API = 'https://api.openstreetmap.org/api/0.6/map?bbox={left:.7f},{bottom:.7f},{right:.7f},{top:.7f}'
USER_AGENT = 'Dittobuffer/0.1 (private family Pokemon server; low-volume map cache)'

_queue: queue.PriorityQueue[tuple[int, int, tuple[int, int]]] = queue.PriorityQueue()
_queued: set[tuple[int, int]] = set()
_events: dict[tuple[int, int], threading.Event] = {}
_failed: dict[tuple[int, int], float] = {}
_sequence = itertools.count()
_lock = threading.Lock()
_worker: threading.Thread | None = None
_expander: threading.Thread | None = None
_last_request = 0.0
_REQUEST_INTERVAL = 2.0
_RETRY_AFTER = 900

# Progressive expansion.  One ring of cells is queued around the area the
# trainer plays in as soon as the queue drains, so the cache keeps growing
# outward for as long as the server is up, at the same polite request pace.
DEFAULT_INTERVAL_S = 2.0
MIN_INTERVAL_S = 0.5
MAX_INTERVAL_S = 60.0
RADIUS_MIN_M = 200
RADIUS_MAX_M = 20000
HUB_SHIFT_M = 5000
EXPAND_PAUSE_S = 5.0
PENDING_TARGET = 8
# Even when the admin leaves the ring cap at zero, never walk past this radius.
# The old unbounded default queued the whole region one ring at a time forever,
# changing the map epoch every few seconds and starving the phone of tiles.
MAX_RING = 12
# The public API answers HTTP 509/429 when an address goes over its quota.
# One hit pauses every map request for a while instead of retrying into a wall.
BLOCKED_PAUSE_S = 900.0
_blocked_until = 0.0

_hub: tuple[float, float] | None = None
_focus: tuple[float, float] | None = None
_ring_index = 0
_expand = True
_ring_max = 0


def _key(lat: float, lng: float) -> tuple[int, int]:
    return round(lat / CELL_DEG), round(lng / CELL_DEG)


def _filename(key: tuple[int, int]) -> Path:
    return AREA_DIR / f'{key[0]}_{key[1]}.osm.gz'


def _bounds(key: tuple[int, int]):
    lat, lng = key[0] * CELL_DEG, key[1] * CELL_DEG
    return lat - CELL_DEG / 2, lng - CELL_DEG / 2, lat + CELL_DEG / 2, lng + CELL_DEG / 2


def _needed(lat: float, lng: float, radius: float) -> list[tuple[int, int]]:
    dy = radius / 111320
    dx = radius / (111320 * max(.05, math.cos(math.radians(lat))))
    keys = []
    for north in range(math.floor((lat - dy) / CELL_DEG), math.ceil((lat + dy) / CELL_DEG) + 1):
        for east in range(math.floor((lng - dx) / CELL_DEG), math.ceil((lng + dx) / CELL_DEG) + 1):
            keys.append((north, east))
    center = _key(lat, lng)
    keys.sort(key=lambda item: (max(abs(item[0] - center[0]), abs(item[1] - center[1])), item))
    return keys


def _penalise(seconds: float = BLOCKED_PAUSE_S):
    """Remember a quota refusal so no further request is made until it expires."""
    global _blocked_until
    _blocked_until = max(_blocked_until, time.monotonic() + seconds)


def _wait_for_quota():
    remaining = _blocked_until - time.monotonic()
    while remaining > 0:
        time.sleep(min(remaining, 30.0))
        remaining = _blocked_until - time.monotonic()


def _fetch(key: tuple[int, int]):
    bottom, left, top, right = _bounds(key)
    global _last_request
    _wait_for_quota()
    with _lock:
        # Reserve the slot, then sleep outside the lock: a map reply must never
        # wait on the downloader's politeness interval.
        wait = _REQUEST_INTERVAL - (time.monotonic() - _last_request)
        _last_request = time.monotonic() + max(0.0, wait)
    if wait > 0:
        time.sleep(wait)
    request = urllib.request.Request(
        API.format(left=left, bottom=bottom, right=right, top=top),
        headers={'User-Agent': USER_AGENT, 'Accept': 'application/xml,text/xml'},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        body = response.read()
    if not body.lstrip().startswith(b'<?xml') and b'<osm' not in body[:200]:
        raise ValueError('OSM API returned a non-XML document')
    AREA_DIR.mkdir(parents=True, exist_ok=True)
    target = _filename(key)
    temporary = target.with_suffix('.tmp')
    with gzip.open(temporary, 'wb') as out:
        out.write(body)
    temporary.replace(target)


def _state_file() -> Path:
    return AREA_DIR / '.expand.json'


def _distance_m(first: tuple[float, float], second: tuple[float, float]) -> float:
    lat1, lng1 = first
    lat2, lng2 = second
    dy = (lat2 - lat1) * 111320
    dx = (lng2 - lng1) * 111320 * max(.05, math.cos(math.radians((lat1 + lat2) / 2)))
    return math.hypot(dx, dy)


def _apply(settings):
    """Pick up admin changes without restarting the server."""
    global _REQUEST_INTERVAL, _expand, _ring_max
    try:
        _REQUEST_INTERVAL = max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, float(settings.get('osm_request_interval_s', DEFAULT_INTERVAL_S))))
    except (TypeError, ValueError):
        _REQUEST_INTERVAL = DEFAULT_INTERVAL_S
    _expand = settings.get('osm_expand', True) is not False
    try:
        _ring_max = max(0, int(settings.get('osm_expand_ring_max', 0)))
    except (TypeError, ValueError):
        _ring_max = 0
    global _focus
    focus = settings.get('osm_expand_focus')
    _focus = None
    if isinstance(focus, dict):
        try:
            _focus = (float(focus['lat']), float(focus['lng']))
        except (KeyError, TypeError, ValueError):
            _focus = None


def _save_state():
    try:
        AREA_DIR.mkdir(parents=True, exist_ok=True)
        _state_file().write_text(json.dumps({'hub': list(_hub) if _hub else None, 'ring': _ring_index,
                                             'updated_ms': int(time.time() * 1000)}), encoding='utf8')
    except OSError:
        pass


def _load_state():
    global _hub, _ring_index
    try:
        value = json.loads(_state_file().read_text(encoding='utf8'))
    except (OSError, ValueError):
        return
    hub = value.get('hub') if isinstance(value, dict) else None
    if isinstance(hub, list) and len(hub) == 2:
        try:
            _hub = (float(hub[0]), float(hub[1]))
            _ring_index = max(0, int(value.get('ring', 0)))
        except (TypeError, ValueError):
            _hub, _ring_index = None, 0


def _remember(lat: float, lng: float):
    """Follow the trainer; a configured focus pins the area instead."""
    global _hub, _ring_index
    target = _focus or (float(lat), float(lng))
    if _hub is None or _distance_m(_hub, target) > HUB_SHIFT_M:
        _hub = target
        _ring_index = 0
        _save_state()


def resume(config=None):
    """Start the trickle at boot so a remembered area keeps filling in."""
    global _hub, _ring_index
    settings = (config or {}).get('world', {})
    if settings.get('osm_download', False) is not True:
        return None
    _apply(settings)
    if _focus and (_hub is None or _distance_m(_hub, _focus) > HUB_SHIFT_M):
        _hub, _ring_index = _focus, 0
        _save_state()
    _start()
    return {'hub': list(_hub) if _hub else None, 'ring': _ring_index, 'expand': _expand,
            'focus': list(_focus) if _focus else None}


def set_focus(lat, lng):
    """Expand around a fixed point, e.g. the family's home area."""
    global _hub, _ring_index
    if lat is None or lng is None:
        return {'hub': list(_hub) if _hub else None, 'ring': _ring_index}
    lat, lng = float(lat), float(lng)
    if not -90 <= lat <= 90 or not -180 <= lng <= 180:
        raise ValueError('Invalid focus coordinates')
    if abs(lat) < 1e-6 and abs(lng) < 1e-6:
        raise ValueError('Invalid focus coordinates')
    _hub, _ring_index = (lat, lng), 0
    _save_state()
    _start()
    return {'hub': [lat, lng], 'ring': _ring_index}


def _ring_cells(ring: int) -> list[tuple[int, int]]:
    """Cells on the Chebyshev ring at ``ring`` around the remembered area."""
    if _hub is None:
        return []
    north, east = _key(_hub[0], _hub[1])
    if ring <= 0:
        return [(north, east)]
    cells = set()
    for offset in range(-ring, ring + 1):
        cells.add((north + offset, east - ring))
        cells.add((north + offset, east + ring))
        cells.add((north - ring, east + offset))
        cells.add((north + ring, east + offset))
    return sorted(cells)


def _enqueue(key: tuple[int, int], priority: int) -> bool:
    if _filename(key).exists():
        return False
    if time.monotonic() - _failed.get(key, 0) < _RETRY_AFTER:
        return False
    with _lock:
        if key in _queued or key in _events:
            return False
        _queued.add(key)
        _events[key] = threading.Event()
        _queue.put((priority, next(_sequence), key))
    return True


def _expansion_step() -> bool:
    """Queue one more ring around the hub; each call reaches further out."""
    global _ring_index
    if _hub is None or not _expand:
        return False
    if _ring_index > (_ring_max or MAX_RING):
        return False
    if _queue.qsize() >= PENDING_TARGET:
        return False
    queued = sum(1 for key in _ring_cells(_ring_index) if _enqueue(key, _ring_index))
    _ring_index += 1
    _save_state()
    return bool(queued)


def _expand_loop():
    while True:
        try:
            # Keep topping the queue up while it has room, then idle politely.
            if _expand and _expansion_step():
                continue
        except Exception:
            logging.exception('OSM expansion step failed')
        time.sleep(EXPAND_PAUSE_S)


def _run():
    while True:
        _, _, key = _queue.get()
        with _lock:
            _queued.discard(key)
        try:
            if not _filename(key).exists():
                if time.monotonic() - _failed.get(key, 0) < _RETRY_AFTER:
                    continue
                _fetch(key)
                from dittobuffer.world import world_map
                world_map.invalidate()
                dropped = world_map.prune_tile_cache()
                logging.info('OSM area downloaded %s%s', key, f' (tile epochs dropped: {dropped})' if dropped else '')
        except urllib.error.HTTPError as error:
            with _lock:
                _failed[key] = time.monotonic()
            if error.code in (429, 509):
                _penalise()
                logging.warning('OSM API quota reached (HTTP %s); pausing map downloads for %.0f minutes',
                                error.code, BLOCKED_PAUSE_S / 60)
            else:
                logging.warning('OSM area %s unavailable: %s', key, error)
        except (OSError, ValueError, urllib.error.URLError) as error:
            with _lock:
                _failed[key] = time.monotonic()
            logging.warning('OSM area %s unavailable: %s', key, error)
        finally:
            event = _events.pop(key, None)
            if event is not None:
                event.set()


def _start():
    global _worker, _expander
    with _lock:
        if _worker is None:
            _load_state()
            _worker = threading.Thread(target=_run, name='osm-map-downloader', daemon=True)
            _worker.start()
        if _expander is None:
            _expander = threading.Thread(target=_expand_loop, name='osm-map-expander', daemon=True)
            _expander.start()


def _ring(key: tuple[int, int], center: tuple[int, int]) -> int:
    return max(abs(key[0] - center[0]), abs(key[1] - center[1]))


def status(settings=None):
    """What the on-the-fly street cache currently holds."""
    settings = settings or {}
    # The admin reads this on every refresh, so switches take effect at once.
    _apply(settings)
    files = list(AREA_DIR.glob('*.osm.gz')) if AREA_DIR.is_dir() else []
    try:
        radius = int(float(settings.get('osm_download_radius_m', DEFAULT_RADIUS_M)))
    except (TypeError, ValueError):
        radius = DEFAULT_RADIUS_M
    try:
        interval = round(max(MIN_INTERVAL_S, min(MAX_INTERVAL_S,
                     float(settings.get('osm_request_interval_s', DEFAULT_INTERVAL_S)))), 2)
    except (TypeError, ValueError):
        interval = DEFAULT_INTERVAL_S
    try:
        ring_max = max(0, int(settings.get('osm_expand_ring_max', 0)))
    except (TypeError, ValueError):
        ring_max = 0
    return {'enabled': settings.get('osm_download') is True,
            'radius_m': max(RADIUS_MIN_M, min(RADIUS_MAX_M, radius)),
            'cells': len(files),
            'bytes': sum(item.stat().st_size for item in files),
            'newest_ms': max((item.stat().st_mtime_ns // 1_000_000 for item in files), default=0),
            'directory': 'data/maps/areas',
            'expand': settings.get('osm_expand', True) is not False,
            'interval_s': interval,
            'ring_max': ring_max,
            'hub': list(_hub) if _hub else None,
            'focus': list(_focus) if _focus else None,
            'ring': _ring_index,
            'queued': _queue.qsize(),
            'pending': len(_events),
            'unavailable': len(_failed),
            'blocked_s': max(0, int(_blocked_until - time.monotonic()))}


def ensure(lat: float, lng: float, config=None, wait: float = 6.0):
    """Queue the cells around a trainer, nearest first, and briefly wait for the centre."""
    if lat is None or lng is None:
        return
    settings = (config or {}).get('world', {})
    if settings.get('osm_download', False) is not True:
        return
    if not -90 <= lat <= 90 or not -180 <= lng <= 180:
        return
    # Some client screens issue a first poll before the GPS fix arrives. That
    # used to fetch Null Island and invalidate the live map tile epoch.
    if abs(lat) < 1e-6 and abs(lng) < 1e-6:
        return
    _apply(settings)
    radius = float(settings.get('osm_download_radius_m', DEFAULT_RADIUS_M))
    _start()
    _remember(lat, lng)
    keys = _needed(lat, lng, radius)
    center = _key(lat, lng)
    for key in keys:
        if _filename(key).exists():
            continue
        if time.monotonic() - _failed.get(key, 0) < _RETRY_AFTER:
            continue
        with _lock:
            if key in _queued or key in _events:
                continue
            _queued.add(key)
            _events[key] = threading.Event()
            _queue.put((_ring(key, center), next(_sequence), key))
    if wait and not _filename(center).exists() and time.monotonic() - _failed.get(center, 0) >= _RETRY_AFTER:
        with _lock:
            event = _events.get(center)
        if event is not None:
            event.wait(max(0.0, wait))
