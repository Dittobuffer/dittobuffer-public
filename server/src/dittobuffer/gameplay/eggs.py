"""Persistent egg/incubator lifecycle in the existing gameplay transaction."""
import hashlib
import math
import random
import secrets
from dittobuffer.gameplay.game import p,now_ms,distance,ROOT
from dittobuffer.gameplay.species import make_pokemon,pool_rules

def validate(game):
    cfg=game.config.get('eggs',{})
    if type(cfg.get('capacity',9)) is not int or not 1<=cfg.get('capacity',9)<=12:raise ValueError('Invalid egg capacity')
    if not 0<=cfg.get('stop_drop_probability',0.5)<=1:raise ValueError('Invalid egg probability')
    for pool in cfg.get('pools',[]):
        if pool['km'] not in (2,5,10) or not pool.get('species'):raise ValueError('Invalid egg pool')
        for entry in pool['species']:pool_rules(game,entry)
        if not math.isfinite(pool.get('weight',1)) or pool.get('weight',1)<=0:raise ValueError('Invalid egg pool weight')
    for key,default in [('xp',200),('stardust',100),('candy',5),('initial_limited_incubators',0)]:
        if type(cfg.get(key,default)) is not int or cfg.get(key,default)<0:raise ValueError('Invalid egg reward/grant')
    walking=game.config.get('walking',{})
    for key,default,lo,hi in [('max_accuracy_m',25,1,100),('max_speed_mps',2.8,0.5,5)]:
        v=walking.get(key,default)
        if type(v) not in (int,float) or not math.isfinite(v) or not lo<=v<=hi:raise ValueError('Invalid walking rule')
    if type(walking.get('allow_debug_fixture',False)) is not bool:raise ValueError('Invalid fixture flag')
    if walking.get('allow_debug_fixture') and (not game.config.get('profile','').startswith(('m5-','m10-')) or not game.db.path.resolve().is_relative_to(ROOT/'builds')):raise ValueError('Debug movement requires an isolated M5/M10 fixture database inside builds')

def storage_id(db):
    return db.execute('SELECT MAX(value)+1 FROM (SELECT COALESCE(MAX(id),0) value FROM pokemon UNION ALL SELECT COALESCE(MAX(id),0) FROM eggs UNION ALL SELECT COALESCE(MAX(egg_id),0) FROM hatches UNION ALL SELECT COALESCE(MAX(seq),0) FROM sqlite_sequence WHERE name=\'pokemon\')').fetchone()[0]

def count(db,player):return db.execute('SELECT COUNT(*) FROM eggs WHERE player_id=?',(player,)).fetchone()[0]

def ensure(game,db,player):
    db.execute('INSERT OR IGNORE INTO walking(player_id) VALUES(?)',(player,))
    changed=False
    if not db.execute('SELECT 1 FROM incubators WHERE player_id=? AND item=901',(player,)).fetchone():
        grant(db,player,901,1);changed=True
    if not db.execute('SELECT 1 FROM initial_incubator_grants WHERE player_id=?',(player,)).fetchone():
        grant(db,player,902,game.config.get('eggs',{}).get('initial_limited_incubators',0))
        db.execute('INSERT INTO initial_incubator_grants VALUES(?)',(player,));changed=True
    if changed:game.touch(db,player)

def grant(db,player,item,count):
    if item==901 and db.execute('SELECT 1 FROM incubators WHERE player_id=? AND item=901',(player,)).fetchone():return
    for _ in range(1 if item==901 else count):
        db.execute('INSERT INTO incubators(id,player_id,item,uses) VALUES(?,?,?,?)',('dittobuffer-'+secrets.token_hex(12),player,item,-1 if item==901 else 3))

def incubator_message(row):
    return p.EggIncubatorProto(item_id=row['id'],item=row['item'],incubator_type=1,uses_remaining=row['uses'],
        pokemon_id=row['egg_id'] or 0,start_km_walked=row['start_km'],target_km_walked=row['target_km'])

