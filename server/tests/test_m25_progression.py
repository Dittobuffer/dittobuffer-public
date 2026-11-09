"""M25 cross-system regressions use isolated databases and real RPC envelopes."""
import copy,json
import pytest
from tests.test_research_lifecycle import fx,enroll,stage
from dittobuffer.gameplay import trainer,research,stories,level_research as levels,pokedex
from dittobuffer.gameplay.game import Game,Database,p
from dittobuffer.gameplay.liveops.catalog import Catalog
from dittobuffer.gameplay.liveops.generator import template
from dittobuffer.gameplay.liveops.bundle import from_generated,inspect
from dittobuffer.gameplay.liveops.research_preview import preview

@pytest.mark.parametrize('event,data,expected',[('gym_battle',{'result':2},1),('gym_battle',{'result':3},0),('pvp_battle',{'won':True,'source':'league'},1),('pvp_battle',{'won':False,'source':'friend'},0),('mega_evolve',{},1),('trade',{'lucky':True},1),('buddy_heart',{},1),('zygarde_cell',{'amount':3},3)])
def test_shared_committed_source_event_once_for_every_eligible_story(fx,event,data,expected):
 t=fx;mapping={'gym_battle':'gym_win','pvp_battle':'pvp_win'};kind=mapping.get(event,event)
 spec={'id':'shared','title':'Shared real event','stages':[stage(kind)]};spec['stages'][0]['objectives'][0]['target']=5
 root=enroll(t,spec)
 with t.db.transaction() as db:
  assert trainer.event(t.game,db,t.player,'same-source',event,data)
  assert not trainer.event(t.game,db,t.player,'same-source',event,data)
 assert t.progress(stories.child_id(root,0))==expected

@pytest.mark.parametrize('key',['research-beginner','research-weekend','research-kanto','research-signals','research-discovery','research-paths'])
def test_original_templates_valid_schema_v1(fx,key):
 t=fx;c=Catalog(json.loads((__import__("dittobuffer.paths",fromlist=["ROOT"]).ROOT/"config/family.json").read_text(encoding="utf8")));bundle=from_generated(template(key,c,'2026-11-01T10:00:00Z'));review=inspect(bundle,c);assert review['valid'],review['errors']

def test_feasibility_preview_is_read_only_and_flags_missing_species(fx):
 t=fx;c=Catalog(json.loads((__import__("dittobuffer.paths",fromlist=["ROOT"]).ROOT/"config/family.json").read_text(encoding="utf8")));doc=template('research-weekend',c,'2026-11-01T10:00:00Z')['document'];doc['stages'][0]['objectives'][0].update(target=20,conditions={'species':[25]})
 before=t.db.path.read_bytes();result=preview(doc,c,defaults=[{'species':[{'id':1}]}]);assert result['read_only'];assert any('20 qualifying catches' in v['message'] for v in result['warnings']);assert t.db.path.read_bytes()==before

def test_modern_storage_rewards_once_and_missing_resources_retained(fx):
 t=fx;t.cfg['progression_schedule']=levels.MODE;t.game=Game(t.cfg,Database(t.db.path))
 with t.db.transaction() as db:
  db.execute('UPDATE players SET xp=48000 WHERE id=?',(t.player,));before=t.game.capacity(db,t.player,'item_capacity')
 result=t.call('LEVEL_UP_REWARDS',p.LevelUpRewardsProto(level=10),p.LevelUpRewardsOutProto);assert result.result in (1,3)
 with t.db.transaction() as db:
  after=t.game.capacity(db,t.player,'item_capacity');assert after>before;ent=db.execute('SELECT COUNT(*) FROM trainer_level_entitlements WHERE player_id=?',(t.player,)).fetchone()[0];assert ent==9
 assert t.call('LEVEL_UP_REWARDS',p.LevelUpRewardsProto(level=10),p.LevelUpRewardsOutProto).result==2
 with t.db.transaction() as db:assert t.game.capacity(db,t.player,'item_capacity')==after

def test_modern_migration_refuses_level_decrease(fx):
 t=fx;t.cfg['progression_schedule']=levels.MODE;t.game=Game(t.cfg,Database(t.db.path))
 with t.db.transaction() as db:db.execute('INSERT OR REPLACE INTO player_stats_history VALUES(?,?,?,?)',(t.player,40,1,p.PlayerStatsProto(level=40).SerializeToString()))
 with pytest.raises(ValueError,match='migration requires review'):t.call('GET_HOLOHOLO_INVENTORY',p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
 with t.db.transaction() as db:assert not db.execute('SELECT 1 FROM trainer_level_caps WHERE player_id=?',(t.player,)).fetchone()

def test_max_and_mega_dex_facts_survive_transfer(fx):
 t=fx
 with t.db.transaction() as db:
  mon=p.PokemonProto(pokemon_id=1,individual_attack=15,individual_defense=15,individual_stamina=15);mon.pokemon_display.form=t.game.species[1]['form'];mon.pokemon_display.bread_mode_enum=1;mon.pokemon_display.shiny=True;mon.pokemon_display.gender=1
  pokedex.record(t.game,db,t.player,'isolated-capture',mon,True);db.execute("INSERT INTO mega_ledger VALUES(?,?,'evolve',1,1,1,0,0,1)",(t.player,'isolated-mega'))
  entry=pokedex.inventory(t.game,db,t.player)[0].pokedex_entry;kinds={v.value.pokedex_category for v in entry.category_status};assert {1,2,11,13,14,18,101,102}<=kinds;assert entry.bread_dex_data[0].modifier_id==1;assert entry.temp_evo_data[0].times_obtained==1
 t.game=Game(t.cfg,Database(t.db.path))
 with t.db.transaction() as db:assert pokedex.inventory(t.game,db,t.player)[0].pokedex_entry==entry


def test_modern_native_reward_templates_match_deliverable_source():
 values=levels.reward_templates({'progression_schedule':levels.MODE});assert len(values)==79
 by_level={v.level_up_rewards.level:v.level_up_rewards for v in values}
 assert by_level[10].items==[2202] and by_level[10].items_count==[25]
 for value in by_level.values():assert 4 not in value.items
 assert levels.reward_templates({'progression_schedule':'local'})==[]


def test_resolved_super_effective_charge_projects_once_across_polls():
 from tests.test_pvp import PvpTests
 from dittobuffer.gameplay import pvp_engine as engine,pvp_rules as rules,pvp_rewards
 t=PvpTests();t.setUp()
 try:
  state=t.state();target=rules.active(state,1)
  move=next(k for k,v in rules.MOVES.items() if v['energy_delta']<0 and v['power']>0 and rules.effectiveness(v['type'],rules.rules_for(t.game,rules.source(target))['types'])>1)
  state['charge']={'side':0,'move':move,'score':1,'shield':False,'defense':1};engine.resolve_charge(t.game,state,0)
  assert state['history'][-1]['super_effective'] and target['hp']<state['history'][-1]['before_hp']
  with t.game.db.transaction() as db:
   pvp_rewards.settle(t.game,db,state);pvp_rewards.settle(t.game,db,state)
   assert db.execute("SELECT COUNT(*) FROM trainer_events WHERE player_id=? AND kind='super_effective_charge'",(t.players[0],)).fetchone()[0]==1
  with t.game.db.transaction() as db:
   pvp_rewards.settle(t.game,db,state)
   assert db.execute("SELECT COUNT(*) FROM trainer_events WHERE player_id=? AND kind='super_effective_charge'",(t.players[0],)).fetchone()[0]==1
 finally:t.tearDown()
