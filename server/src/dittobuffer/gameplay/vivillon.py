"""Persistent regional pin credits and native, recoverable Scatterbug rewards."""
import json,math,secrets
from contextlib import closing
from dittobuffer.gameplay.game import p,now_ms,distance
from dittobuffer.gameplay.world_postcards import PATTERNS,registry

SCHEMA='''
CREATE TABLE IF NOT EXISTS vivillon_regions(player TEXT NOT NULL REFERENCES players(id),region INTEGER NOT NULL,rank INTEGER NOT NULL DEFAULT 0,progress INTEGER NOT NULL DEFAULT 0,goal INTEGER NOT NULL DEFAULT 3,pending INTEGER,last_gift INTEGER NOT NULL DEFAULT 0,updated_ms INTEGER NOT NULL,PRIMARY KEY(player,region));
CREATE TABLE IF NOT EXISTS vivillon_pin_credits(player TEXT NOT NULL REFERENCES players(id),gift INTEGER NOT NULL REFERENCES social_gifts(id),region INTEGER NOT NULL,counted INTEGER NOT NULL,reason TEXT NOT NULL,day TEXT NOT NULL,own_gift INTEGER NOT NULL,created_ms INTEGER NOT NULL,PRIMARY KEY(player,gift));
CREATE TABLE IF NOT EXISTS vivillon_entitlements(id INTEGER PRIMARY KEY,player TEXT NOT NULL REFERENCES players(id),region INTEGER NOT NULL,ordinal INTEGER NOT NULL,spawn INTEGER UNIQUE NOT NULL REFERENCES spawns(id),created_ms INTEGER NOT NULL,caught_pokemon INTEGER,UNIQUE(player,region,ordinal));
'''

def enabled(game):return game.config.get('vivillon',{}).get('enabled',False)

def settings(game):
    cfg={'enabled':False,'thresholds':[3,9,15],'own_daily_limit':2,'level':20,'shiny_rate':0,**game.config.get('vivillon',{})}
    if type(cfg['enabled']) is not bool:raise ValueError('Invalid Vivillon enable flag')
    if cfg['enabled']:
        if len(cfg['thresholds'])!=3 or any(type(v) is not int or not 1<=v<=10000 for v in cfg['thresholds']):raise ValueError('Invalid regional pin thresholds')
        if type(cfg['own_daily_limit']) is not int or not 0<=cfg['own_daily_limit']<=100:raise ValueError('Invalid own-Postcard daily limit')
        if type(cfg['level']) is not int or not 1<=cfg['level']<=40 or type(cfg['shiny_rate']) not in (int,float) or not 0<=cfg['shiny_rate']<=1:raise ValueError('Invalid Scatterbug configuration')
    return cfg

def install(game):
    settings(game)
    if not enabled(game):return
    registry()
    from dittobuffer.gameplay.exploration_events import SCHEMA as events_schema,enabled as events_enabled
    events_enabled(game)
    with closing(game.db.connect()) as db:db.executescript(SCHEMA+events_schema)

def form_for(region,species):
    name=p.VivillonRegion.Name(region).removeprefix('VivillonRegion_VIVILLON_REGION_')
    if name not in PATTERNS or species not in (664,665,666):raise ValueError('Unsupported Vivillon family pattern')
    return p.PokemonDisplayProto.Form.Value('Form_'+{664:'SCATTERBUG',665:'SPEWPA',666:'VIVILLON'}[species]+'_'+name)

def classify(lat,lng):
    """Only reviewed city interiors, not guessed country-sized polygons."""
    if not math.isfinite(lat) or not math.isfinite(lng) or not -85<=lat<=85 or not -180<=lng<=180:return 0
    matches={PATTERNS[v['region']] for v in registry()['origins'] if v['enabled'] and distance(lat,lng,v['lat'],v['lng'])<=1000}
    return next(iter(matches)) if len(matches)==1 else 0

def medal(row):
    return p.ButterflyCollectorRegionMedal(region=row['region'],rank=row['rank'],state=1 if row['pending'] else 0,progress=row['progress'],goal=row['goal'],postcard_origin=row['last_gift'],received_time_ms=row['updated_ms'])

