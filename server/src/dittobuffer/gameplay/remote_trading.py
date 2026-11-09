"""Durable remote negotiation; native final-consent binding remains fail-closed.

0.429.2 RespondRemoteTrade carries no session/revision token. Until a native
round-trip binding is demonstrated, it cannot authorize final ownership changes.
No remote completion or reward path is exposed while that binding is unresolved.
"""
import json,secrets
from contextlib import closing
from dittobuffer.gameplay.game import p,now_ms
from dittobuffer.gameplay import friends as f, trading as t
SCHEMA='''
CREATE TABLE IF NOT EXISTS social_remote_sessions(id TEXT PRIMARY KEY,a TEXT NOT NULL REFERENCES players(id),b TEXT NOT NULL REFERENCES players(id),created_ms INTEGER NOT NULL,expires_ms INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'negotiating',requests_a TEXT NOT NULL DEFAULT '[]',requests_b TEXT NOT NULL DEFAULT '[]',offer_a INTEGER NOT NULL DEFAULT 0,offer_b INTEGER NOT NULL DEFAULT 0,approved_a INTEGER NOT NULL DEFAULT 0,approved_b INTEGER NOT NULL DEFAULT 0,nonce TEXT NOT NULL,trade_id TEXT REFERENCES social_trades(id));
CREATE UNIQUE INDEX IF NOT EXISTS social_remote_active ON social_remote_sessions(a,b) WHERE state='negotiating';
'''
def install(game):
    with closing(game.db.connect()) as db:db.executescript(SCHEMA)
def expire(db):
    db.execute("UPDATE social_remote_sessions SET state='expired' WHERE state='negotiating' AND expires_ms<=?",(now_ms(),))
    for row in db.execute("SELECT trade_id FROM social_remote_sessions WHERE state IN ('expired','cancelled') AND trade_id IS NOT NULL"):
        db.execute('UPDATE social_trades SET state=0 WHERE id=? AND state IN (2,3,4)',(row[0],))
    t.expire(db)
def eligible(db,player,other):
    row=f.pair(db,player,other)
    return row if row and row['points']>=180 and row['remote_credit'] else None
def get(db,player,other):return db.execute("SELECT * FROM social_remote_sessions WHERE a=? AND b=? AND state='negotiating'",tuple(sorted((player,other)))).fetchone()
def mark(game,db,player,body):
    req=p.MarkRemoteTradableProto.FromString(body);expire(db)
    if not db.execute('SELECT 1 FROM social_pairs WHERE active=1 AND points>=175 AND (a=? OR b=?)',(player,player)).fetchone():return p.MarkRemoteTradableOutProto(result=4)
    if not 1<=len(req.pokemon)<=100 or len({v.pokemon_uid for v in req.pokemon})!=len(req.pokemon):return p.MarkRemoteTradableOutProto(result=4)
    values=[]
    for v in req.pokemon:
        try:mon=t.valid_mon(game,db,player,v.pokemon_uid,remote=True)
        except t.Invalid:return p.MarkRemoteTradableOutProto(result=4)
        mon.marked_remote_tradable=v.remote_tradable;values.append(mon)
    for mon in values:db.execute('UPDATE pokemon SET data=? WHERE id=? AND player_id=?',(mon.SerializeToString(),mon.id,player))
    game.touch(db,player);return p.MarkRemoteTradableOutProto(result=1)
def list_other(game,db,player,body):
    req=p.GetRemoteTradablePokemonFromOtherPlayerProto.FromString(body);expire(db)
    if not f.pair(db,player,req.player_id):return p.GetRemoteTradablePokemonFromOtherPlayerOutProto(result=3)
    if not eligible(db,player,req.player_id):return p.GetRemoteTradablePokemonFromOtherPlayerOutProto(result=4)
    out=p.GetRemoteTradablePokemonFromOtherPlayerOutProto(result=1)
    for row in db.execute('SELECT id FROM pokemon WHERE player_id=?',(req.player_id,)):
        try:mon=t.valid_mon(game,db,req.player_id,row[0],remote=True)
        except t.Invalid:continue
        if mon.marked_remote_tradable:out.pokemon.add().CopyFrom(mon)
    return out
