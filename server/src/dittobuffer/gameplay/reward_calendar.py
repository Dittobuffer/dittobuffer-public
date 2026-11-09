"""Transactional catch/spin calendars and separate validated-distance weeks.

No wall-clock overrides, client timestamps or walking credit are accepted here.
Tests may inject game.reward_calendar_clock; the private M24 runner owns its lab
clock. Production uses real UTC translated through the configured IANA timezone.
"""
from contextlib import closing
from datetime import datetime, timezone, timedelta, date
import math
from zoneinfo import ZoneInfo
from dittobuffer.gameplay.game import p, now_ms

SCHEMA = """
CREATE TABLE IF NOT EXISTS reward_streaks(player_id TEXT NOT NULL REFERENCES players(id),kind TEXT NOT NULL,day TEXT NOT NULL,streak INTEGER NOT NULL,PRIMARY KEY(player_id,kind));
CREATE TABLE IF NOT EXISTS reward_days(player_id TEXT NOT NULL REFERENCES players(id),kind TEXT NOT NULL,day TEXT NOT NULL,streak INTEGER NOT NULL,PRIMARY KEY(player_id,kind,day));
CREATE TABLE IF NOT EXISTS reward_throws(player_id TEXT NOT NULL REFERENCES players(id),spawn_id INTEGER NOT NULL REFERENCES spawns(id),count INTEGER NOT NULL,PRIMARY KEY(player_id,spawn_id));
CREATE TABLE IF NOT EXISTS reward_distance(player_id TEXT NOT NULL REFERENCES players(id),week TEXT NOT NULL,meters REAL NOT NULL,PRIMARY KEY(player_id,week));
CREATE TABLE IF NOT EXISTS reward_fitness_claims(player_id TEXT NOT NULL REFERENCES players(id),week TEXT NOT NULL,result BLOB NOT NULL,PRIMARY KEY(player_id,week));
"""
DEFAULTS = dict(enabled=False, timezone='Europe/Amsterdam', daily_xp=1500,
    seventh_xp=6000, daily_dust=600, seventh_dust=3000, first_throw_xp=50,
    nice_xp=20, great_xp=100, excellent_xp=1000, curve_xp=20,
    spin_daily_items={'1':3}, spin_seventh_items={'1':15},
    weather_dust_multiplier=1.25, species_rewards={}, events=[],
    weekly_enabled=False, weekly_reset_hour=9, weekly_rewards=[],
    weekly_provenance='Operator-owned reward table; no official random reward table inferred.')


def install(game):
    cfg = DEFAULTS | game.config.get('reward_bonuses', {})
    ZoneInfo(cfg['timezone'])
    for key in ('daily_xp','seventh_xp','daily_dust','seventh_dust','first_throw_xp','nice_xp','great_xp','excellent_xp','curve_xp'):
        if type(cfg[key]) is not int or not 0 <= cfg[key] <= 100000:raise ValueError('Invalid reward '+key)
    if type(cfg['weekly_reset_hour']) is not int or not 0 <= cfg['weekly_reset_hour'] <= 23:raise ValueError('Invalid weekly reset')
    def factor(v):
        if type(v) not in (float,int) or not math.isfinite(v) or not 0 < v <= 100:raise ValueError('Invalid reward multiplier')
    factor(cfg['weather_dust_multiplier'])
    for rewards in [cfg['spin_daily_items'],cfg['spin_seventh_items']]:
        for key,value in rewards.items():
            if int(key) not in p.Item.values() or type(value) is not int or not 0 <= value <= 10000:raise ValueError('Invalid bonus items')
    for species,rewards in cfg['species_rewards'].items():
        if int(species) not in game.species or any(k not in ('candy','dust') or type(v) is not int or not 0 <= v <= 10000 for k,v in rewards.items()):raise ValueError('Invalid species rewards')
    if not isinstance(cfg['events'],list) or len(cfg['events'])>16:raise ValueError('Bounded reward events required')
    for event in cfg['events']:
        if type(event['start_ms']) is not int or type(event['end_ms']) is not int or not event['start_ms'] < event['end_ms']:raise ValueError('Invalid reward event window')
        for name in ('xp','dust','candy'):factor(event.get(name,1))
    previous=0
    for tier in cfg['weekly_rewards']:
        if type(tier['km']) not in (int,float) or not math.isfinite(tier['km']) or tier['km'] <= previous:raise ValueError('Ordered weekly thresholds required')
        previous=tier['km']
        if type(tier.get('dust',0)) is not int or not 0 <= tier.get('dust',0) <= 100000:raise ValueError('Invalid weekly dust')
        for item,count in tier.get('items',{}).items():
            if int(item) not in p.Item.values() or type(count) is not int or not 0 <= count <= 10000:raise ValueError('Invalid weekly item')
    for name in ('xp','dust','candy'):
        if math.prod(max(1,e.get(name,1)) for e in cfg['events'])>100:raise ValueError('Combined reward multiplier exceeds100')
    game.reward_bonuses=cfg
    with closing(game.db.connect()) as db:db.executescript(SCHEMA)


