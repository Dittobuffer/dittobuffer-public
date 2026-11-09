"""Native Route drafts with ownership, observed traces and explicit moderation.

Nothing submitted by a player can publish itself. Draft geometry is checked
against accepted server location samples before entering the moderation queue.
"""
import json,secrets
from dittobuffer.gameplay.game import p,now_ms,distance
from dittobuffer.gameplay.routes import put,project,validate

SCHEMA='''
CREATE TABLE IF NOT EXISTS route_drafts(id TEXT PRIMARY KEY,player TEXT NOT NULL REFERENCES players(id),status TEXT NOT NULL,version INTEGER NOT NULL,proto BLOB NOT NULL,trace TEXT NOT NULL,created_ms INTEGER NOT NULL,updated_ms INTEGER NOT NULL,paused INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS route_one_draft ON route_drafts(player) WHERE status='DRAFT';
CREATE TABLE IF NOT EXISTS route_reports(player TEXT NOT NULL REFERENCES players(id),route TEXT NOT NULL REFERENCES routes(id),body BLOB NOT NULL,created_ms INTEGER NOT NULL,PRIMARY KEY(player,route));
'''

def eligible(game,db,player):
    from dittobuffer.gameplay.progression import bounds
    return game.routes['enabled'] and bounds(game.config,game.player(db,player)['xp'])[0]>=game.routes['creator_level']

def creation(row):
    return p.RouteCreationProto(created_time=row['created_ms'],last_update_time=row['updated_ms'],paused=bool(row['paused']),status={'DRAFT':1,'PENDING':4,'REJECTED':3,'PUBLISHED':2,'ARCHIVED':2}[row['status']],route=p.SharedRouteProto.FromString(row['proto']))

def inventory(game,db,player):
    out=p.RouteCreationsProto(not_eligible=not eligible(game,db,player))
    if not game.routes['enabled']:return out
    for row in db.execute('SELECT * FROM route_drafts WHERE player=? ORDER BY updated_ms DESC LIMIT 100',(player,)):
        getattr(out,'route' if row['status']=='DRAFT' else 'recently_submitted_route').add().CopyFrom(creation(row))
    return out

def observed(game,db,player,lat,lng):
    row=db.execute("SELECT * FROM route_drafts WHERE player=? AND status='DRAFT'",(player,)).fetchone()
    if not row or row['paused']:return
    trace=json.loads(row['trace'])
    if distance(*trace[-1],lat,lng)<5:return
    if len(trace)>=20000:return
    trace.append([lat,lng]);db.execute('UPDATE route_drafts SET trace=?,updated_ms=? WHERE id=?',(json.dumps(trace),now_ms(),row['id']))

def create(game,db,player,body):
    req=p.CreateRouteDraftProto.FromString(body);out=p.CreateRouteDraftOutProto(result=5)
    if not eligible(game,db,player):return out
    if db.execute("SELECT 1 FROM route_drafts WHERE player=? AND status='DRAFT'",(player,)).fetchone():out.result=3;return out
    fort=db.execute('SELECT * FROM forts WHERE id=?',(req.start_anchor.fort_id,)).fetchone()
    loc=db.execute('SELECT * FROM route_locations WHERE player=?',(player,)).fetchone()
    if not fort or not loc or now_ms()-loc['arrival_ms']>30000 or distance(loc['lat'],loc['lng'],fort['lat'],fort['lng'])>game.routes['start_range_m']:out.result=6;return out
    route=p.SharedRouteProto(id='player-route-'+secrets.token_hex(12),version=1,type=1,reversible=True)
    route.start_poi.anchor.CopyFrom(p.RouteWaypointProto(fort_id=fort['id'],lat_degrees=fort['lat'],lng_degrees=fort['lng']))
    if game.world.path:
        metadata=game.world.details(fort['id'])
        if metadata and metadata.get('url'):route.start_poi.image_url=metadata['url']
    route.creator_info.creator_codename=game.player(db,player)['name']
    route.creator_info.creator_player_id=player;route.creator_info.show_creator_name=False
    stamp=now_ms();db.execute('INSERT INTO route_drafts(id,player,status,version,proto,trace,created_ms,updated_ms) VALUES(?,?,?,?,?,?,?,?)',(route.id,player,'DRAFT',1,route.SerializeToString(),json.dumps([[loc['lat'],loc['lng']]]),stamp,stamp))
    game.touch(db,player);out.result=1;out.updated_route.CopyFrom(creation(db.execute('SELECT * FROM route_drafts WHERE id=?',(route.id,)).fetchone()));return out

