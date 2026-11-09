"""Original, deterministic and editable operator-owned event/season templates."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import random
from .rules import instant
from .store import digest

TEMPLATES={
 'research-beginner':('A New Adventure','Original progressive catch, spin, evolution and raid Research.',True),
 'research-weekend':('Weekend Explorer','Native Timed Research with catch, stop and Route stages.',True),
 'research-kanto':('Kanto Discovery','Native collection of curated Kanto encounters.',True),
 'research-signals':('Mysterious Signals','Original multistage Special Research and an editable encounter.',True),
 'research-discovery':('Seasonal Discovery','Frozen seven-stamp Breakthrough rotation.',True),
 'research-paths':('Field Notes: Choose a Path','Native exclusive catch or landmark observations.',True),
 'season-beginnings':('Season of Beginnings','Four editable Kanto/Johto weeks, raids, mini-events and Research.',True),
 'season-discovery':('Season of Discovery','Routes, eggs, postcards and original exploration Research.',True),
 'community-day':('Community gathering','Featured encounters, catch bonuses and Field Research.',True),
 'spotlight-hour':('An evening spotlight','One focused spawn pool and one catch bonus.',True),
 'raid-hour':('Skyward raid hour','An editable raid rotation using the existing battle engine.',True),
 'research-day':('Field notes day','A short catch-and-discover Field Research event.',True),
 'go-fest':('Habitat celebration','Rotating original habitats, raids and Special Research.',True),
 'kanto-tour':('Kanto field expedition','Kanto encounters, legendary rotations and an original story.',True),
 'johto-tour':('Johto field expedition','Johto encounters and a themed original story.',True),
 'rocket-takeover':('Shadow watch','Existing Rocket lineups and Rocket Research.',True),
 'max-weekend':('Power discovery weekend','Released Max bosses and Max Research.',True),
 'hatch-day':('A day of discoveries','Editable egg pools and earned hatching Research; no simulated walking.',True),
 'go-battle':('Friendly competition','PvP Research adapter and event league scheduling required.',False),
 'wild-area':('Wild frontiers','Mighty encounters and expiring Safari Balls require native adapters.',False),
}

def dates(start=None,hours=3):
    value=instant(start) if start is not None else int(datetime.now(timezone.utc).timestamp()*1000)
    fmt=lambda n:datetime.fromtimestamp(n/1000,timezone.utc).isoformat()
    return fmt(value),fmt(value+hours*3600000)

def base(kind,name,start=None,hours=3):
    start,end=dates(start,hours)
    return dict(kind=kind,name=name,description='',timezone='Europe/Amsterdam',start=start,end=end,
                scope={'type':'global'},priority=10 if kind=='season' else 50,rules={})

def task(event='catch',target=3,species=None):
    value=dict(event=event,target=target,description=f'{event.replace("_"," ").capitalize()} {target} Pokemon' if event=='catch' else f'{event.replace("_"," ").capitalize()} {target} times',rewards=[dict(type='xp',amount=300)])
    if species:value['conditions']={'species':[species]}
    return value

def research(name,start=None,event='catch',special=True,species=25):
    value=base('research',name,start,24*35);value.pop('rules');value['research_type']='special' if special else 'field';value['event_only']=True
    stage=dict(objectives=[task(event)],rewards=[dict(type='item',item=1,amount=5)])
    value['stages']=[stage]
    if special:
        stage['objectives'].append(task('spin',2))
        value['stages'].append(dict(objectives=[task('catch',5)],rewards=[dict(type='encounter',species=species,level=10,shiny_rate=0)]))
    return value

def boss(species,tier=1):
    hp,cp={1:(600,5000),3:(1200,9000),5:(2000,18000)}[tier]
    return dict(species=species,tier=tier,hp=hp,cp=cp,weight=1,level=20,xp={1:1000,3:3000,5:5000}[tier],dust=500,shiny_rate=0)

def template(key,catalog,start=None,featured=None):
    if key not in TEMPLATES:raise ValueError('Unknown template')
    title,description,runnable=TEMPLATES[key]
    if key in ('research-beginner','research-weekend','research-kanto','research-signals','research-discovery','research-paths'):return research_template(key,catalog,start)
    if key in ('season-beginnings','season-discovery'):
        bundle=generate(catalog,dict(name=title,theme='Exploration' if key.endswith('discovery') else 'Kanto and Johto',start=start,weeks=4,seed=key+'-v1',generations=[1,2],legendary_count=4))
        if key.endswith('discovery'):
            story=bundle['research'][0];story['stages']=[dict(objectives=[task('route',1),task('hatch',1)],rewards=[{'type':'xp','amount':1500}]),dict(objectives=[task('postcard_pin',1),task('buddy_candy',1)],rewards=[{'type':'encounter','species':133,'level':10,'shiny_rate':0}])]
        return bundle
    rows=[r for r in catalog.rows() if r['available'] and not r['special']]
    if key=='kanto-tour':rows=[r for r in rows if r['generation']==1]
    if key=='johto-tour':rows=[r for r in rows if r['generation']==2]
    if not rows:raise ValueError('Restore supported Pokemon before using this template')
    selected=next((r for r in rows if r['species']==featured),None) if featured else rows[0]
    if selected is None:raise ValueError('Featured Pokemon is unavailable')
    value=base('event',title,start,1 if key in ('spotlight-hour','raid-hour') else 8 if key in ('go-fest','kanto-tour','johto-tour') else 3)
    value.update(event_type=key,description=description)
    pool=[dict(species=r['species'],weight=10 if i<4 else 1,level_range=[5,25],shiny_rate=1/64 if r['shiny'] else 0) for i,r in enumerate(rows[:10])]
    if key in ('community-day','spotlight-hour'):pool=[dict(species=selected['species'],weight=100,level_range=[5,25],shiny_rate=1/25 if key=='community-day' and selected['shiny'] else 0)]
    value['rules']={'spawns':pool,'bonuses':{'catch_xp':2},'bonus_policy':'max'}
    if key=='raid-hour':value['rules']={'raids':[boss(selected['species'],3)]}
    if key in ('go-fest','kanto-tour','johto-tour'):
        value['rotations']=[dict(id='habitat-'+str(i),offset_minutes=i*60,duration_minutes=60,rules={'spawns':pool[i%len(pool):]+pool[:i%len(pool)] if i%2==0 else pool[:min(4,len(pool))]}) for i in range(8)]
    if key in ('go-fest','kanto-tour','johto-tour'):
        legends=[v for v in catalog.rows() if v['available'] and v['special']==1 and (key=='go-fest' or v['generation']==(1 if key=='kanto-tour' else 2))]
        if legends:value['rules']['raids']=[boss(legends[0]['species'],5),boss(selected['species'],1)]
        # Distinct type-themed pools; these are original rotating event habitats.
        value['rotations']=[]
        for i in range(8):
            typ=(12,11,13,10)[i%4]
            subset=[v for v in pool if typ in catalog.species[v['species']]['types']] or [pool[i%len(pool)]]
            value['rotations'].append(dict(id='habitat-'+str(i),offset_minutes=i*60,duration_minutes=60,rules={'spawns':deepcopy(subset)}))
    event='rocket_grunt' if key=='rocket-takeover' else 'max_win' if key=='max-weekend' else 'hatch' if key=='hatch-day' else 'catch'
    research_doc=research(title+' field notes',value['start'],event,special=key in ('go-fest','kanto-tour','johto-tour'),species=selected['species'])
    if key=='hatch-day':
        runnable=bool(catalog.config.get('eggs',{}).get('pools'))
        value['rules']['eggs']=[dict(species=v['species'],km=2,weight=1,shiny_rate=0) for v in rows[:4]]
        research_doc['stages'][0]['objectives'][0]['target']=1;research_doc['stages'][0]['objectives'][0]['description']='Hatch an egg'
    if key=='rocket-takeover':
        if not catalog.config.get('rocket',{}).get('server_combat'):runnable=False
        else:value['rules']['rocket']={'hideout_probability':.5}
    if key=='max-weekend':
        pools=catalog.config.get('max_system',{}).get('pools',[])
        if not pools:runnable=False
        else:value['rules']['max']=deepcopy(pools[:3])
    if not runnable:value['required_capabilities']=[description]
    return dict(document=value,research=[research_doc],runnable=runnable,provenance='Original DittoBuffer format-inspired sample; custom balance, not an official historical event')


def generate(catalog,options):
    name=options.get('name','Season of Beginnings');weeks=options.get('weeks',4)
    if type(weeks) is not int or not 1<=weeks<=12:raise ValueError('Choose 1 to 12 weeks')
    gens=options.get('generations',[1,2])
    if not isinstance(gens,list) or not gens or any(type(v) is not int or not 1<=v<=9 for v in gens):raise ValueError('Choose available generations')
    seed=str(options.get('seed','beginnings-v1'))
    if len(seed)>120:raise ValueError('Generator seed is too long')
    difficulty=options.get('difficulty','normal')
    if difficulty not in ('easy','normal','challenging'):raise ValueError('Unknown difficulty')
    rng=random.Random(seed);rows=[v for v in catalog.rows() if v['available'] and v['generation'] in gens]
    ordinary=[v for v in rows if not v['special']];legends=[v for v in rows if v['special']==1]
    if len(ordinary)<4:raise ValueError('At least four available ordinary species are required')
    theme=options.get('theme','Discovery');themed=options.get('types',[])
    if themed:ordinary.sort(key=lambda v:(not bool(set(v['types'])&set(themed)),v['species']))
    else:rng.shuffle(ordinary)
    rng.shuffle(legends);legend_count=options.get('legendary_count',min(weeks,len(legends)))
    if type(legend_count) is not int or not 0<=legend_count<=min(weeks,len(legends)):raise ValueError('Legendary count exceeds distinct supported bosses')
    value=base('season',name,options.get('start'),24*7*weeks)
    value.update(theme=theme,generations=gens,description='An original editable season generated from the restored client catalog.',weeks=[],generator={'version':1,'seed':seed,'options':deepcopy(options)})
    value['scope']=deepcopy(options.get('scope',{'type':'global'}))
    value['rules']={'bonuses':{'catch_xp':1},'bonus_policy':'max'}
    research_docs=[research(name+' - First steps',value['start'],species=ordinary[0]['species']),research(name+' - Field notes',value['start'],special=False,species=ordinary[0]['species'])]
    events=[]
    for index in range(weeks):
        chosen=[ordinary[(index*5+i)%len(ordinary)] for i in range(min(12,len(ordinary)))]
        entries=[dict(species=v['species'],weight=12 if v['stats']<350 else 5 if v['stats']<450 else 1,level_range=[3,20] if difficulty=='easy' else [5,30],shiny_rate=1/512 if options.get('shinies',True) and v['shiny'] else 0) for v in chosen]
        raid_entries=[boss(chosen[0]['species'],1),boss(chosen[-1]['species'],3)]
        if index<legend_count:raid_entries.append(boss(legends[index]['species'],5))
        week_rules={'spawns':entries,'raids':raid_entries}
        if catalog.config.get('eggs',{}).get('pools'):
            week_rules['eggs']=[dict(species=v['species'],km=2 if i<2 else 5,weight=1,shiny_rate=0) for i,v in enumerate(chosen[:3])]
        if catalog.config.get('mega',{}).get('enabled'):
            from dittobuffer.gameplay.mega import branch
            megas=[v for v in ordinary if branch(v['species'],1)]
            if megas:
                mega=boss(megas[index%len(megas)]['species'],3)
                mega.update(tier=6,mega_evolution=1,reward_policy='Explicit original studio balance',mega_energy_rewards=[{'max_seconds':3600,'amount':100}])
                week_rules['raids'].append(mega)
        released=catalog.config.get('max_system',{}).get('pools',[])
        if released:
            available=[v for v in released if (v['species'],v.get('form',catalog.species[v['species']]['form'])) in catalog.ready]
            if available:week_rules['max']=[deepcopy(available[index%len(available)])]
        value['weeks'].append(dict(name='Week '+str(index+1),theme=theme,rules=week_rules))
        offset=index*7*86400000
        if options.get('events',True):
            local_start=datetime.fromtimestamp(instant(value['start'])/1000,ZoneInfo(value['timezone']))+timedelta(weeks=index)
            scheduled=lambda days,hour:(local_start+timedelta(days=days)).replace(hour=hour,minute=0,second=0,microsecond=0).isoformat()
            events.append(template('spotlight-hour',catalog,scheduled(2,18),chosen[0]['species']))
            mini=template('research-day',catalog,scheduled(4,11),chosen[1]['species']);mini['document']['name']=name+' - Week '+str(index+1)+' field gathering';events.append(mini)
            if index==min(1,weeks-1):events.append(template('community-day',catalog,scheduled(5,14),chosen[0]['species']))
    # Derived identity only describes the draft's reproducible content; publish
    # IDs are assigned once by the store and reused through edits/regeneration.
    return dict(document=value,research=research_docs,events=events,seed=seed,fingerprint=digest(value),
                warnings=['Collection/Timed Research, featured moves and path selection require native adapters and are not silently enabled.'],
                provenance='Custom operator-owned balance; no official numeric event tables inferred.')


def research_template(key,catalog,start=None):
    title,description,_=TEMPLATES[key];doc=base('research',title,start,48 if key=='research-weekend' else 24*35);doc.pop('rules');doc.update(description=description,research_type='special',event_only=False)
    ordinary=[r for r in catalog.rows() if r['available'] and not r['special']]
    if not ordinary:raise ValueError('Restore verified models before creating Research')
    selected=next((r['species'] for r in ordinary if r['species']==1),ordinary[0]['species'])
    def step(*objectives,final=False):return dict(objectives=list(objectives),rewards=[dict(type='encounter',species=selected,level=10,shiny_rate=0)] if final else [dict(type='xp',amount=500)],dialog='Every observation adds a new detail to our field notes. Let us compare what you discover.')
    if key=='research-beginner':doc['stages']=[step(task('catch',3),task('spin',3)),step(task('evolve',1)),step(task('raid',1),final=True)]
    elif key=='research-weekend':doc.update(research_type='timed',stages=[step(task('catch',5),task('spin',3)),step(task('route',1),final=True)])
    elif key=='research-signals':doc['stages']=[step(task('catch',3),task('spin',2)),step(task('evolve',1)),step(task('raid',1),final=True)]
    elif key=='research-kanto':doc.update(research_type='collection',entries=[dict(species=r['species'],methods=['catch']) for r in ordinary if r['species'] in (1,4,7,16,19,25)],rewards=[dict(type='xp',amount=1000),dict(type='item',item=1,amount=10)])
    elif key=='research-discovery':doc.update(research_type='breakthrough',rewards=[dict(type='xp',amount=3000),dict(type='stardust',amount=2000),dict(type='item',item=1,amount=10)],encounter_pool=[dict(type='encounter',species=r['species'],level=15,shiny_rate=0,weight=1) for r in ordinary[:3]])
    elif key=='research-paths':doc.update(research_type='branching',paths=[dict(id='pokemon',title='Observe Pokemon',description='Focus on new encounters.',rewards=[],stages=[step(task('catch',3),final=True)]),dict(id='landmarks',title='Observe Landmarks',description='Focus on nearby PokeStops.',rewards=[],stages=[step(task('spin',3),final=True)])])
    return dict(document=doc,research=[],runnable=True,provenance='Original DittoBuffer Research sample; explicit editable custom balance. Does not activate until separately published.')
