"""Frozen, multistage Special Research using the shared objective/reward engine."""
import json
import uuid
from dittobuffer.gameplay.game import p,now_ms
from dittobuffer.gameplay import research, research_lifecycle as lifecycle

def root_id(story,index):return story if story.startswith('LEVEL_') and story.endswith('_CAP') else str(uuid.uuid5(uuid.NAMESPACE_URL,f'dittobuffer:research:{story}:{index}'))
def child_id(root,index):return root+'_SUB_'+str(index) if root.startswith('LEVEL_') else str(uuid.uuid5(uuid.UUID(root),str(index)))

def validate(game):
    seen=set()
    for original in game.research['stories']:
        story=lifecycle.prepare(original)
        key=story.get('id')
        if not isinstance(key,str) or not key or key in seen:raise ValueError('Research storyline needs a unique ID')
        seen.add(key)
        if not story.get('title') or not 1<=len(story.get('stages',[]))<=100:raise ValueError('Invalid research storyline')
        for stage in story['stages']:
            limit=100 if lifecycle.kind(story)=='collection' else 3
            if not (0 if lifecycle.kind(story) in ('branching','timed_branching') else 1)<=len(stage.get('objectives',[]))<=limit or (not stage.get('rewards') and lifecycle.kind(story) not in ('branching','timed_branching')):raise ValueError('Invalid research stage')
            for objective in stage['objectives']:
                if objective.get('event') not in research.TYPES or type(objective.get('target')) is not int or not 1<=objective['target']<=10000 or not objective.get('description') or (not objective.get('rewards') and lifecycle.kind(story)!='collection'):raise ValueError('Invalid story objective')
                for reward in objective['rewards']:research.validate_reward(game,reward)
            for reward in stage['rewards']:research.validate_reward(game,reward)

def create_stage(game,db,player,row):
    spec=json.loads(row['definition']);index=row['stage_index'];root=root_id(row['id'],index)
    for n,objective in enumerate(spec['stages'][index]['objectives']):
        objective=dict(objective,template=f'LIBRE_STORY_{row["id"]}_{index}_{n}')
        stamp=now_ms()
        db.execute('INSERT OR IGNORE INTO research_tasks(player_id,id,definition,created_ms,updated_ms,parent_id) VALUES(?,?,?,?,?,?)',
            (player,child_id(root,n),json.dumps(objective),stamp,stamp,root))
    game.touch(db,player)

def ensure(game,db,player):
    for original in game.research['stories']:
        spec=lifecycle.prepare(original)
        if not lifecycle.available(game,spec):continue
        if db.execute('INSERT OR IGNORE INTO research_stories(player_id,id,definition,created_ms) VALUES(?,?,?,?)',(player,spec['id'],json.dumps(spec),now_ms())).rowcount:
            row=db.execute('SELECT * FROM research_stories WHERE player_id=? AND id=?',(player,spec['id'])).fetchone()
            create_stage(game,db,player,row)

def lookup(game,db,player,identifier):
    for row in db.execute('SELECT * FROM research_stories WHERE player_id=?',(player,)):
        for index in range(row['stage_index']+1):
            if root_id(row['id'],index)==identifier:return row,index
    return None

def client_quest(game,db,player,row,index):
    spec=json.loads(row['definition']);stage=spec['stages'][index];root=root_id(row['id'],index)
    if lifecycle.kind(spec)=='level_up':
        from dittobuffer.gameplay.level_research import client_quest as level_quest
        return level_quest(game,db,player,row)
    if lifecycle.kind(spec)=='collection':return lifecycle.collection(game,db,player,row)
    claimed=db.execute('SELECT claimed_ms FROM research_stage_claims WHERE player_id=? AND story_id=? AND stage_index=?',(player,row['id'],index)).fetchone()
    result=p.ClientQuestProto();quest=result.quest
    quest.quest_id=root;quest.template_id=f'LIBRE_STORY_{row["id"]}_{index}'
    quest.quest_type=3;quest.status=2 if claimed else 1
    lifecycle.decorate(game,quest,spec)
    if lifecycle.kind(spec) in ('branching','timed_branching') and index>0:quest.quest_context=4 if lifecycle.kind(spec)=='timed_branching' else 1
    quest.creation_timestamp_ms=row['created_ms'];quest.goal.target=len(stage['objectives'])
    if lifecycle.kind(spec) in ('branching','timed_branching') and index==0:
        quest.goal.target=1;quest.progress=1
        result.quest_display.branching_quest_story_view_button_key='Choose a path'
        for path in spec['paths']:
            from dittobuffer.gameplay.research_presentation import branch
            result.quest_display.branches.add().CopyFrom(branch(game,path))
            rewards=quest.branch_rewards.add()
            for reward in path.get('rewards',[]):rewards.rewards.add().CopyFrom(research.reward_proto(game,reward))
    quest.dialog_read=bool(db.execute('SELECT 1 FROM research_dialog_reads WHERE player_id=? AND quest_id=?',(player,root)).fetchone())
    display=result.quest_display;display.quest_id=root;display.title=f'{spec["title"]} ({index+1}/{len(spec["stages"])})'
    display.story_questline_title=spec['title'];display.description=stage.get('description',spec['title'])
    dialogue=stage.get('dialog')
    if lifecycle.kind(spec) in ('branching','timed_branching') and index==0:dialogue=dialogue or 'Choose the observations you want to record. Your selected path will be saved.'
    if dialogue:display.dialog.add(text=dialogue,character=1,expression=1)
    display.story_ending_quest=index==len(spec['stages'])-1 and not (lifecycle.kind(spec) in ('branching','timed_branching') and index==0)
    display.story_ending_description=spec.get('ending',spec['title'])
    for n in range(len(stage['objectives'])):
        child=db.execute('SELECT * FROM research_tasks WHERE player_id=? AND id=?',(player,child_id(root,n))).fetchone()
        # A restored or partially imported database can miss an objective row.
        # Skip it rather than failing the whole login RPC.
        if child is None or child['definition'] is None:
            continue
        sub=quest.multi_part.sub_quests.add();sub.CopyFrom(research.task_proto(game,child));sub.quest_context=quest.quest_context
        if quest.end_timestamp_ms:sub.end_timestamp_ms=quest.end_timestamp_ms
        child_display=research.client_task(game,child).quest_display;child_display.slot=n
        display.subquest_displays.add().CopyFrom(child_display)
        quest.progress+=bool(child['claimed']);quest.last_update_timestamp_ms=max(quest.last_update_timestamp_ms,child['updated_ms'])
    if claimed:quest.completion_timestamp_ms=claimed[0]
    for reward in stage['rewards']:quest.quest_rewards.add().CopyFrom(research.reward_proto(game,reward))
    return result