def pin(game,db,player,gift,virtual=None):
    if not enabled(game):return None
    if db.execute('SELECT 1 FROM vivillon_pin_credits WHERE player=? AND gift=?',(player,gift['id'])).fetchone():return None
    region=PATTERNS[virtual['region']] if virtual else classify(gift['lat'],gift['lng'])
    if not region:return None
    cfg=settings(game)
    from dittobuffer.gameplay.friends import day
    today=day(game);own=gift['owner']==player and not virtual
    row=db.execute('SELECT * FROM vivillon_regions WHERE player=? AND region=?',(player,region)).fetchone()
    if not row:
        db.execute('INSERT INTO vivillon_regions(player,region,goal,updated_ms) VALUES(?,?,?,?)',(player,region,cfg['thresholds'][0],now_ms()))
        row=db.execute('SELECT * FROM vivillon_regions WHERE player=? AND region=?',(player,region)).fetchone()
    reason='encounter_pending' if row['pending'] else 'own_daily_limit' if own and db.execute('SELECT COUNT(*) FROM vivillon_pin_credits WHERE player=? AND day=? AND own_gift=1 AND counted=1',(player,today)).fetchone()[0]>=cfg['own_daily_limit'] else 'counted'
    db.execute('INSERT INTO vivillon_pin_credits VALUES(?,?,?,?,?,?,?,?)',(player,gift['id'],region,int(reason=='counted'),reason,today,int(own),now_ms()))
    if reason!='counted':return medal(row)
    progress=row['progress']+1;rank=row['rank'];pending=None
    if progress>=row['goal']:
        rank+=1
        from dittobuffer.gameplay.species import make_pokemon
        identifier=secrets.randbits(60) or 1
        while db.execute('SELECT 1 FROM spawns WHERE id=?',(identifier,)).fetchone():identifier=secrets.randbits(60) or 1
        form=form_for(region,664);rules=game.forms[664,form]
        shiny=(664,form) in game.shiny_eligible and secrets.randbelow(1000000)<int(cfg['shiny_rate']*1000000)
        mon=make_pokemon(p,rules,664,cfg['level'],[10+secrets.randbelow(6) for _ in range(3)],shiny=shiny)
        mon.id=identifier;mon.creation_time_ms=now_ms();key=f'vivillon:{region}:{rank}'
        db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',(identifier,key,664,gift['lat'],gift['lng'],2**62))
        db.execute('INSERT INTO spawn_attributes VALUES(?,?,?)',(identifier,mon.SerializeToString(),cfg['level']))
        # Reuse the established private reward-encounter ownership/catch path.
        # These rows are excluded from Research UI and projected in badge data.
        db.execute('INSERT INTO research_encounters VALUES(?,?,?,?,?,?)',(player,key,identifier,now_ms(),15,key))
        pending=db.execute('INSERT INTO vivillon_entitlements(player,region,ordinal,spawn,created_ms) VALUES(?,?,?,?,?)',(player,region,rank,identifier,now_ms())).lastrowid
    db.execute('UPDATE vivillon_regions SET rank=?,progress=?,pending=?,last_gift=?,updated_ms=? WHERE player=? AND region=?',(rank,progress,pending,gift['id'],now_ms(),player,region))
    game.touch(db,player)
    from dittobuffer.gameplay.exploration_events import emit
    emit(game,db,player,'vivillon.region_progressed',gift['id'],dict(region=region,rank=rank,progress=progress,goal=row['goal']))
    if pending:emit(game,db,player,'scatterbug.encounter_unlocked',pending,dict(region=region,entitlement=pending,ordinal=rank,spawn=identifier))
    return medal(db.execute('SELECT * FROM vivillon_regions WHERE player=? AND region=?',(player,region)).fetchone())

