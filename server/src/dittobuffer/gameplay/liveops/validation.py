"""Strict declarative schema; drafts may be incomplete, publications may not."""
from copy import deepcopy
import math,re
from .rules import BONUSES,interval,validate_scope
from .catalog import TASKS,ITEMS

KINDS=('season','event','research')
EVENT_TYPES=('custom','community-day','spotlight-hour','raid-hour','go-fest','kanto-tour','johto-tour','wild-area','research-day','hatch-day','go-battle','rocket-takeover','max-weekend')

def number(value,low,high,label,integer=False):
    if type(value) not in ((int,) if integer else (int,float)) or not math.isfinite(value) or not low<=value<=high:raise ValueError(label+' is outside its permitted range')
    return value

def reward(value,catalog,source='special'):
    if not isinstance(value,dict):raise ValueError('Invalid reward')
    from dittobuffer.gameplay.research import validate_reward
    if value.get('type')=='encounter':
        catalog.mon(value)
        if value.get('special_encounter'):
            from dittobuffer.gameplay.special_encounters import registry,resource_status
            definition=registry().get(value['special_encounter'])
            if not definition or not resource_status(catalog.game(),definition)['ready']:raise ValueError('Restore the native cinematic resource closure before publishing')
        if source=='field' and value.get('special_encounter'):raise ValueError('Native cinematics require Special Research')
    elif value.get('type')=='item' and value.get('item') not in ITEMS:raise ValueError('Unsupported reward item')
    validate_reward(catalog.game(),value)

def objective(value,catalog,source='special'):
    if value.get('event') not in TASKS:raise ValueError('This objective has no supported gameplay counter')
    number(value.get('target'),1,10000,'Task target',True)
    if not isinstance(value.get('description'),str) or not value['description'].strip():raise ValueError('Task description is required')
    if not 1<=len(value.get('rewards',[]))<=5:raise ValueError('Choose one to five task rewards')
    for r in value['rewards']:reward(r,catalog,source)
    conditions=value.get('conditions',{})
    if set(conditions)-{'species','types','shiny','ball','throw_quality','curveball','weather_boosted','form','gender','alignment','max_mode','costume'}:raise ValueError('Unsupported objective condition')
    if conditions and value['event'] not in ('catch','throw','hatch','evolve'):raise ValueError('These filters are only supported for Pokemon acquisition/throws')
    for key in ('shiny','curveball','weather_boosted'):
        if key in conditions and type(conditions[key]) is not bool:raise ValueError('Objective flags must be boolean')
    for sid in conditions.get('species',[]):catalog.mon({'species':sid,**({'form':conditions['form']} if 'form' in conditions else {})})
    for key,hi in (('gender',3),('alignment',2),('max_mode',2),('costume',10000)):
        if key in conditions:number(conditions[key],0,hi,key,True)
    for typ in conditions.get('types',[]):number(typ,1,18,'Pokemon type',True)
    if conditions.get('ball') not in (None,1,2,3,5):raise ValueError('Unsupported ball filter')
    if conditions.get('throw_quality') not in (None,'','nice','great','excellent'):raise ValueError('Unsupported throw quality')


