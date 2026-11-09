"""Research UI contracts and calendar gates; backend evidence, not Android."""
import json,struct,zlib
from unittest.mock import patch
from tests.test_research_lifecycle import fx,enroll
from tests.test_level_research import opted,stats
from dittobuffer.gameplay import research,stories,research_lifecycle as life,level_research as levels
from dittobuffer.gameplay.research_presentation import branch,card_png
from dittobuffer.gameplay.game import p


def test_native_branch_asset_label_and_tiny_original_png(fx):
 t=fx;t.game.config['world']['map_tiles_base']='http://127.0.0.1:38083/map/tiles'
 result=branch(t.game,{'title':'Observe Pokemon','description':'Catch one Pokemon <safely>.'})
 assert result.button_background_image_url=='http://127.0.0.1:38083/assets/dittobuffer-research-card.png'
 assert result.title_key=='Observe Pokemon' and '<size=18>' in result.button_text_key
 assert '<safely>' not in result.button_text_key and result.description_key=='Catch one Pokemon <safely>.'
 body=card_png();assert body.startswith(b'\x89PNG\r\n\x1a\n') and len(body)<2048
 assert struct.unpack('>II',body[16:24])==(512,128)
 at=8;data=b''
 while at<len(body):
  size=struct.unpack('>I',body[at:at+4])[0];kind=body[at+4:at+8];payload=body[at+8:at+8+size]
  assert struct.unpack('>I',body[at+8+size:at+12+size])[0]==zlib.crc32(kind+payload)&0xffffffff
  if kind==b'IDAT':data+=payload
  at+=12+size
 assert len(zlib.decompress(data))==128*(1+512*3)


def test_collection_criteria_ready_is_not_claimed_or_ended(fx):
 t=fx;spec={'id':'ui-collection','title':'Native collection','research_type':'collection','start_ms':0,'end_ms':2**60,'entries':[{'species':1,'methods':['catch']}],'rewards':[{'type':'xp','amount':100}]}
 root=enroll(t,spec)
 with t.db.transaction() as db:
  research.progress(t.game,db,t.player,'catch',context={'species':1})
  row=db.execute('select * from research_stories where id=?',(spec['id'],)).fetchone();q=life.collection(t.game,db,t.player,row).quest
  assert q.progress==1 and q.mini_collection.pokemon[0].caught and not q.mini_collection.completed
 result=t.complete(root);assert result.status==1 and result.quest.quest.mini_collection.completed
 assert t.complete(root).status==4


def test_calendar_poll_resets_incomplete_but_preserves_earned_single_day_gate(fx):
 t=fx;opted(t,300000000)
 with patch('dittobuffer.gameplay.research.day',return_value='2026-10-24'):
  stats(t)
  with t.db.transaction() as db:research.progress(t.game,db,t.player,'catch',99)
 with patch('dittobuffer.gameplay.research.day',return_value='2026-10-25'):
  stats(t);assert t.progress('LEVEL_71_CAP_SUB_3')==0
  with t.db.transaction() as db:research.progress(t.game,db,t.player,'catch',100)
 with patch('dittobuffer.gameplay.research.day',return_value='2026-10-26'):
  stats(t)
  with t.db.transaction() as db:research.progress(t.game,db,t.player,'catch',1)
  assert t.progress('LEVEL_71_CAP_SUB_3')==100


def test_route_streak_repeated_day_and_gap_refresh(fx):
 t=fx;opted(t,300000000)
 with t.db.transaction() as db:db.execute('INSERT INTO trainer_level_caps VALUES(?,?,?,1)',(t.player,71,'Backend-only gate fixture'))
 with patch('dittobuffer.gameplay.research.day',return_value='2026-10-24'):
  stats(t)
  with t.db.transaction() as db:
   research.progress(t.game,db,t.player,'route');research.progress(t.game,db,t.player,'route')
  assert t.progress('LEVEL_72_CAP_SUB_1')==1
 with patch('dittobuffer.gameplay.research.day',return_value='2026-10-25'):stats(t);assert t.progress('LEVEL_72_CAP_SUB_1')==1
 with patch('dittobuffer.gameplay.research.day',return_value='2026-10-27'):
  stats(t);assert t.progress('LEVEL_72_CAP_SUB_1')==0
  with t.db.transaction() as db:research.progress(t.game,db,t.player,'route')
  assert t.progress('LEVEL_72_CAP_SUB_1')==1


