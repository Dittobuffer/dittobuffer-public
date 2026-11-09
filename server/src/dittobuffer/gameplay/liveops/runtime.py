"""Request-local projections over the existing authoritative gameplay engines.

No shared Game configuration is mutated. Existing battle, egg and Research
snapshots keep their original rules. Empty/disabled studio preserves defaults.
"""
from copy import copy,deepcopy
import json
from pathlib import Path
from dittobuffer.paths import ROOT
from .store import Store
from .rules import resolve,interval


def path(config):
    relative=config.get('liveops',{}).get('database')
    if relative is None:
        trainer=(ROOT/config.get('database','data/player.sqlite3')).resolve()
        if not trainer.is_relative_to(ROOT):raise ValueError('Studio requires a project-local trainer database')
        relative=str(trainer.parent.relative_to(ROOT)/'liveops.sqlite3')
    if not isinstance(relative,str) or Path(relative).is_absolute():raise ValueError('Live content database must be project-relative')
    value=(ROOT/relative).resolve()
    if not value.is_relative_to(ROOT) or value.suffix!='.sqlite3':raise ValueError('Invalid live content database')
    return value


def clock(game):
    from dittobuffer.gameplay.game import now_ms
    cfg=game.config.get('liveops',{});value=cfg.get('fixture_clock_ms')
    if value is None and cfg.get('isolated_fixture') and path(game.config).exists():value=Store(path(game.config),False).fixture_clock()
    if value is None:return now_ms()
    if game.config.get('environment','development')!='development' or not cfg.get('isolated_fixture') or not any(game.db.path.resolve().is_relative_to(ROOT/p) for p in ('builds','.private')):raise ValueError('Scheduler clock overrides are restricted to isolated development databases')
    if type(value) is not int:raise ValueError('Invalid isolated scheduler time')
    return value


def location_of(db,player,location):
    import math
    raw=None
    if location is not None:
        if hasattr(location,'latitude'):raw=(location.latitude,location.longitude)
        elif isinstance(location,dict):raw=(location.get('latitude',location.get('lat')),location.get('longitude',location.get('lng')))
    if raw:
        try:
            lat,lng=map(float,raw)
            if math.isfinite(lat) and math.isfinite(lng) and -90<=lat<=90 and -180<=lng<=180:return lat,lng
        except (TypeError,ValueError):pass
    row=db.execute('SELECT lat,lng FROM admin_locations WHERE player_id=?',(player,)).fetchone()
    return (row['lat'],row['lng']) if row else (None,None)


def project(game,db,player,location=None):
    if game.config.get('liveops',{}).get('enabled',True) is False:return game
    # A caller overriding Database for an isolated test/profile must opt into
    # its content explicitly, never inherit the normal trainer's live calendar.
    if 'database' not in game.config.get('liveops',{}):
        configured=(ROOT/game.config.get('database','data/player.sqlite3')).resolve()
        if configured!=game.db.path.resolve() or not configured.is_relative_to(ROOT):return game
    file=path(game.config)
    if not file.exists():return game
    store=Store(file,False);publications=store.published()
    if not publications:return game
    at=clock(game);lat,lng=location_of(db,player,location)
    biome=game.world.habitat(lat,lng) if lat is not None and game.world.path else None
    entitled=lambda key:store.entitled(player,key,at)
    effective=resolve(publications,at,lat,lng,biome,store.regions(),entitled)
    # Special/Field Research may be independently published or referenced by an
    # active event. Their eligibility never alters another trainer's projection.
    from .rules import matches
    selected=[]
    for row in publications:
        spec=row['document']
        if spec['kind']!='research':continue
        start,end=interval(spec)
        if not start<=at<end:continue
        if spec.get('entitlement') and not entitled(spec['entitlement']):continue
        if not matches(spec.get('scope',{}),lat,lng,biome,store.regions()):continue
        if spec.get('event_only') and row['id'] not in effective['research']:continue
        from dittobuffer.gameplay.progression import bounds
        if bounds(game.config,game.player(db,player)['xp'])[0]<spec.get('minimum_level',1):continue
        if spec.get('prerequisite') and not db.execute('SELECT 1 FROM research_stories WHERE player_id=? AND id=? AND completed=1',(player,spec['prerequisite'])).fetchone():continue
        selected.append(row)
    if not effective['sources'] and not selected:return game
    view=copy(game);view.config=deepcopy(game.config);view.liveops_effective=effective;view.liveops_store=store;view.liveops_time=at
    # Instance-installed PvP RPC wrappers must run on the request projection.
    from types import MethodType
    for name,method in vars(game).items():
        if isinstance(method,MethodType) and method.__self__ is game:setattr(view,name,MethodType(method.__func__,view))
    view.config['evolution_xp']=int(game.config.get('evolution_xp',500)*effective['bonuses']['evolve_xp'])
    if 'spawns' in effective:
        entries=effective['spawns'];pools=[]
        for index,e in enumerate(entries):
            pools.append(dict(name='studio-'+str(index),species=[dict(id=e['species'],form=e.get('form',game.species[e['species']]['form']),weight=e.get('weight',1))],
                studio_weight=e.get('weight',1),biomes=[e['biome']] if e.get('biome') else [],time=e.get('time'),weather=e.get('weather',[]),
                shiny_rate=e.get('shiny_rate',0),level_range=e.get('level_range',[5,25])))
        view.spawn_pools=pools;view.liveops_base_pools=game.spawn_pools;view.config['spawn_generation']=effective['generations']['spawns']
    if 'raids' in effective:
        view.raids=deepcopy(game.raids);view.raids['pools']=deepcopy(effective['raids']);view.raids.pop('dev_raid',None)
        for boss in view.raids['pools']:
            boss.setdefault('xp',1000);boss.setdefault('dust',500);boss.setdefault('level',20);boss.setdefault('weight',1)
        view.config['raids']=view.raids
    if 'max' in effective:
        view.max_config=deepcopy(game.max_config);view.max_config['pools']=deepcopy(effective['max']);view.config['max_system']=view.max_config
        from dittobuffer.gameplay.max_schedule import Selection
        from dittobuffer.gameplay.game import now_ms
        wall=now_ms();remaining=max(1,(effective['next_boundary_ms'] or (at+86400000))-at)
        view.liveops_max_window=Selection('studio:'+str(effective['generations']['max']),wall,wall+remaining,tuple(deepcopy(effective['max'])),{},'studio','UTC')
    if 'rocket' in effective:
        view.rocket=deepcopy(game.rocket)|effective['rocket'];view.config['rocket']=view.rocket
    if 'eggs' in effective:
        eggs=deepcopy(game.config.get('eggs',{}))
        # One weighted pool per entry reuses the established egg selector without
        # dropping relative species weights or changing stored eggs.
        eggs['pools']=[dict(km=row['km'],weight=row.get('weight',1),species=[{k:v for k,v in row.items() if k not in ('km','weight')}]) for row in effective['eggs']];view.config['eggs']=eggs
    view.research=deepcopy(game.research)
    if any(row['document'].get('research_type')=='field' for row in selected):view.research['field_pool']=[]
    for row in selected:
        spec=row['document']
        if spec.get('research_type','special')=='field':
            objective=deepcopy(spec['stages'][0]['objectives'][0]);objective['title']=spec['name'];objective['rewards'].extend(deepcopy(spec['stages'][0]['rewards']))
            view.research['field_tasks'][row['id']]=objective
            view.research['field_pool'].append(dict(task=row['id'],weight=1))
        elif spec.get('research_type')=='breakthrough':
            view.research['breakthrough_rewards']=deepcopy(spec['rewards'])
            view.research['breakthrough_pool']=deepcopy(spec.get('encounter_pool',[]))
        else:
            story=deepcopy(spec);story.update(id=row['id'],title=spec['name'],start_ms=interval(spec)[0],end_ms=interval(spec)[1])
            view.research['stories'].append(story)
    if selected:view.research['enabled']=True
    return view


