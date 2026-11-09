"""Opt-in, source-backed 71-80 gates over the existing Research task ledger.

Legacy schedules are unchanged. Unknown conditions fail closed; no fictional
requirements replace supplied Game Master gates. All writes share the RPC tx.
"""
from copy import copy,deepcopy
from functools import lru_cache
import json,re
from pathlib import Path
from types import MethodType
from dittobuffer.gameplay.game import p,now_ms

MODE='pinned-modern-gated'
SUPPORTED_REWARD_ITEMS={1,2,3,101,102,103,104,201,202,701,901,902,1401,1402,1501,1502,1503}
EVENTS={'QUEST_BADGE_RANK':'medal_rank','QUEST_UPGRADE_POKEMON':'power_up','QUEST_LAND_THROW':'throw','QUEST_CATCH_POKEMON':'catch','QUEST_ROUTE_COMPLETE':'route','QUEST_CHARGE_ATTACK':'super_effective_charge','QUEST_GET_STARDUST':'stardust','QUEST_PURIFY_POKEMON':'purify','QUEST_COMPLETE_RAID_BATTLE':'raid','QUEST_ENHANCE_BREAD_MOVE':'max_upgrade','QUEST_WALK':'walk','QUEST_COMPLETE_QUEST':'field_claim','QUEST_HATCH_EGG':'hatch','QUEST_SEND_GIFT':'gift_send','QUEST_BATTLE_TEAM_ROCKET':'rocket','QUEST_COMPLETE_BATTLE':'max_win','QUEST_TRADE_POKEMON':'trade','QUEST_BUDDY_EARN_AFFECTION_POINTS':'buddy_heart','QUEST_COMPLETE_COMBAT':'pvp_win'}

@lru_cache(maxsize=1)
def reference():return json.loads(Path(__file__).with_name('level-research-reference.json').read_text())

def enabled(game):return game.config.get('progression_schedule')==MODE

def enum_value(enum,value):
    if isinstance(value,int):return value
    norm=lambda s:re.sub('[^a-z0-9]','',s.lower())
    needle=norm(value);prefix=norm(enum.name)
    needle=needle.removeprefix(prefix)
    found=[v.number for v in enum.values if norm(v.name.split('_',1)[-1])==needle or norm(v.name).removeprefix(prefix)==needle]
    if len(found)!=1:raise ValueError('Unresolved source enum '+enum.full_name+': '+str(value))
    return found[0]

def fill(message,data):
    aliases={'subdisplay':'subquest_displays','badgeTypesToExclude':'badge_types_to_exclude','characterCategory':'category'}
    fields={f.json_name:f for f in message.DESCRIPTOR.fields};fields.update({f.name:f for f in message.DESCRIPTOR.fields})
    for name,value in data.items():
        field=fields.get(aliases.get(name,name))
        if field is None:raise ValueError('Unresolved source field '+message.DESCRIPTOR.full_name+': '+name)
        repeated=field.is_repeated;dest=getattr(message,field.name)
        if field.message_type:
            if repeated:
                for v in value:fill(dest.add(),v)
            else:fill(dest,value)
        elif field.enum_type:
            if repeated:dest.extend(enum_value(field.enum_type,v) for v in value)
            else:setattr(message,field.name,enum_value(field.enum_type,value))
        elif repeated:dest.extend(value)
        else:setattr(message,field.name,value)
    return message

def cap(game,db,player):
    if not enabled(game):
        from dittobuffer.gameplay.progression import cap as basic
        return basic(game.config)
    row=db.execute('SELECT cap FROM trainer_level_caps WHERE player_id=?',(player,)).fetchone()
    if row:return row[0]
    # Explicit opt-in migration preserves the highest previously issued native
    # level; never infer research completion from surplus XP alone.
    prior=db.execute('SELECT MAX(level) FROM player_stats_history WHERE player_id=?',(player,)).fetchone()[0] or 0
    from bisect import bisect_right
    from dittobuffer.gameplay.progression import pinned
    if prior>bisect_right(pinned()['requiredExperience'],game.player(db,player)['xp']):raise ValueError('Modern progression migration requires review: the new curve would decrease an issued level. Preserve the current profile until an explicit XP/level migration is approved.')
    value=max(70,min(80,prior))
    db.execute('INSERT INTO trainer_level_caps VALUES(?,?,?,?)',(player,value,'Explicit opt-in; preserve issued levels, hold surplus XP until verified gates',now_ms()))
    return value