def stamp(game):return getattr(game,'reward_calendar_clock',now_ms)()
def local(game):return datetime.fromtimestamp(stamp(game)/1000,timezone.utc).astimezone(ZoneInfo(game.reward_bonuses['timezone']))

def streak(game,db,player,kind,consume=False):
    today=local(game).date();key=today.isoformat()
    old=db.execute('SELECT * FROM reward_streaks WHERE player_id=? AND kind=?',(player,kind)).fetchone()
    if old and old['day'] >= key:return 0
    number=old['streak']%7+1 if old and date.fromisoformat(old['day'])==today-timedelta(days=1) else 1
    if consume:
        if not db.execute('INSERT OR IGNORE INTO reward_days VALUES(?,?,?,?)',(player,kind,key,number)).rowcount:return 0
        db.execute('INSERT INTO reward_streaks VALUES(?,?,?,?) ON CONFLICT(player_id,kind) DO UPDATE SET day=excluded.day,streak=excluded.streak',(player,kind,key,number))
    return number


def attempted(game,db,player,spawn,ball):
    if ball not in (1,2,3,5) or not game.reward_bonuses['enabled']:return 0
    db.execute('INSERT INTO reward_throws VALUES(?,?,1) ON CONFLICT(player_id,spawn_id) DO UPDATE SET count=count+1',(player,spawn))
    return db.execute('SELECT count FROM reward_throws WHERE player_id=? AND spawn_id=?',(player,spawn)).fetchone()[0]


def catch(game,db,player,request,pokemon,attempts):
    cfg=game.reward_bonuses;species=cfg['species_rewards'].get(str(pokemon.pokemon_id),{})
    from dittobuffer.gameplay.species import rules_for
    rules=rules_for(game,pokemon)
    base=[1,game.config['catch_xp'],species.get('candy',game.config['catch_candy']+rules.get('capture_bonus_candy',0)),species.get('dust',game.config['catch_stardust']+rules.get('capture_bonus_dust',0))]
    if request.pokeball not in (1,2,3,5) or not cfg['enabled']:return [[1,game.config['catch_xp'],game.config['catch_candy'],game.config['catch_stardust']]]
    rows=[base];quality=request.normalized_reticle_size;hit=request.normalized_hit_position
    # 0.429.2 sends the increasing 0.5..2 modifier, not ring radius. Match
    # the existing ENCOUNTER_SETTINGS thresholds supplied to this client.
    if all(math.isfinite(v) for v in (quality,hit)) and 1.3 <= quality <= 2 and 0 <= hit <= 1:
        key,activity=('excellent_xp',12) if quality>=1.85 else ('great_xp',11) if quality>=1.7 else ('nice_xp',10)
        rows.append([activity,cfg[key],0,0])
    if math.isfinite(request.spin_modifier) and .5<=request.spin_modifier<=1:rows.append([13,cfg['curve_xp'],0,0])
    if attempts==1:rows.append([9,cfg['first_throw_xp'],0,0])
    if pokemon.pokemon_display.weather_boosted_condition:base[3]=math.floor(base[3]*cfg['weather_dust_multiplier'])
    day=streak(game,db,player,'catch',True)
    if day:rows.append([24 if day==7 else 14,cfg['seventh_xp' if day==7 else 'daily_xp'],0,cfg['seventh_dust' if day==7 else 'daily_dust']])
    factors=[1,1,1];now=stamp(game)
    for event in cfg['events']:
        if event['start_ms']<=now<event['end_ms']:
            factors=[v*event.get(k,1) for v,k in zip(factors,('xp','candy','dust'))]
    for row in rows:
        for i,f in enumerate(factors,1):row[i]=math.floor(row[i]*f)
    return rows


def spin(game,db,player,consume=False):
    cfg=game.reward_bonuses
    if not cfg['enabled']:return 0,{},0
    day=streak(game,db,player,'spin',consume)
    if not day:return 0,{},0
    return cfg['seventh_xp' if day==7 else 'daily_xp'],{int(k):v for k,v in cfg['spin_seventh_items' if day==7 else 'spin_daily_items'].items()},day


