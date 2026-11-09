"""Indexed, versioned public geographic content; trainer state stays in its own DB."""
import hashlib
import json
import logging
import math
from pathlib import Path
import sqlite3

from dittobuffer.paths import ROOT
SCHEMA = '''
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS features(id INTEGER PRIMARY KEY,osm TEXT NOT NULL,layer INTEGER NOT NULL,
 kind INTEGER NOT NULL,geometry BLOB NOT NULL,tags TEXT NOT NULL,UNIQUE(osm,layer));
CREATE VIRTUAL TABLE IF NOT EXISTS feature_bounds USING rtree(id,minx,maxx,miny,maxy);
CREATE TABLE IF NOT EXISTS pois(id TEXT PRIMARY KEY,name TEXT NOT NULL,lat REAL NOT NULL,lng REAL NOT NULL,
 category TEXT NOT NULL,score INTEGER NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL,tags TEXT NOT NULL,
 kind INTEGER NOT NULL DEFAULT 1,photo_status TEXT NOT NULL DEFAULT 'PHOTO_PENDING');
CREATE INDEX IF NOT EXISTS poi_location ON pois(lat,lng);
CREATE INDEX IF NOT EXISTS poi_status ON pois(status,score);
'''
CONTROL_SCHEMA = '''
CREATE TABLE IF NOT EXISTS overrides(id TEXT PRIMARY KEY,changes TEXT NOT NULL,reason TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS photos(id TEXT PRIMARY KEY,record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,poi TEXT NOT NULL,action TEXT NOT NULL,payload TEXT NOT NULL,created TEXT NOT NULL);
'''