def project(game,db,player):
    if not enabled(game):return game
    view=copy(game);view.config=deepcopy(game.config);view.config['_earned_level_cap']=cap(game,db,player)
    for name,method in vars(game).items():
        if isinstance(method,MethodType) and method.__self__ is game:setattr(view,name,MethodType(method.__func__,view))
    ensure(view,db,player)
    return view

def ensure(game,db,player):
    if not enabled(game):return
    from dittobuffer.gameplay import stories
    from dittobuffer.gameplay.progression import pinned
    limit=cap(game,db,player)
    if limit>=80 or game.player(db,player)['xp']<pinned()['requiredExperience'][limit-1]:return
    ident=f'LEVEL_{limit+1}_CAP'
    spec={'id':ident,'title':f'Level {limit+1} Research','research_type':'level_up','gate_level':limit+1,'stages':[{'objectives':[],'rewards':[]}]}
    root=reference()['quests'][ident]
    for child in root['quest']['multiPart']['subQuests']:
        value=reference()['quests'][child['questId']];q=value['quest']
        spec['stages'][0]['objectives'].append({'event':EVENTS[q['questType']],'target':q['goal']['target'],'verified_gate':True,'source':child['questId'],'description':value['questDisplay']['description'],'rewards':[{'type':'xp','amount':r['exp']} for r in q.get('questRewards',[]) if r['type']=='EXPERIENCE']})
    if db.execute('INSERT OR IGNORE INTO research_stories(player_id,id,definition,created_ms) VALUES(?,?,?,?)',(player,ident,json.dumps(spec),now_ms())).rowcount:
        row=db.execute('SELECT * FROM research_stories WHERE player_id=? AND id=?',(player,ident)).fetchone();stories.create_stage(game,db,player,row)
    refresh_calendar(game,db,player,ident)
    # Medal gate is cumulative: previously earned medals qualify; other counters
    # begin at enrollment, rather than retrospectively inventing action history.
    platinum=db.execute('SELECT COUNT(*) FROM trainer_medals WHERE player_id=? AND rank>=4 AND badge!=?',(player,enum_value(p.HoloBadgeType.DESCRIPTOR,'BADGE_MINI_COLLECTION'))).fetchone()[0]
    row=db.execute('SELECT * FROM research_tasks WHERE player_id=? AND id=?',(player,ident+'_SUB_0')).fetchone()
    if row and not row['claimed']:
        amount=min(json.loads(row['definition'])['target'],platinum)
        if amount>row['progress']:db.execute('UPDATE research_tasks SET progress=?,updated_ms=? WHERE player_id=? AND id=?',(amount,now_ms(),player,row['id']));game.touch(db,player)

def client_quest(game,db,player,row):
    spec=json.loads(row['definition']);ident=row['id'];out=fill(p.ClientQuestProto(),reference()['quests'][ident]);q=out.quest
    q.creation_timestamp_ms=row['created_ms'];q.status=2 if row['completed'] else 1;q.goal.target=len(q.multi_part.sub_quests)
    out.quest_display.quest_id=ident
    for i,sub in enumerate(q.multi_part.sub_quests):
        saved=db.execute('SELECT * FROM research_tasks WHERE player_id=? AND id=?',(player,sub.quest_id)).fetchone()
        if saved is None:continue
        sub.CopyFrom(fill(p.QuestProto(),reference()['quests'][saved['id']]['quest']))
        sub.progress=saved['progress'];sub.status=2 if saved['claimed'] else 1;sub.creation_timestamp_ms=saved['created_ms'];sub.last_update_timestamp_ms=saved['updated_ms'];sub.completion_timestamp_ms=saved['completed_ms']
        q.progress+=bool(saved['claimed']);q.last_update_timestamp_ms=max(q.last_update_timestamp_ms,saved['updated_ms'])
        out.quest_display.subquest_displays[i].CopyFrom(fill(p.QuestDisplayProto(),reference()['quests'][saved['id']]['questDisplay']));out.quest_display.subquest_displays[i].quest_id=saved['id']
    return out

