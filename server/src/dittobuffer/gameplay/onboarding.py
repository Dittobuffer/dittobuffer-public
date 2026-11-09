"""Fresh-trainer native introduction; old accounts keep their established flow.

The installed classic starter scene owns its local map/throw animation and sends
EncounterTutorialComplete. Its single server entitlement is not a wild spawn or
a raid reward. Owned Pokemon, inventory, Dex and journal use existing stores.
"""
from contextlib import closing
import re,secrets
from dittobuffer.gameplay.game import p,now_ms

SCHEMA="""
CREATE TABLE IF NOT EXISTS onboarding_players(player_id TEXT PRIMARY KEY REFERENCES players(id),created_ms INTEGER NOT NULL,avatar_saved INTEGER NOT NULL DEFAULT 0,nickname_claimed INTEGER NOT NULL DEFAULT 0,starter_id INTEGER,starter_response BLOB,completed_ms INTEGER NOT NULL DEFAULT 0);
"""
CORE={0,1,2,3,4,7,24}


def install(game):
    cfg={'enabled':False,'starter_species':[1,4,7,25],'starter_level':1,'starter_iv':10}|game.config.get('onboarding',{})
    if type(cfg['enabled']) is not bool:raise ValueError('Invalid onboarding flag')
    if not isinstance(cfg['starter_species'],list) or not cfg['starter_species'] or any(type(v) is not int or v not in (1,4,7,25) or v not in game.species for v in cfg['starter_species']):raise ValueError('Unsupported native starter')
    if type(cfg['starter_level']) is not int or not 1<=cfg['starter_level']<=5 or type(cfg['starter_iv']) is not int or not 0<=cfg['starter_iv']<=15:raise ValueError('Invalid starter configuration')
    game.onboarding=cfg
    with closing(game.db.connect()) as db:db.executescript(SCHEMA)


def created(game,db,player):
    if game.config.get('onboarding',{}).get('enabled'):
        db.execute('INSERT INTO onboarding_players(player_id,created_ms) VALUES(?,?)',(player,now_ms()))


def state(db,player):return db.execute('SELECT * FROM onboarding_players WHERE player_id=?',(player,)).fetchone()


def completed_flags(game,db,player):
    saved={r[0] for r in db.execute('SELECT value FROM tutorials WHERE player_id=?',(player,))}
    row=state(db,player)
    if not row:return {0,1,3,4,7}|saved
    # Local family registration replaces production account/legal navigation.
    # This flag is UI readiness, not a record of accepting third-party terms.
    done={0,2}|saved
    if row['avatar_saved']:done.add(1)
    if row['starter_id']:done.update((3,24))
    if row['nickname_claimed']:done.add(4)
    if row['completed_ms']:done.add(7)
    return done


def avatar_saved(game,db,player):
    if db.execute('UPDATE onboarding_players SET avatar_saved=1 WHERE player_id=? AND avatar_saved=0',(player,)).rowcount:game.touch(db,player)


def mark(game,db,player,values):
    known=set(p.TutorialCompletion.values())
    if any(v not in known for v in values):return False
    row=state(db,player)
    if row:
        ready={0,1,2}|({3,24} if row['starter_id'] else set())|({4} if row['nickname_claimed'] else set())
        if row['avatar_saved'] and row['starter_id'] and row['nickname_claimed']:ready.add(7)
        if any(v in CORE and v not in ready for v in values):return False
        if 7 in values:db.execute('UPDATE onboarding_players SET completed_ms=CASE WHEN completed_ms=0 THEN ? ELSE completed_ms END WHERE player_id=?',(now_ms(),player))
    # Native sends AVATAR_SELECTION concurrently with SetNeutralAvatar. Acknowledge
    # that UI intent, but persist/expose completion only after the validated save.
    persisted=[v for v in values if not (row and v==1 and not row['avatar_saved'])]
    db.executemany('INSERT OR IGNORE INTO tutorials VALUES(?,?)',[(player,v) for v in persisted]);game.touch(db,player)
    return True


