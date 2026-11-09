"""Stage/review/rollback real graph candidates; no normal world/player DB writes."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from dittobuffer.gameplay import route_publication as pub
from dittobuffer.gameplay.route_graph import Graph
from dittobuffer.gameplay.game import Game
from dittobuffer.core.database import Database

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--config',required=True);ap.add_argument('--apply',action='store_true');sub=ap.add_subparsers(dest='action',required=True)
    s=sub.add_parser('stage');s.add_argument('--candidates',required=True);s.add_argument('--graph',required=True);s.add_argument('--world',required=True);s.add_argument('--region',required=True)
    d=sub.add_parser('review');d.add_argument('--review',required=True)
    r=sub.add_parser('rollback');r.add_argument('--batch',required=True);r.add_argument('--actor',required=True);r.add_argument('--reason',required=True)
    sub.add_parser('status');args=ap.parse_args();cfg=json.loads((ROOT/args.config).read_text())
    if not cfg.get('profile','').startswith('m20-') or not (ROOT/cfg['database']).resolve().is_relative_to(ROOT/'builds'):raise ValueError('Only isolated M20 builds databases are supported; published world is read-only')
    if args.action=='stage':
        graph=Graph(ROOT/args.graph)
        try:
            if graph.db.execute("SELECT value FROM metadata WHERE key='complete'").fetchone()[0]!='true':raise ValueError('Completed graph required')
            manifest=dict(candidates=pub.identity(ROOT/args.candidates),world=pub.identity(ROOT/args.world),graph_identity=json.loads(graph.db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()[0]),region=args.region)
            candidates=json.loads((ROOT/args.candidates).read_text())['candidates'];print(json.dumps(dict(dry_run=not args.apply,candidates=len(candidates),manifest=manifest)))
            if not args.apply:return
            game=Game(cfg,Database(ROOT/cfg['database']))
            with game.db.transaction() as db:
                batch=pub.stage(game,db,candidates,graph,ROOT/args.world,manifest)
                print(json.dumps(dict(batch=batch,manifest=json.loads(db.execute('SELECT manifest FROM route_generation_batches WHERE id=?',(batch,)).fetchone()[0]))))
        finally:graph.db.close()
        return
    if args.action!='status' and not args.apply:print(json.dumps(dict(dry_run=True,action=args.action)));return
    game=Game(cfg,Database(ROOT/cfg['database']))
    with game.db.transaction() as db:
        if args.action=='review':
            review=json.loads((ROOT/args.review).read_text());batch=db.execute('SELECT manifest FROM route_generation_batches WHERE id=?',(review['batch'],)).fetchone()
            if not batch or json.loads(batch[0])!=review['manifest']:raise ValueError('Review must bind the exact stored input manifest')
            pub.decide(game,db,review['batch'],review['decisions'],review['actor'],review['reason']);print('Explicit review applied')
        elif args.action=='rollback':pub.rollback(game,db,args.batch,args.actor,args.reason);print('Batch archived; history/manual Routes retained')
        else:
            batches=[]
            for row in db.execute('SELECT * FROM route_generation_batches ORDER BY id'):
                counts=dict(db.execute('SELECT r.status,COUNT(*) FROM route_generation_members m JOIN routes r ON r.id=m.route WHERE m.batch=? GROUP BY r.status',(row['id'],)))
                distribution=[dict(v) for v in db.execute('SELECT r.status,COUNT(*) count,MIN(length_m) min_m,MAX(length_m) max_m,SUM(length_m) total_m FROM route_generation_members m JOIN routes r ON r.id=m.route WHERE m.batch=? GROUP BY r.status',(row['id'],))]
                batches.append(dict(id=row['id'],region=json.loads(row['manifest'])['region'],counts=counts,distances=distribution))
            print(json.dumps(dict(batches=batches,total_routes=dict(db.execute('SELECT status,COUNT(*) FROM routes GROUP BY status')),normal_world_publication='UNCHANGED; isolated profile only')))

if __name__=='__main__':main()