def progress_task(game,db,player,row,spec,event,amount,context):
    if row['progress']>=spec['target']:return # earned criteria survive midnight until claim
    expected=spec['event']
    if not (event==expected or expected=='rocket' and event in ('rocket_grunt','rocket_leader')):return
    source=reference()['quests'][spec['source']]['quest'];conditions=source['goal'].get('condition',[])
    state_row=db.execute('SELECT data FROM research_task_state WHERE player_id=? AND task_id=?',(player,row['id'])).fetchone();state=json.loads(state_row[0]) if state_row else {}
    value=row['progress'];unique=False;single_day=False;streak=False
    for cond in conditions:
        typ=cond['type']
        if typ=='WITH_BADGE_TYPE':return # refreshed from durable ranks at enrollment/poll
        elif typ=='WITH_THROW_TYPE':
            quality={'ACTIVITY_CATCH_NICE_THROW':1.3,'ACTIVITY_CATCH_GREAT_THROW':1.7,'ACTIVITY_CATCH_EXCELLENT_THROW':1.85}[cond['withThrowType']['throwType']]
            if not quality<=context.get('quality',0)<=2:return
        elif typ=='WITH_LEGENDARY_POKEMON':
            if game.species.get(context.get('species'),{}).get('class') not in (1,2):return
        elif typ=='WITH_SINGLE_DAY':single_day=True
        elif typ=='WITH_DAYS_IN_A_ROW':streak=True
        elif typ=='WITH_POKEMON_LEVEL':
            if not cond['withPokemonLevel'].get('maxLevel') or context.get('level',0)<50 or not context.get('pokemon_id'):return
            unique=True
        elif typ=='WITH_WIN_RAID_STATUS':
            if event!='raid':return
        elif typ=='WITH_QUEST_CONTEXT':
            if event!='field_claim':return
        elif typ=='WITH_WIN_BATTLE_STATUS':
            if event not in ('rocket_grunt','rocket_leader','max_win','pvp_win'):return
        elif typ=='WITH_INVASION_CHARACTER':
            allowed=cond['withInvasionCharacter']['characterCategory']
            if event=='rocket_grunt' and 'GRUNT' not in allowed or event=='rocket_leader' and not {'ARLO','CLIFF','SIERRA'}&set(allowed):return
        elif typ=='WITH_DISTANCE':
            if context.get('distance_km',-1)<cond['withDistance']['distanceKm']:return
        elif typ=='WITH_LUCKY_POKEMON':
            if not context.get('lucky'):return
        elif typ=='WITH_COMBAT_TYPE':
            kinds=cond['withCombatType']['combatType']
            if event=='max_win' and not {'COMBAT_TYPE_DMAX','COMBAT_TYPE_GMAX'}&set(kinds) or event=='pvp_win' and ('VS_SEEKER' not in kinds or context.get('source')!='league'):return
        elif typ=='WITH_SUPER_EFFECTIVE_CHARGE':
            if event!='super_effective_charge' or not context.get('super_effective'):return
        else:return # unsupported source conditions never become a free completion
    if single_day or streak:
        from dittobuffer.gameplay.research import day
        today=day(game)
        if state.get('day')!=today:
            if single_day:value=0
            if streak:
                from datetime import date,timedelta
                try:previous=(date.fromisoformat(today)-timedelta(days=1)).isoformat()
                except ValueError:previous=str(int(today)-1)
                if state.get('day')!=previous:value=0
            state['day']=today
        elif streak:return
    if unique:
        ids=state.setdefault('pokemon_ids',[])
        if context['pokemon_id'] in ids:return
        ids.append(context['pokemon_id']);amount=1
    value=min(spec['target'],value+amount)
    db.execute('INSERT INTO research_task_state VALUES(?,?,?) ON CONFLICT(player_id,task_id) DO UPDATE SET data=excluded.data',(player,row['id'],json.dumps(state)))
    db.execute('UPDATE research_tasks SET progress=?,updated_ms=?,completed_ms=? WHERE player_id=? AND id=?',(value,now_ms(),now_ms() if value>=spec['target'] else 0,player,row['id']));game.touch(db,player)

