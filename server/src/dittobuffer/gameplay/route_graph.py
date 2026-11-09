"""Offline OSM pedestrian graph. Connections use OSM node IDs, not 2-D crossings."""
import hashlib,heapq,json,math,sqlite3,time
from pathlib import Path
from dittobuffer.gameplay.game import distance

SCHEMA='''
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nodes(id INTEGER PRIMARY KEY,lat REAL NOT NULL,lng REAL NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS node_geo USING rtree(id,min_lat,max_lat,min_lng,max_lng);
CREATE TABLE IF NOT EXISTS edges(src INTEGER NOT NULL,dst INTEGER NOT NULL,way INTEGER NOT NULL,length REAL NOT NULL,weight REAL NOT NULL,tags TEXT NOT NULL,PRIMARY KEY(src,dst,way));
CREATE INDEX IF NOT EXISTS edge_source ON edges(src);
CREATE TABLE IF NOT EXISTS imported_ways(id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS processed_ways(id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS rejected(reason TEXT PRIMARY KEY,count INTEGER NOT NULL);
'''
ALLOWED={'footway','pedestrian','path','living_street','residential','service','steps','cycleway','tertiary','unclassified'}
def suitability(tags):
    highway=tags.get('highway','')
    if highway not in ALLOWED:return None,'not_pedestrian_class'
    if tags.get('area')=='yes' or tags.get('construction') or tags.get('proposed'):return None,'area_or_unbuilt'
    if tags.get('foot') in ('no','private','use_sidepath') or tags.get('access') in ('no','private','customers','permit','military') or tags.get('conditional') or tags.get('foot:conditional') or tags.get('access:conditional'):return None,'access_restricted_or_conditional'
    if tags.get('railway') and highway!='footway':return None,'rail_corridor'
    if highway=='cycleway' and tags.get('foot') not in ('yes','designated','permissive'):return None,'cycleway_no_explicit_foot_access'
    if highway=='service' and (tags.get('service') in ('driveway','parking_aisle','emergency_access') or tags.get('access') not in ('yes','public','permissive')) and tags.get('foot') not in ('yes','designated','permissive'):return None,'service_not_explicit_public_foot'
    if highway in ('tertiary','unclassified') and tags.get('sidewalk') not in ('yes','both','left','right') and tags.get('foot') not in ('yes','designated'):return None,'road_without_supported_walking_access'
    weight=1 if highway in ('footway','pedestrian','path') else 1.15 if highway in ('living_street','residential') else 1.3
    return weight,None