def rules(value,catalog):
    if not isinstance(value,dict):raise ValueError('Gameplay rules must be an object')
    if set(value)-{'spawns','raids','bonuses','bonus_policy','research','eggs','max','rocket'}:raise ValueError('One or more gameplay rules have no runtime adapter')
    if value.get('bonus_policy','max') not in ('max','replace','multiply'):raise ValueError('Unknown bonus combination policy')
    for key,factor in value.get('bonuses',{}).items():
        if key not in BONUSES:raise ValueError('Unsupported bonus')
        number(factor,1,10,'Bonus multiplier')
    for group in ('spawns','raids','eggs','max'):
        if group in value and group=='raids' and not catalog.config.get('raids',{}).get('enabled'):raise ValueError('Enable the existing Raid module before publishing raid rotations')
        if group not in value:continue
        entries=value[group]
        if not isinstance(entries,list) or not 1<=len(entries)<=300:raise ValueError(group+' needs a nonempty, bounded pool')
        for row in entries:
            model=catalog.mon(row)
            number(row.get('weight',1),.0001,100000,'Spawn weight')
            if group=='spawns':
                lo,hi=row.get('level_range',[5,25]);number(lo,1,40,'Minimum level',True);number(hi,lo,40,'Maximum level',True)
                if model.get('class') in (1,2,3) and not row.get('allow_special'):raise ValueError('Legendary/mythical wild spawns require an explicit exception')
                if row.get('time') not in (None,'','day','night'):raise ValueError('Invalid spawn time')
                if row.get('biome') and row['biome'] not in ('forest','park','urban','water','beach','mountain','grassland'):raise ValueError('Unknown spawn habitat')
                for condition in row.get('weather',[]):number(condition,1,7,'Weather condition',True)
            if group=='raids':
                if row.get('tier') not in (1,3,5,6):raise ValueError('Unsupported raid category')
                for k in ('hp','cp'):number(row.get(k),1,1000000,'Raid '+k,True)
                if row.get('tier')==6:
                    from dittobuffer.gameplay.mega import branch
                    if not catalog.config.get('mega',{}).get('enabled',True) or not branch(row['species'],row.get('mega_evolution')):raise ValueError('Unsupported Mega raid branch')
                    if not row.get('mega_energy_rewards') or not row.get('reward_policy'):raise ValueError('Mega rewards require explicit supported tuning')
            if group=='eggs' and row.get('km') not in (2,5,10):raise ValueError('Only supported 2, 5 and 10 km eggs may be published')
            if group=='max':
                cfg=catalog.config.get('max_system',{})
                if not cfg.get('enabled') or not any(v['species']==row['species'] and v.get('mode',1)==row.get('mode',1) for v in cfg.get('pools',[])):raise ValueError('Max boss must be explicitly released in the existing Max configuration')
    if 'rocket' in value:
        allowed={'balloon_period_seconds','balloon_themes','hideout_probability'}
        if set(value['rocket'])-allowed or not catalog.config.get('rocket',{}).get('server_combat'):raise ValueError('Unsupported Rocket event setting')
        if 'balloon_period_seconds' in value['rocket']:number(value['rocket']['balloon_period_seconds'],1200,86400,'Balloon period',True)
        if 'hideout_probability' in value['rocket']:number(value['rocket']['hideout_probability'],0,1,'Hideout chance')
    for key in value.get('research',[]):
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',key):raise ValueError('Research references must be studio IDs')