def codename(game,db,player,body):
    req=p.ClaimCodenameRequestProto.FromString(body);name=req.codename.strip();row=state(db,player)
    if not re.fullmatch(r'[A-Za-z0-9]{3,15}',name):return p.CodenameResultProto(status=3,codename=name,is_assignable=False)
    if any(r['id']!=player and r['name'].casefold()==name.casefold() for r in db.execute('SELECT id,name FROM players')):
        out=p.CodenameResultProto(status=2,codename=name,is_assignable=False)
        if req.generate_suggested_codenames:
            for _ in range(3):out.suggested_codenames.append(name[:10]+str(secrets.randbelow(90000)+10000))
        return out
    if req.generate_suggested_codenames:return p.CodenameResultProto(status=1,codename=name,is_assignable=True)
    current=game.player(db,player)['name']
    if not row or row['nickname_claimed']:
        return p.CodenameResultProto(status=4 if current.casefold()==name.casefold() else 5,codename=current,is_assignable=False)
    db.execute('UPDATE players SET name=? WHERE id=?',(name,player));db.execute('UPDATE onboarding_players SET nickname_claimed=1 WHERE player_id=?',(player,));game.touch(db,player)
    out=p.CodenameResultProto(status=1,codename=name,is_assignable=True)
    out.updated_player.CopyFrom(game.rpc_get_player(db,player,b'').player)
    return out


def starter(game,db,player,body):
    req=p.EncounterTutorialCompleteProto.FromString(body);row=state(db,player)
    if not row or not row['avatar_saved'] or req.pokedex_id not in game.onboarding['starter_species']:return p.EncounterTutorialCompleteOutProto(result=2)
    if row['starter_response']:return p.EncounterTutorialCompleteOutProto.FromString(row['starter_response'])
    from dittobuffer.gameplay.eggs import count,storage_id
    if db.execute('SELECT COUNT(*) FROM pokemon WHERE player_id=?',(player,)).fetchone()[0]+count(db,player)>=game.capacity(db,player,'pokemon_capacity'):return p.EncounterTutorialCompleteOutProto(result=2)
    from dittobuffer.gameplay.species import make_pokemon
    from dittobuffer.core.management import add_candy
    species=req.pokedex_id;cfg=game.onboarding;stamp=now_ms();spawn=secrets.randbits(61) or 1
    mon=make_pokemon(p,game.species[species],species,cfg['starter_level'],[cfg['starter_iv']]*3)
    mon.id=storage_id(db);mon.pokeball=1;mon.creation_time_ms=stamp
    mon.origin_detail.wild_detail.caught_in_wild=True
    loc=db.execute('SELECT lat,lng FROM admin_locations WHERE player_id=?',(player,)).fetchone()
    lat,lng=(loc[0],loc[1]) if loc else (0,0)
    db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,0)',(spawn,'starter:'+secrets.token_hex(16),species,lat,lng))
    db.execute('INSERT INTO spawn_attributes VALUES(?,?,?)',(spawn,mon.SerializeToString(),cfg['starter_level']))
    db.execute('INSERT INTO pokemon VALUES(?,?,?,?,?)',(mon.id,player,spawn,species,mon.SerializeToString()))
    db.execute('INSERT INTO pokemon_levels VALUES(?,?)',(mon.id,cfg['starter_level']))
    from dittobuffer.gameplay.reward_calendar import catch as catch_rewards
    # The tutorial completion carries no throw-quality/attempt telemetry.
    rows=catch_rewards(game,db,player,p.CatchPokemonProto(hit_pokemon=True,pokeball=1),mon,0)
    xp,dust,candy=sum(v[1] for v in rows),sum(v[3] for v in rows),sum(v[2] for v in rows)
    db.execute('UPDATE players SET xp=xp+?,stardust=stardust+?,captures=captures+1 WHERE id=?',(xp,dust,player))
    add_candy(db,player,game.species[species]['family'],candy)
    from dittobuffer.gameplay.pokedex import record
    record(game,db,player,'spawn:'+str(spawn),mon,True)
    from dittobuffer.gameplay.trainer import event
    event(game,db,player,'starter:'+player,'catch',dict(pokemon=mon.SerializeToString().hex(),xp=xp,dust=dust,candy=candy))
    from dittobuffer.gameplay.research import progress
    progress(game,db,player,'catch')
    out=p.EncounterTutorialCompleteOutProto(result=1,pokemon=mon)
    for activity,exp,amount,stardust in rows:
        out.scores.activity_type.append(activity);out.scores.exp.append(exp);out.scores.candy.append(amount);out.scores.stardust.append(stardust)
    db.execute('UPDATE onboarding_players SET starter_id=?,starter_response=? WHERE player_id=?',(mon.id,out.SerializeToString(),player));game.touch(db,player)
    return out
