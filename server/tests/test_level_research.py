from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from tests.test_research_lifecycle import fx
from dittobuffer.gameplay import level_research as levels,progression,research,stories
from dittobuffer.gameplay.game import Game,Database,p

def opted(t,xp):
 t.cfg['progression_schedule']=levels.MODE;t.game=Game(t.cfg,Database(t.db.path))
 with t.db.transaction() as db:db.execute('UPDATE players SET xp=? WHERE id=?',(xp,t.player))

def stats(t):
 out=t.call('GET_HOLOHOLO_INVENTORY',p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
 return next(p.HoloInventoryItemProto.FromString(v.item).player_stats for v in out.inventory_delta.inventory_item if p.HoloInventoryItemProto.FromString(v.item).HasField('player_stats'))

def test_all_source_gate_templates_and_native_wire():
 assert len(levels.reference()['quests'])==50
 for key,value in levels.reference()['quests'].items():
  result=levels.fill(p.ClientQuestProto(),value);assert result.quest.quest_context==6;assert result.quest.quest_id==key
 assert levels.fill(p.WithQuestContextProto(),{'context':'CHALLENGE_QUEST'}).SerializeToString()==b'\x08\x02'
 assert levels.fill(p.WithInvasionCharacterProto(),{'characterCategory':['GRUNT']}).category==[2]

@pytest.mark.parametrize('earned_cap,next_level',[(cap,cap+1) for cap in range(70,80)])
def test_modern_gate_holds_extra_xp_then_advances_once(fx,earned_cap,next_level):
 t=fx;xp=300000000;opted(t,xp)
 with t.db.transaction() as db:db.execute('INSERT OR REPLACE INTO trainer_level_caps VALUES(?,?,?,1)',(t.player,earned_cap,'Isolated earned-gate fixture'))
 result=stats(t);assert result.level==earned_cap and result.experience==xp
 root=f'LEVEL_{next_level}_CAP';assert t.complete(root).status==3
 # Backend-only fixture completion, never evidence of native gameplay.
 with t.db.transaction() as db:
  for row in db.execute('SELECT * FROM research_tasks WHERE player_id=? AND parent_id=?',(t.player,root)).fetchall():db.execute('UPDATE research_tasks SET progress=? WHERE player_id=? AND id=?',(json.loads(row['definition'])['target'],t.player,row['id']))
 for i in range(4):assert t.story_claim(root,root+'_SUB_'+str(i)).status==1
 assert t.complete(root).status==1;assert t.complete(root).status==4
 again=stats(t);assert again.level==next_level and again.experience>=xp and again.level_cap==next_level
 t.game=Game(t.cfg,Database(t.db.path));assert stats(t).level==next_level
 if next_level<80:
  with t.db.transaction() as db:assert db.execute('SELECT 1 FROM research_stories WHERE id=?',(f'LEVEL_{next_level+1}_CAP',)).fetchone()

def test_level71_exact_qualifiers_and_single_day_counter(fx):
 t=fx;opted(t,progression.pinned()['requiredExperience'][69]);stats(t)
 with t.db.transaction() as db:
  research.progress(t.game,db,t.player,'power_up',20,{'species':1});assert db.execute('SELECT progress FROM research_tasks WHERE id=?',('LEVEL_71_CAP_SUB_1',)).fetchone()[0]==0
  research.progress(t.game,db,t.player,'power_up',20,{'species':150});assert db.execute('SELECT progress FROM research_tasks WHERE id=?',('LEVEL_71_CAP_SUB_1',)).fetchone()[0]==20
  research.progress(t.game,db,t.player,'throw',1,{'quality':1.2});research.progress(t.game,db,t.player,'throw',1,{'quality':1.7});assert db.execute('SELECT progress FROM research_tasks WHERE id=?',('LEVEL_71_CAP_SUB_2',)).fetchone()[0]==1
 assert stats(t).level==70
 with t.db.transaction() as db:
  db.execute("UPDATE research_tasks SET progress=0 WHERE id='LEVEL_71_CAP_SUB_1'")
  research.progress(t.game,db,t.player,'power_up',1,{'species':151});assert db.execute("SELECT progress FROM research_tasks WHERE id='LEVEL_71_CAP_SUB_1'").fetchone()[0]==1


@pytest.mark.parametrize('level,slot',[(level,slot) for level in range(71,81) for slot in (1,2,3)])
def test_every_high_level_action_gate_has_a_qualifying_event(fx,level,slot):
 t=fx;opted(t,300000000)
 with t.db.transaction() as db:db.execute('INSERT OR REPLACE INTO trainer_level_caps VALUES(?,?,?,1)',(t.player,level-1,'Backend-only gate qualifier fixture'))
 stats(t);ident=f'LEVEL_{level}_CAP_SUB_{slot}'
 with t.db.transaction() as db:
  row=db.execute('SELECT * FROM research_tasks WHERE id=?',(ident,)).fetchone();spec=json.loads(row['definition']);event=spec['event'];context={}
  conditions=levels.reference()['quests'][ident]['quest']['goal'].get('condition',[])
  for cond in conditions:
   kind=cond['type']
   if kind=='WITH_THROW_TYPE':context['quality']=2
   elif kind=='WITH_LEGENDARY_POKEMON':context['species']=150
   elif kind=='WITH_POKEMON_LEVEL':context.update(level=50,pokemon_id=1234)
   elif kind=='WITH_DISTANCE':context['distance_km']=cond['withDistance']['distanceKm']
   elif kind=='WITH_LUCKY_POKEMON':context['lucky']=True
   elif kind=='WITH_SUPER_EFFECTIVE_CHARGE':context['super_effective']=True
   elif kind=='WITH_INVASION_CHARACTER':event='rocket_grunt' if 'GRUNT' in cond['withInvasionCharacter']['characterCategory'] else 'rocket_leader'
   elif kind=='WITH_COMBAT_TYPE' and event=='pvp_win':context['source']='league'
  research.progress(t.game,db,t.player,'unsupported-event',1,context)
  assert db.execute('SELECT progress FROM research_tasks WHERE id=?',(ident,)).fetchone()[0]==0
  research.progress(t.game,db,t.player,event,1,context)
  assert db.execute('SELECT progress FROM research_tasks WHERE id=?',(ident,)).fetchone()[0]==1
 assert stats(t).level==level-1
