"""Opt-in native Routes, authoritative movement and transactional reward receipts.

Wire field numbers come from the supplied 0.429.2 schema. Local balance/tolerance
defaults below are custom policy, not claims about undocumented official values.
"""
import hashlib,json,math,secrets,sqlite3,time
from contextlib import closing
from s2sphere import CellId,LatLng
from dittobuffer.gameplay.game import ROOT,p,now_ms,distance
from dittobuffer.gameplay.route_geometry import lengths,project,validate

DEFAULTS=dict(enabled=False,min_level=7,creator_level=30,min_m=100,max_m=6000,
    discovery_m=3000,page_size=40,start_range_m=35,end_range_m=35,corridor_m=35,
    max_speed_mps=3.5,max_accuracy_m=25,progress_fraction=.95,distance_fraction=.90,
    cooldown_seconds=300,expiration_seconds=86400,xp=1000,stardust=500,items={'1':3},
    badge_thresholds=[5,20,100],allow_debug_fixture=False,auto_approve=False,trusted_creators=[])
SCHEMA='''
CREATE TABLE IF NOT EXISTS routes(id TEXT PRIMARY KEY,creator TEXT REFERENCES players(id),status TEXT NOT NULL,version INTEGER NOT NULL,proto BLOB NOT NULL,geometry TEXT NOT NULL,length_m REAL NOT NULL,provenance TEXT NOT NULL,created_ms INTEGER NOT NULL,updated_ms INTEGER NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS route_geo USING rtree(id,min_lat,max_lat,min_lng,max_lng);
CREATE INDEX IF NOT EXISTS route_status ON routes(status,created_ms,id);
CREATE TABLE IF NOT EXISTS route_locations(player TEXT PRIMARY KEY REFERENCES players(id),lat REAL NOT NULL,lng REAL NOT NULL,accuracy REAL NOT NULL,fix_ms INTEGER NOT NULL,arrival_ms INTEGER NOT NULL,source TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS route_attempts(id TEXT PRIMARY KEY,player TEXT NOT NULL REFERENCES players(id),route TEXT NOT NULL REFERENCES routes(id),version INTEGER NOT NULL,snapshot BLOB NOT NULL,state TEXT NOT NULL,reversed INTEGER NOT NULL,start_ms INTEGER NOT NULL,expires_ms INTEGER NOT NULL,along_m REAL NOT NULL DEFAULT 0,walked_m REAL NOT NULL DEFAULT 0,last_index INTEGER NOT NULL DEFAULT 0,receipt BLOB,finished_ms INTEGER);
CREATE UNIQUE INDEX IF NOT EXISTS route_one_active ON route_attempts(player) WHERE state IN ('ACTIVE','PAUSED','ELIGIBLE');
CREATE TABLE IF NOT EXISTS route_samples(attempt TEXT NOT NULL REFERENCES route_attempts(id),fix_ms INTEGER NOT NULL,lat REAL NOT NULL,lng REAL NOT NULL,along_m REAL NOT NULL,PRIMARY KEY(attempt,fix_ms));
CREATE TABLE IF NOT EXISTS route_badges(player TEXT NOT NULL REFERENCES players(id),route TEXT NOT NULL REFERENCES routes(id),completions INTEGER NOT NULL,first_ms INTEGER NOT NULL,last_ms INTEGER NOT NULL,PRIMARY KEY(player,route));
CREATE TABLE IF NOT EXISTS route_ratings(player TEXT NOT NULL REFERENCES players(id),route TEXT NOT NULL REFERENCES routes(id),rating INTEGER NOT NULL,favorite INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(player,route));
CREATE TABLE IF NOT EXISTS route_moderation_audit(id INTEGER PRIMARY KEY,route TEXT NOT NULL REFERENCES routes(id),actor TEXT NOT NULL,old_status TEXT,new_status TEXT NOT NULL,reason TEXT NOT NULL,created_ms INTEGER NOT NULL);
'''

