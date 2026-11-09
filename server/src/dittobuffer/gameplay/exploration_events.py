"""Optional durable event journal. No callbacks execute before RPC commit.

Future consumers must read committed rows and keep their own idempotent cursor.
This deliberately implements no addon loader, network delivery or reward hooks.
"""
import json
from dittobuffer.gameplay.game import now_ms
SCHEMA='''CREATE TABLE IF NOT EXISTS exploration_events(sequence INTEGER PRIMARY KEY,event_id TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,player TEXT NOT NULL REFERENCES players(id),payload TEXT NOT NULL,created_ms INTEGER NOT NULL);'''
KINDS=frozenset(('route.started','route.progressed','route.completed','mateo.available','mateo.gift_exchanged','postcard.pinned','vivillon.region_progressed','scatterbug.encounter_unlocked'))
def enabled(game):
    value=game.config.get('routes',{}).get('event_journal',False)
    if type(value) is not bool:raise ValueError('Exploration event_journal must be boolean')
    return value
def emit(game,db,player,kind,key,payload):
    if not enabled(game):return
    if kind not in KINDS:raise ValueError('Unknown exploration event')
    db.execute('INSERT OR IGNORE INTO exploration_events(event_id,kind,player,payload,created_ms) VALUES(?,?,?,?,?)',(kind+':'+player+':'+str(key),kind,player,json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False),now_ms()))