def egg_message(game,db,row):
    egg=p.PokemonProto(id=row['id'],is_egg=True,egg_km_walked_target=row['distance_km'],
        creation_time_ms=row['created_ms'],from_fort=True,egg_slot_type=0)
    if row['incubator_id']:
        inc=db.execute('SELECT * FROM incubators WHERE id=?',(row['incubator_id'],)).fetchone()
        egg.egg_incubator_id=inc['id'];egg.egg_km_walked_start=inc['start_km']
        # PokemonProto.EggKmTotal (0x88F9048) subtracts start from target.
        # Both fields are absolute once assigned, just like the incubator.
        egg.egg_km_walked_target=inc['target_km']
    egg.origin_detail.egg_detail.received_time_ms=row['created_ms']
    for pool in game.config.get('eggs',{}).get('pools',[]):
        if pool['km']==row['distance_km']:
            for definition in pool['species']:
                rules=pool_rules(game,definition)
                entry=egg.egg_distribution.egg_distribution.add(rarity=1,pokemon_id=rules['species'])
                entry.pokemon_display.form=rules['form']
                if isinstance(definition,dict):entry.pokemon_display.costume=definition.get('costume',0)
    return egg

def inventory(game,db,player):
    ensure(game,db,player)
    singleton=p.HoloInventoryItemProto()
    singleton.egg_incubators.SetInParent()
    for row in db.execute('SELECT * FROM incubators WHERE player_id=? AND (uses!=0 OR egg_id IS NOT NULL) ORDER BY item,id',(player,)):
        singleton.egg_incubators.egg_incubator.add().CopyFrom(incubator_message(row))
    values=[singleton]
    for row in db.execute('SELECT * FROM eggs WHERE player_id=? ORDER BY id',(player,)):
        value=p.HoloInventoryItemProto();value.pokemon.CopyFrom(egg_message(game,db,row));values.append(value)
    return values

def award(game,db,player,fort):
    cfg=game.config.get('eggs',{});pools=cfg.get('pools',[])
    if not pools or count(db,player)>=cfg.get('capacity',9):return None
    if count(db,player)+db.execute('SELECT COUNT(*) FROM pokemon WHERE player_id=?',(player,)).fetchone()[0]>=game.capacity(db,player,'pokemon_capacity'):return None
    if secrets.randbelow(1000000)>=cfg.get('stop_drop_probability',0.5)*1000000:return None
    identifier=storage_id(db);rng=random.Random(hashlib.sha256(f'egg:{identifier}:{player}'.encode()).digest())
    pool=rng.choices(pools,weights=[v.get('weight',1) for v in pools])[0];definition=rng.choice(pool['species'])
    rules=pool_rules(game,definition);species=rules['species'];definition={} if type(definition) is int else definition
    level=10
    from dittobuffer.gameplay.species import choose_gender
    shiny=rng.random()<definition.get('shiny_rate',game.config.get('shiny_rate',0)) and (species,rules['form']) in game.shiny_eligible
    value=make_pokemon(p,rules,species,level,[rng.randint(10,15) for _ in range(3)],shiny,definition.get('gender') or choose_gender(game,species,rng),costume=definition.get('costume',0))
    from dittobuffer.gameplay.pokemon_sizes import assign as assign_size
    assign_size(game,value,definition,rng)
    from dittobuffer.gameplay.backgrounds import assign as assign_background
    assign_background(game,value,rng,'egg',definition.get('background',0))
    value.id=identifier;value.creation_time_ms=now_ms();value.hatched_from_egg=True
    db.execute('INSERT INTO eggs VALUES(?,?,?,?,?,?,NULL)',(identifier,player,pool['km'],value.SerializeToString(),now_ms(),fort['id']))
    game.touch(db,player)
    return egg_message(game,db,db.execute('SELECT * FROM eggs WHERE id=?',(identifier,)).fetchone())

