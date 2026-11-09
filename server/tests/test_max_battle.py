"""Max RPC/PBS transaction tests with explicit synthetic development tuning.

These authenticated server peers are not evidence of multiple Android clients.
No live trainer database, device, official balance or battle result is mocked.
"""
import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from dittobuffer.gameplay.game import Game,p,wire,RpcError
from dittobuffer.gameplay import max_battle as battle,max_system as mx,gym_battle as engine
from dittobuffer.gameplay.battle_master import MOVES
from dittobuffer.core.management import owned
from dittobuffer.gameplay.species import make_pokemon
from tests import test_max_system as resource_tests


class MaxBattleTests(unittest.TestCase):
 setUp_resources=resource_tests.MaxTests.setUp
 tearDown=resource_tests.MaxTests.tearDown
 call=resource_tests.MaxTests.call

 def setUp(self):
  self.setUp_resources()
  clock=patch('dittobuffer.gameplay.max_battle.now_ms',lambda:self.clock);clock.start();self.patches.append(clock)
  self.game.max_config.update(battle_enabled=True,development_balance=dict(provenance='Synthetic isolated regression fixture; custom test balance, not official values.',lobby_seconds=30,battle_seconds=120,boss_hp=200,boss_interval_ms=60000,boss_damage_multiplier=1,meter_goal=3,meter_per_attack=1,max_attack_power=[300,350,400],guard_hp=[10,20,30],spirit_fraction=[.1,.2,.3],premier_balls=5,xp=1000,dust=500,items={'1':3}))
  battle.validate_config(self.game)
  reference=dict(self.game.max_reference,templates=dict(self.game.max_reference['templates']))
  mp=p.GameMasterClientTemplateProto(template_id='MP_SHARED_SETTINGS')
  mp.mp_settings.battle_mp_cost_per_tier.add(battle_level=1,bread_battle_catch_mp_cost=250)
  reference['templates'][mp.template_id]=mp.SerializeToString().hex()
  mapping=p.GameMasterClientTemplateProto(template_id='BREAD_MOVE_MAPPING_SETTINGS')
  for kind in sorted({v[0] for v in MOVES.values()}):mapping.bread_move_mappings.mappings.add(type=kind,move=1)
  reference['templates'][mapping.template_id]=mapping.SerializeToString().hex()
  # The fixture's catalog must survive a real Game reconstruction as well as
  # the current instance; do not rely on an in-memory-only synthetic mapping.
  Path(self.game.max_config['reference']).write_text(json.dumps(reference))
  self.game.max_reference.clear();self.game.max_reference.update(reference)
  with self.db.transaction() as db:
   battle.install(db)
   for player in self.players:mx.mutate(self.game,db,player,1000,'explicit-unit-particle-fixture')
  self.parties=[self.create_owned(player) for player in self.players]
  self.sequence=0

 def create_owned(self,player):
  with self.db.transaction() as db:
   spawn=100000+db.execute('SELECT COUNT(*) FROM pokemon').fetchone()[0]
   db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',(spawn,'max-battle-unit',831,52.08,5.12,0))
   mon=make_pokemon(p,self.game.species[831],831,15,[10]*3);mx.attach(self.game,mon)
   for slot in mon.bread_moves:slot.move_level=1
   mon.id=db.execute('INSERT INTO pokemon(player_id,spawn_id,species,data) VALUES(?,?,831,?)',(player,spawn,b'')).lastrowid
   db.execute('UPDATE pokemon SET data=? WHERE id=?',(mon.SerializeToString(),mon.id));return mon

 def join(self,index=0,station=0):
  with self.db.transaction() as db:spot=mx.station(self.game,db,self.players[index],self.stations[station])
  req=p.JoinBreadLobbyProto(station_id=spot.id,bread_battle_seed=spot.battle_details.bread_battle_seed,station_lat_degrees=spot.lat,station_lng_degrees=spot.lng)
  return self.call(2450,req,p.JoinBreadLobbyOutProto,index)

 def prepare(self,lobby,index=0,station=0):
  return self.call(2453,p.PrepareBreadLobbyProto(station_id=self.stations[station]['id'],bread_lobby_id=lobby.bread_lobby_id),p.PrepareBreadLobbyOutProto,index)

 def start(self,index=0,station=0,ids=None):
  with self.db.transaction() as db:spot=mx.station(self.game,db,self.players[index],self.stations[station])
  return self.call(2456,p.StartBreadBattleProto(station_id=spot.id,bread_battle_seed=spot.battle_details.bread_battle_seed,attacking_pokemon_id=ids or [self.parties[index].id]),p.StartBreadBattleOutProto,index)

 def begin(self,index=0):
  joined=self.join(index);self.assertEqual(joined.result,1)
  prepared=self.prepare(joined.bread_lobby,index);self.assertEqual(prepared.result,1)
  self.clock=prepared.bread_lobby.bread_battle_start_ms
  started=self.start(index);self.assertEqual(started.result,1)
  return started

 def room(self,lobby):
  with self.db.transaction() as db:return dict(db.execute('SELECT * FROM max_rooms WHERE id=?',(lobby.bread_lobby_id,)).fetchone())

 def combat(self,started):return json.loads(self.room(started.lobby)['state'])

 def extra_player(self):
  number=len(self.players);ticket=self.game.authenticate(wire.AuthRequest(auth_id='max-battle-unit-'+str(number))).ticket
  with self.db.transaction() as db:
   player=db.execute('SELECT player_id FROM sessions WHERE ticket=?',(ticket,)).fetchone()[0]
   db.execute('UPDATE players SET xp=210000 WHERE id=?',(player,));db.execute('INSERT INTO social_locations VALUES(?,?,?,?,?,?)',(player,52.08,5.12,5,self.clock,self.clock));mx.mutate(self.game,db,player,1000,'explicit-unit-particle-fixture')
  self.players.append(player);self.tickets.append(ticket);self.parties.append(self.create_owned(player));return number

 def headers(self,started,index=0):
  return {'Authorization':'Bearer '+started.rvn_connection.token,'nbl-battle-id':str(started.lobby.bread_lobby_id),'nbl-player-id':self.players[index][:20]}

 def state(self,started,index=0):return battle.handle(self.game,'/max/get-state',self.headers(started,index))

 def event(self,started,event,index=0,sequence=None):
  self.sequence+=1
  req=p.SendBattleEventProto(battle_id=str(started.lobby.bread_lobby_id),player_id=self.players[index][:20],sequence_id=self.sequence if sequence is None else sequence,battle_event=event)
  return battle.handle(self.game,'/max/send-event',self.headers(started,index),req.SerializeToString())

 def complete(self,started,index=0):
  return self.call(2473,p.CompleteBreadBattleProto(station_id=self.stations[0]['id'],bread_battle_id=str(started.lobby.bread_lobby_id)),p.CompleteBreadBattleOutProto,index)

 def attack(self,started,kind=1,index=0):
  mon=self.parties[index]
  return self.event(started,p.BattleEventProto(type=4,actor_id=self.players[index][:20],attack=p.RaidPbsAttack(attack_type=kind,source_pokemon_id=mon.id)),index)

 def win(self,started,index=0):
  # Only accepted timed attacks can defeat the boss. This fixture uses clearly
  # declared development tuning; there is no client victory or HP override.
  for unused in range(40):
   state=self.combat(started)
   if state['status']==2:return
   self.assertEqual(state['status'],1)
   if state['phase'] in (1,3):
    self.clock=max(self.clock,state['phase_due']);self.state(started,index);continue
   result=self.attack(started,5 if state['phase']==2 else 1,index)
   self.assertEqual(result.result,1)
   self.clock+=max(2500,MOVES[self.parties[index].move1][2])
  self.fail('Development battle did not end through server-processed attacks')

 def test_join_reserves_without_spending_and_start_validates_owned_party(self):
  joined=self.join();self.assertEqual(joined.result,1)
  self.assertEqual(len(joined.bread_lobby.players),1)
  self.assertEqual(joined.bread_lobby.rvn_connection.base_uri,'/max/')
  with self.db.transaction() as db:
   self.assertEqual(mx.balance(db,self.players[0]),1000);self.assertEqual(mx.available(db,self.players[0]),750)
  self.assertEqual(self.join().bread_lobby.bread_lobby_id,joined.bread_lobby.bread_lobby_id)
  self.assertEqual(self.join(station=1).result,18)
  prepared=self.prepare(joined.bread_lobby);self.clock=prepared.bread_lobby.bread_battle_start_ms
  self.assertEqual(self.start(ids=[self.parties[1].id]).result,5)
  self.assertEqual(self.start(ids=[self.parties[0].id]*2).result,5)
  started=self.start();self.assertEqual(started.result,1)
  state=self.state(started);self.assertEqual(state.battle_state.state,3)
  with self.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM max_pokemon_locks').fetchone()[0],1)

 def test_leave_releases_hold_and_retry_has_fresh_session(self):
  joined=self.join();self.assertEqual(joined.result,1);old=joined.bread_lobby
  with self.db.transaction() as db:seed=db.execute('SELECT seed FROM max_rooms WHERE id=?',(old.bread_lobby_id,)).fetchone()[0]
  request=p.LeaveBreadLobbyProto(station_id=self.stations[0]['id'],bread_battle_seed=seed,bread_lobby_id=old.bread_lobby_id)
  self.assertEqual(self.call(2455,request,p.LeaveBreadLobbyOutProto).result,1)
  with self.db.transaction() as db:self.assertEqual(mx.available(db,self.players[0]),1000)
  retried=self.join();self.assertEqual(retried.result,1)
  self.assertNotEqual(retried.bread_lobby.bread_lobby_id,old.bread_lobby_id)
  self.assertNotEqual(retried.bread_lobby.rvn_connection.token,old.rvn_connection.token)
  headers={'Authorization':'Bearer '+old.rvn_connection.token,'nbl-battle-id':str(old.bread_lobby_id),'nbl-player-id':self.players[0][:20]}
  with self.assertRaises(RpcError):battle.handle(self.game,'/max/get-time',headers)

 def test_four_real_server_identities_share_lobby_and_fifth_is_rejected(self):
  while len(self.players)<5:self.extra_player()
  replies=[self.join(index) for index in range(5)]
  self.assertEqual([reply.result for reply in replies],[1,1,1,1,13])
  self.assertEqual(len({reply.bread_lobby.bread_lobby_id for reply in replies[:4]}),1)
  self.assertEqual(len({reply.bread_lobby.rvn_connection.token for reply in replies[:4]}),4)
  self.assertEqual(len(replies[3].bread_lobby.players),4)
  with self.db.transaction() as db:
   self.assertEqual(mx.available(db,self.players[4]),1000)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM max_members').fetchone()[0],4)

 def test_normal_damage_energy_cooldown_and_client_victory_rejection(self):
  self.game.max_config['development_balance']['meter_goal']=10000
  started=self.begin();before=self.combat(started);player=self.players[0]
  with self.assertRaises(RpcError):self.attack(started,5)
  self.assertEqual(self.combat(started)['boss']['hp'],before['boss']['hp'])
  self.assertEqual(self.attack(started,2).result,2)
  self.assertEqual(self.combat(started)['boss']['hp'],before['boss']['hp'])
  expected=engine.damage(self.game,before['players'][player]['party'][0],before['boss'],self.parties[0].move1)
  self.assertEqual(self.attack(started).result,1);after=self.combat(started)
  self.assertEqual(after['boss']['hp'],before['boss']['hp']-expected)
  self.assertEqual(after['players'][player]['party'][0]['energy'],MOVES[self.parties[0].move1][3])
  self.assertEqual(self.attack(started).result,2);self.assertEqual(self.combat(started)['boss']['hp'],after['boss']['hp'])
  forged=p.BattleEventProto(type=18,battle_end=p.RaidPbsEnd(reason=1,result=1))
  self.assertEqual(self.event(started,forged).result,2)
  self.assertEqual(self.complete(started).result,3)
  with self.db.transaction() as db:
   self.assertEqual(mx.balance(db,player),1000);self.assertEqual(db.execute('SELECT COUNT(*) FROM max_victories').fetchone()[0],0)
  required=abs(MOVES[self.parties[0].move2][3])
  while self.combat(started)['players'][player]['party'][0]['energy']<required:
   self.clock+=MOVES[self.parties[0].move1][2];self.assertEqual(self.attack(started).result,1)
  self.clock+=MOVES[self.parties[0].move1][2];before=self.combat(started)
  expected=engine.damage(self.game,before['players'][player]['party'][0],before['boss'],self.parties[0].move2)
  self.assertEqual(self.attack(started,2).result,1);after=self.combat(started)
  self.assertEqual(after['boss']['hp'],before['boss']['hp']-expected)
  self.assertEqual(after['players'][player]['party'][0]['energy'],before['players'][player]['party'][0]['energy']-required)

 def test_combat_tokens_and_event_sequences_are_player_scoped(self):
  one=self.join(0);two=self.join(1);self.assertEqual(one.bread_lobby.bread_lobby_id,two.bread_lobby.bread_lobby_id)
  self.prepare(one.bread_lobby,0);prepared=self.prepare(two.bread_lobby,1)
  self.clock=prepared.bread_lobby.bread_battle_start_ms
  one=self.start(0);two=self.start(1)
  wrong=self.headers(one);wrong['nbl-player-id']=self.players[1][:20]
  with self.assertRaises(RpcError):battle.handle(self.game,'/max/get-state',wrong)
  event=p.BattleEventProto(type=4,attack=p.RaidPbsAttack(attack_type=1,source_pokemon_id=self.parties[0].id))
  result=self.event(one,event,sequence=100);before=self.combat(one)['boss']['hp']
  self.assertEqual(self.event(one,event,sequence=100),result);self.assertEqual(self.combat(one)['boss']['hp'],before)
  with self.assertRaises(RpcError):self.event(one,p.BattleEventProto(type=5),sequence=100)
  forged=p.SendBattleEventProto(battle_id=str(one.lobby.bread_lobby_id),player_id=self.players[1][:20],sequence_id=200,battle_event=event)
  with self.assertRaises(RpcError):battle.handle(self.game,'/max/send-event',self.headers(one),forged.SerializeToString())
  self.assertEqual(self.attack(two,index=1).result,1)
  self.assertEqual(len(self.combat(one)['players']),2)

 def test_victory_charges_once_and_bonus_capture_survives_restart(self):
  started=self.begin();player=self.players[0]
  with self.db.transaction() as db:
   initial=dict(self.game.player(db,player));balls=db.execute('SELECT count FROM items WHERE player_id=? AND item=1',(player,)).fetchone()[0]
  self.win(started);result=self.complete(started)
  self.assertEqual(result.result,1);self.assertTrue(result.is_victory)
  encounter=result.battle_results.post_battle_encounter
  self.assertEqual(encounter.pokemon.pokemon_display.bread_mode_enum,1);self.assertEqual(encounter.throws_remaining,5)
  self.assertEqual(self.complete(started),result)
  with self.db.transaction() as db:
   self.assertEqual(mx.balance(db,player),750);current=self.game.player(db,player)
   self.assertEqual(current['xp'],initial['xp']+1000);self.assertEqual(current['stardust'],initial['stardust']+500)
   self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1',(player,)).fetchone()[0],balls+3)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM max_victories').fetchone()[0],1)
  # Deterministic probability is explicit test configuration, not a patched RNG.
  self.game.config['catch_probability']=1
  request=p.CatchPokemonProto(encounter_id=encounter.encounter_id,spawn_point_guid=encounter.spawnpoint_id,pokeball=5,hit_pokemon=True)
  caught=self.call(103,request,p.CatchPokemonOutProto);self.assertEqual(caught.status,1)
  self.assertEqual(self.call(103,request,p.CatchPokemonOutProto),caught)
  config=dict(self.cfg);config['max_system']=dict(self.game.max_config);self.game=Game(config,self.db)
  with self.db.transaction() as db:
   saved=owned(db,player,caught.captured_pokemon_id);self.assertEqual(saved.pokemon_display.bread_mode_enum,1)
   self.assertEqual([(v.move_type,v.move_level) for v in saved.bread_moves],[(1,1),(2,0),(3,0)])
   self.assertEqual(mx.balance(db,player),750)
   self.assertEqual(db.execute('SELECT balls FROM max_members WHERE room_id=? AND player_id=?',(started.lobby.bread_lobby_id,player)).fetchone()[0],4)
  final=self.complete(started);self.assertTrue(final.is_victory);self.assertEqual(final.battle_results.post_battle_encounter.throws_remaining,4)
  self.assertEqual(self.join().result,4)

 def test_expired_attempt_cannot_damage_retry_or_award_rewards(self):
  old=self.begin();self.clock=self.room(old.lobby)['end_ms']+1
  self.state(old);result=self.complete(old)
  self.assertEqual(result.result,1);self.assertFalse(result.is_victory)
  with self.db.transaction() as db:
   self.assertEqual(mx.balance(db,self.players[0]),1000);self.assertEqual(mx.available(db,self.players[0]),1000)
   db.execute('UPDATE social_locations SET arrival_ms=? WHERE player_id=?',(self.clock,self.players[0]))
  retry=self.join();self.assertEqual(retry.result,1);self.assertNotEqual(retry.bread_lobby.bread_lobby_id,old.lobby.bread_lobby_id)
  prepared=self.prepare(retry.bread_lobby);self.clock=prepared.bread_lobby.bread_battle_start_ms;fresh=self.start()
  before=self.combat(fresh)['boss']['hp'];self.assertEqual(self.attack(old).result,2)
  self.assertEqual(self.combat(fresh)['boss']['hp'],before)
  self.assertFalse(self.complete(old).is_victory)

 def test_cooperative_server_peers_receive_independent_once_only_rewards(self):
  one=self.join(0);two=self.join(1)
  self.prepare(one.bread_lobby,0);prepared=self.prepare(two.bread_lobby,1)
  self.clock=prepared.bread_lobby.bread_battle_start_ms;one=self.start(0);two=self.start(1)
  self.assertEqual(self.attack(two,index=1).result,1);self.win(one)
  first=self.complete(one,0);second=self.complete(two,1)
  self.assertTrue(first.is_victory);self.assertTrue(second.is_victory)
  self.assertNotEqual(first.battle_results.post_battle_encounter.encounter_id,second.battle_results.post_battle_encounter.encounter_id)
  self.assertEqual(self.complete(one,0),first);self.assertEqual(self.complete(two,1),second)
  with self.db.transaction() as db:
   self.assertEqual([mx.balance(db,player) for player in self.players],[750,750])
   self.assertEqual(db.execute('SELECT COUNT(*) FROM max_victories').fetchone()[0],2)
   self.assertEqual(db.execute('SELECT COUNT(DISTINCT spawn_id) FROM max_members WHERE reward IS NOT NULL').fetchone()[0],2)

 def test_max_meter_three_turns_guard_spirit_and_revert(self):
  cfg=self.game.max_config['development_balance'];cfg.update(boss_hp=1000,boss_interval_ms=1000,boss_damage_multiplier=.3)
  started=self.begin();player=self.players[0]
  for unused in range(3):
   self.assertEqual(self.attack(started).result,1);self.clock+=2500
  state=self.combat(started);self.assertEqual(state['phase'],1);self.assertEqual(state['meter'],3)
  self.clock=state['phase_due'];snapshot=self.state(started);state=self.combat(started)
  self.assertEqual(state['phase'],2);self.assertEqual(state['players'][player]['max_turns'],3)
  self.assertIn(23,[v.type for v in snapshot.battle_state.events])
  consensus=[v.consensus for v in snapshot.battle_state.events if v.type==32]
  self.assertTrue(consensus);self.assertEqual(consensus[-1].consensus_event_subtype,2)
  self.assertEqual(list(consensus[-1].unblocked_event_types),[4,24])
  self.clock+=4000
  self.assertEqual(self.attack(started,6).result,1);self.assertEqual(self.combat(started)['players'][player]['guard'],10)
  self.assertEqual(self.attack(started,7).result,2);self.assertEqual(self.combat(started)['players'][player]['max_turns'],2)
  self.clock+=2500;before=self.combat(started)['players'][player]['party'][0]['hp']
  self.assertLess(before,self.parties[0].max_stamina)
  self.assertEqual(self.attack(started,7).result,1)
  self.assertGreater(self.combat(started)['players'][player]['party'][0]['hp'],before)
  self.clock+=2500;before=self.combat(started)['boss']['hp'];self.assertEqual(self.attack(started,5).result,1)
  state=self.combat(started);self.assertLess(state['boss']['hp'],before)
  self.assertEqual(state['players'][player]['max_turns'],0);self.assertEqual(state['phase'],3);self.assertEqual(state['meter'],0)
  self.clock=state['phase_due'];self.state(started);state=self.combat(started);self.assertEqual(state['phase'],0)
  self.assertIn(33,[p.BattleEventProto.FromString(bytes.fromhex(v)).type for v in state['events']])

 def test_defender_ai_substitutes_then_faints_party_without_spending(self):
  cfg=self.game.max_config['development_balance'];cfg.update(boss_interval_ms=1000,boss_damage_multiplier=100)
  party=[self.parties[0],self.create_owned(self.players[0]),self.create_owned(self.players[0])]
  joined=self.join();prepared=self.prepare(joined.bread_lobby);self.clock=prepared.bread_lobby.bread_battle_start_ms
  started=self.start(ids=[mon.id for mon in party]);self.assertEqual(started.result,1)
  for unused in range(3):self.clock+=1000;self.state(started)
  state=self.combat(started);self.assertEqual(state['status'],3)
  self.assertEqual([value['hp'] for value in state['players'][self.players[0]]['party']],[0,0,0])
  self.assertEqual(len([raw for raw in state['events'] if p.BattleEventProto.FromString(bytes.fromhex(raw)).type==7]),2)
  result=self.complete(started);self.assertEqual(result.result,1);self.assertFalse(result.is_victory)
  self.assertEqual(self.complete(started),result)
  with self.db.transaction() as db:
   self.assertEqual([owned(db,self.players[0],mon.id).stamina for mon in party],[0,0,0])
   self.assertEqual(mx.balance(db,self.players[0]),1000);self.assertEqual(mx.available(db,self.players[0]),1000)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM max_pokemon_locks').fetchone()[0],0)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM max_victories').fetchone()[0],0)

 def test_victory_debit_and_delayed_claim_survive_restart_without_overwriting_heal(self):
  cfg=self.game.max_config['development_balance'];cfg.update(boss_interval_ms=1000,boss_damage_multiplier=.3)
  started=self.begin();self.win(started);player=self.players[0]
  with self.db.transaction() as db:
   self.assertEqual(mx.balance(db,player),750)
   self.assertIsNone(db.execute('SELECT reward FROM max_members WHERE room_id=? AND player_id=?',(started.lobby.bread_lobby_id,player)).fetchone()[0])
   self.assertLess(owned(db,player,self.parties[0].id).stamina,self.parties[0].max_stamina)
   db.execute('INSERT INTO items VALUES(?,104,1) ON CONFLICT(player_id,item) DO UPDATE SET count=count+1',(player,))
  healed=self.call(113,p.UseItemPotionProto(item=104,pokemon_id=self.parties[0].id),p.UseItemPotionOutProto)
  self.assertEqual(healed.result,1);self.assertEqual(healed.stamina,self.parties[0].max_stamina)
  self.assertEqual(self.join().result,4)
  # The win is committed before completion. A restart, expired hold and rotated
  # Power Spot must neither refund it nor erase the earned reward entitlement.
  self.clock=max(self.room(started.lobby)['end_ms']+120001,self.stations[0]['end_ms']+1)
  config=dict(self.cfg);config['max_system']=dict(self.game.max_config);self.game=Game(config,self.db)
  self.state(started)
  with self.db.transaction() as db:self.assertEqual(owned(db,player,self.parties[0].id).stamina,healed.stamina)
  result=self.complete(started);self.assertEqual(result.result,1);self.assertTrue(result.is_victory)
  self.assertEqual(self.complete(started),result)
  with self.db.transaction() as db:
   self.assertEqual(mx.balance(db,player),750)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM max_victories WHERE player_id=?',(player,)).fetchone()[0],1)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM max_ledger WHERE player_id=? AND source=?',(player,'battle:'+str(started.lobby.bread_lobby_id))).fetchone()[0],1)

 def test_bonus_balls_cannot_be_stolen_changed_or_refilled_after_exhaustion(self):
  started=self.begin();self.win(started);result=self.complete(started)
  encounter=result.battle_results.post_battle_encounter
  request=p.CatchPokemonProto(encounter_id=encounter.encounter_id,spawn_point_guid=encounter.spawnpoint_id,pokeball=5,hit_pokemon=False)
  with self.assertRaises(RpcError):self.call(103,request,p.CatchPokemonOutProto,index=1)
  request.pokeball=1
  with self.assertRaises(RpcError):self.call(103,request,p.CatchPokemonOutProto)
  request.pokeball=5
  for unused in range(5):self.assertEqual(self.call(103,request,p.CatchPokemonOutProto).status,4)
  config=dict(self.cfg);config['max_system']=dict(self.game.max_config);self.game=Game(config,self.db)
  self.assertEqual(self.complete(started).battle_results.post_battle_encounter.throws_remaining,0)
  with self.assertRaises(RpcError):self.call(103,request,p.CatchPokemonOutProto)
  with self.db.transaction() as db:
   self.assertEqual(db.execute('SELECT state FROM encounters WHERE player_id=? AND spawn_id=?',(self.players[0],encounter.encounter_id)).fetchone()[0],'fled')
   self.assertEqual(db.execute('SELECT COUNT(*) FROM pokemon WHERE spawn_id=?',(encounter.encounter_id,)).fetchone()[0],0)
   self.assertEqual(mx.balance(db,self.players[0]),750);self.assertEqual(mx.balance(db,self.players[1]),1000)

 def test_explicit_balance_rejects_malformed_values_and_unimplemented_rewards(self):
  original=deepcopy(self.game.max_config['development_balance'])
  invalid=[
   ('provenance',None),('provenance','official'),
   ('lobby_seconds',-1),('lobby_seconds',None),('battle_seconds',True),
   ('boss_hp',-1),('boss_hp',None),('boss_interval_ms',0),
   ('meter_goal',float('nan')),('meter_per_attack',1001),
   ('xp',-1),('dust',float('inf')),('premier_balls',None),
   ('boss_damage_multiplier',float('nan')),('boss_damage_multiplier',float('inf')),('boss_damage_multiplier',True),
   ('max_attack_power',[1,2]),('max_attack_power',[1,None,3]),('max_attack_power',[1,float('inf'),3]),
   ('guard_hp',[1,-1,3]),('guard_hp',[1,True,3]),('guard_hp',[1,2,3.5]),('guard_hp',[1.,2,3]),('spirit_fraction',[.1,float('nan'),.3]),
   ('items',None),('items',{'1506':1}),('items',{'1402':1}),('items',{'999999':1}),
   ('items',{'1':-1}),('items',{'1':None}),('items',{'1':True}),('items',{'not-an-item':1})]
  try:
   for value in (None,False,0,[],['unlabeled balance'],'unlabeled balance'):
    with self.subTest(balance=value):
     self.game.max_config['development_balance']=value
     with self.assertRaises(ValueError):battle.validate_config(self.game)
   for key,value in invalid:
    with self.subTest(key=key,value=value):
     self.game.max_config['development_balance']=dict(original,**{key:value})
     with self.assertRaises(ValueError):battle.validate_config(self.game)
   self.game.max_config['development_balance']=dict(original,meter_goal=2,meter_per_attack=3)
   with self.assertRaises(ValueError):battle.validate_config(self.game)
  finally:self.game.max_config['development_balance']=original
  battle.validate_config(self.game)

 def test_game_initialization_rejects_invalid_max_balance(self):
  cfg=deepcopy(self.cfg);cfg['max_system']=deepcopy(self.game.max_config)
  cfg['max_system']['development_balance']['boss_hp']=-1
  with self.assertRaisesRegex(ValueError,'boss_hp'):Game(cfg,self.db)


if __name__=='__main__':unittest.main()
