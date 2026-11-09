"""Two-consent native trading. SQLite owns locks, finalization and receipts.

Numeric balance values without an authoritative supplied GM entry are explicit
local configuration defaults; they are not presented as verified official data.
"""
import hashlib,json,math,secrets
from contextlib import closing
from dittobuffer.gameplay.game import p,now_ms,distance
from dittobuffer.gameplay import friends as f
from dittobuffer.core.accounts import principal_session
from dittobuffer.core.management import owned,level_of,add_candy
from dittobuffer.gameplay.species import rules_for,apply_stats
SCHEMA='''
CREATE TABLE IF NOT EXISTS social_trades(id TEXT PRIMARY KEY,a TEXT NOT NULL REFERENCES players(id),b TEXT NOT NULL REFERENCES players(id),created_ms INTEGER NOT NULL,expires_ms INTEGER NOT NULL,state INTEGER NOT NULL DEFAULT 2,joined_a INTEGER NOT NULL DEFAULT 0,joined_b INTEGER NOT NULL DEFAULT 0,session_a TEXT NOT NULL DEFAULT '',session_b TEXT NOT NULL DEFAULT '',mon_a INTEGER NOT NULL DEFAULT 0,mon_b INTEGER NOT NULL DEFAULT 0,hash_a TEXT NOT NULL DEFAULT '',hash_b TEXT NOT NULL DEFAULT '',confirmed_a INTEGER NOT NULL DEFAULT 0,confirmed_b INTEGER NOT NULL DEFAULT 0,token TEXT NOT NULL,remote INTEGER NOT NULL DEFAULT 0,receipt BLOB);
CREATE UNIQUE INDEX IF NOT EXISTS social_active_trade ON social_trades(a,b,remote) WHERE state IN (2,3,4);
CREATE TABLE IF NOT EXISTS social_trade_locks(pokemon_id INTEGER PRIMARY KEY REFERENCES pokemon(id) ON DELETE CASCADE,trade_id TEXT NOT NULL REFERENCES social_trades(id));
CREATE TABLE IF NOT EXISTS social_trade_history(trade_id TEXT PRIMARY KEY REFERENCES social_trades(id),at_ms INTEGER NOT NULL,day TEXT NOT NULL,a TEXT NOT NULL,b TEXT NOT NULL,mon_a INTEGER NOT NULL,mon_b INTEGER NOT NULL,dust INTEGER NOT NULL,lucky INTEGER NOT NULL,special INTEGER NOT NULL,remote INTEGER NOT NULL,data TEXT NOT NULL);
'''
class Invalid(Exception):
    def __init__(self,result):self.result=result
def install(game):
    with closing(game.db.connect()) as db:db.executescript(SCHEMA)
def expire(db):
    db.execute('UPDATE social_trades SET state=0 WHERE state IN (2,3,4) AND expires_ms<=?',(now_ms(),))
    db.execute('DELETE FROM social_trade_locks WHERE trade_id IN (SELECT id FROM social_trades WHERE state NOT IN (2,3,4))')
def locked(game,db,pokemon):
    return f.enabled(game) and db.execute('SELECT 1 FROM social_trade_locks l JOIN social_trades t ON t.id=l.trade_id WHERE l.pokemon_id=? AND t.state IN (2,3,4) AND t.expires_ms>?',(pokemon,now_ms())).fetchone() is not None
def side(row,player):return 'a' if row['a']==player else 'b'
def other(row,player):return row['b'] if row['a']==player else row['a']
def digest(mon):return hashlib.sha256(mon.SerializeToString()).hexdigest()
def candy_award(game,db,mons):
    points=[]
    from s2sphere import CellId,LatLng
    for mon in mons:
        try:
            cell=CellId(mon.captured_s2_cell_id)
            if not cell.is_valid():raise ValueError('No capture cell')
            ll=LatLng.from_point(cell.to_point());points.append((ll.lat().degrees,ll.lng().degrees))
        except (ValueError,AssertionError):
            row=db.execute('SELECT s.lat,s.lng FROM spawns s JOIN pokemon o ON o.spawn_id=s.id WHERE o.id=?',(mon.id,)).fetchone()
            if not row:return 1
            points.append(tuple(row))
    meters=distance(*points[0],*points[1]);limits=game.config.get('social',{}).get('candy_distance_m',[10000,100000])
    return 1+sum(meters>=threshold for threshold in limits)
