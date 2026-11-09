"""Native Research contexts over the existing frozen stories/tasks and claims.

No second progress store: Timed, Collection and path quests use the same rows,
source events and atomic reward grants as the established Special Research.
"""
from copy import deepcopy
import json
from dittobuffer.gameplay.game import p, now_ms

METHODS={'catch':0,'trade':1,'evolve':2,'raid':3,'hatch':4}
CONTEXTS={'special':1,'timed':4,'level_up':6,'collection':9,'branching':11,'timed_branching':17}

def kind(spec):return spec.get('research_type','special')

def clock(game):
    from dittobuffer.gameplay.liveops.runtime import clock as scheduler_clock
    return scheduler_clock(game)

def available(game,spec):
    at=clock(game)
    return at>=spec.get('start_ms',0) and (kind(spec) not in ('timed','collection','timed_branching') or at<spec.get('end_ms',2**62))

def prepare(spec):
    spec=deepcopy(spec)
    if kind(spec)=='collection':
        objectives=[]
        for entry in spec['entries']:
            methods=entry.get('methods',['catch'])
            conditions={k:deepcopy(entry[k]) for k in ('form','gender','alignment','max_mode','costume') if k in entry}
            conditions['species']=[entry['species']]
            objectives.append(dict(event=methods[0],methods=methods,target=1,conditions=conditions,description='Collect the specified Pokemon',rewards=[]))
        # Child rewards are never exposed/granted for a collection criterion.
        spec['stages']=[dict(objectives=objectives,rewards=deepcopy(spec['rewards']))]
    if kind(spec) in ('branching','timed_branching') and 'chosen_path' not in spec:
        spec['stages']=[dict(objectives=[],rewards=[],dialog=spec.get('description') or 'Choose the observations you want to record. Your selected path will be saved.')]
    return spec

def decorate(game,quest,spec,context=None):
    quest.quest_context=CONTEXTS[kind(spec)] if context is None else context
    # The isolated scheduler may jump; present its remaining duration on the
    # native wall clock while enforcing eligibility against scheduler time.
    offset=now_ms()-clock(game)
    if 'start_ms' in spec:quest.start_timestamp_ms=spec['start_ms']+offset
    if kind(spec) in ('timed','collection','timed_branching'):
        quest.end_timestamp_ms=spec['end_ms']+offset
        quest.remove_encounters_on_quest_removal=False
    quest.time_zone_id=spec.get('timezone','UTC')
    from dittobuffer.gameplay.research_presentation import PRESENTATION_VERSION
    quest.story_quest_template_version=PRESENTATION_VERSION

def collection(game,db,player,row):
    from dittobuffer.gameplay import research,stories
    spec=json.loads(row['definition']);root=stories.root_id(row['id'],0)
    out=p.ClientQuestProto();q=out.quest;q.quest_id=root;q.template_id='DITTO_COLLECTION_'+row['id'];q.quest_type=45
    q.creation_timestamp_ms=row['created_ms'];q.status=2 if row['completed'] else 1;decorate(game,q,spec)
    q.goal.target=len(spec['entries']);q.mini_collection.SetInParent()
    for i,entry in enumerate(spec['entries']):
        task=db.execute('SELECT progress,updated_ms FROM research_tasks WHERE player_id=? AND id=?',(player,stories.child_id(root,i))).fetchone()
        done=bool(task and task['progress']>=1);q.progress+=done
        if task:q.last_update_timestamp_ms=max(q.last_update_timestamp_ms,task['updated_ms'])
        v=q.mini_collection.pokemon.add(pokedex_id=entry['species'],caught=done,collection_type=METHODS[entry.get('methods',['catch'])[0]])
        v.collection_types.extend(METHODS[x] for x in entry.get('methods',['catch']))
        from dittobuffer.gameplay.species import pool_rules
        v.display.form=pool_rules(game,entry)['form']
        for key,field in (('gender','gender'),('alignment','alignment'),('max_mode','bread_mode_enum'),('costume','costume')):
            if key in entry:setattr(v.display,field,entry[key])
        v.require_alignment_to_match='alignment' in entry;v.require_bread_mode_to_match='max_mode' in entry
    q.mini_collection.completed=bool(row['completed'])
    for reward in spec['rewards']:q.quest_rewards.add().CopyFrom(research.reward_proto(game,reward))
    out.quest_display.quest_id=root;out.quest_display.title=spec['title'];out.quest_display.description=spec.get('description',spec['title']);out.quest_display.story_questline_title=spec['title']
    return out

