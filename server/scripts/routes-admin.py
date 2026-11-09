"""Offline Routes graph/candidate administration; player/world DBs are read-only."""
import argparse,json,sys,sqlite3,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from dittobuffer.gameplay.route_graph import Graph,build,generate
def output(value):
    path=(ROOT/value).resolve()
    if not path.is_relative_to(ROOT/'builds') or path.exists():raise ValueError('Fresh builds output required; nothing is overwritten')
    path.parent.mkdir(parents=True,exist_ok=True);return path
def main():
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='action',required=True)
    b=sub.add_parser('graph');b.add_argument('--source',required=True);b.add_argument('--output',required=True);b.add_argument('--bbox',nargs=4,type=float);b.add_argument('--resume',action='store_true')
    g=sub.add_parser('generate');g.add_argument('--graph',required=True);g.add_argument('--world',required=True);g.add_argument('--bbox',required=True,nargs=4,type=float);g.add_argument('--output',required=True);g.add_argument('--limit',type=int,default=40);g.add_argument('--min-m',type=float,default=500);g.add_argument('--max-m',type=float,default=1500)
    g.add_argument('--loops',action='store_true')
    n=sub.add_parser('nationwide');n.add_argument('--graph',required=True);n.add_argument('--world',required=True);n.add_argument('--output',required=True);n.add_argument('--resume',action='store_true');n.add_argument('--max-tiles',type=int,default=5);n.add_argument('--per-tile',type=int,default=10);n.add_argument('--grid-degrees',type=float,default=.1)
    c=sub.add_parser('coverage');c.add_argument('--graph',required=True);c.add_argument('--candidates',required=True)
    m=sub.add_parser('moderate');m.add_argument('--config',required=True);m.add_argument('--route',required=True);m.add_argument('--status',required=True,choices=['PUBLISHED','REJECTED','ARCHIVED']);m.add_argument('--actor',required=True);m.add_argument('--reason',required=True);m.add_argument('--apply',action='store_true')
    i=sub.add_parser('stage');i.add_argument('--config',required=True);i.add_argument('--candidates',required=True);i.add_argument('--apply',action='store_true')
    args=ap.parse_args()
    if args.action=='graph':
        path=(ROOT/args.output).resolve()
        if not path.is_relative_to(ROOT/'builds') or (path.exists() and not args.resume):raise ValueError('Fresh builds graph required, or explicit --resume')
        report=build(ROOT/args.source,path,args.bbox);path.with_suffix('.report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
    elif args.action=='generate':
        path=output(args.output);report=generate(Graph(ROOT/args.graph),ROOT/args.world,args.bbox,args.limit,args.min_m,args.max_m,loops=args.loops)
        path.write_text(json.dumps(report,indent=2)+'\n')
        geo=dict(type='FeatureCollection',features=[dict(type='Feature',geometry=dict(type='LineString',coordinates=[[lng,lat] for lat,lng in r['points']]),properties={k:r[k] for k in ['id','name','length_m','quality','status','provenance']}) for r in report['candidates']])
        path.with_suffix('.geojson').write_text(json.dumps(geo)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='candidates'}|dict(candidate_count=len(report['candidates']))))
    elif args.action=='nationwide':
        # Country coverage is a persistent list of actual processed tiles;
        # a bounded invocation never claims that unvisited tiles are covered.
        grid=args.grid_degrees
        if not math.isfinite(grid) or not .02<=grid<=.25 or not 1<=args.max_tiles<=100 or not 1<=args.per_tile<=40:raise ValueError('Invalid bounded generation settings')
        path=(ROOT/args.output).resolve()
        if not path.is_relative_to(ROOT/'builds') or (path.exists() and not args.resume):raise ValueError('Fresh builds directory or explicit --resume required')
        path.mkdir(exist_ok=True);manifest=path/'coverage.json';graph=Graph(ROOT/args.graph)
        identity=json.loads(graph.db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()[0])
        if graph.db.execute("SELECT value FROM metadata WHERE key='complete'").fetchone()[0]!='true':raise ValueError('A completed graph is required')
        from dittobuffer.gameplay.route_publication import identity as source_identity
        world_identity=source_identity(ROOT/args.world)
        report=json.loads(manifest.read_text()) if manifest.exists() else dict(graph_identity=identity,world_identity=world_identity,grid=grid,per_tile=args.per_tile,tiles={},nationally_generated=False)
        if report['graph_identity']!=identity or report.get('world_identity')!=world_identity or report['grid']!=grid or report['per_tile']!=args.per_tile:raise ValueError('Resume configuration/source mismatch; legacy manifests require a fresh job')
        for key,value in report['tiles'].items():
            if source_identity(path/(key+'.json'))['sha256']!=value['sha256']:raise ValueError('Processed partition changed: '+key)
        with sqlite3.connect((ROOT/args.world).resolve().as_uri()+'?mode=ro',uri=True) as db:
            tiles=list(db.execute("SELECT DISTINCT CAST(lng/? AS INT),CAST(lat/? AS INT) FROM pois WHERE status='APPROVED' ORDER BY 1,2",(grid,grid)))
        count=0
        for x,y in tiles:
            key=f'{x}-{y}'
            if key in report['tiles']:continue
            bbox=[x*grid,y*grid,(x+1)*grid,(y+1)*grid];candidate_path=path/(key+'.json')
            # Halo endpoints avoid dropping paths just across a partition edge.
            # A candidate belongs only to its lexicographically first anchor's
            # tile. IDs and the publication geometry gate deduplicate overlaps.
            halo=.02;expanded=[bbox[0]-halo,bbox[1]-halo,bbox[2]+halo,bbox[3]+halo]
            candidate=json.loads(candidate_path.read_text()) if candidate_path.exists() else generate(graph,ROOT/args.world,expanded,args.per_tile*4)
            if candidate_path.exists() and (candidate.get('job_world_identity')!=world_identity or candidate.get('job_graph_identity')!=identity):raise ValueError('Orphan partition source mismatch')
            candidate['candidates']=[v for v in candidate['candidates'] if (math.floor(min((v['start'],v['end']),key=lambda e:e['id'])['lng']/grid),math.floor(min((v['start'],v['end']),key=lambda e:e['id'])['lat']/grid))==(x,y)][:args.per_tile]
            candidate.update(job_world_identity=world_identity,job_graph_identity=identity,partition=key)
            if not candidate_path.exists():
                temp=candidate_path.with_suffix('.pending');temp.write_text(json.dumps(candidate)+'\n');temp.replace(candidate_path)
            report['tiles'][key]=dict(bbox=bbox,candidates=len(candidate['candidates']),eligible_pois=candidate['eligible_pois'],rejected=candidate['rejected'],sha256=source_identity(candidate_path)['sha256'],status='CANDIDATE_ONLY' if candidate['candidates'] else 'SKIPPED_NO_QUALIFYING_CANDIDATE')
            count+=1
            report.update(total_eligible_tiles=len(tiles),processed_tiles=len(report['tiles']),candidate_count=sum(v['candidates'] for v in report['tiles'].values()),nationally_generated=len(report['tiles'])==len(tiles))
            temp=manifest.with_suffix('.pending');temp.write_text(json.dumps(report,indent=2)+'\n');temp.replace(manifest)
            if count>=args.max_tiles:break
        print(json.dumps({k:v for k,v in report.items() if k not in ('tiles','graph_identity')}))
    elif args.action=='coverage':
        graph=Graph(ROOT/args.graph);value=json.loads((ROOT/args.candidates).read_text())
        print(json.dumps(dict(graph_nodes=graph.db.execute('SELECT COUNT(*) FROM nodes').fetchone()[0],graph_edges=graph.db.execute('SELECT COUNT(*) FROM edges').fetchone()[0],graph_bounds=list(graph.db.execute('SELECT MIN(lat),MIN(lng),MAX(lat),MAX(lng) FROM nodes').fetchone()),candidates=len(value.get('candidates',[])),bbox=value.get('bbox'),publication='Not queried; candidate files do not establish published coverage',coverage=value.get('coverage'))))
    else:
        cfg=json.loads((ROOT/args.config).read_text())
        if args.action=='stage':
            candidates=json.loads((ROOT/args.candidates).read_text())['candidates'];print(json.dumps(dict(dry_run=not args.apply,candidate_count=len(candidates),publication='PENDING only; explicit moderation required')))
        else:print(json.dumps(dict(dry_run=not args.apply,route=args.route,status=args.status)))
        if not args.apply:return
        if not cfg.get('profile','').startswith('m20-') or not (ROOT/cfg['database']).resolve().is_relative_to(ROOT/'builds'):raise ValueError('This development CLI applies only to isolated M20 builds databases')
        from dittobuffer.gameplay.game import Game,p
        from dittobuffer.core.database import Database
        from dittobuffer.gameplay.routes import put
        from dittobuffer.gameplay.route_creator import moderate
        game=Game(cfg,Database(ROOT/cfg['database']))
        with game.db.transaction() as db:
            if args.action=='moderate':moderate(game,db,args.route,args.status,args.actor,args.reason)
            else:
                for candidate in candidates:
                    existing=db.execute('SELECT creator,provenance FROM routes WHERE id=?',(candidate['id'],)).fetchone()
                    if existing and (existing['creator'] or json.loads(existing['provenance']).get('source')!='osm-pedestrian-graph-v1'):raise ValueError('Refusing to overwrite a manual Route')
                    if existing:continue # Repeated staging must not reset a moderation decision.
                    route=p.SharedRouteProto(id=candidate['id'],name=candidate['name'][:80],description='Local exploration along mapped OSM walking paths.',reversible=candidate['reversible'])
                    for field,key in [('start_poi','start'),('end_poi','end')]:
                        site=candidate[key];game.world.materialize(db,site['lat'],site['lng']);getattr(route,field).anchor.CopyFrom(p.RouteWaypointProto(fort_id=site['id'],lat_degrees=site['lat'],lng_degrees=site['lng']))
                    put(game,db,route,candidate['points'],candidate['provenance'],status='PENDING')
if __name__=='__main__':main()
