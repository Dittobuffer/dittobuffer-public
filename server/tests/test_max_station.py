"""Isolated Max stationing/assist transactions, never native-device acceptance."""
import json
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from dittobuffer.gameplay.game import Game,p,wire,RpcError
from dittobuffer.gameplay import max_station as station,max_system as resources
from dittobuffer.core.management import owned,candy
from tests import test_max_battle as battles
from tests import test_max_system as resource_tests


class MaxStationTests(unittest.TestCase):
 setUp_resources=resource_tests.MaxTests.setUp
 setUp_battle=battles.MaxBattleTests.setUp
 tearDown=resource_tests.MaxTests.tearDown
 call=resource_tests.MaxTests.call
 create_owned=battles.MaxBattleTests.create_owned
 join=battles.MaxBattleTests.join
 prepare=battles.MaxBattleTests.prepare
 start=battles.MaxBattleTests.start
 begin=battles.MaxBattleTests.begin
 room=battles.MaxBattleTests.room
 combat=battles.MaxBattleTests.combat
 headers=battles.MaxBattleTests.headers
 state=battles.MaxBattleTests.state
 event=battles.MaxBattleTests.event
 attack=battles.MaxBattleTests.attack
 win=battles.MaxBattleTests.win
 extra_player=battles.MaxBattleTests.extra_player

 def setUp(self):
  self.setUp_battle();clock=patch('dittobuffer.gameplay.max_station.now_ms',lambda:self.clock);clock.start();self.patches.append(clock)
  self.game.max_config['stationing']=dict(enabled=True,reward_provenance='Synthetic isolated station reward policy; custom values, not official rewards.',candy_per_assist=1,xl_candy_per_assist=0,max_candy=5,max_xl_candy=0)
  shared=p.GameMasterClientTemplateProto(template_id='BREAD_SHARED_SETTINGS')
  shared.bread_settings.CopyFrom(p.BreadSharedSettingsProto(max_stationed_pokemon=40,max_stationed_pokemon_per_player=10,max_stationed_pokemon_display_count=40,min_ms_to_receive_release_station_rewards=1))
  self.game.max_reference['templates'][shared.template_id]=shared.SerializeToString().hex()
  boost=p.GameMasterClientTemplateProto(template_id='STATIONED_POKEMON_POWER_BOOST_TABLE_SETTINGS')
  boost.stationed_pokemon_table_settings.tier_boosts.add(num_stationed=1,hundredths_of_percent=200,num_boost_icons=1)
  boost.stationed_pokemon_table_settings.tier_boosts.add(num_stationed=2,hundredths_of_percent=350,num_boost_icons=2)
  self.game.max_reference['templates'][boost.template_id]=boost.SerializeToString().hex()
  Path(self.game.max_config['reference']).write_text(json.dumps(self.game.max_reference))
  with closing(self.db.connect()) as db:station.install(self.game,db)

 def victory(self,index=0,spot=0):
  joined=self.join(index,spot);self.assertEqual(joined.result,1)
  prepared=self.prepare(joined.bread_lobby,index,spot);self.assertEqual(prepared.result,1)
  self.clock=prepared.bread_lobby.bread_battle_start_ms
  started=self.start(index,spot);self.assertEqual(started.result,1);self.win(started,index)
  return started

 def assign(self,won,index=0,spot=0,pokemon_id=None):
  row=self.stations[spot]
  return self.call(2460,p.StationPokemonProto(station_id=row['id'],pokemon_id=pokemon_id or self.parties[index].id,station_lat_degrees=row['lat'],station_lng_degrees=row['lng'],bread_battle_id=str(won.lobby.bread_lobby_id)),p.StationPokemonOutProto,index)

 def release(self,index=0,spot=0,pokemon_id=None):
  return self.call(2472,p.ReleaseStationedPokemonProto(station_id=self.stations[spot]['id'],pokemon_id=pokemon_id or self.parties[index].id),p.ReleaseStationedPokemonOutProto,index)

 def statistics(self,index=0,pokemon_id=None):
  return self.call(2476,p.GetNumStationAssistsProto(pokemon_id=pokemon_id or self.parties[index].id),p.GetNumStationAssistsOutProto,index)

 def restart(self):
  cfg=deepcopy(self.cfg);cfg['max_system']=deepcopy(self.game.max_config);self.game=Game(cfg,self.db)

 def test_native_station_ids_and_battle_id_use_audited_wire(self):
  self.assertEqual(p.StationPokemonProto(pokemon_id=1,bread_battle_id='x').SerializeToString(),bytes.fromhex('110100000000000000320178'))
  self.assertEqual(p.ReleaseStationedPokemonProto(pokemon_id=1).SerializeToString(),bytes.fromhex('110100000000000000'))
  self.assertEqual(p.GetNumStationAssistsProto(pokemon_id=1).SerializeToString(),bytes.fromhex('090100000000000000'))
  self.assertEqual(p.PokemonProto.DESCRIPTOR.fields_by_name['deployed_station_id'].number,79)
  self.assertEqual(p.PokemonProto.DESCRIPTOR.fields_by_name['deployed_station_expiration_time_ms'].number,81)

 def test_assignment_requires_owned_max_and_actual_victory_and_range(self):
  request=p.StationPokemonProto(station_id=self.stations[0]['id'],pokemon_id=self.parties[0].id,station_lat_degrees=52.08,station_lng_degrees=5.12,bread_battle_id='1')
  self.assertEqual(self.call(2460,request,p.StationPokemonOutProto).result,8)
  request.pokemon_id=self.parties[1].id;self.assertEqual(self.call(2460,request,p.StationPokemonOutProto).result,6)
  request.pokemon_id=(1<<64)-1;self.assertEqual(self.call(2460,request,p.StationPokemonOutProto).result,6)
  request.pokemon_id=self.parties[0].id;request.bread_battle_id='9'*100
  self.assertEqual(self.call(2460,request,p.StationPokemonOutProto).result,8)
  request.station_lat_degrees=float('nan');self.assertEqual(self.call(2460,request,p.StationPokemonOutProto).result,4)
  self.clock+=120001;request.station_lat_degrees=52.08
  self.assertEqual(self.call(2460,request,p.StationPokemonOutProto).result,4)
  with self.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM max_stationed').fetchone()[0],0)

 def test_native_projection_duplicate_placement_and_injured_health_are_preserved(self):
  self.game.max_config['development_balance'].update(boss_interval_ms=1000,boss_damage_multiplier=.3)
  won=self.victory();result=self.assign(won);self.assertEqual(result.result,1)
  mon=result.player_client_stationed_pokemon.pokemon
  self.assertEqual(mon.id,self.parties[0].id);self.assertLess(mon.stamina,mon.max_stamina)
  self.assertEqual(mon.deployed_station_id,self.stations[0]['id']);self.assertEqual(mon.deployed_station_expiration_time_ms,self.stations[0]['end_ms'])
  self.assertEqual(self.assign(won),result)
  details=self.call(2462,p.GetStationedPokemonDetailsProto(station_id=self.stations[0]['id'],get_full_details=True),p.GetStationedPokemonDetailsOutProto,index=1)
  self.assertEqual(details.result,1);self.assertEqual(details.total_num_stationed_pokemon,1)
  self.assertEqual(details.stationed_pokemons[0].pokemon,mon);self.assertTrue(details.stationed_pokemons[0].HasField('player_neutral_avatar'))
  self.assertEqual(self.call(112,p.ReleasePokemonProto(pokemon_id=mon.id),p.ReleasePokemonOutProto).status,2)
  self.assertEqual(self.call(147,p.UpgradePokemonProto(pokemon_id=mon.id),p.UpgradePokemonOutProto).result,5)
  self.restart()
  self.assertEqual(self.call(112,p.ReleasePokemonProto(pokemon_id=mon.id),p.ReleasePokemonOutProto).status,2)
  self.assertEqual(self.call(147,p.UpgradePokemonProto(pokemon_id=mon.id),p.UpgradePokemonOutProto).result,5)
  self.assertEqual(self.release().result,1)
  with self.db.transaction() as db:
   returned=owned(db,self.players[0],mon.id);self.assertEqual(returned.stamina,mon.stamina)
   self.assertEqual(returned.deployed_station_id,'');self.assertEqual(returned.deployed_station_expiration_time_ms,0)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM max_stationed').fetchone()[0],1)

 def test_recall_is_owned_once_only_and_old_assignment_cannot_redeploy(self):
  won=self.victory();self.assertEqual(self.assign(won).result,1)
  self.assertEqual(self.release(index=1,pokemon_id=self.parties[0].id).result,2)
  self.assertEqual(self.release().result,1);self.assertEqual(self.release().result,1)
  self.assertEqual(self.assign(won).result,2)
  self.assertEqual(self.call(112,p.ReleasePokemonProto(pokemon_id=self.parties[0].id),p.ReleasePokemonOutProto).status,1)
  with self.db.transaction() as db:
   self.assertIsNone(owned(db,self.players[0],self.parties[0].id));self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

 def test_capacity_and_one_spot_per_owned_pokemon_are_authoritative(self):
  won=self.victory();self.assertEqual(self.assign(won).result,1);stationed_id=self.parties[0].id
  shared=resources.gm(self.game.max_reference,'BREAD_SHARED_SETTINGS');shared.bread_settings.max_stationed_pokemon=1
  self.game.max_reference['templates'][shared.template_id]=shared.SerializeToString().hex()
  other=self.victory(index=1);self.assertEqual(self.assign(other,index=1).result,9)
  self.parties[0]=self.create_owned(self.players[0]);another=self.victory(spot=1)
  self.assertEqual(self.assign(another,spot=1,pokemon_id=stationed_id).result,2)
  shared.bread_settings.max_stationed_pokemon_per_player=1;self.game.max_reference['templates'][shared.template_id]=shared.SerializeToString().hex()
  self.assertEqual(self.assign(another,spot=1).result,10)

 def test_assists_count_winning_rooms_and_rewards_grant_once_on_return(self):
  won=self.victory();self.assertEqual(self.assign(won).result,1)
  with self.db.transaction() as db:before=candy(db,self.players[0],self.game.species[831]['family'])
  # A configuration edit cannot retroactively inflate an existing placement.
  self.game.max_config['stationing'].update(candy_per_assist=100,max_candy=100)
  self.assertEqual(self.statistics().num_station_assists,0)
  self.victory(index=1)
  stats=self.statistics();self.assertEqual(stats.num_station_assists,1);self.assertEqual(stats.candy_amount,1)
  self.assertEqual(self.statistics(),stats)
  private=self.statistics(index=1,pokemon_id=self.parties[0].id);self.assertEqual(private.num_station_assists,0);self.assertEqual(private.powerspot_title,'')
  with self.db.transaction() as db:self.assertEqual(candy(db,self.players[0],self.game.species[831]['family']),before)
  self.restart();self.assertEqual(self.release().result,1);self.assertEqual(self.release().result,1)
  with self.db.transaction() as db:
   self.assertEqual(candy(db,self.players[0],self.game.species[831]['family']),before+1)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM max_station_assists').fetchone()[0],1)

 def test_cooperative_room_is_one_assist_even_with_two_actual_winners(self):
  won=self.victory();self.assertEqual(self.assign(won).result,1);other=self.extra_player()
  one=self.join(1);two=self.join(other)
  self.prepare(one.bread_lobby,1);prepared=self.prepare(two.bread_lobby,other)
  self.clock=prepared.bread_lobby.bread_battle_start_ms;one=self.start(1);two=self.start(other)
  self.assertEqual(self.attack(two,index=other).result,1);self.win(one,index=1)
  stats=self.statistics();self.assertEqual(stats.num_station_assists,1);self.assertEqual(stats.candy_amount,1)
  with self.db.transaction() as db:
   self.assertEqual(db.execute('SELECT COUNT(*) FROM max_victories WHERE room_id=?',(one.lobby.bread_lobby_id,)).fetchone()[0],2)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM max_station_assists').fetchone()[0],1)

 def test_reward_caps_and_xl_candy_are_durable_once_only(self):
  self.game.max_config['stationing'].update(candy_per_assist=2,max_candy=3,xl_candy_per_assist=1,max_xl_candy=1)
  won=self.victory();self.assertEqual(self.assign(won).result,1)
  with self.db.transaction() as db:before=candy(db,self.players[0],self.game.species[831]['family'])
  self.victory(index=1);other=self.extra_player();self.victory(index=other)
  stats=self.statistics();self.assertEqual((stats.num_station_assists,stats.candy_amount,stats.xl_candy_amount),(2,3,1))
  self.restart();self.assertEqual(self.release().result,1);self.assertEqual(self.release().result,1)
  self.restart();self.statistics()
  with self.db.transaction() as db:
   self.assertEqual(candy(db,self.players[0],self.game.species[831]['family']),before+3)
   self.assertEqual(db.execute('SELECT count FROM mega_catch_xl WHERE player_id=? AND family=?',(self.players[0],self.game.species[831]['family'])).fetchone()[0],1)

 def test_source_boost_thresholds_and_early_closure_are_projected_without_healing(self):
  won=self.victory();self.assertEqual(self.assign(won).result,1)
  with self.db.transaction() as db:
   boost=station.boost(self.game,db,self.stations[0]['id'])
   self.assertEqual((boost['num_stationed'],boost['boost_icons'],boost['hundredths_of_percent']),(1,1,200))
   before=owned(db,self.players[0],self.parties[0].id)
   db.execute('UPDATE max_stations SET end_ms=? WHERE id=?',(self.clock+1000,self.stations[0]['id']))
  self.clock+=1001
  with self.db.transaction() as db:
   station.inventory(self.game,db,self.players[0]);after=owned(db,self.players[0],self.parties[0].id)
   self.assertEqual(after.stamina,before.stamina);self.assertEqual(after.deployed_station_id,'')
   self.assertEqual(station.boost(self.game,db,self.stations[0]['id'])['boost_icons'],0)

 def test_expired_station_returns_through_inventory_and_preserves_owned_identity(self):
  won=self.victory();self.assertEqual(self.assign(won).result,1)
  with self.db.transaction() as db:before=self.game.player(db,self.players[0])['revision']
  self.clock=self.stations[0]['end_ms']+1;self.restart()
  inventory=self.call(4,p.GetHoloholoInventoryProto(timestamp_millis=before),p.GetHoloholoInventoryOutProto)
  mons=[p.HoloInventoryItemProto.FromString(row.item).pokemon for row in inventory.inventory_delta.inventory_item if row.item and p.HoloInventoryItemProto.FromString(row.item).HasField('pokemon')]
  mon=next(mon for mon in mons if mon.id==self.parties[0].id)
  self.assertEqual(mon.deployed_station_id,'');self.assertEqual(mon.pokemon_display.bread_mode_enum,1)
  self.assertGreater(inventory.inventory_delta.new_timestamp,before)
  self.assertEqual(self.release().result,1)
  with self.db.transaction() as db:self.assertEqual(db.execute('SELECT return_reason FROM max_stationed').fetchone()[0],'expired')

 def test_missing_provenance_and_invalid_station_rewards_are_rejected(self):
  original=deepcopy(self.game.max_config['stationing'])
  try:
   for key,value in [('reward_provenance',None),('enabled','yes'),('candy_per_assist',None),('xl_candy_per_assist',-1),('max_candy',float('inf')),('max_xl_candy',True)]:
    with self.subTest(key=key,value=value):
     self.game.max_config['stationing']=dict(original,**{key:value})
     with self.assertRaises(ValueError):station.policy(self.game)
  finally:self.game.max_config['stationing']=original

 def test_station_source_boost_affects_combat_and_is_frozen_on_recall(self):
  from dittobuffer.gameplay import max_battle as battle
  won=self.victory();self.assertEqual(1,self.assign(won).result)
  joined=self.join(1);prepared=self.prepare(joined.bread_lobby,1)
  self.clock=prepared.bread_lobby.bread_battle_start_ms;started=self.start(1)
  state=self.combat(started)
  self.assertEqual(1,state['station_assist']['num_stationed'])
  self.assertEqual(1.02,state['rules']['station_assist_multiplier'])
  player=state['players'][self.players[1]];source=player['party'][0]
  unboosted=deepcopy(state);unboosted['rules']['station_assist_multiplier']=1
  self.assertGreater(battle.max_damage(self.game,source,state['boss'],1,state),
                     battle.max_damage(self.game,source,state['boss'],1,unboosted))
  self.assertEqual(1,self.release().result)
  self.assertEqual(1.02,self.combat(started)['rules']['station_assist_multiplier'])

 def test_concurrent_placement_choices_and_recalls_cannot_duplicate_rewards(self):
  won=self.victory();another=self.create_owned(self.players[0]);ids=[self.parties[0].id]*2+[another.id]*2
  barrier=Barrier(4);spot=self.stations[0]
  def assign(number):
   body=p.StationPokemonProto(station_id=spot['id'],pokemon_id=ids[number],station_lat_degrees=spot['lat'],station_lng_degrees=spot['lng'],bread_battle_id=str(won.lobby.bread_lobby_id))
   req=wire.RpcRequest(rpc_id=41000+number);req.requests.add(method=2460,body=body.SerializeToString());barrier.wait(timeout=10)
   return p.StationPokemonOutProto.FromString(self.game.rpc(req,self.tickets[0],run='station-concurrent').payloads[0].payload)
  with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(assign,range(4)))
  self.assertEqual(sorted(v.result for v in results),[1,1,2,2])
  with self.db.transaction() as db:
   rows=db.execute('SELECT * FROM max_stationed').fetchall();self.assertEqual(len(rows),1);winner=rows[0]['pokemon_id']
   before=candy(db,self.players[0],self.game.species[831]['family'])
  self.victory(index=1);barrier=Barrier(4)
  def recall(number):
   req=wire.RpcRequest(rpc_id=42000+number);req.requests.add(method=2472,body=p.ReleaseStationedPokemonProto(station_id=spot['id'],pokemon_id=winner).SerializeToString());barrier.wait(timeout=10)
   return p.ReleaseStationedPokemonOutProto.FromString(self.game.rpc(req,self.tickets[0],run='station-concurrent').payloads[0].payload)
  with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(recall,range(4)))
  self.assertEqual([v.result for v in results],[1]*4)
  with self.db.transaction() as db:
   self.assertEqual(candy(db,self.players[0],self.game.species[831]['family']),before+1)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM max_station_assists').fetchone()[0],1)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM max_stationed WHERE returned_ms=0').fetchone()[0],0)
   self.assertEqual(owned(db,self.players[0],winner).deployed_station_id,'')


if __name__=='__main__':unittest.main()
