"""Persistent local Research, inside the existing gameplay transactions."""
import hashlib
import json
import random
import secrets
from dittobuffer.gameplay.game import ROOT,p,now_ms
from dittobuffer.gameplay.species import make_pokemon,pool_rules

from dittobuffer.gameplay.research_task_registry import TASKS
TYPES={key:value[1] for key,value in TASKS.items()}

def validate(game):
    bundled=json.loads((ROOT/'src/dittobuffer/gameplay/research.json').read_text())
    # Admin edits live in data/research.json; the bundled file stays a pristine
    # default so a fresh checkout always boots.
    custom=ROOT/'data/research.json'
    if custom.is_file():
        try:
            value=json.loads(custom.read_text(encoding='utf8'))
            if isinstance(value,dict):bundled.update(value)
        except (OSError,ValueError):
            raise ValueError('data/research.json is not valid JSON') from None
    rules=bundled
    rules.update(game.config.get('research',{}));game.research=rules
    if type(rules['enabled']) is not bool:raise ValueError('Invalid research enable flag')
    if type(rules['capacity']) is not int or not 1<=rules['capacity']<=3:raise ValueError('Invalid research capacity')
    if type(rules['day_offset_minutes']) is not int or not -840<=rules['day_offset_minutes']<=840:raise ValueError('Invalid research day boundary')
    if rules.get('timezone'):
        from zoneinfo import ZoneInfo
        ZoneInfo(rules['timezone'])
    templates=rules['field_tasks']
    for key,value in templates.items():
        if not key or value['event'] not in TYPES or type(value['target']) is not int or not 1<=value['target']<=10000:raise ValueError('Invalid research objective')
        if not value.get('description') or not value.get('rewards'):raise ValueError('Research needs display and rewards')
        for reward in value['rewards']:
            validate_reward(game,reward)
            if reward.get('special_encounter'):raise ValueError('Native cinematics require a StoryQuest research source')
    for entry in rules['field_pool']:
        if entry['task'] not in templates or type(entry['weight']) is not int or not 1<=entry['weight']<=100000:raise ValueError('Invalid research pool')
    if any(v not in templates for v in rules.get('field_assignments',{}).values()):raise ValueError('Invalid fixed research assignment')
    for reward in rules['breakthrough_rewards']:
        validate_reward(game,reward)
        if reward.get('special_encounter'):raise ValueError('Native cinematics require a StoryQuest research source')
    for entry in rules.get("breakthrough_pool",[]):
        if entry.get("type")!="encounter" or type(entry.get("weight",1)) is not int or not 1<=entry.get("weight",1)<=100000:raise ValueError("Invalid breakthrough encounter pool")
        validate_reward(game,entry)
        if entry.get("special_encounter"):raise ValueError("Native cinematics require a StoryQuest research source")
    from dittobuffer.gameplay import stories
    stories.validate(game)

def validate_reward(game,reward):
    kind=reward.get('type')
    if kind not in ('item','xp','stardust','encounter','candy','xl_candy','mega_energy'):raise ValueError('Unsupported research reward')
    if kind=='encounter':
        if reward.get('species') not in game.species or type(reward.get('level',10)) is not int or not 1<=reward.get('level',10)<=40:raise ValueError('Invalid research Pokemon')
        pool_rules(game,reward)
        from dittobuffer.gameplay.special_encounters import validate_reward as validate_special
        validate_special(game,reward)
        if reward.get('max_mode'):
            cfg=game.config.get('max_system',{})
            form=reward.get('form',game.species[reward['species']]['form'])
            if not cfg.get('enabled') or reward['max_mode'] not in (1,2) or not any(v['species']==reward['species'] and v.get('form',game.species[v['species']]['form'])==form and v.get('mode',1)==reward['max_mode'] for v in cfg.get('pools',[])):
                raise ValueError('Research Max reward must be explicitly released')
    else:
        if type(reward.get('amount')) is not int or not 1<=reward['amount']<=100000:raise ValueError('Invalid research reward quantity')
        if kind in ('candy','xl_candy','mega_energy'):
            if reward.get('species') not in game.species:raise ValueError('Candy reward needs a supported species')
            if kind=='mega_energy':
                from dittobuffer.gameplay.mega import enabled,branch
                if not enabled(game) or not branch(reward['species'],reward.get('mega_evolution',1)):raise ValueError('Unsupported Mega Energy reward')
        supported={1,2,3,101,102,103,104,201,202,701,901,902}
        if game.config.get('rocket',{}).get('enabled') and game.config.get('rocket',{}).get('server_combat'):supported.update((1501,1502,1503))
        if kind=='item' and reward.get('item') not in supported:raise ValueError('Unsupported research item')