def assign(game,db,player,body):
    ensure(game,db,player);req=p.UseItemEggIncubatorProto.FromString(body)
    inc=db.execute('SELECT * FROM incubators WHERE id=? AND player_id=?',(req.item_id,player)).fetchone()
    if not inc:return p.UseItemEggIncubatorOutProto(result=2)
    if inc['egg_id']:
        return p.UseItemEggIncubatorOutProto(result=1 if inc['egg_id']==req.pokemond_id else 5,egg_incubator=incubator_message(inc))
    egg=db.execute('SELECT * FROM eggs WHERE id=? AND player_id=?',(req.pokemond_id,player)).fetchone()
    if not egg:
        is_owned=db.execute('SELECT 1 FROM pokemon WHERE id=? AND player_id=?',(req.pokemond_id,player)).fetchone()
        return p.UseItemEggIncubatorOutProto(result=4 if is_owned else 3)
    if egg['incubator_id']:return p.UseItemEggIncubatorOutProto(result=6)
    if inc['uses']==0:return p.UseItemEggIncubatorOutProto(result=7)
    start=db.execute('SELECT total_m/1000 FROM walking WHERE player_id=?',(player,)).fetchone()[0]
    db.execute('UPDATE incubators SET egg_id=?,start_km=?,target_km=? WHERE id=?',(egg['id'],start,start+egg['distance_km'],inc['id']))
    db.execute('UPDATE eggs SET incubator_id=? WHERE id=?',(inc['id'],egg['id']))
    game.touch(db,player)
    return p.UseItemEggIncubatorOutProto(result=1,egg_incubator=incubator_message(db.execute('SELECT * FROM incubators WHERE id=?',(inc['id'],)).fetchone()))

def hatch_ready(game,db,player):
    total=db.execute('SELECT total_m/1000 FROM walking WHERE player_id=?',(player,)).fetchone()[0]
    cfg=game.config.get('eggs',{})
    for inc in db.execute('SELECT * FROM incubators WHERE player_id=? AND egg_id IS NOT NULL AND target_km<=?',(player,total)).fetchall():
        egg=db.execute('SELECT * FROM eggs WHERE id=?',(inc['egg_id'],)).fetchone()
        value=p.PokemonProto.FromString(egg['data']);xp=cfg.get('xp',200);candy=cfg.get('candy',5);dust=cfg.get('stardust',100)
        value.egg_km_walked_start=inc['start_km'];value.egg_km_walked_target=inc['target_km']
        value.origin_detail.egg_detail.received_time_ms=egg['created_ms']
        value.origin_detail.egg_detail.hatched_time_ms=now_ms()
        # The existing owned table requires a durable origin reference. Egg
        # origins have no map generation and can never appear as wild spawns.
        origin=-egg['id']
        db.execute('INSERT INTO spawns VALUES(?,?,?,0,0,0)',(origin,'egg-origin',value.pokemon_id))
        db.execute('INSERT INTO pokemon(id,player_id,spawn_id,species,data) VALUES(?,?,?,?,?)',(egg['id'],player,origin,value.pokemon_id,value.SerializeToString()))
        db.execute('INSERT INTO pokemon_levels VALUES(?,10)',(egg['id'],))
        from dittobuffer.gameplay.pokedex import record
        record(game,db,player,'hatch:'+str(egg['id']),value,True)
        db.execute('INSERT INTO hatches VALUES(?,?,?,?,?,?,?,0)',(egg['id'],player,value.SerializeToString(),xp,candy,dust,egg['distance_km']))
        db.execute('UPDATE incubators SET egg_id=NULL,start_km=0,target_km=0,uses=CASE WHEN uses>0 THEN uses-1 ELSE uses END WHERE id=?',(inc['id'],))
        db.execute('DELETE FROM eggs WHERE id=?',(egg['id'],))
        family=game.species[value.pokemon_id]['family']
        db.execute('INSERT INTO candy VALUES(?,?,?) ON CONFLICT(player_id,family) DO UPDATE SET count=count+excluded.count',(player,family,candy))
        db.execute('UPDATE players SET xp=xp+?,stardust=stardust+? WHERE id=?',(xp,dust,player))
        db.execute('UPDATE walking SET hatches=hatches+1 WHERE player_id=?',(player,))
        from dittobuffer.gameplay.trainer import event
        event(game,db,player,'hatch:'+str(egg['id']),'hatch',dict(pokemon=value.SerializeToString().hex(),xp=xp,candy=candy,dust=dust,km=egg['distance_km']))
        game.touch(db,player)