def near(game,db,a,b):
    rows=[db.execute('SELECT * FROM social_locations WHERE player_id=?',(x,)).fetchone() for x in (a,b)]
    if any(r is None or r['lat'] is None or r['arrival_ms'] is None or now_ms()-r['arrival_ms']>game.config.get('social',{}).get('location_max_age_seconds',120)*1000 for r in rows):return False
    return distance(rows[0]['lat'],rows[0]['lng'],rows[1]['lat'],rows[1]['lng'])<=game.config.get('social',{}).get('trade_range_m',100)
def valid_mon(game,db,player,ident,trade_id='',remote=False):
    if not 0<ident<2**63:raise Invalid(7)
    mon=owned(db,player,ident)
    if mon is None:raise Invalid(7)
    from dittobuffer.gameplay.mega import active
    from dittobuffer.gameplay.gyms import battle_busy
    if mon.is_egg or mon.traded_time_ms or mon.deployed_fort_id or mon.deployed_station_id or mon.is_fusion or mon.is_component or mon.pokemon_display.alignment==1 or active(db,ident) or battle_busy(db,player,[ident]):raise Invalid(7)
    if db.execute('SELECT 1 FROM buddy_active WHERE player_id=? AND pokemon_id=?',(player,ident)).fetchone():raise Invalid(7)
    # Mythicals are excluded conservatively. No undocumented species exception.
    # Official Switch-connection help explicitly permits trading Meltan.
    # Other Mythical exceptions require evidence before enabling them here.
    if rules_for(game,mon)['class']==2 and (remote or mon.pokemon_id!=808) or mon.pokemon_id in game.config.get('social',{}).get('trade_prohibited_species',[]):raise Invalid(7)
    lock=db.execute('SELECT trade_id FROM social_trade_locks WHERE pokemon_id=?',(ident,)).fetchone()
    if lock and lock[0]!=trade_id:raise Invalid(7)
    if remote and now_ms()-mon.creation_time_ms<30*86400000:raise Invalid(7)
    if remote and (mon.pokemon_display.form in (2576,2578) or mon.HasField('iris_social_deployment') and mon.iris_social_deployment.ByteSize()):raise Invalid(7)
    return mon
def registered(game,db,player,mon):
    from dittobuffer.gameplay.pokedex import ensure
    ensure(game,db,player)
    rows=db.execute('SELECT data FROM pokedex_observations WHERE player_id=? AND species=? AND caught=1',(player,mon.pokemon_id))
    for row in rows:
        old=p.PokemonProto.FromString(row[0])
        a,b=old.pokemon_display,mon.pokemon_display
        if (a.form,a.costume,a.shiny,a.location_card.location_card)==(b.form,b.costume,b.shiny,b.location_card.location_card):return True
    return False
def quote(game,db,row):
    pair=f.pair(db,row['a'],row['b'])
    if pair is None:raise Invalid(3)
    mons=[valid_mon(game,db,row[s],row['mon_'+s],row['id'],bool(row['remote'])) for s in ('a','b')]
    special=any(m.pokemon_display.shiny or m.pokemon_display.costume or m.pokemon_display.location_card.location_card or rules_for(game,m)['class'] in (1,2,3) for m in mons)
    unregistered=any(not registered(game,db,row[s],mons[1-i]) for i,s in enumerate(('a','b')))
    special|=unregistered
    if special and pair['points']<1:raise Invalid(7)
    cfg=game.config.get('social',{});tier=f.level(pair['points'])
    base=cfg.get('trade_costs',{'ordinary_registered':100,'ordinary_new':20000,'rare_registered':20000,'rare_new':1000000})
    rare=any(m.pokemon_display.shiny or rules_for(game,m)['class'] in (1,2,3) for m in mons)
    key=('rare' if rare else 'ordinary')+('_new' if unregistered else '_registered')
    cost=math.ceil(base[key]*cfg.get('trade_cost_multipliers',[1,1,.8,.08,.04,.04])[tier])
    for player in (row['a'],row['b']):
        if game.player_stats(game.player(db,player)).level<10:raise Invalid(7)
        if game.player(db,player)['stardust']<cost:raise Invalid(8)
        if db.execute('SELECT COUNT(*) FROM pokemon WHERE player_id=?',(player,)).fetchone()[0]+db.execute('SELECT COUNT(*) FROM eggs WHERE player_id=?',(player,)).fetchone()[0]>game.capacity(db,player,'pokemon_capacity'):raise Invalid(14)
        today=db.execute('SELECT COUNT(*),COALESCE(SUM(special),0) FROM social_trade_history WHERE day=? AND remote=0 AND (a=? OR b=?)',(f.day(game),player,player)).fetchone()
        if not row['remote'] and (today[0]>=cfg.get('trade_daily',100) or special and today[1]>=cfg.get('special_trade_daily',1)):raise Invalid(15)
    if not row['remote'] and not near(game,db,row['a'],row['b']):raise Invalid(7)
    return mons,cost,bool(special),bool(pair['lucky'])
