"""Explicit, consent-aware owner Gift contribution; no generated participants."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from dittobuffer.gameplay.game import Game
from dittobuffer.core.database import Database
from dittobuffer.gameplay.route_mateo import contribute

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--config',required=True);ap.add_argument('--player',required=True);ap.add_argument('--gift',required=True,type=int);ap.add_argument('--route',default='');ap.add_argument('--share-name',action='store_true');ap.add_argument('--apply',action='store_true');args=ap.parse_args()
    cfg=json.loads((ROOT/args.config).read_text())
    if not cfg.get('profile','').startswith('m20-') or not (ROOT/cfg['database']).resolve().is_relative_to(ROOT/'builds'):raise ValueError('Development administrative helper supports only isolated M20 profiles')
    print(json.dumps(dict(dry_run=not args.apply,gift=args.gift,share_name=args.share_name,operation='Reserve one actual owned unsent Gift in the exchange pool; no public player identity without existing consent')))
    if not args.apply:return
    game=Game(cfg,Database(ROOT/cfg['database']))
    with game.db.transaction() as db:contribute(game,db,args.player,args.gift,args.route,args.share_name)

if __name__=='__main__':main()
