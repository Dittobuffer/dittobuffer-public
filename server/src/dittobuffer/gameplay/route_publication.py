"""Reviewed generation batches with immutable input identities and safe rollback."""
import hashlib,json,sqlite3
from contextlib import closing
from pathlib import Path
from dittobuffer.gameplay.game import p,now_ms,distance
from dittobuffer.gameplay.route_geometry import validate

SCHEMA='''
CREATE TABLE IF NOT EXISTS route_generation_batches(id TEXT PRIMARY KEY,manifest TEXT NOT NULL,created_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS route_generation_members(batch TEXT NOT NULL REFERENCES route_generation_batches(id),route TEXT NOT NULL REFERENCES routes(id),staged_proto_sha TEXT NOT NULL,decision TEXT NOT NULL DEFAULT 'PENDING',PRIMARY KEY(batch,route));
'''

def identity(path):
    path=Path(path).resolve();before=path.stat()
    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Source changed while hashing')
    return dict(path=str(path),bytes=before.st_size,sha256=digest)

def geometry_key(points):
    values=[(round(lat,6),round(lng,6)) for lat,lng in points]
    forward=json.dumps(values,separators=(',',':'));reverse=json.dumps(values[::-1],separators=(',',':'))
    return hashlib.sha256(min(forward,reverse).encode()).hexdigest()

def verify(graph,world,candidate,min_m,max_m):
    points=candidate['points'];nodes=candidate['nodes'];validate(points,min_m,max_m)
    if len(nodes)!=len(points) or len(nodes)>20000 or len(set(nodes))<2:raise ValueError('Invalid generated node sequence')
    for node,point in zip(nodes,points):
        value=graph.db.execute('SELECT lat,lng FROM nodes WHERE id=?',(node,)).fetchone()
        if not value or distance(*value,*point)>.1:raise ValueError('Geometry does not match graph node')
    for a,b in zip(nodes,nodes[1:]):
        if not graph.db.execute('SELECT 1 FROM edges WHERE src=? AND dst=?',(a,b)).fetchone():raise ValueError('Disconnected or forbidden pedestrian edge')
    if candidate.get('reversible'):
        for a,b in zip(nodes,nodes[1:]):
            if not graph.db.execute('SELECT 1 FROM edges WHERE src=? AND dst=?',(b,a)).fetchone():raise ValueError('Invalid reversible path')
    for key,point in [('start',points[0]),('end',points[-1])]:
        endpoint=candidate[key];actual=world.execute("SELECT lat,lng FROM pois WHERE id=? AND status='APPROVED'",(endpoint['id'],)).fetchone()
        if not actual or distance(*actual,endpoint['lat'],endpoint['lng'])>.1 or distance(*point,*actual)>30:raise ValueError('Unapproved or disconnected endpoint')
    if candidate.get('provenance',{}).get('source')!='osm-pedestrian-graph-v1':raise ValueError('Unknown graph provenance')
    return geometry_key(points)

def stage(game,db,candidates,graph,world_path,manifest):
    if len(candidates)>500:raise ValueError('Maximum 500 candidates per reviewed batch')
    manifest={**manifest,'selection_sha256':hashlib.sha256(json.dumps(candidates,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
    key=hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:24]
    ident='route-batch-'+key;old=db.execute('SELECT manifest FROM route_generation_batches WHERE id=?',(ident,)).fetchone()
    if old:return ident # Immutable batch retries do not reset decisions.
    seen={geometry_key(json.loads(r['geometry'])) for r in db.execute('SELECT geometry FROM routes')}
    selected=[];ids=set()
    with closing(sqlite3.connect(Path(world_path).resolve().as_uri()+'?mode=ro',uri=True)) as world:
        for candidate in candidates:
            geom=verify(graph,world,candidate,game.routes['min_m'],game.routes['max_m'])
            if candidate['id'] in ids:raise ValueError('Duplicate Route ID in batch')
            ids.add(candidate['id'])
            if db.execute('SELECT 1 FROM routes WHERE id=?',(candidate['id'],)).fetchone() or geom in seen:continue
            seen.add(geom);selected.append(candidate)
    db.execute('INSERT INTO route_generation_batches VALUES(?,?,?)',(ident,json.dumps(manifest,sort_keys=True),now_ms()))
    from dittobuffer.gameplay.routes import put
    for candidate in selected:
        route=p.SharedRouteProto(id=candidate['id'],name=candidate['name'][:80],description='Mapped pedestrian route. OpenStreetMap contributors, ODbL 1.0. Local source review; not a physical safety survey.',reversible=candidate['reversible'])
        for field,key in [('start_poi','start'),('end_poi','end')]:
            site=candidate[key];game.world.materialize(db,site['lat'],site['lng']);getattr(route,field).anchor.CopyFrom(p.RouteWaypointProto(fort_id=site['id'],lat_degrees=site['lat'],lng_degrees=site['lng']))
        put(game,db,route,candidate['points'],{**candidate['provenance'],'generation_batch':ident},status='PENDING')
        db.execute('INSERT INTO route_generation_members(batch,route,staged_proto_sha) VALUES(?,?,?)',(ident,route.id,hashlib.sha256(route.SerializeToString()).hexdigest()))
    return ident

def decide(game,db,batch,decisions,actor,reason):
    if not actor.strip() or not reason.strip() or not decisions:raise ValueError('Explicit review actor/reason/decisions required')
    from dittobuffer.gameplay.route_creator import moderate
    for ident,status in decisions.items():
        row=db.execute('SELECT m.*,r.proto,r.creator,r.provenance,r.status FROM route_generation_members m JOIN routes r ON r.id=m.route WHERE m.batch=? AND m.route=?',(batch,ident)).fetchone()
        if not row or row['creator'] or json.loads(row['provenance']).get('generation_batch')!=batch:raise ValueError('Not an owned generated batch Route')
        if status not in ('PUBLISHED','REJECTED'):raise ValueError('Invalid review decision')
        if row['decision']==status and row['status']==status:continue
        if row['decision']!='PENDING' or row['status']!='PENDING' or hashlib.sha256(row['proto']).hexdigest()!=row['staged_proto_sha']:raise ValueError('Route changed after staging; review a fresh batch')
        moderate(game,db,ident,status,actor,reason)
        db.execute('UPDATE route_generation_members SET decision=? WHERE batch=? AND route=?',(status,batch,ident))

def rollback(game,db,batch,actor,reason):
    from dittobuffer.gameplay.route_creator import moderate
    if not actor.strip() or not reason.strip():raise ValueError('Rollback audit reason required')
    rows=db.execute('SELECT m.route,r.creator,r.provenance,r.status FROM route_generation_members m JOIN routes r ON r.id=m.route WHERE m.batch=?',(batch,)).fetchall()
    if not db.execute('SELECT 1 FROM route_generation_batches WHERE id=?',(batch,)).fetchone():raise ValueError('Unknown batch')
    for row in rows:
        if row['creator'] or json.loads(row['provenance']).get('generation_batch')!=batch:raise ValueError('Refusing to archive an unrelated/manual Route')
        if row['status']!='ARCHIVED':moderate(game,db,row['route'],'ARCHIVED',actor,reason)
        db.execute("UPDATE route_generation_members SET decision='ROLLED_BACK' WHERE batch=? AND route=?",(batch,row['route']))
    # Retain geometry, history, badges and active versioned snapshots. No delete.
