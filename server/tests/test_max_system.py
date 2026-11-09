"""Max transaction and wire regressions; never substitutes for native combat."""
import json,sqlite3,tempfile,unittest
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from dittobuffer.gameplay.game import Game,ROOT,p,wire,now_ms
from dittobuffer.core.database import Database
from dittobuffer.gameplay import max_system as mx

class MaxTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds');self.path=Path(self.tmp.name);self.clock=now_ms();self.counter=0
  self.patches=[patch(v,lambda:self.clock) for v in ('dittobuffer.gameplay.game.now_ms','dittobuffer.gameplay.max_system.now_ms','dittobuffer.gameplay.gyms.now_ms')]
  for v in self.patches:v.start()
  self.cfg=json.loads((ROOT/'config/family.json').read_text());self.cfg.update(authentication={'mode':'local-development'},raids={**self.cfg.get('raids',{}),'participation_limit':1,'multiplayer':False});self.cfg.update(profile='m18-unit',world={'enabled':False},weather={'enabled':False},social={'enabled':True},rocket={'enabled':False})
  self.db=Database(self.path/'max.sqlite3');self.game=Game(self.cfg,self.db)
  # Explicit synthetic unit-test reference: these are not official balance claims.
  templates={}
  for name,value in [('MP_SHARED_SETTINGS',p.GameMasterClientTemplateProto(template_id='MP_SHARED_SETTINGS',mp_settings=p.MpSharedSettingsProto(mp_capacity=1500,mp_base_daily_limit=800))),('BREAD_FEATURE_FLAGS',p.GameMasterClientTemplateProto(template_id='BREAD_FEATURE_FLAGS')),('BREAD_SHARED_SETTINGS',p.GameMasterClientTemplateProto(template_id='BREAD_SHARED_SETTINGS')),('BREAD_MOVE_MAPPING_SETTINGS',p.GameMasterClientTemplateProto(template_id='BREAD_MOVE_MAPPING_SETTINGS')),('BREAD_MOVE_LEVEL_SETTINGS_GROUP_1',p.GameMasterClientTemplateProto(template_id='BREAD_MOVE_LEVEL_SETTINGS_GROUP_1'))]:templates[name]=value.SerializeToString().hex()
  ref=self.path/'ref.json';ref.write_text(json.dumps(dict(templates=templates,species_groups={f"831:{self.game.species[831]['form']}":1})))
  source=self.path/'candidates.sqlite3'
  with closing(sqlite3.connect(source)) as db:
   db.executescript('CREATE TABLE metadata(key TEXT,value TEXT);CREATE TABLE candidates(id TEXT,name TEXT,lat REAL,lng REAL);');db.execute('INSERT INTO metadata VALUES(?,?)',('source',json.dumps({'license':'ODbL-1.0'})));db.executemany('INSERT INTO candidates VALUES(?,?,?,?)',[(f'osm:n{n}',f'Unit public place {n}',52.08+n*.00001,5.12) for n in range(20)]);db.commit()
  self.cfg['max_system']=dict(enabled=True,reference=str(ref),candidates=str(source),active_probability=1,pools=[dict(species=831,tier=1,level=10,mode=1)])
  mx.install(self.game)
  self.tickets=[self.game.authenticate(wire.AuthRequest(auth_id='max-unit-'+str(v))).ticket for v in range(2)];self.players=[]
  with self.db.transaction() as db:
   for ticket in self.tickets:
    player=db.execute('SELECT player_id FROM sessions WHERE ticket=?',(ticket,)).fetchone()[0];self.players.append(player);db.execute('UPDATE players SET xp=210000 WHERE id=?',(player,));db.execute('INSERT INTO social_locations VALUES(?,?,?,?,?,?)',(player,52.08,5.12,5,self.clock,self.clock))
   mx.materialize(self.game,db,[52,53,5,6]);self.stations=db.execute('SELECT * FROM max_stations ORDER BY id').fetchall()
 def tearDown(self):
  for v in self.patches:v.stop()
  self.tmp.cleanup()
 def call(self,method,request,typ,index=0):
  self.counter+=1;req=wire.RpcRequest(rpc_id=self.counter);req.requests.add(method=method,body=request.SerializeToString())
  return typ.FromString(self.game.rpc(req,self.tickets[index],run='max-unit').payloads[0].payload)
 def loot(self,n=0,index=0):
  row=self.stations[n];return self.call(2461,p.LootStationProto(station_id=row['id'],station_lat_degrees=row['lat'],station_lng_degrees=row['lng']),p.LootStationOutProto,index)
 def max_owned(self):
  """Explicit owned fixture; this helper does not stand in for an earned catch."""
  from dittobuffer.gameplay.species import make_pokemon
  from dittobuffer.core.management import add_candy
  with self.db.transaction() as db:
   spawn_id=100000+db.execute('SELECT COUNT(*) FROM pokemon').fetchone()[0]
   db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',(spawn_id,'max-management-unit',831,52.08,5.12,0))
   mon=make_pokemon(p,self.game.species[831],831,15,[10]*3);mx.attach(self.game,mon)
   mon.bread_moves[1].move_level=2;mon.bread_moves[2].move_level=1
   mon.id=db.execute('INSERT INTO pokemon(player_id,spawn_id,species,data) VALUES(?,?,831,?)',(self.players[0],spawn_id,b'')).lastrowid
   db.execute('UPDATE pokemon SET data=? WHERE id=?',(mon.SerializeToString(),mon.id))
   db.execute('UPDATE players SET stardust=100000 WHERE id=?',(self.players[0],))
   add_candy(db,self.players[0],self.game.species[831]['family'],1000)
   return mon
 def test_native_wire_corrections(self):
  from protocol.opaque import fields
  for message,tag,kind in [(p.MpSharedSettingsProto(mp_capacity=1500),9,0),(p.MpSharedSettingsProto(mp_base_daily_limit=800),10,0),(p.PokemonDisplayProto(bread_mode_enum=1),16,0),(p.EnhanceBreadMoveProto(pokemon_id=123),1,1),(p.LootStationOutProto(spawned_encounter_id=123),4,1)]:self.assertIn((tag,kind),[(n,k) for n,k,_,v in fields(message.SerializeToString())])
  self.assertEqual(p.ClientMapCellProto.DESCRIPTOR.fields_by_name['stations'].message_type.full_name,p.StationProto.DESCRIPTOR.full_name)
  self.assertEqual(p.ClientMapCellProto.DESCRIPTOR.fields_by_name['stations'].number,17)
  self.assertEqual(p.StartBreadBattleProto.DESCRIPTOR.fields_by_name['attacking_pokemon_id'].number,3)
  self.assertEqual(p.StartBreadBattleProto.DESCRIPTOR.fields_by_name['attacking_pokemon_id'].type,6)
  self.assertEqual(p.PokemonExtendedSettingsProto.DESCRIPTOR.fields_by_name['bread_overrides'].number,67)
 def test_station_and_battle_detail_preserve_native_deleted_tags(self):
  # Literal native tags prevent a generated descriptor from agreeing with its
  # own incorrect schema while the client silently treats a station as closed.
  self.assertEqual(p.StationProto(is_bread_battle_available=True,is_inactive=True,max_battle_remote_eligibility=1,player_edits_disabled=True).SerializeToString(),bytes.fromhex('5801600168017001'))
  detail=p.BreadBattleDetailProto(min_recommended_player_count=1,max_recommended_player_count=4,battle_music_override_asset_id='x',skip_reward_encounter=True,schedule_source=3)
  self.assertEqual(detail.SerializeToString(),bytes.fromhex('580160047201787801800103'))
 def test_particles_are_not_bag_items_and_claims_survive_restarts(self):
  self.assertEqual(self.loot().status,1)
  self.game=Game(self.cfg,self.db);self.assertEqual(self.loot().status,1)
  with self.db.transaction() as db:
   self.assertEqual(mx.balance(db,self.players[0]),120);self.assertEqual(db.execute('SELECT COUNT(*) FROM max_ledger').fetchone()[0],1);self.assertIsNone(db.execute('SELECT 1 FROM items WHERE item=1506').fetchone());self.assertEqual(mx.inventory(self.game,db,self.players[0])[0].item.count,120)
 def test_daily_threshold_and_capacity_are_authoritative(self):
  for n in range(7):self.assertEqual(self.loot(n).status,1)
  self.assertEqual(self.loot(7).status,7)
  with self.db.transaction() as db:db.execute('UPDATE max_wallet SET balance=1500 WHERE player_id=?',(self.players[0],))
  self.assertEqual(self.loot(8).status,3)
 def test_reservations_do_not_spend_until_win_and_release_safely(self):
  with self.db.transaction() as db:
   player=self.players[0];mx.mutate(self.game,db,player,500,'fixture');self.assertTrue(mx.reserve(self.game,db,player,'battle-1',250,self.clock+60000));self.assertTrue(mx.reserve(self.game,db,player,'battle-1',250,self.clock+60000));self.assertEqual(mx.balance(db,player),500);self.assertEqual(mx.available(db,player),250)
   self.assertFalse(mx.reserve(self.game,db,player,'battle-2',400,self.clock+60000))
   with self.assertRaises(ValueError):mx.mutate(self.game,db,player,-300,'conflicting-upgrade')
   mx.release_hold(db,player,'battle-1');self.assertEqual(mx.available(db,player),500)
   self.assertTrue(mx.reserve(self.game,db,player,'battle-3',250,self.clock+60000));self.assertTrue(mx.charge_hold(self.game,db,player,'battle-3'));self.assertFalse(mx.charge_hold(self.game,db,player,'battle-3'));self.assertEqual(mx.balance(db,player),250)
 def test_expired_holds_cannot_corrupt_new_attempts(self):
  with self.db.transaction() as db:
   player=self.players[0];mx.mutate(self.game,db,player,300,'fixture');self.assertTrue(mx.reserve(self.game,db,player,'old',250,self.clock+1000));self.clock+=1001
   self.assertTrue(mx.reserve(self.game,db,player,'new',250,self.clock+60000))
   with self.assertRaises(ValueError):mx.charge_hold(self.game,db,player,'old')
   self.assertEqual(mx.balance(db,player),300);self.assertTrue(mx.charge_hold(self.game,db,player,'new'));self.assertEqual(mx.balance(db,player),50)
 def test_clamp_policy_can_forbid_overshoot(self):
  self.game.max_config['cap_policy']='clamp'
  for n in range(7):self.assertEqual(self.loot(n).status,1)
  with self.db.transaction() as db:self.assertEqual(mx.balance(db,self.players[0]),800)
 def test_separate_accounts_and_stale_positions(self):
  self.assertEqual(self.loot(index=0).status,1);self.assertEqual(self.loot(index=1).status,1)
  self.clock+=120001;self.assertEqual(self.loot(1,index=0).status,6)
 def test_daily_reset_uses_amsterdam_calendar_across_dst(self):
  for date,expected in [('2026-03-29T04:59:59','2026-03-28'),('2026-03-29T05:00:00','2026-03-29'),('2026-10-25T04:59:59','2026-10-24'),('2026-10-25T05:00:00','2026-10-25')]:
   self.assertEqual(mx.day(self.game,int(datetime.fromisoformat(date).replace(tzinfo=ZoneInfo('Europe/Amsterdam')).timestamp()*1000)),expected)
 def test_stable_spots_are_separate_from_forts_and_do_not_grant_pokemon(self):
  with self.db.transaction() as db:
   mx.materialize(self.game,db,[52,53,5,6]);self.assertEqual(db.execute('SELECT COUNT(*) FROM max_stations').fetchone()[0],20);self.assertEqual(db.execute('SELECT COUNT(*) FROM forts').fetchone()[0],0);self.assertEqual(db.execute('SELECT COUNT(*) FROM pokemon').fetchone()[0],0);self.assertEqual(mx.balance(db,self.players[0]),0)
 def test_disabled_profile_has_no_max_tables(self):
  cfg=dict(self.cfg,max_system={'enabled':False});db=Database(self.path/'disabled.sqlite3');Game(cfg,db)
  with db.transaction() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE 'max_%'").fetchone()[0],0)
 def test_negative_balance_and_reused_ledger_identity_rejected(self):
  with self.db.transaction() as db:
   with self.assertRaises(ValueError):mx.mutate(self.game,db,self.players[0],-1,'negative')
   mx.mutate(self.game,db,self.players[0],50,'credit');self.assertFalse(mx.mutate(self.game,db,self.players[0],50,'credit'))
   with self.assertRaises(ValueError):mx.mutate(self.game,db,self.players[0],51,'credit')
 def test_walking_requires_validated_distance_and_claims_once(self):
  self.assertEqual(self.call(2458,p.StartMpWalkQuestProto(),p.StartMpWalkQuestOutProto).status,1)
  self.assertEqual(self.call(2458,p.StartMpWalkQuestProto(),p.StartMpWalkQuestOutProto).status,2)
  with self.db.transaction() as db:
   quest=mx.walking_quest(self.game,db,self.players[0]);self.assertEqual(quest.quest_context,13);self.assertEqual(quest.goal.target,2000);self.assertEqual(quest.progress,0)
  req=p.CompleteQuestProto(quest_id=quest.quest_id)
  self.assertEqual(self.call(902,req,p.CompleteQuestOutProto).status,3)
  # Controlled distance fixture tests accounting, not physical walking.
  with self.db.transaction() as db:db.execute('UPDATE walking SET total_m=2000 WHERE player_id=?',(self.players[0],))
  self.assertEqual(self.call(902,req,p.CompleteQuestOutProto).status,1);self.assertEqual(self.call(902,req,p.CompleteQuestOutProto).status,4)
  with self.db.transaction() as db:self.assertEqual(mx.balance(db,self.players[0]),300)
 def test_walk_fixture_locations_are_not_distance(self):
  from dittobuffer.gameplay.eggs import sample,ensure
  with self.db.transaction() as db:
   ensure(self.game,db,self.players[0])
   for n in range(2):sample(self.game,db,self.players[0],dict(lat=52.08+n*.001,lng=5.12,accuracy=1,time=self.clock/1000,provider=3,source='debug'));self.clock+=60000
   self.assertEqual(db.execute('SELECT total_m FROM walking WHERE player_id=?',(self.players[0],)).fetchone()[0],0)
 def test_walking_quest_details_are_owner_scoped_and_survive_restart(self):
  self.assertEqual(self.call(2458,p.StartMpWalkQuestProto(),p.StartMpWalkQuestOutProto).status,1)
  with self.db.transaction() as db:
   identifier=mx.walking_quest(self.game,db,self.players[0]).quest_id
   db.execute('UPDATE walking SET total_m=1200 WHERE player_id=?',(self.players[0],))
  self.game=Game(self.cfg,self.db)
  request=p.GetQuestDetailsProto(quest_id=[identifier,identifier])
  result=self.call(901,request,p.GetQuestDetailsOutProto)
  self.assertEqual(result.status,1);self.assertEqual(len(result.quests),1)
  self.assertEqual(result.quests[0].quest.quest_id,identifier);self.assertEqual(result.quests[0].quest.progress,1200)
  self.assertEqual(result.quests[0].quest.quest_rewards[0].item.item,1506)
  other=self.call(901,request,p.GetQuestDetailsOutProto,index=1)
  self.assertEqual(other.status,2);self.assertEqual(len(other.quests),0)
  with self.db.transaction() as db:db.execute('UPDATE walking SET total_m=2000 WHERE player_id=?',(self.players[0],))
  complete=self.call(901,request,p.GetQuestDetailsOutProto)
  self.assertEqual(complete.quests[0].quest.progress,2000);self.assertGreater(complete.quests[0].quest.completion_timestamp_ms,0)
  self.assertEqual(self.call(902,p.CompleteQuestProto(quest_id=identifier),p.CompleteQuestOutProto).status,1)
  self.game=Game(self.cfg,self.db)
  claimed=self.call(901,request,p.GetQuestDetailsOutProto)
  self.assertEqual(claimed.status,2);self.assertEqual(len(claimed.quests),0)
 def test_first_max_research_requires_collection_and_preserves_earned_flags(self):
  from dittobuffer.gameplay import research,stories
  self.cfg['research']=dict(enabled=True,field_tasks={},field_pool=[],stories=[json.loads((ROOT/'config/max-research.example.json').read_text())]);research.validate(self.game)
  with self.db.transaction() as db:
   research.ensure(self.game,db,self.players[0]);root=stories.current(self.game,db,self.players[0])[0].quest.quest_id;child=stories.child_id(root,0)
  claim=p.CompleteQuestProto(quest_id=root,sub_quest_id=child)
  self.assertEqual(self.call(902,claim,p.CompleteQuestOutProto).status,3)
  self.assertEqual(self.loot().status,1);self.assertEqual(self.call(902,claim,p.CompleteQuestOutProto).status,1)
  stage=p.CompleteQuestProto(quest_id=root);self.assertEqual(self.call(902,stage,p.CompleteQuestOutProto).status,1);self.assertEqual(self.call(902,stage,p.CompleteQuestOutProto).status,4)
  with self.db.transaction() as db:
   rows=db.execute('SELECT a.data FROM research_encounters r JOIN spawn_attributes a ON a.spawn_id=r.spawn_id WHERE r.player_id=?',(self.players[0],)).fetchall();self.assertEqual(len(rows),1);mon=p.PokemonProto.FromString(rows[0][0]);self.assertEqual(mon.pokemon_id,831);self.assertEqual(mon.pokemon_display.bread_mode_enum,1);self.assertEqual([(v.move_type,v.move_level) for v in mon.bread_moves],[(1,1),(2,0),(3,0)]);self.assertEqual(db.execute('SELECT COUNT(*) FROM pokemon').fetchone()[0],0)
 def test_move_unlock_consumes_once_and_validates_ownership(self):
  from dittobuffer.gameplay.species import make_pokemon
  from dittobuffer.core.management import add_candy
  ref=json.loads(Path(self.game.max_config['reference']).read_text());template=p.GameMasterClientTemplateProto(template_id='BREAD_MOVE_LEVEL_SETTINGS_GROUP_1');template.bread_move_level_settings.b_settings.add(mp_cost=400,candy_cost=50,xp_reward=4000);ref['templates'][template.template_id]=template.SerializeToString().hex();self.game.max_reference=ref
  with self.db.transaction() as db:
   db.execute("INSERT INTO spawns VALUES(18831,'max-test',831,52.08,5.12,0)")
   mon=make_pokemon(p,self.game.species[831],831,15,[10]*3);mx.attach(self.game,mon);mon.id=db.execute('INSERT INTO pokemon(player_id,spawn_id,species,data) VALUES(?,18831,831,?)',(self.players[0],b'')).lastrowid;db.execute('UPDATE pokemon SET data=? WHERE id=?',(mon.SerializeToString(),mon.id));add_candy(db,self.players[0],self.game.species[831]['family'],100);mx.mutate(self.game,db,self.players[0],500,'unit-particles')
  req=p.EnhanceBreadMoveProto(pokemon_id=mon.id,move_type=2,target_move_level=1)
  self.assertEqual(self.call(2459,req,p.EnhanceBreadMoveOutProto,index=1).result,5)
  self.assertEqual(self.call(2459,req,p.EnhanceBreadMoveOutProto).result,1);self.assertEqual(self.call(2459,req,p.EnhanceBreadMoveOutProto).result,1)
  with self.db.transaction() as db:self.assertEqual(mx.balance(db,self.players[0]),100);self.assertEqual(db.execute('SELECT count FROM candy WHERE player_id=? AND family=?',(self.players[0],self.game.species[831]['family'])).fetchone()[0],50)
 def test_max_management_preserves_identity_flags_and_move_levels(self):
  from dittobuffer.core.management import owned,candy
  mon=self.max_owned();slots=[(v.move_type,v.move_level) for v in mon.bread_moves]
  self.assertEqual(self.call(148,p.SetFavoritePokemonProto(pokemon_id=mon.id,is_favorite=True),p.SetFavoritePokemonOutProto).result,1)
  self.assertEqual(self.call(112,p.ReleasePokemonProto(pokemon_id=mon.id),p.ReleasePokemonOutProto).status,3)
  self.assertEqual(self.call(149,p.NicknamePokemonProto(pokemon_id=mon.id,nickname='Max Partner'),p.NicknamePokemonOutProto).result,1)
  preview=self.call(147,p.UpgradePokemonProto(pokemon_id=mon.id,preview=True),p.UpgradePokemonOutProto)
  self.assertEqual(preview.result,1)
  upgraded=self.call(147,p.UpgradePokemonProto(pokemon_id=mon.id),p.UpgradePokemonOutProto)
  self.assertEqual(upgraded.result,1);self.assertGreater(upgraded.upgraded_pokemon.cp,mon.cp)
  for value in (preview.upgraded_pokemon,upgraded.upgraded_pokemon):
   self.assertEqual(value.id,mon.id);self.assertEqual(value.pokemon_display.bread_mode_enum,1)
   self.assertEqual([(v.move_type,v.move_level) for v in value.bread_moves],slots)
   self.assertTrue(value.favorite);self.assertEqual(value.nickname,'Max Partner')
  preview=self.call(125,p.EvolvePokemonProto(pokemon_id=mon.id,preview=True),p.EvolvePokemonOutProto)
  evolved=self.call(125,p.EvolvePokemonProto(pokemon_id=mon.id),p.EvolvePokemonOutProto)
  self.assertEqual(preview.result,1);self.assertEqual(evolved.result,1)
  self.assertEqual(preview.evolved_pokemon,evolved.evolved_pokemon)
  self.assertEqual(evolved.evolved_pokemon.pokemon_id,832);self.assertEqual(evolved.evolved_pokemon.id,mon.id)
  self.game=Game(self.cfg,self.db)
  with self.db.transaction() as db:
   stored=owned(db,self.players[0],mon.id);self.assertEqual(stored,evolved.evolved_pokemon)
   self.assertEqual(stored.pokemon_display.bread_mode_enum,1);self.assertEqual([(v.move_type,v.move_level) for v in stored.bread_moves],slots)
   self.assertTrue(stored.favorite);self.assertEqual(stored.nickname,'Max Partner')
   before=candy(db,self.players[0],self.game.species[831]['family'])
  inventory=self.call(4,p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
  values=[p.HoloInventoryItemProto.FromString(v.item) for v in inventory.inventory_delta.inventory_item if v.item]
  self.assertEqual([v.pokemon for v in values if v.HasField('pokemon')],[evolved.evolved_pokemon])
  self.assertEqual(self.call(148,p.SetFavoritePokemonProto(pokemon_id=mon.id,is_favorite=False),p.SetFavoritePokemonOutProto).result,1)
  released=self.call(112,p.ReleasePokemonProto(pokemon_id=mon.id),p.ReleasePokemonOutProto)
  self.assertEqual(released.status,1);self.assertEqual(released.candy_awarded,1)
  with self.db.transaction() as db:
   self.assertIsNone(owned(db,self.players[0],mon.id));self.assertEqual(candy(db,self.players[0],self.game.species[831]['family']),before+1)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM pokemon_tombstones WHERE player_id=? AND pokemon_id=?',(self.players[0],mon.id)).fetchone()[0],1)
  inventory=self.call(4,p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
  self.assertFalse(any(p.HoloInventoryItemProto.FromString(v.item).HasField('pokemon') for v in inventory.inventory_delta.inventory_item if v.item))
  # Routes add their own deletion key to the same delta; only Pokemon keys
  # belong to this tombstone assertion.
  keys=[key.pokemon_id for item in inventory.inventory_delta.inventory_item
        if item.deleted_item_key
        for key in [p.HoloInventoryKeyProto.FromString(item.deleted_item_key)]
        if key.pokemon_id]
  self.assertEqual(keys,[mon.id])
 def test_active_max_battle_lock_prevents_management_mutations(self):
  from dittobuffer.core.management import owned
  mon=self.max_owned()
  with self.db.transaction() as db:
   db.execute('INSERT INTO max_pokemon_locks VALUES(?,?,?,?)',(mon.id,self.players[0],'isolated-unit-battle',self.clock+60000))
   initial=tuple(db.execute('SELECT xp,stardust,revision FROM players WHERE id=?',(self.players[0],)).fetchone())
  for method,request,typ,field,status in [
   (112,p.ReleasePokemonProto(pokemon_id=mon.id),p.ReleasePokemonOutProto,'status',2),
   (147,p.UpgradePokemonProto(pokemon_id=mon.id),p.UpgradePokemonOutProto,'result',5),
   (125,p.EvolvePokemonProto(pokemon_id=mon.id),p.EvolvePokemonOutProto,'result',5),
   (2459,p.EnhanceBreadMoveProto(pokemon_id=mon.id,move_type=1,target_move_level=2),p.EnhanceBreadMoveOutProto,'result',5)]:
   self.assertEqual(getattr(self.call(method,request,typ),field),status)
  with self.db.transaction() as db:
   self.assertEqual(owned(db,self.players[0],mon.id),mon)
   self.assertEqual(tuple(db.execute('SELECT xp,stardust,revision FROM players WHERE id=?',(self.players[0],)).fetchone()),initial)
  self.clock+=60001
  self.assertEqual(self.call(147,p.UpgradePokemonProto(pokemon_id=mon.id),p.UpgradePokemonOutProto).result,1)
 def test_concurrent_move_upgrade_requests_charge_one_durable_operation(self):
  from dittobuffer.core.management import owned,candy
  mon=self.max_owned();mon.bread_moves[1].move_level=0
  ref=json.loads(Path(self.game.max_config['reference']).read_text());template=p.GameMasterClientTemplateProto(template_id='BREAD_MOVE_LEVEL_SETTINGS_GROUP_1')
  template.bread_move_level_settings.b_settings.add(mp_cost=400,candy_cost=50,xp_reward=4000)
  ref['templates'][template.template_id]=template.SerializeToString().hex();self.game.max_reference=ref
  with self.db.transaction() as db:
   db.execute('UPDATE pokemon SET data=? WHERE id=?',(mon.SerializeToString(),mon.id));mx.mutate(self.game,db,self.players[0],500,'concurrent-unit-particles')
   initial_xp=self.game.player(db,self.players[0])['xp'];initial_candy=candy(db,self.players[0],self.game.species[831]['family'])
  barrier=Barrier(4)
  def submit(number):
   # Distinct transport identities model separate submissions, not merely
   # replaying a cached RPC receipt. SQLite must serialize the domain change.
   req=wire.RpcRequest(rpc_id=10000+number);req.requests.add(method=2459,body=p.EnhanceBreadMoveProto(pokemon_id=mon.id,move_type=2,target_move_level=1).SerializeToString())
   barrier.wait(timeout=10)
   return p.EnhanceBreadMoveOutProto.FromString(self.game.rpc(req,self.tickets[0],run='max-concurrent-unit').payloads[0].payload)
  with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(submit,range(4)))
  self.assertEqual([v.result for v in results],[1]*4)
  self.assertEqual([(v.bread_move_slot.move_type,v.bread_move_slot.move_level) for v in results],[(2,1)]*4)
  with self.db.transaction() as db:
   self.assertEqual(mx.balance(db,self.players[0]),100)
   self.assertEqual(candy(db,self.players[0],self.game.species[831]['family']),initial_candy-50)
   self.assertEqual(self.game.player(db,self.players[0])['xp'],initial_xp+4000)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM max_ledger WHERE player_id=? AND source=?',(self.players[0],f'upgrade:{mon.id}:2:1')).fetchone()[0],1)
   self.assertEqual([(v.move_type,v.move_level) for v in owned(db,self.players[0],mon.id).bread_moves],[(1,1),(2,1),(3,1)])
if __name__=='__main__':unittest.main()
