"""Persistent ordinary gyms. Every mutation shares the existing RPC transaction."""
import math
from dittobuffer.gameplay.game import p,now_ms,distance
from dittobuffer.core.management import owned,update_owned
from dittobuffer.gameplay.progression import bounds

PLACEHOLDER_IMAGE='/assets/dittobuffer-stop.png'
IMAGE='http://127.0.0.1:8445/assets/dittobuffer-stop.png'

def image_url(game):
    """Return the reachable origin of the shipped stop/gym placeholder.

    The native client loads fort photos over plain HTTP, and the raid lobby
    paints the gym's photo behind the party panel. The old constant always
    targeted the admin origin, which only exposes the postcard; on the phone
    that request 404s and the raid lobby rendered black.
    """
    world=game.config.get('world') or {}
    base=str(world.get('public_base_url') or '').rstrip('/')
    if base:
        return base+PLACEHOLDER_IMAGE
    host=str(game.config.get('host') or '127.0.0.1')
    if host in ('0.0.0.0','::',''):host='127.0.0.1'
    try:port=int(game.config.get('port') or 8445)
    except (TypeError,ValueError):port=8445
    return f'http://{host}:{port}{PLACEHOLDER_IMAGE}'
DEFAULTS={'capacity':6,'max_deployed':20,'minimum_level':5,'motivation_loss':1/3,
          'battle_xp':100,'spin_xp':25,'feed_xp':50,'feed_dust':30,'feed_cooldown_seconds':30,
          'coin_minutes':10,'daily_coin_cap':50,'motivation_decay_per_hour':.1}

def rules(game):
    result=DEFAULTS|game.config.get('gyms',{})
    for key,value in result.items():
        if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
            raise ValueError('Invalid gym rule: '+key)
    if not 1<=result['capacity']<=6 or not 0<result['motivation_loss']<=1 or result['coin_minutes']<=0:
        raise ValueError('Invalid gym capacity, motivation or coin interval')
    for key in ('capacity','max_deployed','minimum_level','battle_xp','spin_xp','feed_xp','feed_dust','daily_coin_cap'):
        if type(result[key]) is not int:raise ValueError('Gym rule must be an integer: '+key)
    return result

def team(db,player):
    db.execute('INSERT OR IGNORE INTO trainer_teams(player_id) VALUES(?)',(player,))
    return db.execute('SELECT * FROM trainer_teams WHERE player_id=?',(player,)).fetchone()

def gym(db,fort_id):
    fort=db.execute('SELECT * FROM forts WHERE id=? AND kind=0',(fort_id,)).fetchone()
    if fort:
        db.execute('INSERT OR IGNORE INTO gyms(fort_id,modified_ms) VALUES(?,?)',(fort_id,now_ms()))
    return fort

def accessible(game,fort):
    if not fort:return False
    place=game.world.poi(fort['id'])
    # Local/community gyms can coexist with the published OSM snapshot. Match
    # map visibility: absence from that snapshot is not a moderation rejection.
    return not game.world.path or not place or place['status']=='APPROVED'


def near(game,fort,lat,lng):
    return (accessible(game,fort) and math.isfinite(lat) and math.isfinite(lng)
            and -90<=lat<=90 and -180<=lng<=180
            and distance(lat,lng,fort['lat'],fort['lng'])<=game.config['interaction_range_meters'])

def active_raid(game,db,fort):
    """Eggs allow ordinary gym play; hatched, unexpired raids reserve the gym."""
    from dittobuffer.gameplay.raids import schedule
    raid=schedule(game,db,fort)
    return raid if raid and raid['battle_ms']<=now_ms()<raid['end_ms'] else None

def defenders(db,fort_id):
    return db.execute('SELECT d.*,p.data FROM gym_defenders d JOIN pokemon p ON p.id=d.pokemon_id WHERE fort_id=? ORDER BY deploy_ms,pokemon_id',(fort_id,)).fetchall()