def complete(game,db,player,req,row):
    from dittobuffer.gameplay import research
    spec=json.loads(row['definition']);level=spec['gate_level']
    if not enabled(game) or req.choice_id not in (-1,0) or req.claim_all_enum or row['completed']:return p.CompleteQuestOutProto(status=4)
    if req.sub_quest_id:
        child=db.execute('SELECT * FROM research_tasks WHERE player_id=? AND parent_id=? AND id=?',(player,row['id'],req.sub_quest_id)).fetchone()
        if not child:return p.CompleteQuestOutProto(status=5)
        task=json.loads(child['definition'])
        if child['claimed']:return p.CompleteQuestOutProto(status=4)
        if child['progress']<task['target']:return p.CompleteQuestOutProto(status=3)
        research.grant(game,db,player,child['id'],task['rewards']);db.execute('UPDATE research_tasks SET claimed=1,updated_ms=? WHERE player_id=? AND id=?',(now_ms(),player,child['id']))
        out=p.CompleteQuestOutProto(status=1,quest=client_quest(game,db,player,row))
        for reward in task['rewards']:out.rewards.add().CopyFrom(research.reward_proto(game,reward))
        return out
    from dittobuffer.gameplay.progression import pinned
    if cap(game,db,player)!=level-1 or game.player(db,player)['xp']<pinned()['requiredExperience'][level-1] or db.execute('SELECT 1 FROM research_tasks WHERE player_id=? AND parent_id=? AND claimed=0',(player,row['id'])).fetchone():return p.CompleteQuestOutProto(status=3)
    db.execute('UPDATE trainer_level_caps SET cap=? WHERE player_id=?',(level,player));game.config['_earned_level_cap']=level
    db.execute('UPDATE research_stories SET completed=1 WHERE player_id=? AND id=?',(player,row['id']));db.execute('INSERT INTO research_stage_claims VALUES(?,?,0,?)',(player,row['id'],now_ms()));game.touch(db,player)
    newer=db.execute('SELECT * FROM research_stories WHERE player_id=? AND id=?',(player,row['id'])).fetchone();out=p.CompleteQuestOutProto(status=1,quest=client_quest(game,db,player,newer));out.rewards.add(type=10,level_cap=level);ensure(game,db,player);return out


def templates(config):
    if config.get('progression_schedule')!=MODE:return []
    values=[]
    for key,source in reference()['quests'].items():
        t=p.GameMasterClientTemplateProto(template_id=key);t.client_quest_template.CopyFrom(fill(p.ClientQuestProto(),source));values.append(t)
    return values


def readiness():
    return {'mode':MODE,'source_sha256':reference()['source_sha256'],'levels':list(range(71,81)),
      'blocked_counters':[],'automatic_migration':False,
      'note':'Exact gates are immutable. Charged-attack progress uses resolved Trainer, Gym, Raid and ordinary Max-battle charged hits; repeat receipts are deduplicated. All 71-80 native transitions are not yet verified.'}