def spawn_selection(game,pools,rng,context=None):
    if not hasattr(game,'liveops_effective') or 'spawns' not in game.liveops_effective:return None
    context=context or {}
    eligible=[v for v in pools if (not v.get('time') or v['time']==('day' if context.get('day',True) else 'night')) and (not v.get('weather') or context.get('condition') in v['weather'])]
    # An unfulfilled habitat/weather condition never manufactures an encounter.
    eligible=eligible or getattr(game,'liveops_base_pools',[])
    return rng.choices(eligible,weights=[p.get('studio_weight',1) for p in eligible])[0]


def score(game,rows):
    effective=getattr(game,'liveops_effective',None)
    if not effective:return rows
    import math
    bonuses=effective['bonuses']
    # Legacy calendar factors are already present in these canonical score rows.
    # Coexist by taking the stronger event factor, rather than multiplying two
    # independent 2x configurations into an accidental 4x reward.
    from dittobuffer.gameplay.reward_calendar import stamp
    cfg=game.reward_bonuses;at=stamp(game);legacy=[1,1,1]
    if cfg['enabled']:
        for event in cfg['events']:
            if event['start_ms']<=at<event['end_ms']:legacy=[v*event.get(k,1) for v,k in zip(legacy,('xp','candy','dust'))]
    factors=[max(old,bonuses[key])/old for old,key in zip(legacy,('catch_xp','catch_candy','catch_dust'))]
    return [[kind,*[math.floor(value*factor) for value,factor in zip((xp,candy,dust),factors)]] for kind,xp,candy,dust in rows]


def task_matches(game,spec,context):
    conditions=spec.get('conditions',{})
    if not conditions:return True
    if not context:return False
    if conditions.get('species') and context.get('species') not in conditions['species']:return False
    for key in ('form','gender','alignment','max_mode','costume'):
        if key in conditions and context.get(key)!=conditions[key]:return False
    if conditions.get('types') and not set(conditions['types'])&set(context.get('types',[])):return False
    if conditions.get('shiny') and not context.get('shiny'):return False
    if conditions.get('weather_boosted') and not context.get('weather_boosted'):return False
    if conditions.get('ball') and context.get('ball')!=conditions['ball']:return False
    if conditions.get('curveball') and not context.get('curveball'):return False
    quality=conditions.get('throw_quality')
    if quality:
        import math
        value=context.get('quality',0)
        if not isinstance(value,(int,float)) or not math.isfinite(value) or not {'nice':1.3,'great':1.7,'excellent':1.85}[quality]<=value<=2:return False
    return True