def preview_mon(game,db,mon,receiver,floor):
    level=min(level_of(db,mon),game.player_stats(game.player(db,receiver)).level+2)
    low=p.PokemonProto();low.CopyFrom(mon);high=p.PokemonProto();high.CopyFrom(mon)
    for field in ('individual_attack','individual_defense','individual_stamina'):setattr(low,field,floor);setattr(high,field,15)
    apply_stats(low,rules_for(game,mon),level);apply_stats(high,rules_for(game,mon),level)
    return p.TradingPokemonProto(pokemon_id=mon.id,pokedex_entry_number=mon.pokemon_id,original_cp=mon.cp,adjusted_cp_min=low.cp,adjusted_cp_max=high.cp,original_stamina=mon.max_stamina,adjusted_stamina_min=low.max_stamina,adjusted_stamina_max=high.max_stamina,move1=mon.move1,move2=mon.move2,pokemon_display=mon.pokemon_display,pokeball=mon.pokeball,creation_time_ms=mon.creation_time_ms,individual_attack=mon.individual_attack,individual_defense=mon.individual_defense,individual_stamina=mon.individual_stamina,nickname=mon.nickname,height_m=mon.height_m,weight_kg=mon.weight_kg)
def view(game,db,row,player):
    out=p.TradingProto(state=row['state'],expiration_ms=row['expires_ms'],transaction_log=row['token'])
    pair=f.pair(db,row['a'],row['b'])
    if pair:out.friendship_level_data.CopyFrom(f.level_data(game,pair));out.is_lucky_friend_trading=bool(pair['lucky'])
    try:mons,cost,special,lucky=quote(game,db,row)
    except Invalid:mons=None;cost=0;special=False;lucky=False
    out.is_special_trading=special
    floor=12 if lucky else [0,1,2,3,5,5][f.level(pair['points'])] if pair else 0
    receipt=json.loads(row['receipt']) if row['receipt'] else {}
    for key,ident in [('player',player),('friend',other(row,player))]:
        value=getattr(out,key);s=side(row,ident)
        value.player_id=ident;value.nia_account_id=ident;value.public_profile.CopyFrom(f.profile(game,db,ident));value.has_confirmed=bool(row['confirmed_'+s]);value.can_afford_trading=game.player(db,ident)['stardust']>=cost
        value.special_trade_limit=game.config.get('social',{}).get('special_trade_daily',1)
        if cost:value.price.loot_item.add(stardust=True,count=cost)
        if row['mon_'+s]:
            mon=owned(db,ident,row['mon_'+s])
            if mon:value.trading_pokemon.CopyFrom(preview_mon(game,db,mon,other(row,ident),floor))
        if receipt:
            # Each TradingPlayer retains the Pokémon that participant OFFERED.
            # Its final blob now belongs to the opposite participant. Android
            # opens Friend.TradingPokemon.TradedPokemon after the animation.
            receiver=other(row,ident);mon=p.PokemonProto.FromString(bytes.fromhex(receipt[receiver]));value.trading_pokemon.CopyFrom(preview_mon(game,db,mon,receiver,12 if mon.is_lucky else floor));value.trading_pokemon.traded_pokemon.CopyFrom(mon)
        value.bonus.loot_item.add(pokemon_candy=(rules_for(game,mons[0 if s=='a' else 1])['family'] if mons else 1),count=candy_award(game,db,mons) if mons else 1)
    return out