def propose(game,db,player,body):
    req=p.ProposeRemoteTradeProto.FromString(body);expire(db)
    if not f.pair(db,player,req.player_id):return p.ProposeRemoteTradeOutProto(result=3)
    if not eligible(db,player,req.player_id):return p.ProposeRemoteTradeOutProto(result=4)
    ids=list(req.requested_pokemon_uids)
    if len(ids)>3 or len(set(ids))!=len(ids):return p.ProposeRemoteTradeOutProto(result=5)
    for ident in ids:
        try:mon=t.valid_mon(game,db,req.player_id,ident,remote=True)
        except t.Invalid:return p.ProposeRemoteTradeOutProto(result=5)
        if not mon.marked_remote_tradable:return p.ProposeRemoteTradeOutProto(result=5)
    row=get(db,player,req.player_id)
    if row is None:
        a,b=sorted((player,req.player_id));ident=secrets.token_hex(16)
        db.execute('INSERT INTO social_remote_sessions(id,a,b,created_ms,expires_ms,nonce) VALUES(?,?,?,?,?,?)',(ident,a,b,now_ms(),now_ms()+48*3600000,secrets.token_hex(24)));row=get(db,player,req.player_id)
    side=t.side(row,player);peer='b' if side=='a' else 'a';offer=req.offered_pokemon.id
    if offer:
        try:mon=t.valid_mon(game,db,player,offer,remote=True)
        except t.Invalid:return p.ProposeRemoteTradeOutProto(result=5)
        if offer not in json.loads(row['requests_'+peer]) or not mon.marked_remote_tradable:return p.ProposeRemoteTradeOutProto(result=5)
    new_ids=json.dumps(ids)
    if new_ids!=row['requests_'+side] or offer!=row['offer_'+side]:
        db.execute('UPDATE social_remote_sessions SET requests_'+side+'=?,offer_'+side+'=?,approved_a=0,approved_b=0,nonce=?,expires_ms=? WHERE id=?',(new_ids,offer,secrets.token_hex(24),now_ms()+48*3600000,row['id']))
    return p.ProposeRemoteTradeOutProto(result=1)
def pending(game,db,player,body):
    req=p.GetPendingRemoteTradeProto.FromString(body);expire(db)
    if not f.pair(db,player,req.player_id):return p.GetPendingRemoteTradeOutProto(result=3)
    row=get(db,player,req.player_id)
    if row is None:return p.GetPendingRemoteTradeOutProto(result=6)
    side=t.side(row,player);peer='b' if side=='a' else 'a';out=p.GetPendingRemoteTradeOutProto(result=1,incoming=True)
    for ident in json.loads(row['requests_'+peer]):
        mon=t.owned(db,player,ident)
        if mon:out.requested_pokemon.add().CopyFrom(mon)
    mon=t.owned(db,req.player_id,row['offer_'+peer])
    if mon:out.offered_pokemon.CopyFrom(mon)
    return out
def cancel(game,db,player,body):
    req=p.CancelRemoteTradeProto.FromString(body)
    if not f.pair(db,player,req.player_id):return p.CancelRemoteTradeOutProto(result=3)
    db.execute("UPDATE social_remote_sessions SET state='cancelled' WHERE a=? AND b=? AND state='negotiating'",tuple(sorted((player,req.player_id))));expire(db)
    return p.CancelRemoteTradeOutProto(result=1)
def respond(game,db,player,body):
    req=p.RespondRemoteTradeProto.FromString(body)
    if not req.accept_trade:
        out=cancel(game,db,player,p.CancelRemoteTradeProto(player_id=req.player_id).SerializeToString());return p.RespondRemoteTradeOutProto(result=1 if out.result==1 else 3)
    # A successful reply here would falsely assert a completed trade. Keep the
    # native feature disabled until its final consent can be safely correlated.
    return p.RespondRemoteTradeOutProto(result=7)
