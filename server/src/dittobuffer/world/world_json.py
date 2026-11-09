"""JSON-backed development world.

Replaces the SQLite world snapshot for local/private play.  The world is a
single JSON document so gyms, stops and the dev home location can be edited by
hand; persistent trainer state (players, inventory, raids) intentionally stays
in the server database, where it is transactional.

Document shape (``data/world/dev-world.json``)::

    {
      "epoch": 1,
      "pois": [{"id": "dev:gym-1", "name": "...", "lat": 60.27, "lng": 25.02,
                "kind": 0, "status": "APPROVED"}],
      "home_gym": {"id": "dev:home", "name": "Kotikuntosali"}
    }

``home_gym`` follows the player's reported position so a gym (and its raid)
is always inside interaction range, which is what makes the setup work in any
city without importing an OSM extract.  Map tiles are empty unless the JSON
document provides ``features`` (each with a WKB hex geometry), which this dev
world does not.
"""
import json
import math
from pathlib import Path

from dittobuffer.paths import ROOT


def project_path(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('World paths must stay in the project')
    return path


def _poi(entry):
    row = dict(entry)
    row.setdefault('status', 'APPROVED')
    row.setdefault('kind', 0)
    row.setdefault('score', 100)
    row.setdefault('reason', 'development fixture')
    row.setdefault('category', 'gym' if row['kind'] == 0 else 'stop')
    row.setdefault('tags', '{}')
    row.setdefault('photo_status', 'PHOTO_PENDING')
    if not isinstance(row.get('id'), str) or not row['id']:
        raise ValueError('World POI requires an id')
    if not isinstance(row.get('name'), str) or not row['name']:
        raise ValueError('World POI requires a name')
    if row['kind'] not in (0, 1):
        raise ValueError('World POI kind must be 0 (gym) or 1 (stop)')
    if not isinstance(row.get('lat'), (int, float)) or not -90 <= row['lat'] <= 90:
        raise ValueError('Invalid world POI latitude')
    if not isinstance(row.get('lng'), (int, float)) or not -180 <= row['lng'] <= 180:
        raise ValueError('Invalid world POI longitude')
    return row


class JsonWorld:
    def __init__(self, config=None):
        cfg = config or {}
        self.path = None
        self.manifest = None
        self.epoch = None
        self.palette = {}
        self._last = None
        self.pois_list = []
        self.home = None
        if not cfg.get('enabled', False):
            return
        source = project_path(cfg.get('json', 'data/world/dev-world.json'))
        if not source.is_file():
            raise ValueError('Enabled JSON world requires a world document')
        self.path = source
        document = json.loads(source.read_text(encoding='utf8'))
        self.document = document
        self.epoch = int(document.get('epoch', 1))
        self.manifest = {'database': source.relative_to(ROOT).as_posix(),
                         'epoch': self.epoch, 'json': True}
        self.pois_list = [_poi(entry) for entry in document.get('pois', [])]
        home = document.get('home_gym')
        self.home = _poi(dict(home, id=home.get('id', 'dev:home'), lat=0, lng=0,
                              kind=0)) if home else None

    # ---------------------------------------------------------------- queries
    def render_kind(self, layer, kind):
        return kind

    def features(self, bounds, selected=None, limit=20000):
        return []

    def pois(self, lat, lng, radius=1500):
        rows = []
        dy = radius / 111320
        dx = dy / max(.1, math.cos(math.radians(lat)))
        for row in self.pois_list:
            if abs(row['lat'] - lat) <= dy and abs(row['lng'] - lng) <= dx:
                if row['status'] == 'APPROVED':
                    rows.append(dict(row))
        if self.home:
            rows.append(dict(self.home, lat=round(lat, 7), lng=round(lng, 7)))
        from dittobuffer.world import world_admin
        seen={row['id'] for row in rows}
        rows.extend(row for row in world_admin.near(lat,lng,radius) if row['id'] not in seen)
        return rows

    def poi(self, identity):
        from dittobuffer.world import world_admin
        local=world_admin.by_id(identity)
        if local is not None:return local
        for row in self.pois_list:
            if row['id'] == identity:
                return dict(row)
        if self.home and identity == self.home['id'] and self._last:
            return dict(self.home, lat=self._last[0], lng=self._last[1])
        return None

    def photo(self, identity):
        return None

    def photo_by_hash(self, digest):
        return None

    def details(self, identity):
        row = self.poi(identity)
        if not row:
            return None
        from dittobuffer.world.world_images import placeholder
        return dict(name=row['name'],
                    description='JSON development world location; not an official Wayspot.',
                    url=placeholder()['url'], photo=None, status=row['status'])

    def materialize(self, db, lat, lng):
        self._last = (lat, lng)
        rows = self.pois(lat, lng)
        for row in rows:
            old = db.execute('SELECT * FROM forts WHERE id=?', (row['id'],)).fetchone()
            if old is None:
                db.execute('INSERT INTO forts VALUES(?,?,?,?,?)',
                           (row['id'], row['kind'], row['name'], row['lat'], row['lng']))
            else:
                db.execute('UPDATE forts SET kind=?,name=?,lat=?,lng=? WHERE id=?',
                           (row['kind'], row['name'], row['lat'], row['lng'], row['id']))
        return rows

    def reconcile_retired(self, game, db, player):
        return None

    def habitat(self, lat, lng):
        return 'urban'

    def spawn_point(self, lat, lng, rng):
        return lat + (rng.random() - .5) * .0004, lng + (rng.random() - .5) * .0004