def test_pve_charge_progress_only_after_resolved_hit_and_once(fx):
 from dittobuffer.gameplay import gym_battle as engine
 from dittobuffer.gameplay.species import make_pokemon
 t=fx;source=make_pokemon(p,t.game.species[831],831,35,[15]*3);source.id=1001
 target=make_pokemon(p,t.game.species[130],130,35,[15]*3);target.id=1002;target.stamina=target.max_stamina=10000
 state={'start':0,'end':100000,'clock':0,'status':1,'active':0,'party':[engine.entry(source)],'defender':engine.entry(target),'ai_due':90000,'pending':[],'events':[]}
 state['party'][0]['energy']=100
 assert engine.schedule(t.game,state,0,3,0);assert not state.get('research_charges')
 engine.advance(t.game,state,10000);assert state['defender']['hp']<10000 and len(state['research_charges'])==1
 with t.db.transaction() as db:
  engine.project_research_charges(t.game,db,t.player,'actual-hit-fixture',state);engine.project_research_charges(t.game,db,t.player,'actual-hit-fixture',state)
  assert db.execute("select count(*) from trainer_events where player_id=? and kind='super_effective_charge'",(t.player,)).fetchone()[0]==1


def test_max_ordinary_charged_hit_emitter_deduplicates_real_request():
 from tests.test_max_battle import MaxBattleTests
 from dittobuffer.gameplay.species import make_pokemon
 from dittobuffer.gameplay import max_battle as battle
 t=MaxBattleTests();t.setUp()
 try:
  started=t.begin();state=t.combat(started)
  # Backend matchup fixture: a real catalog Water/Flying defender makes the
  # owned Wooloo's source-backed Wild Charge super-effective. Not Android proof.
  target=make_pokemon(p,t.game.species[130],130,35,[15]*3);target.id=999;target.stamina=target.max_stamina=10000
  state['boss']=__import__('dittobuffer.gameplay.gym_battle',fromlist=['entry']).entry(target)
  ps=state['players'][t.players[0]];ps['party'][ps['active']]['energy']=100
  with t.db.transaction() as db:db.execute('update max_rooms set state=? where id=?',(json.dumps(state),started.lobby.bread_lobby_id))
  event=p.BattleEventProto(type=4,actor_id=t.players[0][:20],attack=p.RaidPbsAttack(attack_type=2,source_pokemon_id=t.parties[0].id))
  first=t.event(started,event,sequence=8765);again=t.event(started,event,sequence=8765);assert first==again and first.result==1
  assert t.combat(started)['boss']['hp']<10000
  with t.db.transaction() as db:assert db.execute("select count(*) from trainer_events where player_id=? and kind='super_effective_charge'",(t.players[0],)).fetchone()[0]==1
 finally:t.tearDown()


def test_collection_medal_history_requires_a_real_claim_and_survives_expiry(fx):
 from dittobuffer.gameplay.medals import inventory
 t=fx;spec={'id':'ui-medal','title':'Original collection','research_type':'collection','start_ms':0,'end_ms':2**60,'entries':[{'species':1,'methods':['catch']}],'rewards':[{'type':'xp','amount':100}]};root=enroll(t,spec)
 with t.db.transaction() as db:
  research.progress(t.game,db,t.player,'catch',context={'species':1});assert inventory(t.game,db,t.player)==[]
 assert t.complete(root).status==1;assert t.complete(root).status==4
 with t.db.transaction() as db:
  item,=inventory(t.game,db,t.player);event,=item.badge_data.mini_collection.event
  assert item.badge_data.badge==1002 and event.event_id=='ui-medal' and event.completed_timestamp>0
  spec['end_ms']=1;db.execute('update research_stories set definition=? where id=?',(json.dumps(spec),spec['id']))
  assert inventory(t.game,db,t.player)==[item] and inventory(t.game,db,'other-player')==[]
 result=t.call('GET_HOLOHOLO_INVENTORY',p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
 assert any(p.HoloInventoryItemProto.FromString(v.item).badge_data==item.badge_data for v in result.inventory_delta.inventory_item)