def caught(game,db,player,spawn,pokemon):
    if not enabled(game):return
    row=db.execute('SELECT * FROM vivillon_entitlements WHERE player=? AND spawn=? AND caught_pokemon IS NULL',(player,spawn)).fetchone()
    if not row:return
    db.execute('UPDATE vivillon_entitlements SET caught_pokemon=? WHERE id=?',(pokemon,row['id']))
    cfg=settings(game);goal=cfg['thresholds'][min(row['ordinal'],2)]
    db.execute('UPDATE vivillon_regions SET progress=0,goal=?,pending=NULL,updated_ms=? WHERE player=? AND region=? AND pending=?',(goal,now_ms(),player,row['region'],row['id']))
    game.touch(db,player)

def encounter(game,db,player,body):
    req=p.ButterflyCollectorRewardEncounterProtoRequest.FromString(body)
    out=p.ButterflyCollectorRewardEncounterProtoResponse(result=3)
    if not enabled(game) or req.region not in PATTERNS.values():return out
    row=db.execute('SELECT e.* FROM vivillon_regions r JOIN vivillon_entitlements e ON e.id=r.pending WHERE r.player=? AND r.region=? AND e.caught_pokemon IS NULL',(player,req.region)).fetchone()
    if not row:return out
    from dittobuffer.gameplay.eggs import count
    if db.execute('SELECT COUNT(*) FROM pokemon WHERE player_id=?',(player,)).fetchone()[0]+count(db,player)>=game.capacity(db,player,'pokemon_capacity'):out.result=2;return out
    mon=p.PokemonProto.FromString(db.execute('SELECT data FROM spawn_attributes WHERE spawn_id=?',(row['spawn'],)).fetchone()[0])
    db.execute('INSERT OR IGNORE INTO encounters(player_id,spawn_id) VALUES(?,?)',(player,row['spawn']))
    from dittobuffer.gameplay.items import probabilities
    out.result=1;out.pokemon.CopyFrom(mon);out.capture_probability.CopyFrom(probabilities(game));out.encounter_id=row['spawn'];out.rewards.SetInParent()
    return out

def inventory(game,db,player):
    if not enabled(game):return []
    item=p.HoloInventoryItemProto();item.badge_data.badge=1003;data=item.badge_data.butterfly_collector_data;data.version=1
    rows={v['region']:v for v in db.execute('SELECT * FROM vivillon_regions WHERE player=?',(player,))}
    for region in PATTERNS.values():
        row=rows.get(region)
        if row:data.region.add().CopyFrom(medal(row))
        else:data.region.add(region=region,goal=settings(game)['thresholds'][0])
    for row in db.execute('SELECT e.*,a.data FROM vivillon_entitlements e JOIN spawn_attributes a ON a.spawn_id=e.spawn WHERE e.player=? AND e.caught_pokemon IS NULL',(player,)):
        data.encounter.add(quest_id=f'vivillon:{row["region"]}:{row["ordinal"]}',pokemon=p.PokemonProto.FromString(row['data']),encounter_type=15)
    return [item]

def profile(game,db,player):
    if not enabled(game):return []
    value=db.execute('SELECT COUNT(*) FROM vivillon_regions WHERE player=? AND rank>0',(player,)).fetchone()[0]
    targets=[1,5,10,18];rank=sum(value>=v for v in targets)
    return [p.PlayerBadgeProto(badge_type=1003,rank=rank,current_value=value,start_value=targets[rank-1] if rank else 0,end_value=targets[min(rank,3)])]

def templates(config):
    if not config.get('vivillon',{}).get('enabled'):return []
    value=p.GameMasterClientTemplateProto(template_id='BUTTERFLY_COLLECTOR_SETTINGS');value.butterfly_collector_settings.CopyFrom(p.ButterflyCollectorSettings(enabled=True,version=1,region=list(PATTERNS.values()),daily_progress_from_inventory=config.get('vivillon',{}).get('own_daily_limit',2)))
    badge=p.GameMasterClientTemplateProto(template_id='BADGE_BUTTERFLY_COLLECTOR');badge.badge.CopyFrom(p.BadgeSettingsProto(badge_type=1003,badge_ranks=4,targets=[1,5,10,18]))
    return [value,badge]