def validate(document,catalog,lookup=None):
    value=deepcopy(document)
    if value.get('kind') not in KINDS:raise ValueError('Choose Season, Event or Research')
    for key in ('id',):
        if key in value and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value[key]):raise ValueError('Invalid content identity')
    if not isinstance(value.get('name'),str) or not 1<=len(value['name'].strip())<=160:raise ValueError('Name is required (maximum 160 characters)')
    if len(value.get('description',''))>8000:raise ValueError('Description is too long')
    interval(value);validate_scope(value.get('scope',{}));number(value.get('priority',10 if value['kind']=='season' else 50),0,1000,'Priority',True)
    if value.get('event_type','custom') not in EVENT_TYPES:raise ValueError('Unknown event format')
    if value.get('kind')=='research':
        typ=value.get('research_type','special')
        if typ not in ('field','special','timed','collection','branching','timed_branching','breakthrough'):raise ValueError('This native Research layout is not yet supported by the studio')
        number(value.get('minimum_level',1),1,80,'Required trainer level',True)
        if value.get('prerequisite') and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value['prerequisite']):raise ValueError('Invalid prerequisite Research')
        def stages_check(stages):
            if not 1<=len(stages)<=100:raise ValueError('Research needs 1 to 100 steps')
            for stage in stages:
                if not 1<=len(stage.get('objectives',[]))<=3:raise ValueError('Native multi-part Research supports one to three tasks per step')
                for task in stage['objectives']:objective(task,catalog,typ)
                if not stage.get('rewards'):raise ValueError('Choose a step reward')
                for r in stage['rewards']:reward(r,catalog,typ)
        allowed_fields={'collection':{'entries','rewards'},'breakthrough':{'rewards','encounter_pool'},'branching':{'paths'},'timed_branching':{'paths'}}.get(typ,{'stages'})
        for field in {'entries','rewards','encounter_pool','paths','stages'}-allowed_fields:
            if value.get(field):raise ValueError('Research type '+typ+' cannot consume '+field+'; remove it explicitly')
        if typ=='collection':
            entries=value.get('entries',[])
            if not 1<=len(entries)<=100:raise ValueError('Collection needs one to 100 Pokemon criteria')
            for entry in entries:
                catalog.mon(entry)
                methods=entry.get('methods',['catch'])
                if not isinstance(methods,list) or not methods or len(set(methods))!=len(methods) or set(methods)-{'catch','evolve','hatch','trade','raid'}:raise ValueError('Unsupported collection acquisition method')
            if not value.get('rewards'):raise ValueError('Collection needs rewards')
            for r in value['rewards']:reward(r,catalog,typ)
        elif typ=='breakthrough':
            if not value.get('rewards'):raise ValueError('Breakthrough needs rewards')
            for r in value['rewards']:reward(r,catalog,typ)
            for r in value.get('encounter_pool',[]):
                reward(r,catalog,typ);number(r.get('weight',1),1,100000,'Encounter weight',True)
                if r['type']!='encounter':raise ValueError('Breakthrough pool entries must be encounters')
        elif typ in ('branching','timed_branching'):
            paths=value.get('paths',[])
            if not 2<=len(paths)<=4 or len({v.get('id') for v in paths})!=len(paths):raise ValueError('Choose two to four unique Research paths')
            for path in paths:
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',path.get('id','')) or not path.get('title'):raise ValueError('Each path needs an ID and title')
                stages_check(path.get('stages',[]))
                for r in path.get('rewards',[]):reward(r,catalog,typ)
        else:
            stages_check(value.get('stages',[]))
            if typ=='field' and (len(value['stages'])!=1 or len(value['stages'][0]['objectives'])!=1):raise ValueError('Field Research has a single objective')

    else:
        if value.get('entitlement') and any(any(k in rule for k in ('raids','max','rocket')) for rule in [value.get('rules',{}),*[w.get('rules',{}) for w in value.get('weeks',[])],*[r.get('rules',{}) for r in value.get('rotations',[])]]):raise ValueError('Shared Raid, Max and Rocket world objects cannot use per-player entitlements')
        rules(value.get('rules',{}),catalog)
        for week in value.get('weeks',[]):rules(week.get('rules',{}),catalog)
        for row in value.get('rotations',[]):
            if not isinstance(row.get('id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',row['id']):raise ValueError('Timed rotation needs a stable name using letters, digits, dashes or underscores')
            number(row.get('offset_minutes'),0,527040,'Rotation offset',True);number(row.get('duration_minutes'),1,527040,'Rotation length',True);rules(row.get('rules',{}),catalog)
        if len(value.get('weeks',[]))>52 or len(value.get('rotations',[]))>400:raise ValueError('Schedule is too large')
        if lookup:
            for rule in [value.get('rules',{}),*[w.get('rules',{}) for w in value.get('weeks',[])],*[w.get('rules',{}) for w in value.get('rotations',[])]]:
                for key in rule.get('research',[]):
                    found=lookup(key)
                    if found['document']['kind']!='research' or found['published_revision'] is None:raise ValueError('Publish referenced Research before its event')
    return value