def battle_busy(db,player,ids):
    import json
    ids=set(ids)
    # Trainer battles, Max battles and local Rocket sessions all place the party
    # under an exclusive lock; management mutations must respect every one.
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pvp_battles'").fetchone():
        for row in db.execute('SELECT b.state,m.side FROM pvp_battles b JOIN pvp_members m ON m.battle_id=b.id '
                              'WHERE m.player_id=? AND b.finished=0 AND b.end_ms>?',(player,now_ms())):
            state=json.loads(row['state'])
            if any(p.PokemonProto.FromString(bytes.fromhex(value['pokemon'])).id in ids
                   for value in state['members'][row['side']]['party']):return True
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='max_pokemon_locks'").fetchone():
        if any(row['pokemon_id'] in ids for row in db.execute(
                'SELECT pokemon_id FROM max_pokemon_locks WHERE player_id=? AND expires_ms>?',(player,now_ms()))):return True
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rocket_battles'").fetchone():
        for row in db.execute('SELECT state FROM rocket_battles WHERE player_id=? AND finalized=0 AND end_ms>?',(player,now_ms())):
            state=json.loads(row['state'])
            if state['status']=='active' and any(p.PokemonProto.FromString(bytes.fromhex(value['pokemon'])).id in ids for value in state['party']):return True
    for row in db.execute('SELECT state FROM gym_battles WHERE player_id=? AND settled=0 AND end_ms>? UNION ALL SELECT state FROM raid_battles WHERE player_id=? AND settled=0 AND end_ms>?',
                          (player,now_ms(),player,now_ms())):
        state=json.loads(row['state'])
        if state['status']!=1:continue
        if any(p.PokemonProto.FromString(bytes.fromhex(value['pokemon'])).id in ids for value in state['party']):return True
    return False

def public(game,db,player):
    row=game.player(db,player)
    out=p.PlayerPublicProfileProto(name=row['name'],level=bounds(game.config,row['xp'])[0],
        team=team(db,player)['team'],player_id=player[:20],experience=row['xp'],caught_pokemon=row['captures'])
    out.neutral_avatar.CopyFrom(game.appearance(db,player))
    return out

def decay(game,db,fort_id,stamp):
    changed=False
    for row in defenders(db,fort_id):
        since=row['motivation_ms'] or max(row['deploy_ms'],row['last_feed_ms'])
        loss=max(0,stamp-since)*rules(game)['motivation_decay_per_hour']/3600000
        if loss:
            changed=True
            db.execute('UPDATE gym_defenders SET motivation=MIN(motivation,MAX(.2,motivation-?)),motivation_ms=? WHERE pokemon_id=?',
                (loss,stamp,row['pokemon_id']))
    if changed:
        db.execute('UPDATE gyms SET modified_ms=? WHERE fort_id=?',(stamp,fort_id))

def map_fort(game,db,fort):
    gym(db,fort['id'])
    stamp=now_ms();decay(game,db,fort['id'],stamp)
    state=db.execute('SELECT * FROM gyms WHERE fort_id=?',(fort['id'],)).fetchone()
    rows=defenders(db,fort['id'])
    out=p.PokemonFortProto(fort_id=fort['id'],fort_type=0,latitude=fort['lat'],longitude=fort['lng'],
        enabled=True,last_modified_ms=state['modified_ms'],team=state['team'],image_url=image_url(game))
    # A missing local photo/placeholder must never abort the whole map reply.
    try:place=game.world.details(fort['id'])
    except (OSError,ValueError):place=None
    if place:out.image_url=place['url']
    out.gym_display.slots_available=rules(game)['capacity']-len(rows)
    out.gym_display.occupied_millis=max(0,now_ms()-state['occupied_ms']) if rows else 0
    out.gym_display.lowest_pokemon_motivation=min((r['motivation'] for r in rows),default=0)
    for row in rows:
        pokemon=p.PokemonProto.FromString(row['data'])
        out.gym_display.total_gym_cp+=max(10,int(pokemon.cp*row['motivation']))
    if rows:
        pokemon=p.PokemonProto.FromString(rows[0]['data'])
        out.guard_pokemon_id=pokemon.pokemon_id;out.guard_pokemon_level=1
        out.guard_pokemon_display.CopyFrom(pokemon.pokemon_display)
    # A removed raid must carry a newer fort revision than its egg/boss.
    # Keep history/reward rows intact; derive the tombstone from persisted expiry.
    ended=db.execute('SELECT MAX(end_ms) FROM raids WHERE fort_id=? AND end_ms<=?',(fort['id'],stamp)).fetchone()[0]
    if ended is not None:out.last_modified_ms=max(out.last_modified_ms,ended)
    from dittobuffer.gameplay.raids import schedule,info
    raid=schedule(game,db,fort)
    if raid:
        out.raid_info.CopyFrom(info(raid));out.last_modified_ms=max(out.last_modified_ms,raid['battle_ms'] if stamp>=raid['battle_ms'] else raid['spawn_ms'])
    return out