def install(game):
    game.routes={**DEFAULTS,**game.config.get('routes',{})}
    cfg=game.routes
    for key in ('min_m','max_m','discovery_m','start_range_m','end_range_m','corridor_m','max_speed_mps','max_accuracy_m','expiration_seconds'):
        if type(cfg[key]) not in (float,int) or not math.isfinite(cfg[key]) or cfg[key]<=0:raise ValueError('Invalid Routes rule: '+key)
    if not 1<=cfg['page_size']<=100 or not 0<cfg['progress_fraction']<=1 or not 0<cfg['distance_fraction']<=1:raise ValueError('Invalid Routes bounds')
    if cfg['min_m']>cfg['max_m'] or cfg['xp']<0 or cfg['stardust']<0:raise ValueError('Invalid Routes rewards/distance')
    if not isinstance(cfg['badge_thresholds'],list) or len(cfg['badge_thresholds'])!=3 or any(type(v) is not int or not 1<=v<=1000000 for v in cfg['badge_thresholds']) or sorted(set(cfg['badge_thresholds']))!=cfg['badge_thresholds']:raise ValueError('Three increasing earned Route badge thresholds required')
    if type(cfg['auto_approve']) is not bool or not isinstance(cfg['trusted_creators'],list) or any(not isinstance(v,str) or not v.strip() for v in cfg['trusted_creators']):raise ValueError('Explicit trusted Route creator IDs required')
    for item,count in cfg['items'].items():
        if int(item) not in p.Item.values() or type(count) is not int or count<0:raise ValueError('Invalid Routes item reward')
    if cfg['allow_debug_fixture'] and (not game.config.get('profile','').startswith('m20-') or not game.db.path.resolve().is_relative_to(ROOT/'builds')):raise ValueError('Routes debug fixes require an isolated M20 builds database')
    if not cfg['enabled']:return
    from dittobuffer.gameplay.route_creator import SCHEMA as creator_schema
    from dittobuffer.gameplay.route_cells import SCHEMA as cell_schema,settings as cell_settings
    from dittobuffer.gameplay.route_mateo import SCHEMA as mateo_schema,settings as mateo_settings
    from dittobuffer.gameplay.route_rewards import SCHEMA as reward_schema,settings as reward_settings
    from dittobuffer.gameplay.route_publication import SCHEMA as publication_schema
    from dittobuffer.gameplay.exploration_events import SCHEMA as event_schema,enabled as event_enabled
    event_enabled(game)
    cell_settings(game)
    mateo_settings(game)
    reward_settings(game)
    with closing(game.db.connect()) as db:
        db.executescript(SCHEMA+creator_schema+cell_schema+mateo_schema+reward_schema+publication_schema+event_schema)
        # Additive migration: preserve already earned days without re-awarding
        # historical bonuses. Subsequent completions carry exact streaks.
        from dittobuffer.gameplay.route_rewards import settings as reward_cfg
        offset=reward_cfg(game).get('day_offset_minutes',0)*60000
        db.execute("INSERT OR IGNORE INTO route_completion_days SELECT player,(finished_ms+?)/86400000,MIN(id),1 FROM route_attempts WHERE state='COMPLETED' GROUP BY player,(finished_ms+?)/86400000",(offset,offset));db.commit()
        if 'paused' not in {r[1] for r in db.execute('PRAGMA table_info(route_drafts)')}:db.execute('ALTER TABLE route_drafts ADD COLUMN paused INTEGER NOT NULL DEFAULT 0');db.commit()