def build(source,output,bbox=None):
    """Resume by stable way IDs; PBF streaming location cache lives beside output."""
    import osmium
    source=Path(source).resolve();output=Path(output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(output);db.executescript(SCHEMA);db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA cache_size=-32768')
    # Source identity is immutable across a resumed import. A changed extract
    # requires a new graph, never replacement of the published world.
    identity=json.dumps(dict(source=str(source),bytes=source.stat().st_size,mtime_ns=source.stat().st_mtime_ns,bbox=bbox,policy='walkable-v1'),sort_keys=True)
    old=db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
    if old and old[0]!=identity:raise ValueError('Use a fresh graph output for another extract/bounds/policy')
    db.execute("INSERT OR IGNORE INTO metadata VALUES('identity',?)",(identity,));db.commit()
    def report():
        return dict(nodes=db.execute('SELECT COUNT(*) FROM nodes').fetchone()[0],directed_edges=db.execute('SELECT COUNT(*) FROM edges').fetchone()[0],ways=db.execute('SELECT COUNT(*) FROM imported_ways').fetchone()[0],rejected=dict(db.execute('SELECT * FROM rejected')),bbox=bbox,source=str(source),licence='ODbL-1.0',attribution='OpenStreetMap contributors',complete=True)
    if db.execute("SELECT 1 FROM metadata WHERE key='complete' AND value='true'").fetchone():
        value=report();db.close();return value
    blocked=set();seen=0
    class Handler(osmium.SimpleHandler):
        def node(self,node):
            if not node.tags:return
            tags=dict(node.tags)
            if tags.get('foot') in ('no','private') or (tags.get('barrier') in ('gate','fence','wall','lift_gate','swing_gate') and tags.get('foot') not in ('yes','designated','permissive')):blocked.add(node.id)
        def way(self,way):
            nonlocal seen
            if db.execute('SELECT 1 FROM processed_ways WHERE id=?',(way.id,)).fetchone():return
            db.execute('INSERT INTO processed_ways VALUES(?)',(way.id,))
            tags=dict(way.tags);weight,reason=suitability(tags)
            if reason:
                if 'highway' in tags:db.execute('INSERT INTO rejected VALUES(?,1) ON CONFLICT(reason) DO UPDATE SET count=count+1',(reason,))
                return
            if db.execute('SELECT 1 FROM imported_ways WHERE id=?',(way.id,)).fetchone():return
            try:values=[(v.ref,v.lat,v.lon) for v in way.nodes if v.location.valid()]
            except osmium.InvalidLocationError:return
            if len(values)!=len(way.nodes) or len(values)<2:return
            # Bounds filter whole edges; never join clipped endpoints or
            # fabricate intersections between bridges and tunnels.
            encoded=json.dumps({k:tags[k] for k in ('highway','name','foot','access','bridge','tunnel','layer','sidewalk','surface') if k in tags},sort_keys=True)
            for a,b in zip(values,values[1:]):
                if a[0] in blocked or b[0] in blocked:continue
                if bbox and not all(bbox[0]<=v[2]<=bbox[2] and bbox[1]<=v[1]<=bbox[3] for v in (a,b)):continue
                length=distance(a[1],a[2],b[1],b[2])
                if not 0<length<=250:continue
                for ident,lat,lng in (a,b):
                    db.execute('INSERT OR IGNORE INTO nodes VALUES(?,?,?)',(ident,lat,lng));db.execute('INSERT OR IGNORE INTO node_geo VALUES(?,?,?,?,?)',(ident,lat,lat,lng,lng))
                directions=[(a[0],b[0]),(b[0],a[0])]
                if tags.get('oneway:foot') in ('yes','1'):directions=directions[:1]
                if tags.get('oneway:foot')=='-1':directions=directions[1:]
                for src,dst in directions:db.execute('INSERT OR IGNORE INTO edges VALUES(?,?,?,?,?,?)',(src,dst,way.id,length,length*weight,encoded))
            db.execute('INSERT OR IGNORE INTO imported_ways VALUES(?)',(way.id,));seen+=1
            if seen%2000==0:db.commit();print(json.dumps(dict(imported_walkable_ways=seen)),flush=True)
    # Bounded small extracts need no persistent mmap; country imports use the
    # disk index in the dedicated CLI process, which releases it on exit.
    handler=Handler();cache='flex_mem' if source.stat().st_size<=64*1024*1024 else 'sparse_file_array,'+str(output.with_suffix('.locations'))
    handler.apply_file(str(source),locations=True,idx=cache)
    # Release Python callback references before returning. On Windows libosmium
    # can retain disk-index handles until process exit; the CLI is process scoped.
    del handler
    import gc
    gc.collect()
    db.execute("INSERT OR REPLACE INTO metadata VALUES('complete','true')");db.commit()
    value=report();db.close();return value

class Graph:
    def __init__(self,path):
        self.db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True);self.db.row_factory=sqlite3.Row
    def snap(self,lat,lng,range_m=30):
        dy=range_m/111195;dx=dy/max(.1,math.cos(math.radians(lat)))
        rows=self.db.execute('SELECT n.* FROM node_geo g JOIN nodes n ON n.id=g.id WHERE g.min_lat<=? AND g.max_lat>=? AND g.min_lng<=? AND g.max_lng>=?',(lat+dy,lat-dy,lng+dx,lng-dx))
        result=min(((distance(lat,lng,v['lat'],v['lng']),v['id']) for v in rows),default=None)
        return result[1] if result and result[0]<=range_m else None
    def path(self,start,end,max_m=6000,max_visited=50000,avoid_way=None,avoid_edges=frozenset()):
        todo=[(0,0,start)];cost={start:0};previous={};visited=0
        while todo:
            score,meters,node=heapq.heappop(todo)
            if score!=cost.get(node):continue
            visited+=1
            if visited>max_visited:return None
            if node==end:
                ids=[node];ways=[]
                while node!=start:node,way=previous[node];ids.append(node);ways.append(way)
                ids.reverse();points=[tuple(self.db.execute('SELECT lat,lng FROM nodes WHERE id=?',(v,)).fetchone()) for v in ids]
                reversible=all(self.db.execute('SELECT 1 FROM edges WHERE src=? AND dst=?',(b,a)).fetchone() for a,b in zip(ids,ids[1:]))
                return dict(points=points,nodes=ids,length_m=meters,ways=sorted(set(ways)),reversible=reversible,quality=round(meters/max(score,1),4))
            for edge in self.db.execute('SELECT * FROM edges WHERE src=?',(node,)):
                if edge['way']==avoid_way or (node,edge['dst']) in avoid_edges:continue
                total=meters+edge['length'];value=score+edge['weight']
                if total<=max_m and value<cost.get(edge['dst'],float('inf')):
                    cost[edge['dst']]=value;previous[edge['dst']]=(node,edge['way']);heapq.heappush(todo,(value,total,edge['dst']))
        return None

    def loop(self,start,via,max_m=3000):
        outward=self.path(start,via,max_m/2)
        if not outward:return None
        avoid={(a,b) for a,b in zip(outward['nodes'],outward['nodes'][1:])}
        back=self.path(via,start,max_m-outward['length_m'],avoid_edges=avoid|{(b,a) for a,b in avoid})
        if not back:return None
        return dict(points=outward['points']+back['points'][1:],nodes=outward['nodes']+back['nodes'][1:],length_m=outward['length_m']+back['length_m'],ways=sorted(set(outward['ways']+back['ways'])),reversible=outward['reversible'] and back['reversible'],quality=min(outward['quality'],back['quality']))