def commit(game,db,row):
    mons,cost,special,lucky=quote(game,db,row)
    if any(digest(m)!=row['hash_'+s] for m,s in zip(mons,('a','b'))):raise Invalid(12)
    cfg=game.config.get('social',{});chance=cfg.get('lucky_rate',.05)
    oldest=max(max(0,now_ms()-mon.creation_time_ms)/86400000 for mon in mons)
    # Optional age bands are explicit local balance; official help confirms the
    # age effect, but publishes no numeric probability table.
    for band in cfg.get('lucky_age_rates',[]):
        if oldest>=band['days']:chance=max(chance,band['rate'])
    lucky=lucky or secrets.randbelow(1000000)<chance*1000000;candy=candy_award(game,db,mons)
    pair=f.pair(db,row['a'],row['b']);floor=12 if lucky else [0,1,2,3,5,5][f.level(pair['points'])]
    receipt={};details=[]
    for s,mon in zip(('a','b'),mons):
        owner=row[s];receiver=other(row,owner);new=p.PokemonProto();new.CopyFrom(mon)
        target=min(level_of(db,mon),game.player_stats(game.player(db,receiver)).level+2)
        for field in ('individual_attack','individual_defense','individual_stamina'):setattr(new,field,floor+secrets.randbelow(16-floor))
        new.is_lucky=lucky;new.traded_time_ms=now_ms();new.original_owner_nickname=game.player(db,owner)['name'];new.trading_original_owner_hash=int.from_bytes(hashlib.sha256(owner.encode()).digest()[:4],'little',signed=True)
        new.favorite=False;del new.pokemon_tag_ids[:]
        apply_stats(new,rules_for(game,new),target);new.stamina=new.max_stamina
        db.execute('UPDATE pokemon SET player_id=?,data=? WHERE id=? AND player_id=?',(receiver,new.SerializeToString(),new.id,owner))
        db.execute('UPDATE pokemon_levels SET level=? WHERE pokemon_id=?',(target,new.id))
        db.execute('UPDATE players SET stardust=stardust-? WHERE id=?',(cost,owner));game.touch(db,owner);game.touch(db,receiver)
        db.execute('INSERT OR REPLACE INTO pokemon_tombstones VALUES(?,?,?)',(owner,new.id,game.player(db,owner)['revision']))
        add_candy(db,owner,rules_for(game,mon)['family'],candy)
        from dittobuffer.gameplay.pokedex import record
        record(game,db,receiver,'trade:'+row['id'],new,True);receipt[receiver]=new.SerializeToString().hex();details.append({'from':owner,'to':receiver,'pokemon':new.id,'old':mon.SerializeToString().hex(),'new':new.SerializeToString().hex()})
    from dittobuffer.gameplay.trainer import event
    kilometres=origin_distance(db,mons)
    for receiver,blob in receipt.items():event(game,db,receiver,'trade:'+row['id'],'trade',{'pokemon':blob,'lucky':bool(lucky),'distance_km':kilometres if kilometres is not None else -1})
    db.execute('INSERT INTO social_trade_history VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(row['id'],now_ms(),f.day(game),row['a'],row['b'],mons[0].id,mons[1].id,cost,int(lucky),int(special and not row['remote']),row['remote'],json.dumps(details)))
    if pair['lucky']:db.execute('UPDATE social_pairs SET lucky=0 WHERE a=? AND b=?',(row['a'],row['b']))
    f.interact(game,db,row['a'],row['b'],'trade','trade:'+row['id'])
    db.execute('UPDATE social_trades SET state=5,receipt=? WHERE id=?',(json.dumps(receipt),row['id']));db.execute('DELETE FROM social_trade_locks WHERE trade_id=?',(row['id'],))
def handle(game,db,player,body,kind):
    types={'open':'OpenTrading','get':'GetTrading','update':'UpdateTrading','confirm':'ConfirmTrading','cancel':'CancelTrading'};name=types[kind]
    req=getattr(p,name+'Proto').FromString(body);out=getattr(p,name+'OutProto')(result=1)
    if not f.enabled(game):out.result=2;return out
    other_id=req.player_id
    if other_id==player or not f.pair(db,player,other_id):out.result=3;return out
    expire(db);a,b=sorted((player,other_id));session=principal_session.get() or ''
    row=db.execute('SELECT * FROM social_trades WHERE a=? AND b=? AND remote=0 ORDER BY created_ms DESC,rowid DESC LIMIT 1',(a,b)).fetchone()
    if kind=='open':
        if not near(game,db,player,other_id):out.result=10;return out
        if game.player_stats(game.player(db,player)).level<10:out.result=11;return out
        if game.player_stats(game.player(db,other_id)).level<10:out.result=16;return out
        if row is None or row['state'] not in (2,3,4):
            ident=secrets.token_hex(16);db.execute('INSERT INTO social_trades(id,a,b,created_ms,expires_ms,token) VALUES(?,?,?,?,?,?)',(ident,a,b,now_ms(),now_ms()+300000,secrets.token_hex(24)))
            row=db.execute('SELECT * FROM social_trades WHERE id=?',(ident,)).fetchone()
        s=side(row,player)
        if row['session_'+s] and row['session_'+s]!=session:
            # Explicit authenticated re-entry supersedes this participant's old
            # connection. Fence delayed callbacks and require both approvals anew.
            db.execute('UPDATE social_trades SET confirmed_a=0,confirmed_b=0,token=? WHERE id=?',(secrets.token_hex(24),row['id']))
        db.execute('UPDATE social_trades SET joined_'+s+'=1,session_'+s+'=? WHERE id=?',(session,row['id']))
        db.execute('UPDATE social_trades SET state=3 WHERE id=? AND joined_a=1 AND joined_b=1',(row['id'],))
    elif row is None or row['state']==0:out.result=5;return out
    else:
        s=side(row,player)
        if not row['joined_'+s] or row['session_'+s]!=session:out.result=5;return out
        if kind=='cancel':
            if row['state']==5:out.result=5;return out
            db.execute('UPDATE social_trades SET state=0 WHERE id=?',(row['id'],));db.execute('DELETE FROM social_trade_locks WHERE trade_id=?',(row['id'],))
        elif kind=='update':
            if row['state'] not in (3,4):out.result=5;return out
            try:mon=valid_mon(game,db,player,req.pokemon_id,row['id'])
            except Invalid:out.result=7;return out
            db.execute('DELETE FROM social_trade_locks WHERE trade_id=? AND pokemon_id=?',(row['id'],row['mon_'+s]))
            db.execute('INSERT OR IGNORE INTO social_trade_locks VALUES(?,?)',(mon.id,row['id']))
            db.execute('UPDATE social_trades SET mon_'+s+'=?,hash_'+s+'=?,confirmed_a=0,confirmed_b=0,state=3,token=? WHERE id=?',(mon.id,digest(mon),secrets.token_hex(24),row['id']))
        elif kind=='confirm':
            if not secrets.compare_digest(req.transaction_log,row['token']):out.result=12;return out
            if row['state']!=5:
                try:
                    quote(game,db,row)
                    # Savepoint makes partial failure roll back both consents
                    # and every collection/resource mutation, without losing
                    # the enclosing RPC receipt transaction.
                    db.execute('SAVEPOINT trade_commit')
                    db.execute('UPDATE social_trades SET confirmed_'+s+'=1,state=4 WHERE id=?',(row['id'],))
                    updated=db.execute('SELECT * FROM social_trades WHERE id=?',(row['id'],)).fetchone()
                    if updated['confirmed_a'] and updated['confirmed_b']:commit(game,db,updated)
                    db.execute('RELEASE trade_commit')
                except Invalid as error:
                    if db.in_transaction:
                        try:db.execute('ROLLBACK TO trade_commit');db.execute('RELEASE trade_commit')
                        except Exception:pass
                    out.result=error.result;return out
    row=db.execute('SELECT * FROM social_trades WHERE id=?',(row['id'],)).fetchone();out.trading.CopyFrom(view(game,db,row,player));return out


def origin_distance(db,mons):
    """Capture-origin distance; missing historical evidence never satisfies a gate."""
    from s2sphere import CellId,LatLng
    points=[]
    for mon in mons:
        try:
            cell=CellId(mon.captured_s2_cell_id)
            if not cell.is_valid():raise ValueError('Unknown origin')
            ll=LatLng.from_point(cell.to_point());points.append((ll.lat().degrees,ll.lng().degrees))
        except (ValueError,AssertionError):
            row=db.execute('SELECT s.lat,s.lng FROM spawns s JOIN pokemon o ON o.spawn_id=s.id WHERE o.id=?',(mon.id,)).fetchone()
            if not row:return None
            points.append(tuple(row))
    return distance(*points[0],*points[1])/1000
