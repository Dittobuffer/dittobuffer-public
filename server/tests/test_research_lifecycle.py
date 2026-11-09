import json
import pytest
from unittest.mock import patch
from tests import test_research as research_fixture
from dittobuffer.gameplay import stories,research,research_lifecycle as life
from dittobuffer.gameplay.game import p,Game,Database

@pytest.fixture
def fx():
 t=research_fixture.ResearchTests();t.setUp()
 try:yield t
 finally:t.tearDown()

def enroll(t,spec):
 t.game.research['stories']=[spec];t.cfg['research']['stories']=[spec]
 with t.db.transaction() as db:stories.ensure(t.game,db,t.player)
 return stories.root_id(spec['id'],0)

def stage(event='spin'):
 return {'objectives':[{'event':event,'target':1,'description':'Record a real observation','rewards':[{'type':'xp','amount':10}]}],'rewards':[{'type':'item','item':1,'amount':2}]}

def test_native_timed_sections_progress_expiration_and_recovery(fx):
 t=fx;spec={'id':'weekend','title':'Weekend Explorer','research_type':'timed','start_ms':1000,'end_ms':10000,'stages':[stage(),stage('catch')]}
 with patch.object(life,'clock',return_value=2000):
  root=enroll(t,spec);ui=t.call('GET_QUEST_UI',p.GetQuestUiProto(),p.GetQuestUiOutProto)
  assert [v.timed_quest.quest_id for v in ui.season_view.sections]==[root]
  with t.db.transaction() as db:research.progress(t.game,db,t.player,'spin')
  child=stories.child_id(root,0);assert t.call('COMPLETE_QUEST',p.CompleteQuestProto(quest_id=root,sub_quest_id=child),p.CompleteQuestOutProto).status==1
  assert t.complete(root).status==1
  t.game=Game(t.cfg,Database(t.db.path));nextroot=stories.root_id('weekend',1)
  assert t.call('GET_NEW_QUESTS',p.GetNewQuestsProto(),p.GetNewQuestsOutProto).quests[0].quest.quest_id==nextroot
 with patch.object(life,'clock',return_value=10000):
  with t.db.transaction() as db:research.progress(t.game,db,t.player,'catch')
  assert t.progress(stories.child_id(nextroot,0))==0
  assert t.complete(nextroot).status==2
  result=t.call('GET_NEW_QUESTS',p.GetNewQuestsProto(),p.GetNewQuestsOutProto);assert not result.quests and nextroot in result.removed_quest_ids

def test_collection_exact_form_method_no_storage_credit_and_once_claim(fx):
 t=fx;form=t.game.species[1]['form'];spec={'id':'kanto','title':'Kanto Discovery','research_type':'collection','start_ms':0,'end_ms':2**60,'entries':[{'species':1,'form':form,'methods':['evolve']}],'rewards':[{'type':'xp','amount':100}]}
 root=enroll(t,spec);child=stories.child_id(root,0);assert t.progress(child)==0
 with t.db.transaction() as db:
  research.progress(t.game,db,t.player,'catch',context={'species':1,'form':form});research.progress(t.game,db,t.player,'evolve',context={'species':1,'form':form+1})
 assert t.progress(child)==0
 with t.db.transaction() as db:research.progress(t.game,db,t.player,'evolve',context={'species':1,'form':form})
 assert t.progress(child)==1
 ui=t.call('GET_QUEST_UI',p.GetQuestUiProto(),p.GetQuestUiOutProto);assert ui.season_view.sections[0].mini_collection.quest_id==root
 q=t.call('GET_NEW_QUESTS',p.GetNewQuestsProto(),p.GetNewQuestsOutProto).quests[0].quest;assert q.quest_type==45 and q.mini_collection.pokemon[0].caught
 assert t.complete(root).status==1;assert t.complete(root).status==4

def test_branch_selection_freezes_one_path_and_rejects_other_rewards(fx):
 t=fx;spec={'id':'paths','title':'Choose an observation','research_type':'branching','paths':[{'id':'catch','title':'Catch path','stages':[stage('catch')],'rewards':[{'type':'xp','amount':20}]},{'id':'spin','title':'Spin path','stages':[stage('spin')],'rewards':[{'type':'xp','amount':30}]}]}
 root=enroll(t,spec);before=t.game.research
 assert t.call('COMPLETE_QUEST',p.CompleteQuestProto(quest_id=root,choice_id=1),p.CompleteQuestOutProto).status==1
 assert t.call('COMPLETE_QUEST',p.CompleteQuestProto(quest_id=root,choice_id=0),p.CompleteQuestOutProto).status==4
 t.game=Game(t.cfg,Database(t.db.path))
 with t.db.transaction() as db:
  row=db.execute('SELECT * FROM research_stories WHERE id=?',('paths',)).fetchone();assert json.loads(row['definition'])['chosen_path']=='spin';research.progress(t.game,db,t.player,'catch')
 assert t.progress(stories.child_id(stories.root_id('paths',1),0))==0
 with t.db.transaction() as db:research.progress(t.game,db,t.player,'spin')
 assert t.progress(stories.child_id(stories.root_id('paths',1),0))==1

def test_exclusive_branch_race_commits_one_path(fx):
 from concurrent.futures import ThreadPoolExecutor
 t=fx;spec={'id':'race','title':'Choose once','research_type':'branching','paths':[{'id':'catch','title':'Catch','stages':[stage('catch')],'rewards':[{'type':'xp','amount':20}]},{'id':'spin','title':'Spin','stages':[stage('spin')],'rewards':[{'type':'xp','amount':30}]}]};root=enroll(t,spec)
 def select(i):return t.call('COMPLETE_QUEST',p.CompleteQuestProto(quest_id=root,choice_id=i),p.CompleteQuestOutProto,rpc_id=9900+i).status
 with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(select,range(2)))==[1,4]
 with t.db.transaction() as db:
  definition=json.loads(db.execute('SELECT definition FROM research_stories WHERE id=?',('race',)).fetchone()[0]);assert definition['chosen_path'] in ('catch','spin');assert db.execute('SELECT COUNT(*) FROM research_stage_claims').fetchone()[0]==1
