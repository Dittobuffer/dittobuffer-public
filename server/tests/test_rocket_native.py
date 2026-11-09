"""Isolated authoritative Rocket transactions; these are not Android acceptance."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from dittobuffer.gameplay.game import Game,ROOT,p,wire,now_ms
from dittobuffer.core.database import Database
from dittobuffer.gameplay.species import make_pokemon
from dittobuffer.core.management import owned,add_candy
from dittobuffer.gameplay import rocket_native as rocket,rocket_battle

class RocketTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds');self.clock=now_ms();self.counter=0;self.action_counter=0
  self.patches=[patch(name,lambda:self.clock) for name in ['dittobuffer.gameplay.game.now_ms','dittobuffer.gameplay.rocket_native.now_ms','dittobuffer.gameplay.rocket_progression.now_ms','dittobuffer.gameplay.rocket_battle.now_ms','dittobuffer.gameplay.rocket_shadow.now_ms']]
  for v in self.patches:v.start()
  self.cfg=json.loads((ROOT/'config/family.json').read_text());self.cfg['profile']='m17-unit';self.cfg['authentication']={'mode':'local-development'};self.cfg['social']={'enabled':True};self.cfg['world']={'enabled':False};self.cfg['weather']={'enabled':False};self.cfg['rocket']={'enabled':False};self.cfg['raids'].update(participation_limit=1,multiplayer=False);self.cfg['max_system']={'enabled':False};self.cfg['pvp']={'enabled':False}
  self.db=Database(Path(self.tmp.name)/'rocket.sqlite3');self.game=Game(self.cfg,self.db)
  # Controlled unit-test prices/settings are not a production GM or native fixture.
  templates={}
  setting=p.GameMasterClientTemplateProto(template_id='COMBAT_SETTINGS',combat_settings=p.CombatSettingsProto(round_duration_seconds=270,turn_duration_seconds=.5,minigame_duration_seconds=7,same_type_attack_bonus_multiplier=1.2,fast_attack_bonus_multiplier=1.3,charge_attack_bonus_multiplier=1.3,shadow_pokemon_attack_bonus_multiplier=1.2,shadow_pokemon_defense_bonus_multiplier=5/6,quick_swap_cooldown_duration_seconds=45))
  for v in [setting,p.GameMasterClientTemplateProto(template_id='COMBAT_STAT_STAGE_SETTINGS'),p.GameMasterClientTemplateProto(template_id='CHARACTER_GRASS_GRUNT_MALE')]:templates[v.template_id]=v.SerializeToString().hex()
  templates['SHARED_MOVE_SETTINGS']=p.GameMasterClientTemplateProto(template_id='SHARED_MOVE_SETTINGS',shared_move_settings=p.SharedMoveSettingsProto(shadow_third_move_unlock_stardust_multiplier=1.2,shadow_third_move_unlock_candy_multiplier=1.2,purified_third_move_unlock_stardust_multiplier=.8,purified_third_move_unlock_candy_multiplier=.8)).SerializeToString().hex()
  data=dict(templates=templates,shadow={f'{s}:{f}':dict(dust=3000,candy=3,frustration=322,return_move=323) for s,f in self.game.forms},upgrade=dict(shadow_candy_multiplier=1.2,shadow_stardust_multiplier=1.2,purified_candy_multiplier=.9,purified_stardust_multiplier=.9))
  ref=Path(self.tmp.name)/'reference.json';ref.write_text(json.dumps(data));self.cfg['rocket']={'enabled':True,'server_combat':True,'reference':str(ref),'force_fort_id':'rocket-stop','period_seconds':86400,'duration_seconds':86400,'probability':0}
  rocket.install(self.game)
  self.tickets=[self.game.authenticate(wire.AuthRequest(auth_id='rocket-unit-'+str(i))).ticket for i in range(2)];self.players=[];self.parties=[]
  with self.db.transaction() as db:
   db.execute("INSERT INTO forts VALUES('rocket-stop',1,'Original local stop',52.08,5.12)");fort=db.execute("SELECT * FROM forts WHERE id='rocket-stop'").fetchone();incident=rocket.schedule(self.game,db,fort);self.lookup=p.IncidentLookupProto(incident_id=incident['id'],fort_id='rocket-stop',fort_lat=52.08,fort_lng=5.12)
   for j,ticket in enumerate(self.tickets):
    player=db.execute('SELECT player_id FROM sessions WHERE ticket=?',(ticket,)).fetchone()[0];self.players.append(player);db.execute('UPDATE players SET xp=210000,stardust=100000 WHERE id=?',(player,));db.execute('INSERT INTO social_locations VALUES(?,?,?,?,?,?)',(player,52.08,5.12,5,self.clock,self.clock));self.parties.append([])
    for i,species in enumerate((6,9,3)):
     spawn=17000+j*10+i;db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',(spawn,str(spawn),species,52.08,5.12,0));mon=make_pokemon(p,self.game.species[species],species,25,[12]*3);mon.id=db.execute('INSERT INTO pokemon(player_id,spawn_id,species,data) VALUES(?,?,?,?)',(player,spawn,species,b'')).lastrowid;db.execute('UPDATE pokemon SET data=? WHERE id=?',(mon.SerializeToString(),mon.id));db.execute('INSERT INTO pokemon_levels VALUES(?,25)',(mon.id,));self.parties[j].append(mon.id);add_candy(db,player,self.game.species[species]['family'],100)
 def tearDown(self):
  for v in self.patches:v.stop()
  self.tmp.cleanup()
 def call(self,method,request,typ,index=0):
  self.counter+=1
  with self.db.transaction() as db:db.execute('UPDATE social_locations SET arrival_ms=?,fix_ms=? WHERE player_id=?',(self.clock,self.clock,self.players[index]))
  req=wire.RpcRequest(rpc_id=self.counter);req.requests.add(method=method,body=request.SerializeToString())
  return typ.FromString(self.game.rpc(req,self.tickets[index],run='rocket-unit').payloads[0].payload)
 def start(self,index=0):
  self.assertEqual(self.call(1200,p.StartIncidentProto(incident_lookup=self.lookup),p.StartIncidentOutProto,index).status,1)
  value=self.call(1202,p.OpenInvasionCombatSessionProto(incident_lookup=self.lookup,attacking_pokemon_id=self.parties[index]),p.OpenInvasionCombatSessionOutProto,index)
  self.assertEqual(value.status,1);self.clock=value.combat.combat_start_ms+500;return value.combat
 def action(self,battle,kind=0,target=0):
  self.action_counter+=1;turn=(self.clock-battle.combat_start_ms)//500
  action=p.CombatActionProto(type=kind,action_start_turn=turn,target_pokemon_id=target,minigame_score=1)
  return self.call(1001,p.UpdateCombatProto(combat_id=battle.combat_id,action=action,combat_request_counter=self.action_counter),p.UpdateCombatOutProto)
 def win(self,claim=True):
  battle=self.start()
  for _ in range(260):
   self.clock+=1000;value=self.action(battle);current=value.combat
   if current.combat_state in (6,7,8):break
   if current.combat_state==4:kind=6 if current.opponent_triggered else 4
   elif current.combat_state==5:kind=8
   else:
    mon=current.player.active_pokemon;move=rocket_battle.MOVES[mon.move2];kind=2 if mon.energy>=-move['energy_delta'] else 1
   result=self.action(battle,kind,self.parties[0][1] if kind==8 else 0)
  with self.db.transaction() as db:self.assertEqual(json.loads(db.execute('SELECT state FROM rocket_battles WHERE id=?',(battle.combat_id,)).fetchone()[0])['status'],'won')
  if not claim:return battle,None
  result=self.call(1203,p.UpdateInvasionBattleProto(incident_lookup=self.lookup,complete_battle=True,update_type=1),p.UpdateInvasionBattleOutProto);self.assertEqual(result.status,1)
  return battle,result
 def test_native_wire_repairs(self):
  from protocol.opaque import fields
  values=[(p.PurifyPokemonProto(pokemon_id=123),1,1),(p.CombatActionProto(active_pokemon_id=123),8,1),(p.InvasionEncounterOutProto(encounter_id=123),6,1)]
  for value,tag,kind in values:self.assertIn((tag,kind),[(n,k) for n,k,_,v in fields(value.SerializeToString())])
  self.assertEqual(p.ClientIncidentStepProto(invasion_battle=p.ClientInvasionBattleStepProto(character=23)).WhichOneof('client_incident_step'),'invasion_battle')
 def test_radar_inventory_category_is_registered_only_when_rocket_enabled(self):
  from dittobuffer.core.master import templates
  values=templates(self.cfg)
  category=next(v for v in values if v.template_id=='ITEM_INVENTORY_UPDATE_SETTINGS')
  self.assertTrue(any(21 in v.category for v in category.item_inventory_update_settings.category_proto))
  self.cfg['rocket']['enabled']=False
  category=next(v for v in templates(self.cfg) if v.template_id=='ITEM_INVENTORY_UPDATE_SETTINGS')
  self.assertFalse(any(21 in v.category for v in category.item_inventory_update_settings.category_proto))
 def test_hideouts_require_inventory_and_leaders_have_two_shields(self):
  self.game.rocket['hideouts']={'rocket-stop':'arlo'}
  from dittobuffer.gameplay.rocket_progression import hideout
  with self.db.transaction() as db:
   fort=db.execute("SELECT * FROM forts WHERE id='rocket-stop'").fetchone()
   self.assertIsNone(hideout(self.game,db,self.players[0],fort))
   db.execute('INSERT INTO items VALUES(?,1502,1)',(self.players[0],))
   row=hideout(self.game,db,self.players[0],fort);self.lookup.incident_id=row['id']
  battle=self.start();self.assertEqual(battle.opponent.minigame_defense_chances_left,2)
  self.assertEqual(self.call(1203,p.UpdateInvasionBattleProto(incident_lookup=self.lookup,update_type=1),p.UpdateInvasionBattleOutProto).status,12)
  with self.db.transaction() as db:self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1502',(self.players[0],)).fetchone()[0],1)
 def test_leader_reward_consumes_once_and_does_not_grant_component(self):
  self.test_hideouts_require_inventory_and_leaders_have_two_shields()
  # Controlled terminal fixture tests transaction semantics, not native combat.
  with self.db.transaction() as db:
   b=db.execute('SELECT * FROM rocket_battles ORDER BY rowid DESC LIMIT 1').fetchone();state=json.loads(b['state']);state['status']='won';state['phase']=6
   db.execute('UPDATE rocket_battles SET state=?,finalized=1 WHERE id=?',(json.dumps(state),b['id']))
   before=db.execute('SELECT stardust FROM players WHERE id=?',(self.players[0],)).fetchone()[0]
  req=p.UpdateInvasionBattleProto(incident_lookup=self.lookup,update_type=1)
  reward=self.call(1203,req,p.UpdateInvasionBattleOutProto);self.assertEqual(reward.status,1);self.assertEqual(self.call(1203,req,p.UpdateInvasionBattleOutProto),reward)
  with self.db.transaction() as db:
   self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1502',(self.players[0],)).fetchone()[0],0)
   self.assertIsNone(db.execute('SELECT 1 FROM items WHERE player_id=? AND item=1501',(self.players[0],)).fetchone())
   self.assertEqual(db.execute('SELECT stardust FROM players WHERE id=?',(self.players[0],)).fetchone()[0],before+1000)
  self.assertEqual(self.call(1204,p.InvasionEncounterProto(incident_lookup=self.lookup,step=1),p.InvasionEncounterOutProto).encounter_pokemon.pokemon_id,1)
 def test_balloon_identity_ownership_equipment_and_cooldown(self):
  self.game.rocket['balloons_enabled']=True
  # Test a same-slot cooldown, independent of the wall-clock minute at launch.
  period=self.game.rocket['balloon_period_seconds']*1000
  self.clock=(self.clock//period+1)*period
  slot_start=self.clock
  self.assertLess(self.game.rocket['balloon_duration_seconds']*1000+1,period)
  a=self.call(1206,p.GetRocketBalloonProto(),p.GetRocketBalloonOutProto);self.assertEqual(a.status,1)
  b=self.call(1206,p.GetRocketBalloonProto(),p.GetRocketBalloonOutProto);self.assertEqual(b.status,1);self.assertEqual(a.display,b.display)
  self.assertEqual(self.call(1206,p.GetRocketBalloonProto(equipped_item=1503),p.GetRocketBalloonOutProto).status,5)
  lookup=p.IncidentLookupProto(context=1,incident_id=a.display.incident_display.incident_id)
  self.assertEqual(self.call(1207,p.StartRocketBalloonIncidentProto(incident_lookup=lookup),p.StartIncidentOutProto).status,1)
  self.assertEqual(self.call(1207,p.StartRocketBalloonIncidentProto(incident_lookup=lookup),p.StartIncidentOutProto,1).status,4)
  self.clock+=self.game.rocket['balloon_duration_seconds']*1000+1
  self.assertEqual(self.call(1206,p.GetRocketBalloonProto(),p.GetRocketBalloonOutProto).status,2)
  self.clock=slot_start+period
  renewed=self.call(1206,p.GetRocketBalloonProto(),p.GetRocketBalloonOutProto)
  self.assertEqual(renewed.status,1)
  self.assertNotEqual(renewed.display.incident_display.incident_id,a.display.incident_display.incident_id)
 def test_balloon_without_social_uses_validated_walking_fix(self):
  self.game.rocket['balloons_enabled']=True;self.game.config['social']['enabled']=False
  with self.db.transaction() as db:
   db.execute('INSERT INTO walking(player_id,lat,lng,arrival_ms) VALUES(?,52.08,5.12,?)',(self.players[0],self.clock))
  first=self.call(1206,p.GetRocketBalloonProto(),p.GetRocketBalloonOutProto)
  self.assertEqual(first.status,1)
  with self.db.transaction() as db:db.execute('UPDATE rocket_incidents SET end_ms=? WHERE id=?',(self.clock+300000,first.display.incident_display.incident_id))
  period=self.game.rocket['balloon_period_seconds']*1000
  self.clock=(self.clock//period+1)*period
  # Cross a nearby boundary while the same incident is still active.
  with self.db.transaction() as db:db.execute('UPDATE rocket_incidents SET end_ms=? WHERE id=?',(self.clock+1000,first.display.incident_display.incident_id))
  self.assertEqual(self.call(1206,p.GetRocketBalloonProto(),p.GetRocketBalloonOutProto).display,first.display)
 def test_balloon_map_fix_does_not_award_walking_credit(self):
  self.game.rocket['balloons_enabled']=True;self.game.config['social']['enabled']=False
  with self.db.transaction() as db:
   db.execute('INSERT INTO admin_locations(player_id,lat,lng,seen_ms) VALUES(?,52.08,5.12,?)',(self.players[0],self.clock))
  self.assertEqual(self.call(1206,p.GetRocketBalloonProto(),p.GetRocketBalloonOutProto).status,1)
  with self.db.transaction() as db:self.assertEqual(db.execute('SELECT COALESCE(SUM(total_m),0) FROM walking').fetchone()[0],0)
 def test_balloon_radar_snapshot_and_consumption(self):
  self.game.rocket['balloons_enabled']=True
  with self.db.transaction() as db:db.execute('INSERT INTO items VALUES(?,1502,1)',(self.players[0],))
  first=self.call(1206,p.GetRocketBalloonProto(equipped_item=1502),p.GetRocketBalloonOutProto)
  with self.db.transaction() as db:
   value=json.loads(db.execute('SELECT definition FROM rocket_incidents WHERE id=?',(first.display.incident_display.incident_id,)).fetchone()[0])
   self.assertEqual(value['kind'],'leader');self.assertEqual(value['shields'],2)
   self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1502',(self.players[0],)).fetchone()[0],1)
  # Unequipping/re-polling cannot reroll the persisted leader into a Grunt.
  self.assertEqual(self.call(1206,p.GetRocketBalloonProto(),p.GetRocketBalloonOutProto).display,first.display)
 def test_balloon_gate_and_stale_fix(self):
  self.game.rocket['balloons_enabled']=True;self.game.config['social']['enabled']=False
  self.assertEqual(self.call(1206,p.GetRocketBalloonProto(),p.GetRocketBalloonOutProto).status,3)
  with self.db.transaction() as db:db.execute('UPDATE players SET xp=0 WHERE id=?',(self.players[0],))
  self.assertEqual(self.call(1206,p.GetRocketBalloonProto(),p.GetRocketBalloonOutProto).status,4)
 def test_balloon_completion_reward_and_restart_are_private(self):
  self.game.rocket['balloons_enabled']=True
  first=self.call(1206,p.GetRocketBalloonProto(),p.GetRocketBalloonOutProto)
  peer=self.call(1206,p.GetRocketBalloonProto(),p.GetRocketBalloonOutProto,1)
  self.assertNotEqual(first.display,peer.display)
  self.lookup=p.IncidentLookupProto(context=1,incident_id=first.display.incident_display.incident_id)
  _,receipt=self.win()
  self.assertEqual(self.call(1206,p.GetRocketBalloonProto(),p.GetRocketBalloonOutProto).status,2)
  self.cfg['rocket']['balloons_enabled']=True;self.game=Game(self.cfg,self.db)
  self.assertEqual(self.call(1203,p.UpdateInvasionBattleProto(incident_lookup=self.lookup,complete_battle=True,update_type=1),p.UpdateInvasionBattleOutProto),receipt)
  self.assertEqual(self.call(1206,p.GetRocketBalloonProto(),p.GetRocketBalloonOutProto,1).display,peer.display)
 def test_super_radar_decoy_preserves_ticket_and_boss_reward_is_legendary(self):
  from dittobuffer.gameplay.rocket_progression import definition,hideout
  self.assertEqual(definition(self.game,'giovanni')['reward_species'],144)
  self.game.rocket['hideouts']={'rocket-stop':'decoy'}
  with self.db.transaction() as db:
   fort=db.execute("SELECT * FROM forts WHERE id='rocket-stop'").fetchone();self.assertIsNone(hideout(self.game,db,self.players[0],fort))
   db.execute('INSERT INTO items VALUES(?,1503,1)',(self.players[0],));incident=hideout(self.game,db,self.players[0],fort)
   self.assertEqual(json.loads(incident['definition'])['ticket'],0)
   self.lookup.incident_id=incident['id']
  self.start()
  with self.db.transaction() as db:self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1503',(self.players[0],)).fetchone()[0],1)
 def test_fake_victory_and_peer_combat_rejected(self):
  battle=self.start();req=p.UpdateInvasionBattleProto(incident_lookup=self.lookup,complete_battle=True,update_type=1)
  self.assertEqual(self.call(1203,req,p.UpdateInvasionBattleOutProto).status,12)
  self.assertEqual(self.call(1001,p.UpdateCombatProto(combat_id=battle.combat_id),p.UpdateCombatOutProto,1).result,3)
  with self.db.transaction() as db:self.assertEqual(db.execute('SELECT completed_ms FROM rocket_players').fetchone()[0],0)
 def test_scheduler_and_per_player_completion_are_independent(self):
  self.start()
  other=self.call(1200,p.StartIncidentProto(incident_lookup=self.lookup),p.StartIncidentOutProto,1)
  self.assertEqual(other.status,1)
  with self.db.transaction() as db:
   fort=db.execute("SELECT * FROM forts WHERE id='rocket-stop'").fetchone();a=rocket.schedule(self.game,db,fort);b=rocket.schedule(self.game,db,fort);self.assertEqual(dict(a),dict(b));self.assertEqual(db.execute('SELECT COUNT(*) FROM rocket_players').fetchone()[0],2)
 def test_party_ownership_and_duplicate_party_denied(self):
  self.call(1200,p.StartIncidentProto(incident_lookup=self.lookup),p.StartIncidentOutProto)
  for party in [self.parties[1],self.parties[0][:2],[self.parties[0][0]]*3]:self.assertEqual(self.call(1202,p.OpenInvasionCombatSessionProto(incident_lookup=self.lookup,attacking_pokemon_id=party),p.OpenInvasionCombatSessionOutProto).status,30)
 def test_rotation_change_never_overlaps_an_active_stop_incident(self):
  with self.db.transaction() as db:
   fort=db.execute("SELECT * FROM forts WHERE id='rocket-stop'").fetchone();first=rocket.schedule(self.game,db,fort)
   self.game.rocket.update(seed='another-rotation',force_theme='fire')
   second=rocket.schedule(self.game,db,fort)
   self.assertEqual(first['id'],second['id']);self.assertEqual(first['definition'],second['definition'])
   self.assertEqual(db.execute('SELECT COUNT(*) FROM rocket_incidents WHERE fort_id=? AND end_ms>?',(fort['id'],self.clock)).fetchone()[0],1)
 def test_private_balloon_and_ticket_hideout_never_become_public_invasions(self):
  from dittobuffer.gameplay.rocket_progression import hideout
  self.game.rocket.update(balloons_enabled=True,hideouts={'rocket-stop':'arlo'})
  balloon=self.call(1206,p.GetRocketBalloonProto(),p.GetRocketBalloonOutProto)
  self.assertEqual(balloon.status,1)
  with self.db.transaction() as db:
   db.execute('INSERT INTO items VALUES(?,1502,1)',(self.players[0],))
   fort=db.execute("SELECT * FROM forts WHERE id='rocket-stop'").fetchone()
   leader=hideout(self.game,db,self.players[0],fort)
   self.assertIsNotNone(leader)
   self.assertIsNone(hideout(self.game,db,self.players[1],fort))
   public=rocket.schedule(self.game,db,fort)
   self.assertTrue(public['id'].startswith('rocket-'))
   self.assertNotEqual(public['id'],leader['id'])
   self.assertNotEqual(public['id'],balloon.display.incident_display.incident_id)
 def test_action_receipts_and_changed_counter_payload(self):
  battle=self.start();req=p.UpdateCombatProto(combat_id=battle.combat_id,combat_request_counter=1,action=p.CombatActionProto(type=1,action_start_turn=1))
  a=self.call(1001,req,p.UpdateCombatOutProto);self.assertEqual(a.result,1);self.assertEqual(self.call(1001,req,p.UpdateCombatOutProto),a)
  req.action.type=2;self.assertEqual(self.call(1001,req,p.UpdateCombatOutProto).result,5)
 def test_surrender_rematch_new_state_and_no_reward(self):
  a=self.start();self.assertEqual(self.call(1002,p.QuitCombatProto(combat_id=a.combat_id),p.QuitCombatOutProto).result,1);b=self.start();self.assertNotEqual(a.combat_id,b.combat_id)
  self.assertEqual(self.call(1203,p.UpdateInvasionBattleProto(incident_lookup=self.lookup,complete_battle=True,update_type=1),p.UpdateInvasionBattleOutProto).status,12)
 def test_real_actions_win_reward_shadow_catch_and_purification(self):
  battle,reward=self.win();self.assertEqual(self.call(1203,p.UpdateInvasionBattleProto(incident_lookup=self.lookup,complete_battle=True,update_type=1),p.UpdateInvasionBattleOutProto),reward)
  encounter=self.call(1204,p.InvasionEncounterProto(incident_lookup=self.lookup,step=1),p.InvasionEncounterOutProto);self.assertEqual(encounter.status,1);self.assertEqual(encounter.encounter_pokemon.pokemon_display.alignment,1);self.assertEqual(encounter.encounter_pokemon.move2,322)
  req=p.CatchPokemonProto(encounter_id=encounter.encounter_id,spawn_point_guid=encounter.spawn_point_guid,pokeball=5,hit_pokemon=True)
  with patch('dittobuffer.gameplay.game.secrets.randbelow',return_value=0):caught=self.call(103,req,p.CatchPokemonOutProto)
  self.assertEqual(caught.status,1);ident=caught.captured_pokemon_id;self.assertEqual(self.call(103,req,p.CatchPokemonOutProto),caught)
  before=self.call(1205,p.PurifyPokemonProto(pokemon_id=ident),p.PurifyPokemonOutProto);self.assertEqual(before.status,1);self.assertEqual(before.purified_pokemon.pokemon_display.alignment,2);self.assertEqual(before.purified_pokemon.move2,323)
  self.assertEqual(before.purified_pokemon.id,ident);self.assertEqual(self.call(1205,p.PurifyPokemonProto(pokemon_id=ident),p.PurifyPokemonOutProto),before)
  with self.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM rocket_purifications').fetchone()[0],1)
 def test_earned_encounter_and_reward_survive_incident_expiry(self):
  battle,reward=self.win()
  request=p.InvasionEncounterProto(incident_lookup=self.lookup,step=1)
  encounter=self.call(1204,request,p.InvasionEncounterOutProto)
  with self.db.transaction() as db:
   db.execute('UPDATE rocket_incidents SET end_ms=? WHERE id=?',(self.clock-1,self.lookup.incident_id))
  self.assertEqual(self.call(1204,request,p.InvasionEncounterOutProto),encounter)
  self.assertEqual(self.call(1203,p.UpdateInvasionBattleProto(incident_lookup=self.lookup,update_type=1),p.UpdateInvasionBattleOutProto),reward)
  self.assertEqual(self.call(1200,p.StartIncidentProto(incident_lookup=self.lookup),p.StartIncidentOutProto).status,4)
  self.clock+=self.game.rocket['encounter_seconds']*1000+1
  self.assertEqual(self.call(1204,request,p.InvasionEncounterOutProto).status,8)
 def test_battle_opened_before_map_expiry_can_finish_and_claim_once(self):
  battle,_=self.win(claim=False)
  with self.db.transaction() as db:db.execute('UPDATE rocket_incidents SET end_ms=? WHERE id=?',(battle.combat_start_ms,self.lookup.incident_id))
  self.assertEqual(self.call(1200,p.StartIncidentProto(incident_lookup=self.lookup),p.StartIncidentOutProto).status,4)
  req=p.UpdateInvasionBattleProto(incident_lookup=self.lookup,complete_battle=True,update_type=1)
  reward=self.call(1203,req,p.UpdateInvasionBattleOutProto);self.assertEqual(reward.status,1)
  self.assertEqual(self.call(1203,req,p.UpdateInvasionBattleOutProto),reward)
 def test_expired_incomplete_battle_has_bounded_delivery_grace_and_no_reward(self):
  battle=self.start()
  with self.db.transaction() as db:db.execute('UPDATE rocket_incidents SET end_ms=? WHERE id=?',(battle.combat_start_ms,self.lookup.incident_id))
  progress=p.UpdateInvasionBattleProto(incident_lookup=self.lookup)
  self.assertEqual(self.call(1203,progress,p.UpdateInvasionBattleOutProto).status,1)
  self.assertEqual(self.call(1203,p.UpdateInvasionBattleProto(incident_lookup=self.lookup,update_type=1),p.UpdateInvasionBattleOutProto).status,12)
  self.clock=battle.combat_end_ms+60001
  self.assertEqual(self.call(1203,progress,p.UpdateInvasionBattleOutProto).status,4)
 def test_purification_capacity_funds_clamp_and_ownership(self):
  with self.db.transaction() as db:
   mon=owned(db,self.players[0],self.parties[0][0]);mon.pokemon_display.alignment=1;mon.individual_attack=15;db.execute('UPDATE pokemon SET data=? WHERE id=?',(mon.SerializeToString(),mon.id));db.execute('UPDATE players SET stardust=0 WHERE id=?',(self.players[0],))
  req=p.PurifyPokemonProto(pokemon_id=mon.id);self.assertEqual(self.call(1205,req,p.PurifyPokemonOutProto,1).status,5);self.assertEqual(self.call(1205,req,p.PurifyPokemonOutProto).status,3)
  with self.db.transaction() as db:db.execute('UPDATE players SET stardust=100000 WHERE id=?',(self.players[0],))
  result=self.call(1205,req,p.PurifyPokemonOutProto);self.assertEqual(result.status,1);self.assertEqual(result.purified_pokemon.individual_attack,15)
 def test_shadow_costs_and_damage_differ_from_normal(self):
  from dittobuffer.gameplay.rocket_shadow import costs
  with self.db.transaction() as db:mon=owned(db,self.players[0],self.parties[0][0]);enemy=owned(db,self.players[1],self.parties[1][0])
  normal=rocket_battle.damage(self.game,rocket_battle.entry(mon),rocket_battle.entry(enemy),mon.move1)
  mon.pokemon_display.alignment=1;self.assertEqual(costs(self.game,mon,100,1),(120,2));self.assertGreater(rocket_battle.damage(self.game,rocket_battle.entry(mon),rocket_battle.entry(enemy),mon.move1),normal)
  mon.pokemon_display.alignment=2;self.assertEqual(costs(self.game,mon,100,10),(90,9))
  self.assertEqual(costs(self.game,mon,100,10,third=True),(80,8))
  from dittobuffer.gameplay.rocket_shadow import client_cost_multiplier
  import math
  self.assertEqual(math.ceil(100*client_cost_multiplier(1.2)),120)
  self.assertEqual(math.ceil(10000*client_cost_multiplier(.8)),8000)
 def test_every_native_poll_echoes_its_own_sequence(self):
  battle=self.start()
  for counter in (7,8,100):
   response=self.call(1001,p.UpdateCombatProto(combat_id=battle.combat_id,combat_request_counter=counter),p.UpdateCombatOutProto)
   self.assertEqual(response.combat.combat_request_counter,counter)
 def test_expired_battle_never_overwrites_later_medicine(self):
  battle=self.start();self.clock=battle.combat_end_ms+1
  with self.db.transaction() as db:
   value=owned(db,self.players[0],self.parties[0][0]);value.stamina=value.max_stamina
   db.execute('UPDATE pokemon SET data=? WHERE id=?',(value.SerializeToString(),value.id))
  out=self.call(1001,p.UpdateCombatProto(combat_id=battle.combat_id),p.UpdateCombatOutProto)
  self.assertEqual(out.combat.combat_state,8)
  with self.db.transaction() as db:self.assertEqual(owned(db,self.players[0],value.id).stamina,value.max_stamina)
  self.assertEqual(self.call(1203,p.UpdateInvasionBattleProto(incident_lookup=self.lookup,update_type=1),p.UpdateInvasionBattleOutProto).status,12)
 def test_swap_cannot_bypass_cooldown_or_replay_older_counter(self):
  battle=self.start()
  self.assertEqual(self.action(battle,8,self.parties[0][1]).result,14)
  result=self.action(battle,9,self.parties[0][1]);self.assertEqual(result.result,1)
  self.assertEqual(result.combat.player.last_active_pokemon.pokemon_id,self.parties[0][0])
  self.assertEqual(result.combat.player.active_pokemon.pokemon_id,self.parties[0][1])
  self.assertEqual(self.action(battle,9,self.parties[0][2]).result,14)
  out=self.call(1001,p.UpdateCombatProto(combat_id=battle.combat_id,combat_request_counter=0,action=p.CombatActionProto(type=1)),p.UpdateCombatOutProto)
  self.assertEqual(out.result,5)
 def test_defensive_start_does_not_spend_a_shield(self):
  battle=self.start()
  with self.db.transaction() as db:
   row=db.execute('SELECT * FROM rocket_battles WHERE id=?',(battle.combat_id,)).fetchone();state=json.loads(row['state'])
   rocket_battle.active(state,1)['energy']=100;rocket_battle.charge(self.game,state,1,2,1)
   db.execute('UPDATE rocket_battles SET state=? WHERE id=?',(json.dumps(state),battle.combat_id))
  self.assertEqual(self.action(battle,5).combat.player.minigame_defense_chances_left,2)
  self.clock+=7100
  value=self.action(battle,6);self.assertEqual(value.result,1);self.assertEqual(value.combat.player.minigame_defense_chances_left,1)
  self.assertEqual(value.combat.opponent.minigame_action.type,4)
  self.assertEqual(value.combat.player.minigame_action.type,6)
  self.assertEqual(value.combat.combat_state,3)
 def test_delayed_native_defense_start_preserves_shield_choice_and_blocks_damage(self):
  battle=self.start()
  with self.db.transaction() as db:
   row=db.execute('SELECT * FROM rocket_battles WHERE id=?',(battle.combat_id,)).fetchone();state=json.loads(row['state'])
   rocket_battle.active(state,1)['energy']=100;rocket_battle.charge(self.game,state,1,2,1)
   hp=state['party'][0]['hp'];db.execute('UPDATE rocket_battles SET state=? WHERE id=?',(json.dumps(state),battle.combat_id))
  # Native fly-out can delay the defense UI well beyond the server announce.
  self.clock+=6000;self.assertEqual(self.action(battle,5).result,1)
  with self.db.transaction() as db:first=json.loads(db.execute('SELECT state FROM rocket_battles WHERE id=?',(battle.combat_id,)).fetchone()[0])['charge']['end_ms']
  self.clock+=1000;self.assertEqual(self.action(battle,5).result,1)
  with self.db.transaction() as db:self.assertEqual(json.loads(db.execute('SELECT state FROM rocket_battles WHERE id=?',(battle.combat_id,)).fetchone()[0])['charge']['end_ms'],first)
  self.clock+=6100;out=self.action(battle,6)
  self.assertEqual(out.result,1);self.assertEqual(out.combat.player.minigame_defense_chances_left,1)
  self.assertEqual(out.combat.player.active_pokemon.stamina,hp-1)
  with self.db.transaction() as db:
   history=json.loads(db.execute('SELECT state FROM rocket_battles WHERE id=?',(battle.combat_id,)).fetchone()[0])['charge_history']
   self.assertTrue(history[-1]['shield']);self.assertEqual(history[-1]['before_hp']-history[-1]['after_hp'],1)
 def shadow_fixture(self):
  with self.db.transaction() as db:
   value=owned(db,self.players[0],self.parties[0][0]);value.pokemon_display.alignment=1;value.move2=322
   db.execute('UPDATE pokemon SET data=? WHERE id=?',(value.SerializeToString(),value.id))
  return value
 def test_concurrent_purification_deducts_resources_once(self):
  value=self.shadow_fixture();body=p.PurifyPokemonProto(pokemon_id=value.id).SerializeToString()
  def perform(_):
   from dittobuffer.gameplay.rocket_shadow import purify
   with self.db.transaction() as db:return purify(self.game,db,self.players[0],body).SerializeToString()
  with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(perform,range(3)))
  self.assertEqual(len(set(results)),1)
  with self.db.transaction() as db:
   self.assertEqual(db.execute('SELECT stardust FROM players WHERE id=?',(self.players[0],)).fetchone()[0],97000)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM rocket_purifications').fetchone()[0],1)
 def test_purification_dex_counts_and_restart_receipt(self):
  from dittobuffer.gameplay.pokedex import record,inventory
  value=self.shadow_fixture()
  with self.db.transaction() as db:record(self.game,db,self.players[0],'shadow-fixture',value,True)
  response=self.call(1205,p.PurifyPokemonProto(pokemon_id=value.id),p.PurifyPokemonOutProto)
  with self.db.transaction() as db:
   dex=next(v.pokedex_entry for v in inventory(self.game,db,self.players[0]) if v.pokedex_entry.pokedex_entry_number==value.pokemon_id)
   self.assertEqual(dex.times_captured,1);self.assertTrue({15,16}.issubset({v.value.pokedex_category for v in dex.category_status}))
  self.game=Game(self.cfg,self.db)
  self.assertEqual(self.call(1205,p.PurifyPokemonProto(pokemon_id=value.id),p.PurifyPokemonOutProto),response)
 def test_purification_research_only_progresses_once(self):
  value=self.shadow_fixture();self.game.research.update(enabled=True,stories=[])
  definition=dict(event='purify',target=1,template='ORIGINAL_TEST',description='Purify one Shadow Pokemon.',rewards=[dict(type='xp',amount=100)])
  with self.db.transaction() as db:db.execute('INSERT INTO research_tasks(player_id,id,definition,created_ms,updated_ms) VALUES(?,?,?,?,?)',(self.players[0],'purify-task',json.dumps(definition),self.clock,self.clock))
  for _ in range(2):self.assertEqual(self.call(1205,p.PurifyPokemonProto(pokemon_id=value.id),p.PurifyPokemonOutProto).status,1)
  with self.db.transaction() as db:self.assertEqual(db.execute('SELECT progress FROM research_tasks WHERE id=?',('purify-task',)).fetchone()[0],1)
 def test_sixth_component_assembles_once(self):
  with self.db.transaction() as db:db.execute('INSERT INTO items VALUES(?,1501,5)',(self.players[0],))
  battle,reward=self.win();self.assertTrue(reward.map_fragment_upgraded)
  self.call(1203,p.UpdateInvasionBattleProto(incident_lookup=self.lookup,update_type=1),p.UpdateInvasionBattleOutProto)
  with self.db.transaction() as db:
   self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1501',(self.players[0],)).fetchone()[0],0)
   self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1502',(self.players[0],)).fetchone()[0],1)
 def test_frustration_tm_is_event_gated_without_spending_on_failure(self):
  value=self.shadow_fixture()
  with self.db.transaction() as db:db.execute('INSERT INTO items VALUES(?,1202,2)',(self.players[0],))
  req=p.UseItemMoveRerollProto(pokemon_id=value.id,item=1202)
  self.assertEqual(self.call(813,req,p.UseItemMoveRerollOutProto).result,7)
  self.game.rocket.update(frustration_event_start_ms=self.clock-1000,frustration_event_end_ms=self.clock+1000)
  out=self.call(813,req,p.UseItemMoveRerollOutProto);self.assertEqual(out.result,1);self.assertNotEqual(out.updated_pokemon.move2,322)
  with self.db.transaction() as db:self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1202',(self.players[0],)).fetchone()[0],1)
 def test_shadow_second_charge_keeps_frustration_and_real_prices(self):
  value=self.shadow_fixture();rules=self.game.species[value.pokemon_id]
  with self.db.transaction() as db:before=db.execute('SELECT stardust FROM players WHERE id=?',(self.players[0],)).fetchone()[0]
  out=self.call(1004,p.UnlockPokemonMoveProto(pokemon_id=value.id),p.UnlockPokemonMoveOutProto)
  self.assertEqual(out.result,1);self.assertEqual(out.unlocked_pokemon.move2,322);self.assertTrue(out.unlocked_pokemon.move3)
  from dittobuffer.gameplay.rocket_shadow import costs
  dust,candies=costs(self.game,value,rules['third_move']['stardust'],rules['third_move']['candy'],third=True)
  with self.db.transaction() as db:self.assertEqual(db.execute('SELECT stardust FROM players WHERE id=?',(self.players[0],)).fetchone()[0],before-dust)
 def test_shadow_evolution_preserves_identity_alignment_and_frustration(self):
  value=make_pokemon(p,self.game.species[4],4,20,[12]*3);value.id=self.parties[0][0];value.pokemon_display.alignment=1;value.move2=322
  with self.db.transaction() as db:
   db.execute('UPDATE pokemon SET species=4,data=? WHERE id=?',(value.SerializeToString(),value.id));db.execute('UPDATE pokemon_levels SET level=20 WHERE pokemon_id=?',(value.id,))
  out=self.call(125,p.EvolvePokemonProto(pokemon_id=value.id),p.EvolvePokemonOutProto)
  self.assertEqual(out.result,1);self.assertEqual((out.evolved_pokemon.id,out.evolved_pokemon.pokemon_id,out.evolved_pokemon.pokemon_display.alignment,out.evolved_pokemon.move2),(value.id,5,1,322))
if __name__=='__main__':unittest.main()