def quests(game,db,player,out):
    if not game.reward_bonuses['enabled']:return
    today=local(game).date()
    for kind,typ in [('catch',1),('spin',2)]:
        row=db.execute('SELECT * FROM reward_streaks WHERE player_id=? AND kind=?',(player,kind)).fetchone()
        count=row['streak'] if row and row['day']>= (today-timedelta(days=1)).isoformat() else 0
        quest=out.quest.add(quest_type=typ,quest_id='daily-'+kind)
        last_bucket=(date.fromisoformat(row['day'])-date(1970,1,1)).days if row else 0
        quest.daily_quest.CopyFrom(p.DailyQuestProto(current_period_bucket=last_bucket,current_streak_count=count))


def week(game):
    value=local(game);start=value.replace(hour=game.reward_bonuses['weekly_reset_hour'],minute=0,second=0,microsecond=0)-timedelta(days=value.weekday())
    if value<start:start-=timedelta(days=7)
    return start


def walked(game,db,player,meters):
    if not game.reward_bonuses['weekly_enabled']:return
    # Called only after the established sampler has validated and credited a fix.
    db.execute('INSERT INTO reward_distance VALUES(?,?,?) ON CONFLICT(player_id,week) DO UPDATE SET meters=meters+excluded.meters',(player,week(game).date().isoformat(),meters))


def fitness_report(game,db,player,body):
    req=p.GetFitnessReportProto.FromString(body)
    if any(v<0 for v in (req.num_of_weeks,req.num_of_days,req.num_of_hours)) or req.num_of_weeks>52 or req.num_of_days>366 or req.num_of_hours>168:return p.GetFitnessReportOutProto(status=4)
    if not game.reward_bonuses['weekly_enabled']:return p.GetFitnessReportOutProto(status=3)
    start=week(game);out=p.GetFitnessReportOutProto(status=1,week_reset_timestamp_since_monday_ms=game.reward_bonuses['weekly_reset_hour']*3600000)
    for i in range(min(req.num_of_weeks or 1,52)):
        key=(start-timedelta(days=7*i)).date().isoformat();row=db.execute('SELECT meters FROM reward_distance WHERE player_id=? AND week=?',(player,key)).fetchone()
        report=out.weekly_reports.add(week_offset_from_now=i);report.metrics.distance_walked_meters=row[0] if row else 0
        report.game_data=p.HoloFitnessReportProto(distance_walked_km=(row[0]/1000 if row else 0)).SerializeToString()
    return out


def fitness_rewards(game,db,player):
    cfg=game.reward_bonuses
    if not cfg['weekly_enabled']:return p.GetFitnessRewardsOutProto(result=2)
    from dittobuffer.gameplay.progression import bounds
    if bounds(game.config,game.player(db,player)['xp'])[0]<5:return p.GetFitnessRewardsOutProto(result=2)
    key=(week(game)-timedelta(days=7)).date().isoformat()
    old=db.execute('SELECT result FROM reward_fitness_claims WHERE player_id=? AND week=?',(player,key)).fetchone()
    if old:return p.GetFitnessRewardsOutProto(result=2)
    row=db.execute('SELECT meters FROM reward_distance WHERE player_id=? AND week=?',(player,key)).fetchone();km=row[0]/1000 if row else 0
    tier=next((v for v in reversed(cfg['weekly_rewards']) if km>=v['km']),None)
    if not tier:return p.GetFitnessRewardsOutProto(result=2)
    items={int(k):v for k,v in tier.get('items',{}).items()}
    if game.bag_count(db,player)+sum(items.values())>game.capacity(db,player,'item_capacity'):return p.GetFitnessRewardsOutProto(result=3)
    out=p.GetFitnessRewardsOutProto(result=1)
    for item,count in items.items():
        db.execute('INSERT INTO items VALUES(?,?,?) ON CONFLICT(player_id,item) DO UPDATE SET count=count+excluded.count',(player,item,count));out.rewards.loot_item.add(item=item,count=count)
    dust=tier.get('dust',0)
    if dust:db.execute('UPDATE players SET stardust=stardust+? WHERE id=?',(dust,player));out.rewards.loot_item.add(stardust=True,count=dust)
    db.execute('INSERT INTO reward_fitness_claims VALUES(?,?,?)',(player,key,out.SerializeToString()));game.touch(db,player)
    return out
