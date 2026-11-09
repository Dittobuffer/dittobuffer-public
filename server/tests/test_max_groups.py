"""Authenticated simulated Max peers; never a 20-device/native claim."""
import json
import unittest

from dittobuffer.gameplay.game import p, RpcError
from dittobuffer.gameplay import max_battle as battle, max_system as mx, gym_battle as engine
from dittobuffer.core.management import owned
from tests.test_max_battle import MaxBattleTests as Base


class MaxGroupTests(unittest.TestCase):
    setUp_resources = Base.setUp_resources
    setUp = Base.setUp
    tearDown = Base.tearDown
    call = Base.call
    create_owned = Base.create_owned
    join = Base.join
    prepare = Base.prepare
    start = Base.start
    begin = Base.begin
    room = Base.room
    combat = Base.combat
    extra_player = Base.extra_player
    headers = Base.headers
    state = Base.state
    event = Base.event
    complete = Base.complete
    attack = Base.attack
    win = Base.win

    def gigantamax_fixture(self):
        """Explicit synthetic source fixture, no assets or owned G-Max grants."""
        self.game.max_config['development_balance'].update(boss_hp=1000000, boss_interval_ms=60000)
        self.game.max_released_pools.append(dict(species=6, mode=2))
        self.game.max_reference['species_groups'][f"6:{self.game.species[6]['form']}"] = 1
        shared=p.GameMasterClientTemplateProto(template_id='BREAD_SHARED_SETTINGS')
        shared.bread_settings.allowed_sourdough_pokemon.add(pokemon_id=6, form=[self.game.species[6]['form']], bread_mode=2)
        self.game.max_reference['templates'][shared.template_id]=shared.SerializeToString().hex()
        source=p.GameMasterClientTemplateProto(template_id='BREAD_BATTLE_CLIENT_SETTINGS')
        source.bread_battle_client_settings.max_players_per_bread_lobby=4
        source.bread_battle_client_settings.max_players_per_bread_dough_lobby=40
        self.game.max_reference['templates'][source.template_id]=source.SerializeToString().hex()
        mp=mx.gm(self.game.max_reference,'MP_SHARED_SETTINGS')
        mp.mp_settings.battle_mp_cost_per_tier.add(battle_level=7,bread_battle_catch_mp_cost=800)
        self.game.max_reference['templates'][mp.template_id]=mp.SerializeToString().hex()
        with self.db.transaction() as db:
            db.execute('UPDATE max_stations SET definition=? WHERE id=?',
                       (json.dumps(dict(species=6,mode=2,tier=7,level=10)),self.stations[0]['id']))
            self.stations=db.execute('SELECT * FROM max_stations ORDER BY id').fetchall()

    def participants(self, count):
        while len(self.players)<count:self.extra_player()

    def start_all(self, count, reverse=False):
        joined=[self.join(index) for index in range(count)]
        self.assertEqual([1]*count,[v.result for v in joined])
        prepared=None
        for index in range(count):prepared=self.prepare(joined[index].bread_lobby,index)
        self.clock=prepared.bread_lobby.bread_battle_start_ms
        started={index:self.start(index) for index in (reversed(range(count)) if reverse else range(count))}
        self.assertTrue(all(v.result==1 for v in started.values()))
        return started

    def fill_group_zero(self, started):
        for unused in range(3):
            self.assertEqual(self.attack(started[0]).result,1);self.clock+=2500
        state=self.combat(started[0]);self.clock=state['groups']['0']['phase_due']
        self.state(started[0]);self.clock+=4000
        return self.combat(started[0])

    def test_twenty_independent_authenticated_peers_and_reject_twenty_first(self):
        self.gigantamax_fixture();self.participants(21)
        replies=[self.join(index) for index in range(21)]
        self.assertEqual([1]*20+[13],[v.result for v in replies])
        self.assertEqual(1,len({v.bread_lobby.bread_lobby_id for v in replies[:20]}))
        self.assertEqual(20,len({v.bread_lobby.rvn_connection.token for v in replies[:20]}))
        self.assertTrue(replies[19].bread_lobby.bread_dough_mode)
        self.assertEqual(20,len(replies[19].bread_lobby.players))
        self.assertEqual(list(range(1,21)),[v.player_number for v in replies[19].bread_lobby.players])
        state=json.loads(self.room(replies[0].bread_lobby)['state'])
        self.assertEqual(self.players[:20],state['join_order'])
        self.assertEqual([str(index//4) for index in range(20)],
                         [state['player_groups'][key] for key in self.players[:20]])
        with self.db.transaction() as db:
            self.assertEqual(21,db.execute('SELECT COUNT(DISTINCT player_id) FROM sessions').fetchone()[0])
            self.assertEqual(20,db.execute('SELECT COUNT(*) FROM max_members').fetchone()[0])
            self.assertEqual(1000,mx.available(db,self.players[20]))
            self.assertEqual(200,mx.available(db,self.players[0]))

    def test_native_lobby_team_numbers_for_one_four_and_five_participants(self):
        # ARM64 bvjm0x87BA6C0 uses (PlayerNumber-1)//4; bvjl0x87BDE18
        # takes max(PlayerNumber) for the team-count denominator. Field16 is
        # int32, encoded with literal tag8001. These are native UI conventions,
        # independent of the server's zero-based combat actor party_id.
        self.gigantamax_fixture();self.participants(5)
        for index in range(5):
            joined=self.join(index);self.assertEqual(1,joined.result)
            count=index+1
            if count not in (1,4,5):continue
            lobby=p.BreadLobbyProto.FromString(joined.bread_lobby.SerializeToString())
            numbers=[v.player_number for v in lobby.players]
            self.assertEqual(list(range(1,count+1)),numbers)
            for number in numbers:
                self.assertEqual(bytes.fromhex('8001')+bytes([number]),
                                 p.BreadBattleParticipantProto(player_number=number).SerializeToString())
            team=(numbers[-1]-1)//4+1;total=(max(numbers)+3)//4
            self.assertEqual((1,1) if count<=4 else (2,2),(team,total))
        # Reauthentication/lobby refresh must not change the assigned ordering.
        self.assertEqual([1,2,3,4,5],[v.player_number for v in self.join(0).bread_lobby.players])

    def test_normal_cap_and_source_caps_are_bounded(self):
        self.participants(5)
        replies=[self.join(index) for index in range(5)]
        self.assertEqual([1,1,1,1,13],[v.result for v in replies])
        self.assertFalse(replies[0].bread_lobby.bread_dough_mode)
        self.assertEqual(4,battle.source_capacity(self.game,1))
        self.assertEqual(20,battle.source_capacity(self.game,2))
        self.assertEqual(20,battle.source_capacity(self.game,3))

    def test_unreleased_owned_mode_three_cannot_enter_max_party(self):
        joined=self.join();self.assertEqual(1,joined.result)
        with self.db.transaction() as db:
            mon=owned(db,self.players[0],self.parties[0].id)
            mon.pokemon_display.bread_mode_enum=3
            db.execute('UPDATE pokemon SET data=? WHERE id=?',(mon.SerializeToString(),mon.id))
            self.assertFalse(battle.eligible(mon))
            self.assertEqual([],battle.party_for(db,self.players[0]))
        prepared=self.prepare(joined.bread_lobby);self.clock=prepared.bread_lobby.bread_battle_start_ms
        self.assertEqual(5,self.start().result)
        with self.db.transaction() as db:
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM max_pokemon_locks').fetchone()[0])
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM max_victories').fetchone()[0])

    def test_shared_boss_separate_group_meter_phase_and_native_party_projection(self):
        self.gigantamax_fixture();self.participants(8)
        started=self.start_all(8,reverse=True)
        state=self.fill_group_zero(started)
        self.assertEqual(2,state['groups']['0']['phase']);self.assertEqual(0,state['groups']['1']['phase'])
        self.assertEqual(3,state['groups']['0']['meter']);self.assertEqual(0,state['groups']['1']['meter'])
        self.assertEqual([3]*4,[state['players'][v]['max_turns'] for v in self.players[:4]])
        self.assertEqual([0]*4,[state['players'][v]['max_turns'] for v in self.players[4:8]])
        self.assertEqual(self.attack(started[4],index=4).result,1)
        later=self.combat(started[4]);self.assertLess(later['boss']['hp'],state['boss']['hp'])
        self.assertEqual(1,later['groups']['1']['meter']);self.assertEqual(3,later['groups']['0']['meter'])
        one=self.state(started[0],0).battle_state;two=self.state(started[4],4).battle_state
        self.assertEqual(2,one.ui_mode);self.assertEqual(0,two.ui_mode)
        a=one.actors[self.players[0][:20]].party_id;b=one.actors[self.players[4][:20]].party_id
        self.assertNotEqual(a,b);self.assertEqual(4,one.party_member_count[a]);self.assertEqual(4,one.party_member_count[b])
        self.assertEqual(one.pokemon[engine.mon(later['boss']).id].resources[1].quantity,
                         two.pokemon[engine.mon(later['boss']).id].resources[1].quantity)
        self.assertTrue(any(v.type==31 and v.cinematic.event_type==2 for v in one.events))
        self.assertFalse(any(v.type==31 and v.cinematic.event_type==2 for v in two.events))

    def test_max_spirit_and_three_turns_stay_within_four_trainer_group(self):
        self.gigantamax_fixture();self.participants(8)
        self.game.max_config['development_balance'].update(boss_interval_ms=1000,boss_damage_multiplier=.01)
        started=self.start_all(8)
        self.clock+=3000;self.state(started[0])
        self.fill_group_zero(started)
        self.state(started[0])  # Advance genuine boss AI before comparing the heal.
        before=self.combat(started[0]);before_hp={key:v['party'][v['active']]['hp'] for key,v in before['players'].items()}
        self.assertLess(before_hp[self.players[1]],self.parties[1].max_stamina)
        self.assertEqual(self.attack(started[0],7).result,1)
        after=self.combat(started[0])
        for key in self.players[:4]:self.assertGreater(after['players'][key]['party'][0]['hp'],before_hp[key])
        for key in self.players[4:8]:self.assertEqual(after['players'][key]['party'][0]['hp'],before_hp[key])
        self.assertEqual(2,after['players'][self.players[0]]['max_turns'])
        self.assertEqual(3,after['players'][self.players[1]]['max_turns'])
        self.assertEqual(0,after['players'][self.players[4]]['max_turns'])

    def test_existing_lobby_member_can_finish_loading_during_its_max_phase(self):
        self.gigantamax_fixture();self.participants(4)
        joined=[self.join(index) for index in range(4)]
        for index in range(4):prepared=self.prepare(joined[index].bread_lobby,index)
        self.clock=prepared.bread_lobby.bread_battle_start_ms
        started={index:self.start(index) for index in range(3)}
        self.fill_group_zero(started)
        late=self.start(3);self.assertEqual(late.result,1)
        state=self.combat(late)
        self.assertEqual('0',state['player_groups'][self.players[3]])
        self.assertEqual(3,state['players'][self.players[3]]['max_turns'])
        self.assertEqual(2,self.state(late,3).battle_state.ui_mode)

    def test_room_balance_and_station_rewards_are_frozen_across_live_config_changes(self):
        with self.db.transaction() as db:
            definition=json.loads(self.stations[0]['definition']);definition['difficulty_multipliers']={'boss_hp':.5,'boss_damage':.5,'player_damage':2,'battle_duration':.5}
            db.execute('UPDATE max_stations SET definition=? WHERE id=?',(json.dumps(definition),self.stations[0]['id']))
            self.stations=db.execute('SELECT * FROM max_stations ORDER BY id').fetchall()
        joined=self.join();room=self.room(joined.bread_lobby);state=json.loads(room['state'])
        self.assertEqual(100,state['rules']['boss_hp']);self.assertEqual(60,state['rules']['battle_seconds'])
        self.assertEqual(.5,state['rules']['boss_damage_multiplier']);self.assertEqual(2,state['rules']['player_damage_multiplier'])
        self.game.max_config['development_balance'].update(boss_hp=999999,xp=999999,dust=999999,meter_goal=1000,battle_seconds=3600)
        prepared=self.prepare(joined.bread_lobby);self.clock=prepared.bread_lobby.bread_battle_start_ms
        started=self.start();self.assertEqual(100,self.combat(started)['boss']['hp'])
        self.win(started)
        # A retired source pool cannot replace/erase the earned frozen reward.
        self.game.max_released_pools=[];self.game.max_config['pools']=[]
        result=self.complete(started)
        self.assertTrue(result.is_victory);self.assertEqual(1000,result.battle_results.player_xp_awarded)
        self.assertEqual(831,result.battle_results.post_battle_encounter.pokemon.pokemon_id)
        self.assertEqual(1,result.battle_results.post_battle_encounter.pokemon.pokemon_display.bread_mode_enum)

    def test_legacy_single_group_migration_does_not_reset_hp_energy_or_meter(self):
        started=self.begin();self.assertEqual(self.attack(started).result,1)
        original=self.combat(started)
        with self.db.transaction() as db:
            state=json.loads(db.execute('SELECT state FROM max_rooms WHERE id=?',(started.lobby.bread_lobby_id,)).fetchone()[0])
            for key in ('groups','player_groups','join_order','rules','rules_provenance','boss_mode','max_participants','station_snapshot'):state.pop(key,None)
            db.execute('UPDATE max_rooms SET state=? WHERE id=?',(json.dumps(state),started.lobby.bread_lobby_id))
        self.game.max_config['development_balance']['boss_hp']=999999
        self.state(started);migrated=self.combat(started)
        self.assertEqual(original['boss'],migrated['boss'])
        self.assertEqual(original['players'][self.players[0]]['party'],migrated['players'][self.players[0]]['party'])
        self.assertEqual(original['meter'],migrated['groups']['0']['meter'])
        self.assertEqual('0',migrated['player_groups'][self.players[0]])
        self.assertEqual(engine.mon(original['boss']).max_stamina,migrated['rules']['boss_hp'])
        self.assertIn('unavailable',migrated['rules_provenance'])

    def test_unverified_native_cheering_is_rejected_without_state_or_rewards(self):
        started=self.begin();before=self.combat(started)
        with self.assertRaisesRegex(RpcError,'cheering'):
            self.event(started,p.BattleEventProto(type=25))
        after=self.combat(started)
        self.assertEqual(before['boss'],after['boss']);self.assertEqual(before['meter'],after['meter'])
        with self.db.transaction() as db:self.assertEqual(0,db.execute('SELECT COUNT(*) FROM max_victories').fetchone()[0])

    def cooperative_injury(self):
        self.game.max_config['development_balance'].update(boss_hp=1000000,boss_interval_ms=1000,boss_damage_multiplier=.01)
        started=self.start_all(2)
        self.clock+=2000;self.state(started[0])
        with self.db.transaction() as db:
            self.assertLess(owned(db,self.players[0],self.parties[0].id).stamina,self.parties[0].max_stamina)
            db.execute('INSERT INTO items VALUES(?,104,1) ON CONFLICT(player_id,item) DO UPDATE SET count=count+1',(self.players[0],))
        return started

    def heal_departed(self):
        healed=self.call(113,p.UseItemPotionProto(item=104,pokemon_id=self.parties[0].id),p.UseItemPotionOutProto)
        self.assertEqual(1,healed.result);self.assertEqual(self.parties[0].max_stamina,healed.stamina)
        return healed.stamina

    def assert_departed_heal_survives(self,rpc_leave):
        started=self.cooperative_injury()
        if rpc_leave:
            room=self.room(started[0].lobby)
            req=p.LeaveBreadLobbyProto(station_id=room['station_id'],bread_battle_seed=room['seed'],bread_lobby_id=room['id'])
            self.assertEqual(1,self.call(2455,req,p.LeaveBreadLobbyOutProto).result)
        else:self.assertEqual(1,self.event(started[0],p.BattleEventProto(type=3)).result)
        healed=self.heal_departed();self.clock+=2000;self.state(started[1],1)
        with self.db.transaction() as db:self.assertEqual(healed,owned(db,self.players[0],self.parties[0].id).stamina)

    def test_departed_pbs_player_heal_survives_teammate_poll(self):
        self.assert_departed_heal_survives(False)

    def test_departed_lobby_player_heal_survives_teammate_poll(self):
        self.assert_departed_heal_survives(True)

    def test_unpolled_expired_attempt_cannot_overwrite_heal_or_replacement_lock(self):
        started=self.cooperative_injury();old=self.room(started[0].lobby)
        self.clock=old['end_ms']+120001
        with self.db.transaction() as db:
            db.execute('UPDATE social_locations SET fix_ms=?,arrival_ms=? WHERE player_id=?',(self.clock,self.clock,self.players[0]))
        healed=self.heal_departed()
        replacement=self.begin();self.assertNotEqual(old['id'],replacement.lobby.bread_lobby_id)
        self.state(started[1],1)  # First terminal poll of the old active room.
        with self.db.transaction() as db:
            self.assertEqual(healed,owned(db,self.players[0],self.parties[0].id).stamina)
            lock=db.execute('SELECT room_id FROM max_pokemon_locks WHERE pokemon_id=?',(self.parties[0].id,)).fetchone()
            self.assertEqual(str(replacement.lobby.bread_lobby_id),lock['room_id'])
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM max_victories').fetchone()[0])

    def test_reauthenticated_account_rotates_transport_without_resetting_attempt(self):
        from dittobuffer.core.accounts import Accounts
        self.game.accounts=Accounts(self.game)
        password='Temporary isolated Max reconnect password'
        self.game.accounts.register('max-reconnect',password,'Max Reconnect',self.game.accounts.invite())
        credential=self.game.accounts.login('max-reconnect',password)['token']
        self.tickets[0]=self.game.accounts.exchange(credential)
        with self.db.transaction() as db:
            self.players[0]=db.execute("SELECT player_id FROM family_accounts WHERE username='max-reconnect'").fetchone()[0]
            db.execute('UPDATE players SET xp=210000 WHERE id=?',(self.players[0],))
            db.execute('INSERT INTO social_locations VALUES(?,?,?,?,?,?)',(self.players[0],52.08,5.12,5,self.clock,self.clock))
            mx.mutate(self.game,db,self.players[0],1000,'explicit-unit-reconnect-fixture')
        self.parties[0]=self.create_owned(self.players[0])
        started=self.begin();self.attack(started);before=self.combat(started)
        self.game.accounts.revoke(credential)
        with self.assertRaisesRegex(RpcError,'expired'):self.state(started)
        renewed=self.game.accounts.login('max-reconnect',password)['token']
        self.tickets[0]=self.game.accounts.exchange(renewed)
        joined=self.join();self.assertEqual(1,joined.result)
        self.assertEqual(started.lobby.bread_lobby_id,joined.bread_lobby.bread_lobby_id)
        self.assertNotEqual(started.rvn_connection.token,joined.bread_lobby.rvn_connection.token)
        resumed=self.start();self.state(resumed)
        self.assertEqual(before['boss'],self.combat(resumed)['boss'])
        self.assertEqual(before['groups'],self.combat(resumed)['groups'])
        with self.assertRaisesRegex(RpcError,'Invalid Max combat session'):self.state(started)


# Helpers are borrowed, not another copy of the imported test case suite.
del Base

if __name__=='__main__':unittest.main()