def put(game,db,route,points,provenance,status='PUBLISHED',creator=None):
    total=validate(points,game.routes['min_m'],game.routes['max_m'])
    if not route.id or not route.name.strip() or len(route.name)>80 or len(route.description)>2000:raise ValueError('Invalid Routes identity/text')
    for anchor in (route.start_poi.anchor,route.end_poi.anchor):
        fort=db.execute('SELECT lat,lng FROM forts WHERE id=?',(anchor.fort_id,)).fetchone()
        if not fort or distance(fort['lat'],fort['lng'],anchor.lat_degrees,anchor.lng_degrees)>10:raise ValueError('Missing or inconsistent Route endpoint')
    # Native badge creation offers the actual endpoint images. Omitted
    # RoutePoiAnchor.image_url leaves a selectable blank square in 0.429.2.
    for poi in (route.start_poi,route.end_poi):
        if not poi.image_url and game.world.path:
            metadata=game.world.details(poi.anchor.fort_id)
            if metadata and metadata.get('url'):poi.image_url=metadata['url']
    if distance(*points[0],route.start_poi.anchor.lat_degrees,route.start_poi.anchor.lng_degrees)>game.routes['start_range_m'] or distance(*points[-1],route.end_poi.anchor.lat_degrees,route.end_poi.anchor.lng_degrees)>game.routes['end_range_m']:raise ValueError('Geometry misses anchor')
    route.route_distance_meters=round(total);route.route_duration_seconds=round(total/1.25);route.version=max(1,route.version)
    route.type=1;route.path_type=2 if route.start_poi.anchor.fort_id==route.end_poi.anchor.fort_id else 1
    route.aggregated_stats.SetInParent();route.player_stats.SetInParent()
    if not creator:
        route.creator_info.creator_player_id='librenlp-route-generator'
        route.creator_info.creator_codename='LibreNLP Routes';route.creator_info.show_creator_name=False
    del route.route_submission_status[:]
    route.route_submission_status.add(status={'PUBLISHED':2,'PENDING':1,'ARCHIVED':8,'REJECTED':4}.get(status,0),submission_status_update_time_ms=now_ms())
    del route.s2_ground_cells[:]
    route.s2_ground_cells.extend(sorted({CellId.from_lat_lng(LatLng.from_degrees(lat,lng)).parent(15).id() for lat,lng in points}))
    del route.waypoints[:]
    for index,(lat,lng) in enumerate(points):route.waypoints.add(lat_degrees=lat,lng_degrees=lng,fort_id=route.start_poi.anchor.fort_id if index==0 else route.end_poi.anchor.fort_id if index==len(points)-1 else '')
    old=db.execute('SELECT rowid,* FROM routes WHERE id=?',(route.id,)).fetchone()
    if old and (old['creator']!=creator or json.loads(old['provenance']).get('source')!=provenance.get('source')):raise ValueError('Cannot overwrite another Route provenance/creator')
    stamp=now_ms();db.execute('INSERT INTO routes VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,version=excluded.version,proto=excluded.proto,geometry=excluded.geometry,length_m=excluded.length_m,updated_ms=excluded.updated_ms',
        (route.id,creator,status,route.version,route.SerializeToString(),json.dumps(points),total,json.dumps(provenance),stamp,stamp))
    ident=db.execute('SELECT rowid FROM routes WHERE id=?',(route.id,)).fetchone()[0]
    db.execute('INSERT OR REPLACE INTO route_geo VALUES(?,?,?,?,?)',(ident,min(v[0] for v in points),max(v[0] for v in points),min(v[1] for v in points),max(v[1] for v in points)))
    return route

def active(db,player):return db.execute("SELECT * FROM route_attempts WHERE player=? AND state IN ('ACTIVE','PAUSED','ELIGIBLE')",(player,)).fetchone()
def expire(game,db,player):
    if db.execute("UPDATE route_attempts SET state='INVALIDATED' WHERE player=? AND state IN ('ACTIVE','PAUSED','ELIGIBLE') AND expires_ms<?",(player,now_ms())).rowcount:game.touch(db,player)
def points_of(attempt):
    route=p.SharedRouteProto.FromString(attempt['snapshot']);points=[(v.lat_degrees,v.lng_degrees) for v in route.waypoints]
    return list(reversed(points)) if attempt['reversed'] else points