def project_path(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('World paths must stay in the project')
    return path


class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()


def connect(*args,**kwargs):return sqlite3.connect(*args,factory=ClosingConnection,**kwargs)


def read_db(path):
    db = connect(path.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def snapshot_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require_unpublished(path):
    """Published snapshots stay immutable, including retained rollback versions."""
    manifests = list((ROOT/'data/world/publications').glob('*.json'))
    pointer = ROOT/'data/world/current.json'
    if pointer.exists():manifests.append(pointer)
    for manifest in manifests:
        value = json.loads(manifest.read_text(encoding='utf8'))
        if project_path(value['database']) == path.resolve():
            raise ValueError('Published snapshot is immutable; import a new dated database')


def category(tags):
    if tags.get('historic') in ('monument', 'memorial', 'castle', 'ruins', 'archaeological_site', 'city_gate', 'manor'):
        return 'historic', 95
    if tags.get('tourism') in ('artwork', 'museum', 'gallery'):
        return tags['tourism'], 95
    if tags.get('leisure') in ('playground', 'park', 'recreation_ground', 'sports_centre'):
        return tags['leisure'], 85
    if tags.get('amenity') in ('library', 'community_centre', 'arts_centre', 'place_of_worship', 'theatre'):
        return tags['amenity'], 85
    if tags.get('place') == 'square':
        return 'square', 80
    return None


def decision(tags):
    candidate = category(tags)
    if not candidate:
        return None
    reason = None
    if tags.get('access') in ('private', 'no', 'customers', 'permit', 'members', 'military'):
        reason = 'restricted_access'
    if tags.get('foot') == 'no' or tags.get('motorroad') == 'yes':
        reason = 'no_pedestrian_access'
    if tags.get('amenity') in ('school', 'kindergarten', 'police', 'fire_station', 'hospital') or tags.get('military'):
        reason = 'sensitive_site'
    if tags.get('residential') or tags.get('leisure') == 'garden' or tags.get('building') in ('house', 'apartments', 'residential'):
        reason = 'private_residence'
    name = tags.get('name') or tags.get('name:nl')
    if not name:
        reason = reason or 'unnamed_requires_review'
    if tags.get('leisure')=='sports_centre' and tags.get('access') not in ('yes','permissive') and tags.get('operator:type') not in ('public','government') and tags.get('ownership')!='public':
        reason=reason or 'commercial_sports_access_requires_review'
    if name and 'school' in name.casefold() and tags.get('amenity') in ('arts_centre','community_centre'):
        reason=reason or 'possible_school_requires_review'
    held=('unnamed_requires_review','commercial_sports_access_requires_review','possible_school_requires_review')
    return candidate[0], candidate[1], ('REJECTED' if reason and reason not in held else 'REVIEW'), reason or 'pedestrian_and_spatial_review'


def layers(tags, area=False):
    result = []
    highway = tags.get('highway')
    if not area and highway and highway not in ('proposed', 'construction', 'raceway'):
        kind = 35 if highway in ('footway', 'steps', 'path', 'cycleway', 'bridleway') else 36 if highway == 'pedestrian' else 21 if highway in ('motorway', 'trunk', 'motorway_link', 'trunk_link') else 28 if highway in ('primary', 'secondary', 'tertiary') else 30
        result.append((7, kind))
    if not area and tags.get('waterway') in ('river', 'canal', 'stream', 'ditch', 'drain'):
        result.append((9, {'river':47,'canal':2,'stream':53,'ditch':9,'drain':11}[tags['waterway']]))
    if not area and tags.get('natural') == 'coastline':
        result.append((9, 83))
    if area:
        if tags.get('building') and tags['building'] != 'no':
            result.append((2, 63))
        if tags.get('natural') == 'water' or tags.get('waterway') == 'riverbank' or tags.get('landuse') in ('reservoir', 'basin'):
            result.append((9, {'river':47,'canal':2,'lake':25}.get(tags.get('water'),58)))
        kind = {'park':33,'garden':17,'playground':40,'recreation_ground':43,'pitch':37,'nature_reserve':31,'golf_course':19}.get(tags.get('leisure'))
        kind = kind or {'wood':60,'grassland':20,'scrub':82,'wetland':59,'sand':81,'beach':81}.get(tags.get('natural'))
        kind = kind or {'forest':16,'grass':20,'meadow':29,'farmland':13,'farmyard':12,'residential':45,'commercial':6,'retail':46,'industrial':24,'recreation_ground':43,'village_green':20,'orchard':103}.get(tags.get('landuse'))
        if kind:
            result.append((4, kind))
        if tags.get('amenity') in ('school','kindergarten','hospital','police','fire_station') or tags.get('military') or tags.get('access') in ('private','no','military'):
            result.append((-1, 50))
        if tags.get('boundary') == 'administrative' and tags.get('admin_level') == '4' and tags.get('ISO3166-2','').startswith('NL-'):
            result.append((-2, 65))
    return result


class WorldStore:
    def __init__(self, config=None):
        cfg = config or {}
        pointer = project_path(cfg.get('manifest', 'data/world/current.json'))
        self.control = project_path(cfg.get('control', 'data/world/control.sqlite3'))
        # The published snapshot is optional. A fresh checkout without the
        # multi-gigabyte database still serves OSM map tiles and lets the
        # background downloader fill the area; forts, stops and photos simply
        # stay empty until the snapshot is restored.
        self.manifest = None
        self.path = None
        if cfg.get('enabled', False):
            if pointer.is_file():
                manifest = json.loads(pointer.read_text(encoding='utf-8'))
                candidate = project_path(manifest['database'])
                if candidate.is_file():
                    if manifest.get('published') and snapshot_sha(candidate) != manifest['sha256']:
                        raise ValueError('Published world snapshot checksum mismatch')
                    self.manifest, self.path = manifest, candidate
                else:
                    logging.getLogger(__name__).warning(
                        'World snapshot %s is missing; starting with an empty world. Map tiles and the OSM downloader still work.',
                        candidate,
                    )
            else:
                logging.getLogger(__name__).warning(
                    'World manifest %s is missing; starting with an empty world. Map tiles and the OSM downloader still work.',
                    pointer,
                )
        self.epoch = self.manifest['epoch'] if self.manifest else None
        self.palette={}
        if self.path and cfg.get('palette'):
            value=json.loads(project_path(cfg['palette']).read_text(encoding='utf8'))
            allowed={2:{63},4:{6,12,13,16,17,19,20,24,29,31,33,37,40,43,45,46,59,60,81,82,103},7:{21,28,30,35,36},9:{2,9,11,25,32,47,53,58,83}}
            for key,kind in value.get('kinds',{}).items():
                layer,original=map(int,key.split(':'))
                if layer not in allowed or original not in allowed[layer] or type(kind) is not int or kind not in allowed[layer]:raise ValueError('Palette must select native kinds within the same semantic layer')
                if original!=kind:self.palette[layer,original]=kind
            if self.palette and self.epoch is not None:
                digest=hashlib.sha256(json.dumps(sorted((a,b,c) for (a,b),c in self.palette.items())).encode()).digest()
                self.epoch=1+(self.epoch+int.from_bytes(digest[:4],'big'))%2147483646

    def render_kind(self,layer,kind):return self.palette.get((layer,kind),kind)

    def features(self, bounds, selected=None, limit=20000):
        if not self.path:
            return []
        minx, miny, maxx, maxy = bounds
        with read_db(self.path) as db:
            sql = 'SELECT f.* FROM feature_bounds r JOIN features f ON f.id=r.id WHERE r.maxx>=? AND r.minx<=? AND r.maxy>=? AND r.miny<=?'
            args = [minx,maxx,miny,maxy]
            if selected:
                sql += ' AND f.layer IN (' + ','.join('?' for _ in selected) + ')'
                args.extend(selected)
            rows = db.execute(sql+' LIMIT ?',args+[limit+1]).fetchall()
            if len(rows)>limit:
                raise ValueError('World query exceeds bounded feature budget')
            return rows

    def pois(self, lat, lng, radius=1500):
        # The published snapshot is optional; the admin overlay (hand-placed
        # and OSM-imported POIs) must still produce forts on a fresh install.
        dy=radius/111320; dx=dy/max(.1,math.cos(math.radians(lat)))
        rows = []
        if self.path:
            with read_db(self.path) as db:
                rows = [dict(r) for r in db.execute("SELECT * FROM pois WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ? ORDER BY score DESC,id LIMIT 1000",(lat-dy,lat+dy,lng-dx,lng+dx))]
            if self.control.exists():
                with read_db(self.control) as db:
                    for row in rows:
                        override=db.execute('SELECT changes FROM overrides WHERE id=?',(row['id'],)).fetchone()
                        if override:row.update(json.loads(override['changes']))
                    present={row['id'] for row in rows}
                    for override in db.execute('SELECT id,changes FROM overrides'):
                        change=json.loads(override['changes'])
                        if override['id'] not in present and ('lat' in change or 'lng' in change):
                            moved=self.poi(override['id'])
                            if moved and lat-dy<=moved['lat']<=lat+dy and lng-dx<=moved['lng']<=lng+dx:rows.append(moved)
        from dittobuffer.world import world_admin
        seen={r['id'] for r in rows}
        rows.extend(r for r in world_admin.near(lat,lng,radius) if r['id'] not in seen)
        return [r for r in rows if r['status']=='APPROVED' and (r['lat']-lat)**2+((r['lng']-lng)*math.cos(math.radians(lat)))**2 <= (radius/111320)**2]

    def poi(self, identity):
        from dittobuffer.world import world_admin
        local=world_admin.by_id(identity)
        if local is not None:return local
        if not self.path or not identity.startswith('osm:'):
            return None
        with read_db(self.path) as db:
            row=db.execute('SELECT * FROM pois WHERE id=?',(identity,)).fetchone()
        if not row:return None
        result=dict(row)
        if self.control.exists():
            with read_db(self.control) as db:
                override=db.execute('SELECT changes FROM overrides WHERE id=?',(identity,)).fetchone()
                if override:result.update(json.loads(override['changes']))
        return result

    def photo(self, identity):
        if not self.control.exists():return None
        with read_db(self.control) as db:
            row=db.execute('SELECT record FROM photos WHERE id=?',(identity,)).fetchone()
        record=json.loads(row['record']) if row else None
        return record if record and record['status']=='VERIFIED' else None

    def photo_by_hash(self, digest):
        if not self.control.exists():return None
        with read_db(self.control) as db:
            row=db.execute("SELECT record FROM photos WHERE json_extract(record,'$.sha256')=? AND json_extract(record,'$.status')='VERIFIED' LIMIT 1",(digest,)).fetchone()
        return json.loads(row['record']) if row else None

    def details(self, identity):
        row=self.poi(identity)
        if not row:return None
        photo=self.photo(identity)
        description='OSM '+row['category']+'. Community world location; not an official Wayspot.'
        if photo:
            url='http://127.0.0.1:8080/world/photos/'+photo['sha256']+'.jpg'
            description+=' Photo: '+photo['attribution']+' '+photo['license_url']+' Source: '+photo['source_url']
        else:
            from dittobuffer.world.world_images import placeholder
            url=placeholder()['url']
            description+=' Real photograph pending (PHOTO_PENDING); supplied placeholder shown.'
        return dict(name=row['name'],description=description,url=url,photo=photo,status=row['status'])

    def materialize(self, db, lat, lng):
        for row in self.pois(lat,lng):
            old=db.execute('SELECT * FROM forts WHERE id=?',(row['id'],)).fetchone()
            if old is None:
                db.execute('INSERT INTO forts VALUES(?,?,?,?,?)',(row['id'],row['kind'],row['name'],row['lat'],row['lng']))
            elif old['kind']==row['kind']:
                # No deletes, and no kind conversion underneath attached defenders/history.
                db.execute('UPDATE forts SET name=?,lat=?,lng=? WHERE id=?',(row['name'],row['lat'],row['lng'],row['id']))
            elif row['id'].startswith('admin:'):
                # Administrator placements own no historical attachments yet; keep
                # kind edits reversible while the overlay row exists.
                attached=db.execute('SELECT 1 FROM gym_defenders WHERE fort_id=? LIMIT 1',(row['id'],)).fetchone()
                active=db.execute('SELECT 1 FROM gym_battles WHERE fort_id=? AND settled=0 LIMIT 1',(row['id'],)).fetchone()
                if not attached and not active:
                    db.execute('UPDATE forts SET kind=?,name=?,lat=?,lng=? WHERE id=?',(row['kind'],row['name'],row['lat'],row['lng'],row['id']))
            else:
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'gym_defenders','gym_battles','raids','raid_battles'}<=tables:continue
                attached=db.execute('SELECT 1 FROM gym_defenders WHERE fort_id=? LIMIT 1',(row['id'],)).fetchone()
                active=db.execute('SELECT 1 FROM gym_battles WHERE fort_id=? AND settled=0 LIMIT 1',(row['id'],)).fetchone()
                if attached or active:continue
                from dittobuffer.gameplay.game import now_ms
                if db.execute('SELECT 1 FROM raids WHERE fort_id=? AND end_ms>? LIMIT 1',(row['id'],now_ms())).fetchone():continue
                db.execute('UPDATE forts SET kind=?,name=?,lat=?,lng=? WHERE id=?',(row['kind'],row['name'],row['lat'],row['lng'],row['id']))

    def reconcile_retired(self,game,db,player):
        """Only an explicit administrator retirement returns a defender.

        Source tag changes, missing OSM objects and review queues never delete
        defender/ownership history. Returns reuse the existing durable receipt.
        """
        if not self.path or not self.control.exists():return
        rows=db.execute("SELECT * FROM gym_defenders WHERE owner_id=? AND fort_id LIKE 'osm:%'",(player,)).fetchall()
        with read_db(self.control) as control:
            for row in rows:
                override=control.execute('SELECT changes FROM overrides WHERE id=?',(row['fort_id'],)).fetchone()
                if override and json.loads(override['changes']).get('status')=='RETIRED':
                    from dittobuffer.gameplay.gyms import return_defender
                    return_defender(game,db,row,preserve_health=True)

    def habitat(self, lat, lng):
        if not self.path:return 'urban'
        from shapely import from_wkb
        from shapely.geometry import Point
        point=Point(lng,lat)
        for row in self.features((lng-.0004,lat-.0004,lng+.0004,lat+.0004),(9,4),limit=300):
            shape=from_wkb(row['geometry'])
            if row['layer']==9 and shape.distance(point)<.0003:return 'water'
            if shape.covers(point):
                if row['kind'] in (16,60):return 'forest'
                if row['kind'] in (17,20,29,31,33,40,43):return 'park'
        return 'urban'

    def spawn_point(self, lat, lng, rng):
        """Anchor a geographic cell to mapped walkable ways, rather than water/private land."""
        from shapely import from_wkb
        from shapely.geometry import Point
        bounds=(lng-.0007,lat-.0005,lng+.0007,lat+.0005)
        roads=[]
        rows=self.features(bounds,(7,-1,9),limit=1000)
        for row in rows:
            if row['layer']!=7:continue
            tags=json.loads(row['tags'])
            if tags.get('highway') in ('motorway','trunk','motorway_link','trunk_link','construction') or tags.get('foot')=='no' or tags.get('access') in ('private','no','military'):continue
            shape=from_wkb(row['geometry'])
            from shapely.geometry import box
            shape=shape.intersection(box(*bounds))
            if shape.geom_type=='LineString' and not shape.is_empty:roads.append(shape)
        rng.shuffle(roads)
        for road in roads:
            point=road.interpolate(rng.random(),normalized=True)
            if any(row['layer'] in (-1,9) and from_wkb(row['geometry']).geom_type in ('Polygon','MultiPolygon') and from_wkb(row['geometry']).covers(point) for row in rows):continue
            return point.y,point.x
        return None