def update(game,db,player,body):
    message=p.UpdateRouteDraftProto.FromString(body);req=message.proposed_route_draft;out=p.UpdateRouteDraftOutProto(result=4)
    if not game.routes['enabled']:return out
    row=db.execute('SELECT * FROM route_drafts WHERE id=? AND player=?',(req.id,player)).fetchone()
    if not row or row['status']!='DRAFT':out.result=5;return out
    if req.version!=row['version']:return out
    if len(req.name)>80 or len(req.description)>2000 or len(req.waypoints)>20000 or len(req.tags)>5:out.result=3;return out
    old=p.SharedRouteProto.FromString(row['proto'])
    if req.start_poi.anchor.fort_id!=old.start_poi.anchor.fort_id:out.result=3;return out
    if message.HasField('pause'):
        if not message.pause:
            loc=db.execute('SELECT * FROM route_locations WHERE player=?',(player,)).fetchone()
            if not loc or now_ms()-loc['arrival_ms']>30000 or distance(loc['lat'],loc['lng'],*json.loads(row['trace'])[-1])>game.routes['start_range_m']:out.result=3;return out
        db.execute('UPDATE route_drafts SET paused=? WHERE id=?',(int(message.pause),row['id']))
    route=p.SharedRouteProto();route.CopyFrom(req);route.version=row['version']+1;route.creator_info.CopyFrom(old.creator_info);route.start_poi.CopyFrom(old.start_poi)
    if route.end_poi.anchor.fort_id and not route.end_poi.image_url and game.world.path:
        metadata=game.world.details(route.end_poi.anchor.fort_id)
        if metadata and metadata.get('url'):route.end_poi.image_url=metadata['url']
    # The native recorder submits metadata/paused state without waypoints;
    # its foreground breadcrumb stream and transport fixes carry the path.
    # Project the independently accepted recording, never client-made distance.
    trace=json.loads(row['trace'])
    if not route.waypoints or list(route.waypoints)==list(old.waypoints):
        del route.waypoints[:]
        for lat,lng in trace:route.waypoints.add(lat_degrees=lat,lng_degrees=lng)
    if route.waypoints:
        from dittobuffer.gameplay.route_geometry import lengths
        route.route_distance_meters=round(lengths([(v.lat_degrees,v.lng_degrees) for v in route.waypoints])[-1])
    db.execute('UPDATE route_drafts SET proto=?,version=?,updated_ms=? WHERE id=?',(route.SerializeToString(),route.version,now_ms(),route.id));game.touch(db,player)
    out.result=1;out.updated_route.CopyFrom(creation(db.execute('SELECT * FROM route_drafts WHERE id=?',(route.id,)).fetchone()));return out