def matches_collection(spec,event,context):
    methods=spec.get('methods')
    if not methods:return spec['event']==event
    if event=='catch':return 'catch' in methods or ('raid' in methods and (context or {}).get('source')=='raid')
    return event in methods

def choice(game,db,player,req,row,index):
    from dittobuffer.gameplay import research,stories
    spec=json.loads(row['definition'])
    if kind(spec) not in ('branching','timed_branching') or index!=0 or 'chosen_path' in spec:return None
    if req.sub_quest_id or req.claim_all_enum or not 0<=req.choice_id<len(spec['paths']):return p.CompleteQuestOutProto(status=12)
    selected=spec['paths'][req.choice_id];rewards=selected.get('rewards',[])
    if not research.can_grant(game,db,player,rewards):return p.CompleteQuestOutProto(status=11)
    research.grant(game,db,player,req.quest_id,rewards,encounter_type=10 if kind(spec)=='timed_branching' else 4,client_quest_id=req.quest_id)
    spec['chosen_path']=selected['id'];spec['stages']=[spec['stages'][0]]+deepcopy(selected['stages'])
    db.execute('INSERT INTO research_stage_claims VALUES(?,?,0,?)',(player,row['id'],now_ms()))
    db.execute('UPDATE research_stories SET definition=?,stage_index=1 WHERE player_id=? AND id=?',(json.dumps(spec),player,row['id']))
    newer=db.execute('SELECT * FROM research_stories WHERE player_id=? AND id=?',(player,row['id'])).fetchone();stories.create_stage(game,db,player,newer)
    result=p.CompleteQuestOutProto(status=1,quest=stories.client_quest(game,db,player,newer,0))
    for reward in rewards:result.rewards.add().CopyFrom(research.reward_proto(game,reward))
    return result

def claim_collection(game,db,player,req,row):
    from dittobuffer.gameplay import research,stories
    spec=json.loads(row['definition'])
    if req.sub_quest_id or req.choice_id not in (-1,0) or req.claim_all_enum:return p.CompleteQuestOutProto(status=12)
    if row['completed']:return p.CompleteQuestOutProto(status=4)
    tasks=db.execute('SELECT progress FROM research_tasks WHERE player_id=? AND parent_id=?',(player,req.quest_id)).fetchall()
    if len(tasks)!=len(spec['entries']) or any(r['progress']<1 for r in tasks):return p.CompleteQuestOutProto(status=3)
    if not research.can_grant(game,db,player,spec['rewards']):return p.CompleteQuestOutProto(status=11)
    research.grant(game,db,player,req.quest_id,spec['rewards'],encounter_type=13,client_quest_id=req.quest_id)
    db.execute('INSERT INTO research_stage_claims VALUES(?,?,0,?)',(player,row['id'],now_ms()))
    db.execute('UPDATE research_stories SET completed=1 WHERE player_id=? AND id=?',(player,row['id']))
    db.execute('UPDATE research_tasks SET claimed=1 WHERE player_id=? AND parent_id=?',(player,req.quest_id))
    from dittobuffer.gameplay.trainer import event
    event(game,db,player,'collection:'+row['id'],'collection',{'research_id':row['id']})
    newer=db.execute('SELECT * FROM research_stories WHERE player_id=? AND id=?',(player,row['id'])).fetchone()
    result=p.CompleteQuestOutProto(status=1,quest=collection(game,db,player,newer))
    for reward in spec['rewards']:result.rewards.add().CopyFrom(research.reward_proto(game,reward))
    return result