def status(game,db,fort):
    out=p.GymStatusAndDefendersProto(pokemon_fort_proto=map_fort(game,db,fort))
    for row in defenders(db,fort['id']):
        pokemon=p.PokemonProto.FromString(row['data'])
        defender=out.gym_defender.add()
        defender.motivated_pokemon.CopyFrom(p.MotivatedPokemonProto(pokemon=pokemon,deploy_ms=row['deploy_ms'],
            cp_when_deployed=pokemon.cp,motivation_now=row['motivation'],cp_now=max(10,int(pokemon.cp*row['motivation'])),
            berry_value=.2,feed_cooldown_duration_millis=max(0,row['last_feed_ms']+int(rules(game)['feed_cooldown_seconds']*1000)-now_ms())))
        # 0.429.2's ordinary battle nameplate initializes from this gym-view
        # Pokemon, before observing the separate combat frame. Its AdjustedCp
        # getter reads Pokemon.cp, rather than MotivatedPokemon.cp_now. Project
        # current CP in the copied view, keeping full deployment CP separately
        # and never writing reduced stats into the owner's Pokemon record.
        defender.motivated_pokemon.pokemon.cp=defender.motivated_pokemon.cp_now
        for item,increase in ((701,.2),(703,.2),(705,.2),(706,1),(708,.5)):
            defender.motivated_pokemon.food_value.add(food_item=item,motivation_increase=increase,
                cp_increase=max(1,int(pokemon.cp*min(increase,1-row['motivation']))))
        defender.deployment_totals.CopyFrom(p.DeploymentTotalsProto(times_fed=row['berries'],battles_won=row['battles_won'],
            battles_lost=row['battles_lost'],deployment_duration_ms=max(0,now_ms()-row['deploy_ms'])))
        defender.trainer_public_profile.CopyFrom(public(game,db,row['owner_id']))
    return out

def badge(game,db,player,fort,score=0,**increments):
    db.execute('INSERT OR IGNORE INTO gym_badges(player_id,fort_id,updated_ms) VALUES(?,?,?)',(player,fort['id'],now_ms()))
    db.execute('UPDATE gym_badges SET score=score+?,updated_ms=? WHERE player_id=? AND fort_id=?',(score,now_ms(),player,fort['id']))
    for key,value in increments.items():
        if key not in ('battles_won','battles_lost','deploys','berries','defended_ms'):raise ValueError(key)
        db.execute(f'UPDATE gym_badges SET {key}={key}+? WHERE player_id=? AND fort_id=?',(value,player,fort['id']))
    row=db.execute('SELECT * FROM gym_badges WHERE player_id=? AND fort_id=?',(player,fort['id'])).fetchone()
    place=game.world.details(fort['id'])
    thresholds=[0,500,4000,30000];level=sum(row['score']>=v for v in thresholds)
    lower=thresholds[level-1];upper=thresholds[level] if level<4 else lower+1
    out=p.AwardedGymBadge(fort_id=fort['id'],gym_badge_type=level,score=row['score'],last_update_timestamp_ms=row['updated_ms'],
        last_check_timestamp_ms=now_ms(),name=fort['name'],image_url=place['url'] if place else image_url(game),description=place['description'] if place else 'Libre community gym',
        latitude=fort['lat'],longitude=fort['lng'],earned_points=score,progress=min(1,(row['score']-lower)/(upper-lower)))
    out.gym_badge_stats.CopyFrom(p.GymBadgeStats(total_time_defended_ms=row['defended_ms'],num_battles_won=row['battles_won'],
        num_battles_lost=row['battles_lost'],num_deploys=row['deploys'],num_berries_fed=row['berries']))
    from dittobuffer.gameplay.raids import history
    out.raids.CopyFrom(history(db,player,fort['id']))
    return out

def select_team(game,db,player,body):
    request=p.SetPlayerTeamProto.FromString(body);current=team(db,player)
    if current['team']:return p.SetPlayerTeamOutProto(status=2,player=game.rpc_get_player(db,player,b'').player)
    if request.team not in (1,2,3) or bounds(game.config,game.player(db,player)['xp'])[0]<rules(game)['minimum_level']:
        return p.SetPlayerTeamOutProto(status=3)
    db.execute('UPDATE trainer_teams SET team=? WHERE player_id=?',(request.team,player));game.touch(db,player)
    return p.SetPlayerTeamOutProto(status=1,player=game.rpc_get_player(db,player,b'').player)