def current(game,db,player):
    return [client_quest(game,db,player,row,row['stage_index']) for row in db.execute('SELECT * FROM research_stories WHERE player_id=? AND completed=0 ORDER BY created_ms,id',(player,)) if lifecycle.available(game,json.loads(row['definition']))]

def completed(db,player):
    return [root_id(row['id'],row['stage_index']) for row in db.execute('SELECT * FROM research_stories WHERE player_id=? AND completed=1',(player,))]

def past_roots(db,player):
    return [root_id(row['story_id'],row['stage_index']) for row in db.execute('SELECT * FROM research_stage_claims WHERE player_id=?',(player,))]

def complete(game,db,player,req,row,index):
    spec=json.loads(row['definition'])
    if not lifecycle.available(game,spec):return p.CompleteQuestOutProto(status=2)
    if lifecycle.kind(spec)=='level_up':
        from dittobuffer.gameplay.level_research import complete as complete_level
        return complete_level(game,db,player,req,row)
    if row['completed'] or index!=row['stage_index']:return p.CompleteQuestOutProto(status=4)
    if lifecycle.kind(spec)=='collection':return lifecycle.claim_collection(game,db,player,req,row)
    selected=lifecycle.choice(game,db,player,req,row,index)
    if selected is not None:return selected
    if req.choice_id not in (-1,0) or req.claim_all_enum:return p.CompleteQuestOutProto(status=12)
    if row['completed'] or index!=row['stage_index']:return p.CompleteQuestOutProto(status=4)
    root=req.quest_id
    if req.sub_quest_id:
        child=db.execute('SELECT * FROM research_tasks WHERE player_id=? AND id=? AND parent_id=?',(player,req.sub_quest_id,root)).fetchone()
        if not child:return p.CompleteQuestOutProto(status=5)
        if child['claimed']:return p.CompleteQuestOutProto(status=4)
        spec=json.loads(child['definition'])
        if child['progress']<spec['target']:return p.CompleteQuestOutProto(status=3)
        rewards=spec['rewards'];key=child['id']
    else:
        if db.execute('SELECT 1 FROM research_stage_claims WHERE player_id=? AND story_id=? AND stage_index=?',(player,row['id'],index)).fetchone():return p.CompleteQuestOutProto(status=4)
        if db.execute('SELECT COUNT(*) FROM research_tasks WHERE player_id=? AND parent_id=? AND claimed=0',(player,root)).fetchone()[0]:return p.CompleteQuestOutProto(status=3)
        rewards=json.loads(row['definition'])['stages'][index]['rewards'];key=root
    if not research.can_grant(game,db,player,rewards):return p.CompleteQuestOutProto(status=11)
    # Android resolves individual rewards against their visible parent story.
    # Keep separate durable reward keys so a deferred child encounter and a
    # stage encounter can coexist without replacing one another.
    research.grant(game,db,player,key,rewards,encounter_type=10 if lifecycle.kind(json.loads(row['definition'])) in ('timed','timed_branching') else 4,client_quest_id=root)
    if req.sub_quest_id:
        db.execute('UPDATE research_tasks SET claimed=1,updated_ms=? WHERE player_id=? AND id=?',(now_ms(),player,key))
    else:
        db.execute('INSERT INTO research_stage_claims VALUES(?,?,?,?)',(player,row['id'],index,now_ms()))
        spec=json.loads(row['definition'])
        if index+1<len(spec['stages']):
            db.execute('UPDATE research_stories SET stage_index=stage_index+1 WHERE player_id=? AND id=?',(player,row['id']))
            next_row=db.execute('SELECT * FROM research_stories WHERE player_id=? AND id=?',(player,row['id'])).fetchone()
            create_stage(game,db,player,next_row)
        else:db.execute('UPDATE research_stories SET completed=1 WHERE player_id=? AND id=?',(player,row['id']))
    out=p.CompleteQuestOutProto(status=1,quest=client_quest(game,db,player,row,index))
    for reward in rewards:out.rewards.add().CopyFrom(research.reward_proto(game,reward))
    game.touch(db,player)
    return out

def read_dialog(game,db,player,body):
    if not game.research['enabled']:return p.ReadQuestDialogOutProto(status=2)
    req=p.ReadQuestDialogProto.FromString(body);story=lookup(game,db,player,req.quest_id)
    if not story:return p.ReadQuestDialogOutProto(status=2)
    row,index=story
    if not json.loads(row['definition'])['stages'][index].get('dialog'):return p.ReadQuestDialogOutProto(status=3)
    if db.execute('INSERT OR IGNORE INTO research_dialog_reads VALUES(?,?)',(player,req.quest_id)).rowcount:game.touch(db,player)
    return p.ReadQuestDialogOutProto(status=1)