def day(game):
    from dittobuffer.gameplay.research_lifecycle import clock
    at=clock(game) if game.config.get('liveops',{}).get('isolated_fixture') else now_ms()
    if game.research.get('timezone'):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(at/1000,ZoneInfo(game.research['timezone'])).date().isoformat()
    return str((at+game.research['day_offset_minutes']*60000)//86400000)

def settings(game):
    """Safe JSON snapshot used by all admin surfaces."""
    return {key:value for key,value in game.research.items()}


def save(game,value,path=None):
    """Validate and persist a research document, returning the normalised copy."""
    if not isinstance(value,dict):raise ValueError('Research settings must be an object')
    merged=json.loads(json.dumps(game.research));merged.update(value)
    previous=game.research;game.research=merged
    try:
        validate(game)
    except Exception:
        game.research=previous
        raise
    target=ROOT/'data/research.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix('.tmp')
    temporary.write_text(json.dumps(game.research,indent=2)+'\n',encoding='utf8')
    temporary.replace(target)
    return settings(game)


def ensure(game,db,player):
    if game.research['enabled'] and db.execute('INSERT OR IGNORE INTO research_cards(player_id) VALUES(?)',(player,)).rowcount:game.touch(db,player)
    if game.research['enabled']:
        from dittobuffer.gameplay import stories
        stories.ensure(game,db,player)
        from dittobuffer.gameplay.level_research import ensure as ensure_levels
        ensure_levels(game,db,player)
        rows=active(db,player);used={json.loads(row['definition'])['slot'] for row in rows if 'slot' in json.loads(row['definition'])}
        for row in rows:
            spec=json.loads(row['definition'])
            if 'slot' in spec:continue
            spec['slot']=next(n for n in range(game.research['capacity']) if n not in used);used.add(spec['slot'])
            db.execute('UPDATE research_tasks SET definition=? WHERE player_id=? AND id=?',(json.dumps(spec),player,row['id']));game.touch(db,player)

def card(game,db,player):
    ensure(game,db,player)
    cycle=db.execute('SELECT cycle FROM research_cards WHERE player_id=?',(player,)).fetchone()[0]
    out=p.QuestStampCardProto(target=7,id=f'dittobuffer-stamps-{cycle}',remaining_daily_stamps=0 if db.execute('SELECT 1 FROM research_stamps WHERE player_id=? AND day=?',(player,day(game))).fetchone() else 1)
    for row in db.execute('SELECT stamp_ms FROM research_stamps WHERE player_id=? AND cycle=? ORDER BY stamp_ms',(player,cycle)):out.stamp.add(context=2,timestamp_ms=row[0])
    if len(out.stamp)>=7:freeze_breakthrough(game,db,player)
    return out

def reward_proto(game,value):
    result=p.QuestRewardProto()
    if value['type']=='item':result.type=2;result.item.item=value['item'];result.item.amount=value['amount']
    elif value['type']=='xp':result.type=1;result.exp=value['amount']
    elif value['type']=='stardust':result.type=3;result.stardust=value['amount']
    elif value['type'] in ('candy','xl_candy','mega_energy'):
        field={'candy':'candy','xl_candy':'xl_candy','mega_energy':'mega_resource'}[value['type']]
        result.type={'candy':4,'xl_candy':9,'mega_energy':12}[value['type']]
        getattr(result,field).pokemon_id=value['species'];getattr(result,field).amount=value['amount']
    else:
        result.type=7;result.pokemon_encounter.pokemon_id=value['species']
        result.pokemon_encounter.pokemon_display.form=pool_rules(game,value)['form']
        result.pokemon_encounter.pokemon_display.costume=value.get('costume',0)
        if value.get('shiny_rate')==1:result.pokemon_encounter.pokemon_display.shiny=True
        result.pokemon_encounter.pokemon_display.bread_mode_enum=value.get('max_mode',0)
    return result

def task_proto(game,row):
    spec=json.loads(row['definition'])
    quest=p.QuestProto(quest_id=row['id'],template_id=spec['template'],quest_context=1 if row['parent_id'] else 2,
        quest_type=TYPES[spec['event']],progress=row['progress'],status=2 if row['claimed'] else 1,
        creation_timestamp_ms=row['created_ms'],last_update_timestamp_ms=row['updated_ms'],
        completion_timestamp_ms=row['completed_ms'],fort_id=row['fort_id'] or '')
    quest.goal.target=spec['target']
    if spec['event']=='catch':quest.catch_pokemon.SetInParent()
    if spec['event']=='spin':quest.spin_pokestop.SetInParent()
    for value in spec['rewards']:quest.quest_rewards.add().CopyFrom(reward_proto(game,value))
    return quest

def client_task(game,row):
    spec=json.loads(row['definition']);result=p.ClientQuestProto(quest=task_proto(game,row))
    result.quest_display.quest_id=row['id'];result.quest_display.description=spec['description']
    result.quest_display.title=spec.get('title','Local Field Research')
    result.quest_display.slot=spec.get('slot',0)
    return result

def active(db,player):
    return db.execute('SELECT * FROM research_tasks WHERE player_id=? AND parent_id IS NULL AND claimed=0 AND abandoned=0 ORDER BY created_ms,id',(player,)).fetchall()

def award_field(game,db,player,fort):
    if not game.research['enabled'] or not game.research['field_pool']:return None
    if len(active(db,player))>=game.research['capacity']:return None
    today=day(game)
    if db.execute('SELECT 1 FROM research_visits WHERE player_id=? AND fort_id=? AND day=?',(player,fort['id'],today)).fetchone():return None
    task=game.research.get('field_assignments',{}).get(fort['id'])
    if task is None:
        rng=random.Random(hashlib.sha256(f'{fort["id"]}:{today}'.encode()).digest())
        pool=game.research['field_pool'];task=rng.choices([v['task'] for v in pool],weights=[v['weight'] for v in pool])[0]
    used={json.loads(row['definition']).get('slot',0) for row in active(db,player)}
    spec=dict(game.research['field_tasks'][task],template='LIBRE_FIELD_'+task,slot=next(n for n in range(game.research['capacity']) if n not in used))
    identifier='dittobuffer-field-'+secrets.token_hex(12);stamp=now_ms()
    db.execute('INSERT INTO research_tasks(player_id,id,definition,created_ms,updated_ms,fort_id) VALUES(?,?,?,?,?,?)',
        (player,identifier,json.dumps(spec),stamp,stamp,fort['id']))
    db.execute('INSERT INTO research_visits VALUES(?,?,?,?)',(player,fort['id'],today,identifier))
    game.touch(db,player)
    return client_task(game,db.execute('SELECT * FROM research_tasks WHERE player_id=? AND id=?',(player,identifier)).fetchone())

def progress(game,db,player,event,amount=1,context=None):
    if not game.research['enabled']:return
    from dittobuffer.gameplay.level_research import ensure as ensure_levels
    ensure_levels(game,db,player)
    for row in db.execute('SELECT * FROM research_tasks WHERE player_id=? AND claimed=0 AND abandoned=0',(player,)).fetchall():
        spec=json.loads(row['definition'])
        from dittobuffer.gameplay import research_lifecycle as lifecycle, stories
        if spec.get('verified_gate'):
            from dittobuffer.gameplay.level_research import progress_task
            progress_task(game,db,player,row,spec,event,amount,context or {})
            continue
        if not lifecycle.matches_collection(spec,event,context) or row['progress']>=spec['target']:continue
        if row['parent_id']:
            owner=stories.lookup(game,db,player,row['parent_id'])
            if not owner or not lifecycle.available(game,json.loads(owner[0]['definition'])):continue
        from dittobuffer.gameplay.liveops.runtime import task_matches
        if not task_matches(game,spec,context):continue
        value=min(spec['target'],row['progress']+amount);stamp=now_ms()
        db.execute('UPDATE research_tasks SET progress=?,updated_ms=?,completed_ms=? WHERE player_id=? AND id=?',
            (value,stamp,stamp if value==spec['target'] else 0,player,row['id']))
        game.touch(db,player)

def inventory(game,db,player):
    result=p.HoloInventoryItemProto();result.quests.SetInParent()
    from dittobuffer.gameplay.reward_calendar import quests as daily_quests
    daily_quests(game,db,player,result.quests)
    from dittobuffer.gameplay.max_system import walking_quest
    walk=walking_quest(game,db,player)
    if walk:result.quests.quest.add().CopyFrom(walk)
    if game.research['enabled']:
        result.quests.stamp_card.CopyFrom(card(game,db,player))
        for row in active(db,player):result.quests.quest.add().CopyFrom(task_proto(game,row))
        from dittobuffer.gameplay import stories
        for quest in stories.current(game,db,player):result.quests.quest.add().CopyFrom(quest.quest)
        result.quests.completed_story_quest.extend(stories.completed(db,player))
        for row in db.execute("SELECT r.*,a.data,e.state FROM research_encounters r JOIN spawn_attributes a ON a.spawn_id=r.spawn_id LEFT JOIN encounters e ON e.player_id=r.player_id AND e.spawn_id=r.spawn_id WHERE r.player_id=? AND r.encounter_type!=15 AND (e.state IS NULL OR e.state!='caught') ORDER BY r.created_ms",(player,)):
            result.quests.quest_pokemon_encounter.add(quest_id=row['client_quest_id'],pokemon=p.PokemonProto.FromString(row['data']),encounter_type=row['encounter_type'])
    return result

def get_new(game,db,player,body):
    p.GetNewQuestsProto.FromString(body);out=p.GetNewQuestsOutProto(status=1)
    if game.research['enabled']:
        for row in active(db,player):out.quests.add().CopyFrom(client_task(game,row))
        from dittobuffer.gameplay import stories
        for quest in stories.current(game,db,player):out.quests.add().CopyFrom(quest)
        out.removed_quest_ids.extend(stories.past_roots(db,player))
        from dittobuffer.gameplay import research_lifecycle as lifecycle
        out.removed_quest_ids.extend(stories.root_id(r['id'],r['stage_index']) for r in db.execute('SELECT * FROM research_stories WHERE player_id=? AND completed=0',(player,)) if not lifecycle.available(game,json.loads(r['definition'])))
    return out

def details(game,db,player,body):
    req=p.GetQuestDetailsProto.FromString(body);out=p.GetQuestDetailsOutProto(status=1)
    for identifier in dict.fromkeys(req.quest_id):
        from dittobuffer.gameplay.max_system import walking_quest
        walk=walking_quest(game,db,player)
        if walk and walk.quest_id==identifier:
            out.quests.add().quest.CopyFrom(walk);continue
        from dittobuffer.gameplay import stories
        story=stories.lookup(game,db,player,identifier)
        if story:
            from dittobuffer.gameplay.research_lifecycle import available
            if available(game,json.loads(story[0]['definition'])):out.quests.add().CopyFrom(stories.client_quest(game,db,player,*story))
            continue
        row=db.execute('SELECT * FROM research_tasks WHERE player_id=? AND id=?',(player,identifier)).fetchone()
        if row and not row['abandoned']:out.quests.add().CopyFrom(client_task(game,row))
        else:out.status=2
    return out

def ui(game,db,player,body):
    p.GetQuestUiProto.FromString(body);out=p.GetQuestUiOutProto(status=1)
    if not game.research['enabled']:return out
    out.today_view.inner_layer_type=2
    out.today_view.inner_label_key='Field Research'
    out.today_view.sections.add().stamp_cards.SetInParent()
    section=out.today_view.sections.add().challenge_quests
    section.SetInParent()
    section.quest_id.extend(row['id'] for row in active(db,player))
    out.special_view.inner_layer_type=4
    out.special_view.inner_label_key='Special Research'
    from dittobuffer.gameplay import stories
    section=out.special_view.sections.add().story_quests
    section.SetInParent()
    out.season_view.inner_layer_type=3;out.season_view.inner_label_key='Events'
    for value in stories.current(game,db,player):
        quest=value.quest
        if quest.quest_context==9:out.season_view.sections.add().mini_collection.quest_id=quest.quest_id
        elif quest.quest_context==4:out.season_view.sections.add().timed_quest.quest_id=quest.quest_id
        elif quest.quest_context==17:out.season_view.sections.add().timed_branching_quest.quest_id=quest.quest_id
        elif quest.quest_context!=6:section.quest_id.append(quest.quest_id)
    return out

def remove(game,db,player,body):
    req=p.RemoveQuestProto.FromString(body)
    if not game.research['enabled']:return p.RemoveQuestOutProto(status=2)
    from dittobuffer.gameplay import stories
    if stories.lookup(game,db,player,req.quest_id):return p.RemoveQuestOutProto(status=3)
    row=db.execute('SELECT * FROM research_tasks WHERE player_id=? AND id=?',(player,req.quest_id)).fetchone()
    if not row or row['claimed'] or row['abandoned']:return p.RemoveQuestOutProto(status=2)
    if row['parent_id']:return p.RemoveQuestOutProto(status=3)
    db.execute('UPDATE research_tasks SET abandoned=1,updated_ms=? WHERE player_id=? AND id=?',(now_ms(),player,req.quest_id));game.touch(db,player)
    return p.RemoveQuestOutProto(status=1)

def can_grant(game,db,player,rewards):
    bag=game.bag_count(db,player)
    extra=sum(v['amount'] for v in rewards if v['type']=='item' and v['item'] not in (901,902))
    if bag+extra>game.capacity(db,player,'item_capacity'):return False
    if any(v['type']=='encounter' for v in rewards):
        from dittobuffer.gameplay.eggs import count
        if db.execute('SELECT COUNT(*) FROM pokemon WHERE player_id=?',(player,)).fetchone()[0]+count(db,player)>=game.capacity(db,player,'pokemon_capacity'):return False
    return True

def grant(game,db,player,key,rewards,encounter_type=6,client_quest_id=None):
    for index,value in enumerate(rewards):
        kind=value['type']
        if kind=='item':
            if value['item'] in (901,902):
                from dittobuffer.gameplay.eggs import grant as incubators
                incubators(db,player,value['item'],value['amount'])
            else:db.execute('INSERT INTO items VALUES(?,?,?) ON CONFLICT(player_id,item) DO UPDATE SET count=count+excluded.count',(player,value['item'],value['amount']))
        elif kind in ('candy','xl_candy'):
            from dittobuffer.core.management import add_candy,add_xl_candy
            (add_candy if kind=='candy' else add_xl_candy)(db,player,game.species[value['species']]['family'],value['amount'])
        elif kind=='mega_energy':
            from dittobuffer.gameplay.mega import grant as grant_energy
            grant_energy(game,db,player,value['species'],value.get('mega_evolution',1),value['amount'],'research:'+key+':'+str(index))
        elif kind in ('xp','stardust'):
            column='xp' if kind=='xp' else 'stardust'
            db.execute(f'UPDATE players SET {column}={column}+? WHERE id=?',(value['amount'],player))
        else:
            encounter_key=key if sum(v['type']=='encounter' for v in rewards)==1 else f'{key}-reward-{index}'
            # Frozen encounter entitlements survive retries and interruption.
            if db.execute('SELECT 1 FROM research_encounters WHERE player_id=? AND quest_id=?',(player,encounter_key)).fetchone():continue
            identifier=secrets.randbits(60)+1
            while db.execute('SELECT 1 FROM spawns WHERE id=?',(identifier,)).fetchone():identifier=secrets.randbits(60)+1
            species=value['species'];level=value.get('level',10);rng=random.Random(secrets.token_bytes(16))
            from dittobuffer.gameplay.species import choose_gender
            rules=pool_rules(game,value)
            shiny=rng.random()<value.get('shiny_rate',game.config.get('shiny_rate',0)) and (species,rules['form']) in game.shiny_eligible
            pokemon=make_pokemon(p,rules,species,level,[rng.randint(10,15) for _ in range(3)],shiny,value.get('gender') or choose_gender(game,species,rng),costume=value.get('costume',0))
            from dittobuffer.gameplay.pokemon_sizes import assign as assign_size
            assign_size(game,pokemon,value,rng)
            from dittobuffer.gameplay.backgrounds import assign as assign_background
            assign_background(game,pokemon,rng,'research',value.get('background',0))
            pokemon.id=identifier;pokemon.creation_time_ms=now_ms()
            if value.get('max_mode'):
                from dittobuffer.gameplay.max_system import attach
                attach(game,pokemon,value['max_mode'])
            db.execute('INSERT INTO spawns VALUES(?,?,?,0,0,?)',(identifier,'quest:'+encounter_key,species,2**62))
            db.execute('INSERT INTO spawn_attributes VALUES(?,?,?)',(identifier,pokemon.SerializeToString(),level))
            db.execute('INSERT INTO research_encounters VALUES(?,?,?,?,?,?)',(player,encounter_key,identifier,now_ms(),encounter_type,client_quest_id or encounter_key))
            from dittobuffer.gameplay.special_encounters import snapshot
            snapshot(db,player,identifier,pokemon,encounter_type,value,now_ms())
    from dittobuffer.gameplay.trainer_rewards import trigger
    row=db.execute('SELECT definition FROM research_tasks WHERE player_id=? AND id=?',(player,key)).fetchone()
    definition=json.loads(row[0])['template'] if row else key
    trigger(game,db,player,'research',definition,key)
    game.touch(db,player)

def complete(game,db,player,body):
    req=p.CompleteQuestProto.FromString(body)
    from dittobuffer.gameplay.max_system import claim_walk
    walk=claim_walk(game,db,player,req)
    if walk is not None:return walk
    if not game.research['enabled']:return p.CompleteQuestOutProto(status=2)
    import logging
    logging.info('Research claim: choice=%d has_subquest=%s claim_all=%d',req.choice_id,bool(req.sub_quest_id),req.claim_all_enum)
    from dittobuffer.gameplay import stories
    story=stories.lookup(game,db,player,req.quest_id)
    if story:return stories.complete(game,db,player,req,*story)
    row=db.execute('SELECT * FROM research_tasks WHERE player_id=? AND id=?',(player,req.quest_id)).fetchone()
    if not row or row['abandoned'] or row['parent_id']:return p.CompleteQuestOutProto(status=2)
    if row['claimed']:return p.CompleteQuestOutProto(status=4)
    spec=json.loads(row['definition'])
    if row['progress']<spec['target']:return p.CompleteQuestOutProto(status=3)
    if req.sub_quest_id or req.choice_id not in (-1,0) or req.claim_all_enum:return p.CompleteQuestOutProto(status=12)
    stamp_card=card(game,db,player)
    if len(stamp_card.stamp)>=7:return p.CompleteQuestOutProto(status=10)
    if not can_grant(game,db,player,spec['rewards']):return p.CompleteQuestOutProto(status=11)
    grant(game,db,player,row['id'],spec['rewards'])
    db.execute('UPDATE research_tasks SET claimed=1,updated_ms=? WHERE player_id=? AND id=?',(now_ms(),player,row['id']))
    progress(game,db,player,'field_claim')
    row=db.execute('SELECT * FROM research_tasks WHERE player_id=? AND id=?',(player,row['id'])).fetchone()
    out=p.CompleteQuestOutProto(status=1,quest=client_task(game,row))
    if stamp_card.remaining_daily_stamps:
        cycle=db.execute('SELECT cycle FROM research_cards WHERE player_id=?',(player,)).fetchone()[0]
        stamp=__import__('dittobuffer.gameplay.research_lifecycle',fromlist=['clock']).clock(game) if game.config.get('liveops',{}).get('isolated_fixture') else now_ms();db.execute('INSERT INTO research_stamps VALUES(?,?,?,?)',(player,cycle,day(game),stamp))
        out.stamp.add(context=2,timestamp_ms=stamp)
        freeze_breakthrough(game,db,player)
    for value in spec['rewards']:out.rewards.add().CopyFrom(reward_proto(game,value))
    return out

def freeze_breakthrough(game,db,player):
    cycle=db.execute('SELECT cycle FROM research_cards WHERE player_id=?',(player,)).fetchone()[0]
    if db.execute('SELECT COUNT(*) FROM research_stamps WHERE player_id=? AND cycle=?',(player,cycle)).fetchone()[0]<7:return None
    row=db.execute('SELECT * FROM research_breakthroughs WHERE player_id=? AND cycle=?',(player,cycle)).fetchone()
    if row:return row
    from copy import deepcopy
    rewards=deepcopy(game.research['breakthrough_rewards']);pool=game.research.get('breakthrough_pool',[])
    if pool:
        rng=random.Random(hashlib.sha256(f'breakthrough:{player}:{cycle}'.encode()).digest())
        encounter=deepcopy(rng.choices(pool,weights=[v.get('weight',1) for v in pool])[0]);encounter.pop('weight',None);rewards.append(encounter)
    db.execute('INSERT INTO research_breakthroughs(player_id,cycle,definition,earned_ms) VALUES(?,?,?,?)',(player,cycle,json.dumps(rewards),now_ms()))
    return db.execute('SELECT * FROM research_breakthroughs WHERE player_id=? AND cycle=?',(player,cycle)).fetchone()

def complete_card(game,db,player,body):
    p.CompleteQuestStampCardProto.FromString(body)
    if not game.research['enabled'] or len(card(game,db,player).stamp)<7:return p.CompleteQuestStampCardOutProto(status=2)
    entitlement=freeze_breakthrough(game,db,player)
    if entitlement is None or entitlement['claimed_ms']:return p.CompleteQuestStampCardOutProto(status=2)
    rewards=json.loads(entitlement['definition'])
    if not can_grant(game,db,player,rewards):return p.CompleteQuestStampCardOutProto(status=2)
    current=card(game,db,player);grant(game,db,player,current.id,rewards,encounter_type=5)
    result=p.CompleteQuestStampCardOutProto(status=1)
    for reward in rewards:result.reward.add().CopyFrom(reward_proto(game,reward))
    db.execute('UPDATE research_breakthroughs SET claimed_ms=?,result=? WHERE player_id=? AND cycle=?',(now_ms(),result.SerializeToString(),player,entitlement['cycle']))
    db.execute('UPDATE research_cards SET cycle=cycle+1 WHERE player_id=?',(player,))
    game.touch(db,player)
    return result


def encounter(game,db,player,body):
    from dittobuffer.gameplay.items import probabilities
    req=p.QuestEncounterProto.FromString(body)
    if not game.research['enabled']:return p.QuestEncounterOutProto(result=2)
    row=db.execute('SELECT * FROM research_encounters WHERE player_id=? AND client_quest_id=? AND spawn_id=?',(player,req.quest_id,req.pokemon_id)).fetchone()
    if not row:return p.QuestEncounterOutProto(result=2)
    old=db.execute('SELECT state FROM encounters WHERE player_id=? AND spawn_id=?',(player,row['spawn_id'])).fetchone()
    if old and old['state']=='caught':return p.QuestEncounterOutProto(result=3)
    if not can_grant(game,db,player,[{'type':'encounter'}]):return p.QuestEncounterOutProto(result=4)
    from dittobuffer.gameplay.special_encounters import opened
    if not opened(game,db,player,row['spawn_id'],now_ms()):return p.QuestEncounterOutProto(result=2)
    db.execute('INSERT OR IGNORE INTO encounters(player_id,spawn_id) VALUES(?,?)',(player,row['spawn_id']))
    data=db.execute('SELECT data FROM spawn_attributes WHERE spawn_id=?',(row['spawn_id'],)).fetchone()[0]
    return p.QuestEncounterOutProto(result=1,pokemon=p.PokemonProto.FromString(data),capture_probability=probabilities(game))