def info(game,db,player,body):
    request=p.GymGetInfoProto.FromString(body);fort=gym(db,request.gym_id)
    if not fort:return p.GymGetInfoOutProto(result=3)
    place=game.world.details(fort['id'])
    image=place['url'] if place else image_url(game)
    return p.GymGetInfoOutProto(result=1,gym_status_and_defenders=status(game,db,fort),name=fort['name'],
        description=place['description'] if place else 'Libre community gym',url=image,secondary_url=image,checkin_image_url=image,poi_images_count=1,
        server_ms=now_ms(),awarded_gym_badge=badge(game,db,player,fort))

def deploy(game,db,player,body):
    request=p.GymDeployProto.FromString(body);fort=gym(db,request.fort_id);r=rules(game)
    def fail(result):return p.GymDeployOutProto(result=result)
    if not fort:return fail(12)
    if not near(game,fort,request.player_lat_degrees,request.player_lng_degrees):return fail(5)
    if active_raid(game,db,fort):return fail(19)
    ownteam=team(db,player)['team'];state=db.execute('SELECT * FROM gyms WHERE fort_id=?',(fort['id'],)).fetchone()
    if not ownteam:return fail(6)
    if bounds(game.config,game.player(db,player)['xp'])[0]<r['minimum_level']:return fail(8)
    if state['team'] not in (0,ownteam):return fail(3)
    rows=defenders(db,fort['id'])
    if any(row['owner_id']==player for row in rows):return fail(2)
    if len(rows)>=r['capacity']:return fail(4)
    pokemon=owned(db,player,request.pokemon_id)
    if not pokemon or pokemon.is_egg or pokemon.deployed_fort_id or pokemon.deployed_station_id:return fail(13)
    from dittobuffer.gameplay.mega import active as active_mega
    if active_mega(db,pokemon.id):return fail(13)
    if db.execute('SELECT 1 FROM buddy_active WHERE player_id=? AND pokemon_id=?',(player,pokemon.id)).fetchone():return fail(9)
    if battle_busy(db,player,[pokemon.id]):return fail(18)
    # The ordinary species supported by the catalog have no legendary/buddy exemptions.
    if game.species[pokemon.pokemon_id]['class'] in (1,2,3):return fail(17)
    if pokemon.stamina!=pokemon.max_stamina:return fail(7)
    if any(p.PokemonProto.FromString(row['data']).pokemon_id==pokemon.pokemon_id for row in rows):return fail(14)
    if db.execute('SELECT COUNT(*) FROM gym_defenders WHERE owner_id=?',(player,)).fetchone()[0]>=r['max_deployed']:return fail(15)
    stamp=now_ms()
    db.execute('INSERT INTO gym_defenders(pokemon_id,fort_id,owner_id,deploy_ms,motivation_ms) VALUES(?,?,?,?,?)',(pokemon.id,fort['id'],player,stamp,stamp))
    db.execute('UPDATE gyms SET team=?,modified_ms=?,occupied_ms=CASE WHEN occupied_ms=0 THEN ? ELSE occupied_ms END WHERE fort_id=?',
        (ownteam,stamp,stamp,fort['id']))
    pokemon.deployed_fort_id=fort['id'];pokemon.owner_name=game.player(db,player)['name']
    pokemon.deployed_gym_lat_degree=fort['lat'];pokemon.deployed_gym_lng_degree=fort['lng']
    update_owned(game,db,player,pokemon)
    return p.GymDeployOutProto(result=1,gym_status_and_defenders=status(game,db,fort),awarded_gym_badge=badge(game,db,player,fort,100,deploys=1))