def sample(game,db,player,fix):
    if not game.routes['enabled'] or not fix:return
    cfg=game.routes;now=now_ms()
    try:lat=float(fix['lat']);lng=float(fix['lng']);accuracy=float(fix['accuracy']);stamp=round(float(fix['time'])*1000);provider=int(fix['provider'])
    except (KeyError,ValueError,TypeError,OverflowError):return
    if not all(math.isfinite(v) for v in (lat,lng,accuracy)) or not -85<=lat<=85 or not -180<=lng<=180 or not 0<accuracy<=cfg['max_accuracy_m'] or provider!=3 or not now-30000<=stamp<=now+2000:return
    source=fix.get('source');debug=source=='debug' and cfg['allow_debug_fixture']
    if source!='device' and not debug:return
    old=db.execute('SELECT * FROM route_locations WHERE player=?',(player,)).fetchone()
    if old and stamp<=old['fix_ms']:return
    if old:
        elapsed=min((stamp-old['fix_ms'])/1000,max(.001,(now-old['arrival_ms'])/1000)+2)
        moved=distance(old['lat'],old['lng'],lat,lng)
        if elapsed<=0 or (elapsed<120 and moved>max(accuracy+old['accuracy'],elapsed*cfg['max_speed_mps'])):return
    db.execute('INSERT INTO route_locations VALUES(?,?,?,?,?,?,?) ON CONFLICT(player) DO UPDATE SET lat=excluded.lat,lng=excluded.lng,accuracy=excluded.accuracy,fix_ms=excluded.fix_ms,arrival_ms=excluded.arrival_ms,source=excluded.source',(player,lat,lng,accuracy,stamp,now,source))
    if old and 3<=elapsed<=120 and moved>=max(5,accuracy):
        from dittobuffer.gameplay.route_creator import observed
        observed(game,db,player,lat,lng)
    attempt=active(db,player)
    if not attempt:return
    if now>attempt['expires_ms']:
        db.execute("UPDATE route_attempts SET state='INVALIDATED' WHERE id=?",(attempt['id'],));game.touch(db,player);return
    if attempt['state']=='PAUSED' or not old or elapsed<3 or elapsed>120 or moved<max(4,accuracy):return
    points=points_of(attempt);result=project(points,lat,lng,max(0,attempt['along_m']-20),attempt['along_m']+moved+2*accuracy+10)
    if not result or result[0]>cfg['corridor_m']:return
    along=max(attempt['along_m'],result[1]);walked=attempt['walked_m']+moved;total=lengths(points)[-1]
    state='ELIGIBLE' if along>=total*cfg['progress_fraction'] and walked>=total*cfg['distance_fraction'] and distance(lat,lng,*points[-1])<=cfg['end_range_m'] else 'ACTIVE'
    db.execute('UPDATE route_attempts SET along_m=?,walked_m=?,last_index=?,state=? WHERE id=?',(along,walked,result[2],state,attempt['id']))
    db.execute('INSERT INTO route_samples VALUES(?,?,?,?,?)',(attempt['id'],stamp,lat,lng,along));game.touch(db,player)
    if int(along//50)>int(attempt['along_m']//50):
        from dittobuffer.gameplay.exploration_events import emit
        emit(game,db,player,'route.progressed',attempt['id']+':'+str(int(along//50)),dict(attempt=attempt['id'],route=attempt['route'],along_m=along,walked_m=walked))

def play(game,db,attempt):
    out=p.RoutePlayProto(route=p.SharedRouteProto.FromString(attempt['snapshot']),play_version=attempt['version'],expiration_time_ms=attempt['expires_ms'],start_time_ms=attempt['start_ms'],paused=attempt['state']=='PAUSED',completed_walk=attempt['state'] in ('ELIGIBLE','COMPLETED'),acquired_reward=attempt['state']=='COMPLETED',travel_in_reverse=bool(attempt['reversed']),total_distance_travelled_meters=attempt['walked_m'])
    latest=db.execute('SELECT MAX(fix_ms) FROM route_samples WHERE attempt=?',(attempt['id'],)).fetchone()[0]
    out.last_progress_time_ms=latest or attempt['start_ms']
    for row in db.execute('SELECT * FROM route_samples WHERE attempt=? ORDER BY fix_ms DESC LIMIT 256',(attempt['id'],)).fetchall()[::-1]:out.player_breadcrumbs.add(lat_degrees=row['lat'],lng_degrees=row['lng'],timestamp_ms=row['fix_ms'])
    last=db.execute('SELECT * FROM route_badges WHERE player=? AND route=?',(attempt['player'],attempt['route'])).fetchone();out.is_first_time=not last
    from dittobuffer.gameplay.route_rewards import rewards
    out.is_first_travel_today=rewards(game,db,attempt['player'])[1]
    from dittobuffer.gameplay.route_mateo import attach
    attach(game,db,attempt,out)
    return out

def inventory(game,db,player):
    if not game.routes['enabled']:return []
    expire(game,db,player)
    result=[];attempt=active(db,player)
    if not attempt:attempt=db.execute('SELECT * FROM route_attempts WHERE player=? ORDER BY start_ms DESC LIMIT 1',(player,)).fetchone()
    # Presence itself means an active play record to 0.429.2. An empty
    # singleton creates a phantom Route (and its map/navigation state).
    if attempt and attempt['state'] not in ('QUIT','INVALIDATED','COMPLETED'):
        value=p.HoloInventoryItemProto();value.route_play.CopyFrom(play(game,db,attempt));result.append(value)
    from dittobuffer.gameplay.route_creator import inventory as creations
    value=p.HoloInventoryItemProto();value.route_creations.CopyFrom(creations(game,db,player));result.append(value)
    return result

def nearby(game,db,player,body):
    req=p.GetRoutesProto.FromString(body);out=p.GetRoutesOutProto(status=1)
    if not game.routes['enabled']:return out
    requested=set(req.cell_id)
    if len(requested)>100 or any(not CellId(c).is_valid() or CellId(c).level()<12 for c in requested):
        from dittobuffer.gameplay.game import RpcError
        raise RpcError('Invalid Route cells')
    location=db.execute('SELECT * FROM route_locations WHERE player=?',(player,)).fetchone()
    if requested:
        # Native discovery is also a map browser. A fresh viewport can arrive
        # before a new physical GPS fix after resume/area transitions. Public
        # discovery follows those requested cells; starting/earning still uses
        # independently validated current movement and proximity below.
        centers=[CellId(c).to_lat_lng() for c in requested]
        lats=[v.lat().degrees for v in centers];lngs=[v.lng().degrees for v in centers]
        if max(lats)-min(lats)>1 or max(lngs)-min(lngs)>1:
            from dittobuffer.gameplay.game import RpcError
            raise RpcError('Route viewport is not geographically bounded')
        lat=sum(lats)/len(lats);lng=sum(lngs)/len(lngs)
    elif not location or now_ms()-location['arrival_ms']>120000:
        if game.routes.get('diagnostics') and game.db.path.resolve().is_relative_to(ROOT/'builds'):
            import logging
            logging.info('Routes discovery no accepted fresh location')
        return out
    else:lat,lng=location['lat'],location['lng']
    radius=game.routes['discovery_m'];dy=radius/111195;dx=dy/max(.1,math.cos(math.radians(lat)))
    rows=db.execute("SELECT r.* FROM route_geo g JOIN routes r ON r.rowid=g.id WHERE r.status='PUBLISHED' AND g.min_lat<=? AND g.max_lat>=? AND g.min_lng<=? AND g.max_lng>=? LIMIT 2000",(lat+dy,lat-dy,lng+dx,lng-dx)).fetchall()
    ranked=[]
    for row in rows:
        route=p.SharedRouteProto.FromString(row['proto']);a=route.start_poi.anchor;b=route.end_poi.anchor
        gap=min(distance(lat,lng,a.lat_degrees,a.lng_degrees),distance(lat,lng,b.lat_degrees,b.lng_degrees))
        if gap<=radius:ranked.append((gap,route.id,route))
    cells={c:[] for c in requested}
    for _,_,route in sorted(ranked,key=lambda v:(v[0],v[1]))[:game.routes['page_size']]:
        leaves=[CellId.from_lat_lng(LatLng.from_degrees(w.lat_degrees,w.lng_degrees)) for w in route.waypoints]
        targets=sorted(c for c in requested if any(CellId(c).contains(leaf) for leaf in leaves)) if requested else sorted({leaf.parent(15).id() for leaf in leaves})
        if not targets:continue
        badge=db.execute('SELECT completions FROM route_badges WHERE player=? AND route=?',(player,route.id)).fetchone()
        if badge:route.player_stats.num_completions=badge[0]
        out.route_list.add(route=route,s2_cell_id=targets)
        for cell in targets:cells.setdefault(cell,[]).append(route)
    for cell,routes in cells.items():out.route_map_cell.add(s2_cell_id=cell,route_list_hash=hashlib.sha256(b''.join(r.SerializeToString() for r in routes)).hexdigest(),route=routes)
    if game.routes.get('diagnostics') and game.db.path.resolve().is_relative_to(ROOT/'builds'):
        import logging
        logging.info('Routes discovery location=(%.7f,%.7f) rows=%d ranked=%d requested=%d returned=%s',lat,lng,len(rows),len(ranked),len(requested),[v.route.id for v in out.route_list])
    if req.request_version>=2:
        out.route_tabs.add(title_string_id='route_discovery_tab_all',route_ids=[v.route.id for v in out.route_list])
    return out

def map_cells(game,db,player,cells):
    """Advertise real Route content on the ordinary native GMO cell stream."""
    if not game.routes['enabled']:return
    routes=[entry.route for entry in nearby(game,db,player,b'').route_list]
    for cell in cells:
        # The native creator filters out unmarked forts, even when they are
        # visible in the overworld. Only the existing approved-world GMO
        # selection reaches this point; closed/disabled places stay excluded.
        for fort in cell.fort:
            fort.is_route_eligible=fort.enabled and not fort.closed
        ident=CellId(cell.s2_cell_id)
        content=[route for route in routes if any(ident.contains(CellId.from_lat_lng(LatLng.from_degrees(w.lat_degrees,w.lng_degrees))) for w in route.waypoints)]
        # The hash advertises discovery; GET_ROUTES owns the full Route
        # payload. Avoid injecting duplicate scene entities through GMO.
        cell.route_list_hash=hashlib.sha256(b''.join(r.SerializeToString() for r in content)).hexdigest()

def start(game,db,player,body):
    req=p.StartRouteProto.FromString(body);out=p.StartRouteOutProto(status=3)
    if not game.routes['enabled']:return out
    row=db.execute('SELECT * FROM routes WHERE id=?',(req.route_id,)).fetchone()
    if not row:return out
    if row['status']!='PUBLISHED':out.status=12;return out
    from dittobuffer.gameplay.progression import bounds
    if bounds(game.config,game.player(db,player)['xp'])[0]<game.routes['min_level']:out.status=10;return out
    route=p.SharedRouteProto.FromString(row['proto']);anchor=route.end_poi.anchor if req.travel_in_reverse else route.start_poi.anchor
    if req.entry_fort_id!=anchor.fort_id or (req.travel_in_reverse and not route.reversible):out.status=5;return out
    loc=db.execute('SELECT * FROM route_locations WHERE player=?',(player,)).fetchone()
    if not loc or now_ms()-loc['arrival_ms']>30000 or distance(loc['lat'],loc['lng'],anchor.lat_degrees,anchor.lng_degrees)>game.routes['start_range_m']:out.status=6;return out
    expire(game,db,player);old=active(db,player)
    if old:
        if old['route']!=route.id or bool(old['reversed'])!=req.travel_in_reverse:out.status=6;return out
        out.status=1;out.route_play.CopyFrom(play(game,db,old));return out
    last=db.execute("SELECT finished_ms FROM route_attempts WHERE player=? AND route=? AND state='COMPLETED' ORDER BY finished_ms DESC LIMIT 1",(player,route.id)).fetchone()
    if last and now_ms()<last[0]+game.routes['cooldown_seconds']*1000:out.status=8;return out
    stamp=now_ms();ident=secrets.token_hex(16)
    db.execute('INSERT INTO route_attempts(id,player,route,version,snapshot,state,reversed,start_ms,expires_ms) VALUES(?,?,?,?,?,?,?,?,?)',(ident,player,route.id,route.version,route.SerializeToString(),'ACTIVE',int(req.travel_in_reverse),stamp,stamp+game.routes['expiration_seconds']*1000))
    from dittobuffer.gameplay.exploration_events import emit
    emit(game,db,player,'route.started',ident,dict(attempt=ident,route=route.id,version=route.version))
    game.touch(db,player);out.status=1;out.route_play.CopyFrom(play(game,db,active(db,player)));return out

def badge(game,db,player,route):
    row=db.execute('SELECT * FROM route_badges WHERE player=? AND route=?',(player,route.id)).fetchone()
    level=sum(row['completions']>=n for n in game.routes['badge_thresholds'])
    return p.AwardedRouteBadge(route_id=route.id,route_type=route.type,num_completions=row['completions'],last_played_time=row['last_ms'],route_name=route.name,route_description=route.description,route_creator_codename=route.creator_info.creator_codename,route_image_url=route.image.image_url,route_duration_seconds=route.route_duration_seconds,route_badge_type=level,badge_level=level,start_lat=route.start_poi.anchor.lat_degrees,start_lng=route.start_poi.anchor.lng_degrees,route_distance_meters=route.route_distance_meters,route=route,can_preview=True)

def progress(game,db,player,body):
    req=p.ProgressRouteProto.FromString(body);out=p.ProgressRouteOutProto(status=9);attempt=active(db,player) if game.routes['enabled'] else None
    if not attempt:
        last=db.execute('SELECT receipt FROM route_attempts WHERE player=? ORDER BY start_ms DESC LIMIT 1',(player,)).fetchone() if game.routes['enabled'] else None
        return p.ProgressRouteOutProto.FromString(last[0]) if last and last[0] and req.acquire_reward else out
    if now_ms()>attempt['expires_ms']:expire(game,db,player);out.status=7;return out
    if req.HasField('pause'):
        if req.pause:
            db.execute("UPDATE route_attempts SET state='PAUSED' WHERE id=?",(attempt['id'],))
        else:
            loc=db.execute('SELECT * FROM route_locations WHERE player=?',(player,)).fetchone();points=points_of(attempt)
            match=project(points,loc['lat'],loc['lng'],max(0,attempt['along_m']-20),attempt['along_m']+20) if loc else None
            if not loc or now_ms()-loc['arrival_ms']>30000 or not match or match[0]>game.routes['corridor_m']:out.status=6;return out
            total=lengths(points)[-1];cfg=game.routes
            state='ELIGIBLE' if attempt['along_m']>=total*cfg['progress_fraction'] and attempt['walked_m']>=total*cfg['distance_fraction'] and distance(loc['lat'],loc['lng'],*points[-1])<=cfg['end_range_m'] else 'ACTIVE'
            db.execute('UPDATE route_attempts SET state=? WHERE id=?',(state,attempt['id']))
        game.touch(db,player);attempt=active(db,player)
    out.status=1;out.progression_state=2 if attempt['state']=='ELIGIBLE' else 1
    if req.acquire_reward:
        if attempt['state']!='ELIGIBLE':out.status=6;out.route_play.CopyFrom(play(game,db,attempt));return out
        loc=db.execute('SELECT * FROM route_locations WHERE player=?',(player,)).fetchone();points=points_of(attempt)
        if not loc or now_ms()-loc['arrival_ms']>30000 or distance(loc['lat'],loc['lng'],*points[-1])>game.routes['end_range_m']:out.status=6;return out
        route=p.SharedRouteProto.FromString(attempt['snapshot']);cfg=game.routes;stamp=now_ms();before_play=play(game,db,attempt)
        from dittobuffer.gameplay.route_rewards import rewards,record
        reward,first,streak=rewards(game,db,player)
        total=game.bag_count(db,player)
        if total+sum(reward['items'].values())>game.capacity(db,player,'item_capacity'):out.status=2;out.route_play.CopyFrom(play(game,db,attempt));return out
        db.execute('UPDATE players SET xp=xp+?,stardust=stardust+? WHERE id=?',(reward['xp'],reward['stardust'],player))
        out.route_loot.loot_item.add(experience=True,count=reward['xp']);out.route_loot.loot_item.add(stardust=True,count=reward['stardust'])
        for item,count in reward['items'].items():
            db.execute('INSERT INTO items VALUES(?,?,?) ON CONFLICT(player_id,item) DO UPDATE SET count=count+excluded.count',(player,int(item),count));out.route_loot.loot_item.add(item=int(item),count=count)
        from dittobuffer.gameplay.route_cells import award as award_cells
        cells=award_cells(game,db,attempt)
        if cells:out.route_loot.loot_item.add(item=650,count=cells)
        from dittobuffer.gameplay.research import progress as research_progress
        # Field and Special Research share the same durable task progress rows.
        research_progress(game,db,player,'route')
        db.execute('INSERT INTO route_badges VALUES(?,?,1,?,?) ON CONFLICT(player,route) DO UPDATE SET completions=completions+1,last_ms=excluded.last_ms',(player,route.id,stamp,stamp))
        db.execute("UPDATE route_attempts SET state='COMPLETED',finished_ms=? WHERE id=?",(stamp,attempt['id']))
        record(game,db,attempt,streak)
        attempt=db.execute('SELECT * FROM route_attempts WHERE id=?',(attempt['id'],)).fetchone();out.awarded_route_badge.CopyFrom(badge(game,db,player,route));out.cooldown_finish_ms=stamp+cfg['cooldown_seconds']*1000
        out.route_play.CopyFrom(play(game,db,attempt));out.route_play.is_first_time=before_play.is_first_time;out.route_play.is_first_travel_today=before_play.is_first_travel_today
        db.execute('UPDATE route_attempts SET receipt=? WHERE id=?',(out.SerializeToString(),attempt['id']));game.touch(db,player)
        from dittobuffer.gameplay.exploration_events import emit
        emit(game,db,player,'route.completed',attempt['id'],dict(attempt=attempt['id'],route=route.id,xp=reward['xp'],stardust=reward['stardust'],items=reward['items'],cells=cells))
    else:out.route_play.CopyFrom(play(game,db,attempt))
    return out

def cancel(game,db,player,body):
    p.CancelRouteProto.FromString(body)
    if game.routes['enabled']:db.execute("UPDATE route_attempts SET state='QUIT',finished_ms=? WHERE player=? AND state IN ('ACTIVE','PAUSED','ELIGIBLE')",(now_ms(),player));game.touch(db,player)
    return p.CancelRouteOutProto(status=1)

def list_badges(game,db,player,body):
    p.ListRouteBadgesProto.FromString(body);out=p.ListRouteBadgesOutProto()
    if not game.routes['enabled']:return out
    for row in db.execute('SELECT r.proto FROM route_badges b JOIN routes r ON r.id=b.route WHERE b.player=? ORDER BY b.last_ms DESC',(player,)):
        route=p.SharedRouteProto.FromString(row[0]);awarded=badge(game,db,player,route);out.awarded_route_badges.add().CopyFrom(awarded)
        out.route_badges.add(route_id=route.id,route_type=route.type,start_lat=awarded.start_lat,start_lng=awarded.start_lng,route_name=route.name,route_image_url=route.image.image_url,last_play_end_time=awarded.last_played_time,num_completions=awarded.num_completions,route_duration_seconds=route.route_duration_seconds)
    return out

def templates(config):
    cfg={**DEFAULTS,**config.get('routes',{})}
    if not cfg['enabled']:return []
    values=[]
    # Native RoutesService 0x82C0288/310/398/420 reads white/bronze/silver/gold
    # at indices 0..3. Omitting white=0 makes the first earned threshold the
    # white minimum; early completions select color fallback 9 and throw in
    # GetBadgeFrameColor (0x82C01F0) before NUI can open the completion screen.
    badge_settings=p.GameMasterClientTemplateProto(template_id='ROUTE_BADGE_SETTINGS');badge_settings.route_badge_settings.target.extend([0,*cfg['badge_thresholds']]);values.append(badge_settings)
    for name,field,kwargs in [
        ('ROUTE_PLAY_SETTINGS','route_play_settings',dict(min_player_level=cfg['min_level'],route_cooldown_minutes=math.ceil(cfg['cooldown_seconds']/60),route_expiration_minutes=cfg['expiration_seconds']//60,pause_distance_meters=50,pause_duration_s=15,player_breadcrumb_trail_max_count=256,margin_percentage=.05,margin_minimum_meters=15,resume_range_meters=35,buddy_total_candy_distance_multiplier=1)),
        ('ROUTE_DISCOVERY_SETTINGS','route_discovery_settings',dict(min_player_level=cfg['min_level'],nearby_visible_radius_meters=cfg['discovery_m'],max_routes_viewable=cfg['page_size'],max_client_map_panning_distance_meters=cfg['discovery_m'],enable_badge_routes_discovery=True)),
        ('ROUTE_CREATION_SETTINGS','route_creation_settings',dict(enabled=True,min_player_level=cfg['creator_level'],max_open_routes=1,min_total_distance_m=cfg['min_m'],max_total_distance_m=cfg['max_m'],max_name_length=80,max_description_length=2000,max_distance_from_anchor_pois_m=35,min_breadcrumb_distance_delta_meters=5,max_breadcrumb_distance_delta_meters=100,max_recording_speed_meters_per_second=4,interaction_range_meters=35,resume_range_meters=35,moderation_enabled=True,allowable_gps_drift_meters=25,max_client_map_panning_distance_meters=cfg['discovery_m'],creation_limit_window_days=7,creation_limit_per_window=10,duration_distance_to_speed_multiplier=1/1.4,duration_buffer_s=30))]:
        value=p.GameMasterClientTemplateProto(template_id=name);getattr(value,field).CopyFrom(getattr(p,dict(route_play_settings='RoutePlaySettingsProto',route_discovery_settings='RouteDiscoverySettingsProto',route_creation_settings='RoutesCreationSettingsProto')[field])(**kwargs));values.append(value)
    return values