def reward(game,db,player,level,out):
    if not enabled(game):return None
    definition=reference()['rewards'].get(str(level),{});items={};pending=[];storage=[0,0]
    # Existing gameplay supports these consumables. Other source-backed rewards
    # remain explicit durable entitlements until their item handlers/assets work.
    for name,count in zip(definition.get('items',[]),definition.get('itemsCount',[])):
        item=enum_value(p.Item.DESCRIPTOR,name)
        if item==2201:storage[0]+=count
        elif item==2202:storage[1]+=count
        elif item==4:pending.append(dict(item=item,amount=count,reason='Master Ball explicitly excluded'))
        elif item in SUPPORTED_REWARD_ITEMS:items[str(item)]=items.get(str(item),0)+count
        else:pending.append(dict(item=item,amount=count,reason='Source-backed item delivery is deferred until its native handler is verified'))
    if any(storage):db.execute('INSERT INTO trainer_storage_rewards VALUES(?,?,?) ON CONFLICT(player_id) DO UPDATE SET pokemon=pokemon+excluded.pokemon,items=items+excluded.items',(player,*storage))
    from dittobuffer.gameplay.wardrobe import registry,grant
    articles={a['id'] for a in registry()['articles']}
    for article in definition.get('neutralAvatarItemTemplates',[]):
        if article['itemTemplateId'] in articles:
            if grant(game,db,player,article['itemTemplateId'],'modern-level:'+str(level)):out.neutral_avatar_item_templates.add(item_template_id=article['itemTemplateId'],display_template_id=article['displayTemplateId'])
        else:pending.append(dict(avatar=article,reason='Missing verified wardrobe mapping; entitlement retained'))
    db.execute('INSERT INTO trainer_level_entitlements VALUES(?,?,?,?)',(player,level,json.dumps(definition),json.dumps(pending)))
    return items


def reward_templates(config):
    """Native reward screens describe only deliveries the server can honor.

    Unsupported source entries remain durable pending entitlements in reward().
    They must not masquerade as granted items, including the excluded Master Ball.
    """
    if config.get('progression_schedule')!=MODE:return []
    from dittobuffer.gameplay.wardrobe import registry
    articles={a['id'] for a in registry()['articles']}
    values=[]
    for level,source in reference()['rewards'].items():
        definition=deepcopy(source);pairs=[(name,count) for name,count in zip(source.get('items',[]),source.get('itemsCount',[])) if enum_value(p.Item.DESCRIPTOR,name) in SUPPORTED_REWARD_ITEMS|{2201,2202}]
        definition['items']=[name for name,count in pairs];definition['itemsCount']=[count for name,count in pairs]
        definition['neutralAvatarItemTemplates']=[v for v in source.get('neutralAvatarItemTemplates',[]) if v['itemTemplateId'] in articles]
        item=p.GameMasterClientTemplateProto(template_id='LEVEL_UP_REWARDS_LEVEL_'+level)
        fill(item.level_up_rewards,definition);values.append(item)
    return values


def refresh_calendar(game,db,player,root):
    """Reset only incomplete calendar counters; never revoke earned criteria."""
    from dittobuffer.gameplay.research import day
    from datetime import date,timedelta
    today=day(game)
    try:previous=(date.fromisoformat(today)-timedelta(days=1)).isoformat()
    except ValueError:previous=str(int(today)-1)
    for row in db.execute('SELECT * FROM research_tasks WHERE player_id=? AND parent_id=? AND claimed=0',(player,root)).fetchall():
        spec=json.loads(row['definition'])
        if row['progress']==0 or row['progress']>=spec['target']:continue
        types={c['type'] for c in reference()['quests'][spec['source']]['quest']['goal'].get('condition',[])}
        state=db.execute('SELECT data FROM research_task_state WHERE player_id=? AND task_id=?',(player,row['id'])).fetchone()
        at=json.loads(state[0]).get('day') if state else None
        if ('WITH_SINGLE_DAY' in types and at!=today) or ('WITH_DAYS_IN_A_ROW' in types and at not in (today,previous)):
            db.execute('UPDATE research_tasks SET progress=0,completed_ms=0,updated_ms=? WHERE player_id=? AND id=?',(now_ms(),player,row['id']));game.touch(db,player)