def hatched(game,db,player,body):
    ensure(game,db,player)
    hatch_ready(game,db,player)
    out=p.GetHatchedEggsOutProto(success=True)
    for row in db.execute('SELECT * FROM hatches WHERE player_id=? AND reported=0 ORDER BY egg_id',(player,)):
        out.pokemon_id.append(row['egg_id']);out.exp_awarded.append(row['xp']);out.candy_awarded.append(row['candy'])
        out.stardust_awarded.append(row['dust']);out.egg_km_walked.append(row['distance_km'])
        out.hatched_pokemon.add().ParseFromString(row['data']);out.xl_candy_awarded.append(0)
    db.execute('UPDATE hatches SET reported=1 WHERE player_id=? AND reported=0',(player,))
    return out

def sample(game,db,player,fix):
    if not fix:return
    ensure(game,db,player)
    try:
        lat=float(fix['lat']);lng=float(fix['lng']);acc=float(fix['accuracy']);stamp=round(float(fix['time'])*1000);status=int(fix['provider'])
    except (ValueError,KeyError,TypeError,OverflowError):return
    now=now_ms();cfg=game.config.get('walking',{})
    if not all(math.isfinite(v) for v in (lat,lng,acc)) or not -85<=lat<=85 or not -180<=lng<=180:return
    if status!=3 or not 0<acc<=cfg.get('max_accuracy_m',25) or not now-30000<=stamp<=now+2000:return
    if fix.get('source')!='device' and not (fix.get('source')=='debug' and game.config.get('profile','').startswith(('m5-','m10-')) and cfg.get('allow_debug_fixture',False) and game.db.path.resolve().is_relative_to(ROOT/'builds')):return
    old=db.execute('SELECT * FROM walking WHERE player_id=?',(player,)).fetchone()
    if old['seen_ms'] is not None and stamp<=old['seen_ms']:return
    db.execute('UPDATE walking SET seen_ms=? WHERE player_id=?',(stamp,player))
    if old['fix_ms'] is None or now-(old['arrival_ms'] or 0)>120000 or stamp-old['fix_ms']>120000:
        db.execute('UPDATE walking SET lat=?,lng=?,fix_ms=?,arrival_ms=? WHERE player_id=?',(lat,lng,stamp,now,player));return
    elapsed=min((stamp-old['fix_ms'])/1000,(now-(old['arrival_ms'] or now))/1000+2)
    moved=distance(old['lat'],old['lng'],lat,lng)
    if elapsed<5 or moved<max(5,acc*1.5):return
    if moved/elapsed>cfg.get('max_speed_mps',2.8):return
    db.execute('UPDATE walking SET total_m=total_m+?,lat=?,lng=?,fix_ms=?,arrival_ms=? WHERE player_id=?',(moved,lat,lng,stamp,now,player))
    from dittobuffer.gameplay.research import progress
    before=db.execute('SELECT total_m FROM walking WHERE player_id=?',(player,)).fetchone()[0]-moved
    kilometres=int((before+moved)//1000)-int(before//1000)
    if kilometres:progress(game,db,player,'walk',kilometres)
    from dittobuffer.gameplay.reward_calendar import walked
    walked(game,db,player,moved)
    from dittobuffer.gameplay.buddy import walk
    walk(game,db,player,moved)
    # Keep a ready egg/incubator visible until GET_HATCHED_EGGS, otherwise
    # Android loses the completed egg before it can schedule its hatch UI.
    game.touch(db,player)