def feed(game,db,player,body):
    from dittobuffer.gameplay.items import quantity,consume
    request=p.GymFeedPokemonProto.FromString(body);fort=gym(db,request.gym_id)
    def fail(result):return p.GymFeedPokemonOutProto(result=result)
    if not fort:return fail(4)
    row=db.execute('SELECT * FROM gym_defenders WHERE fort_id=? AND pokemon_id=?',(fort['id'],request.pokemon_id)).fetchone()
    if not row:return fail(4)
    if not near(game,fort,request.player_lat_degrees,request.player_lng_degrees):return fail(3)
    if active_raid(game,db,fort):return fail(12)
    if team(db,player)['team']!=db.execute('SELECT team FROM gyms WHERE fort_id=?',(fort['id'],)).fetchone()[0]:return fail(7)
    if request.item not in (701,703,705,706,708):return fail(2)
    if quantity(db,player,request.item)<=0:return fail(6)
    if quantity(db,player,request.item)!=request.starting_quantity:return fail(8)
    r=rules(game);stamp=now_ms();cooldown=int(r['feed_cooldown_seconds']*1000)
    if stamp<row['last_feed_ms']+cooldown:return fail(9)
    decay(game,db,fort['id'],stamp)
    consume(db,player,request.item,1)
    db.execute('UPDATE gym_defenders SET motivation=MIN(1,motivation+?),berries=berries+1,last_feed_ms=?,motivation_ms=? WHERE pokemon_id=?',
        (1 if request.item==706 else .5 if request.item==708 else .2,stamp,stamp,request.pokemon_id))
    db.execute('UPDATE gyms SET modified_ms=? WHERE fort_id=?',(stamp,fort['id']))
    db.execute('UPDATE players SET xp=xp+?,stardust=stardust+? WHERE id=?',(r['feed_xp'],r['feed_dust'],player));game.touch(db,player)
    return p.GymFeedPokemonOutProto(result=1,gym_status_and_defenders=status(game,db,fort),gym_badge=badge(game,db,player,fort,10,berries=1),
        xp_awarded=r['feed_xp'],stardust_awarded=r['feed_dust'],cooldown_complete=stamp+cooldown)

def return_defender(game,db,row,preserve_health=False):
    stamp=now_ms();duration=max(0,stamp-row['deploy_ms']);r=rules(game)
    today=stamp//86400000
    earned=db.execute('SELECT COALESCE(SUM(coins),0) FROM gym_returns WHERE player_id=? AND returned_ms/86400000=?',(row['owner_id'],today)).fetchone()[0]
    coins=max(0,min(int(duration/(r['coin_minutes']*60000)),r['daily_coin_cap']-earned))
    if not db.execute('INSERT OR IGNORE INTO gym_returns VALUES(?,?,?,?,?,?)',(row['pokemon_id'],row['deploy_ms'],row['owner_id'],stamp,duration,coins)).rowcount:return
    pokemon=owned(db,row['owner_id'],row['pokemon_id']);pokemon.deployed_fort_id=''
    if not preserve_health:pokemon.stamina=0
    pokemon.coins_returned=coins;pokemon.deployed_duration_ms=duration;pokemon.deployed_returned_timestamp_ms=stamp
    update_owned(game,db,row['owner_id'],pokemon);team(db,row['owner_id'])
    db.execute('UPDATE trainer_teams SET coins=coins+? WHERE player_id=?',(coins,row['owner_id']))
    from dittobuffer.gameplay.gym_notifications import returned
    returned(game,db,row,pokemon,coins,earned+coins)
    from dittobuffer.gameplay.trainer import event
    event(game,db,row['owner_id'],'return:'+str(row['pokemon_id'])+':'+str(row['deploy_ms']),'gym_return',dict(pokemon=pokemon.SerializeToString().hex(),coins=coins,duration_ms=duration),stamp)
    fort=gym(db,row['fort_id']);badge(game,db,row['owner_id'],fort,int(duration/60000),defended_ms=duration)
    db.execute('DELETE FROM gym_defenders WHERE pokemon_id=?',(row['pokemon_id'],))
    if not defenders(db,fort['id']):db.execute('UPDATE gyms SET team=0,occupied_ms=0,modified_ms=? WHERE fort_id=?',(stamp,fort['id']))

def list_badges(game,db,player,body):
    out=p.ListGymBadgesOutProto()
    for row in db.execute('SELECT f.* FROM forts f JOIN gym_badges b ON b.fort_id=f.id WHERE b.player_id=?',(player,)).fetchall():
        out.gym_badge.add().CopyFrom(badge(game,db,player,row))
    return out

def badge_details(game,db,player,body):
    request=p.GetGymBadgeDetailsProto.FromString(body);fort=gym(db,request.fort_id)
    if not fort:return p.GetGymBadgeDetailsOutProto(success=False)
    out=p.GetGymBadgeDetailsOutProto(success=True,gym_badge=badge(game,db,player,fort))
    for defender in status(game,db,fort).gym_defender:
        if defender.trainer_public_profile.player_id==player[:20]:out.gym_defender.CopyFrom(defender)
    return out