def submit(game,db,player,body):
    req=p.SubmitRouteDraftProto.FromString(body);out=p.SubmitRouteDraftOutProto(result=5)
    if not game.routes['enabled']:return out
    row=db.execute('SELECT * FROM route_drafts WHERE id=? AND player=?',(req.route_id,player)).fetchone()
    if not row:return out
    if row['status'] in ('PENDING','PUBLISHED'):out.result=11;out.submitted_route.CopyFrom(creation(row));return out
    if row['status']!='DRAFT':return out
    if row['version']!=req.route_version:out.result=4;return out
    route=p.SharedRouteProto.FromString(row['proto']);points=[(w.lat_degrees,w.lng_degrees) for w in route.waypoints];trace=json.loads(row['trace'])
    try:
        validate(points,game.routes['min_m'],game.routes['max_m'])
        from dittobuffer.gameplay.route_publication import geometry_key
        fingerprint=geometry_key(points)
        if any(geometry_key(json.loads(saved['geometry']))==fingerprint for saved in db.execute("SELECT geometry FROM routes WHERE id<>? AND status IN ('PENDING','PUBLISHED')",(route.id,))):raise ValueError('Duplicate recorded Route geometry')
        if len(trace)<2 or any(project(trace,*point)[0]>game.routes['corridor_m'] for point in points):raise ValueError('Unobserved recording')
        from dittobuffer.gameplay.route_geometry import lengths
        if lengths(trace)[-1]<lengths(points)[-1]*game.routes['distance_fraction']:raise ValueError('Insufficient observed recording')
        # Validate endpoints and text through the same publication validator.
        put(game,db,route,points,dict(source='native-recording',licence='private-user-route',observed_samples=len(trace)),status='PENDING',creator=player)
    except ValueError:out.result=3;return out
    db.execute("UPDATE route_drafts SET status='PENDING',updated_ms=? WHERE id=?",(now_ms(),route.id));game.touch(db,player)
    if game.routes['auto_approve'] and player in game.routes['trusted_creators']:
        moderate(game,db,route.id,'PUBLISHED','configured-trusted-creator','Explicit administrator allowlist; local auto-approval policy')
    # approval_override is deliberately ignored: only an administrator can publish.
    out.result=11;out.submitted_route.CopyFrom(creation(db.execute('SELECT * FROM route_drafts WHERE id=?',(route.id,)).fetchone()));return out

def delete(game,db,player,body):
    req=p.DeleteRouteDraftProto.FromString(body);out=p.DeleteRouteDraftOutProto(result=1)
    if not game.routes['enabled']:return out
    row=db.execute('SELECT * FROM route_drafts WHERE id=? AND player=?',(req.route_id,player)).fetchone()
    if row and row['status']!='DRAFT':out.result=4;return out
    if row:db.execute('DELETE FROM route_drafts WHERE id=?',(row['id'],));game.touch(db,player)
    return out

def get(game,db,player,body):
    req=p.GetRouteDraftProto.FromString(body);out=p.GetRouteDraftOutProto(result=3)
    row=db.execute('SELECT * FROM route_drafts WHERE id=? AND player=?',(req.id,player)).fetchone() if game.routes['enabled'] else None
    if row:out.result=1;out.route.CopyFrom(creation(row))
    return out

def moderate(game,db,ident,status,actor,reason):
    if status not in ('PUBLISHED','REJECTED','ARCHIVED') or not actor.strip() or not reason.strip():raise ValueError('Explicit moderation decision/actor/reason required')
    row=db.execute('SELECT status,proto FROM routes WHERE id=?',(ident,)).fetchone()
    if not row:raise ValueError('Unknown Route')
    route=p.SharedRouteProto.FromString(row['proto']);del route.route_submission_status[:]
    route.route_submission_status.add(status={'PUBLISHED':2,'REJECTED':4,'ARCHIVED':8}[status],submission_status_update_time_ms=now_ms())
    db.execute('UPDATE routes SET status=?,proto=?,updated_ms=? WHERE id=?',(status,route.SerializeToString(),now_ms(),ident))
    db.execute('UPDATE route_drafts SET status=?,updated_ms=? WHERE id=?',(status,now_ms(),ident))
    db.execute('INSERT INTO route_moderation_audit(route,actor,old_status,new_status,reason,created_ms) VALUES(?,?,?,?,?,?)',(ident,actor,row['status'],status,reason,now_ms()))