def generate(graph,world,bbox,limit=40,min_m=500,max_m=1500,max_neighbors=8,loops=False):
    """Bounded deterministic candidates, never writes to the world or player DB."""
    source=sqlite3.connect(Path(world).resolve().as_uri()+'?mode=ro',uri=True);source.row_factory=sqlite3.Row
    pois=[dict(v) for v in source.execute("SELECT * FROM pois WHERE lng BETWEEN ? AND ? AND lat BETWEEN ? AND ? AND status='APPROVED' ORDER BY id LIMIT 10000",(bbox[0],bbox[2],bbox[1],bbox[3]))];source.close()
    result=[];rejected={};pairs=set();snapped={v['id']:graph.snap(v['lat'],v['lng']) for v in pois}
    for a in pois:
        if snapped[a['id']] is None:rejected['start_not_on_graph']=rejected.get('start_not_on_graph',0)+1;continue
        neighbors=sorted((b for b in pois if b['id']!=a['id']),key=lambda b:(distance(a['lat'],a['lng'],b['lat'],b['lng']),b['id']))[:max_neighbors]
        accepted=0
        for b in neighbors:
            pair=tuple(sorted((a['id'],b['id'])))
            if pair in pairs or snapped[b['id']] is None:continue
            pairs.add(pair);path=graph.loop(snapped[a['id']],snapped[b['id']],max_m) if loops else graph.path(snapped[a['id']],snapped[b['id']],max_m)
            if not path or path['length_m']<min_m:
                key='disconnected_or_distance';rejected[key]=rejected.get(key,0)+1;continue
            ident='osm-route-'+hashlib.sha256((('loop|' if loops else '')+'|'.join(pair)+'|'+'|'.join(map(str,path['ways']))).encode()).hexdigest()[:24]
            result.append(dict(id=ident,name=a['name']+(' via ' if loops else ' to ')+b['name'],start=a,end=a if loops else b,**path,status='PENDING_REVIEW',provenance=dict(source='osm-pedestrian-graph-v1',osm_ways=path['ways'],licence='ODbL-1.0',attribution='OpenStreetMap contributors')))
            accepted+=1
            if len(result)>=limit or accepted>=2:break
        if len(result)>=limit:break
    return dict(candidates=result,eligible_pois=len(pois),bbox=bbox,rejected=rejected,coverage='Only the declared bounding box, not nationwide coverage',policy='Custom generation distances and conservative OSM tag restrictions; approval is explicit')
