import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from dittobuffer.gameplay.game import Game,ROOT,p,wire,now_ms,RpcError
from dittobuffer.core.database import Database
from dittobuffer.gameplay.species import make_pokemon
from dittobuffer.gameplay import raids, raid_transport, gym_battle

class RaidTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds');self.clock=now_ms();self.counter=0
        self.patches=[patch(name,lambda:self.clock) for name in ('dittobuffer.gameplay.game.now_ms','dittobuffer.gameplay.raids.now_ms','dittobuffer.gameplay.raid_transport.now_ms','dittobuffer.gameplay.gym_battle.now_ms','dittobuffer.gameplay.gyms.now_ms')]
        for v in self.patches:v.start()
        self.config=json.loads((ROOT/'config/development.json').read_text(encoding='utf8'))
        self.config.update(catch_probability=1,raids={'enabled':True,'epoch_ms':self.clock-2000,'egg_seconds':1,'active_seconds':3600,
            'lobby_seconds':20,'relobby_seconds':20,
            'pools':[{'species':16,'tier':1,'weight':1,'hp':60,'cp':1000,'xp':1000,'dust':500,'items':{'101':2}}]})
        self.db=Database(Path(self.tmp.name)/'raids.sqlite3');self.game=Game(self.config,self.db)
        self.tickets=[self.game.authenticate(wire.AuthRequest(auth_id='raid-'+str(i))).ticket for i in range(2)]
        with self.db.transaction() as db:
            self.players=[db.execute('SELECT player_id FROM sessions WHERE ticket=?',(t,)).fetchone()[0] for t in self.tickets]
            db.execute('UPDATE players SET xp=10000');db.execute("INSERT INTO forts VALUES('gym',0,'Raid Garden',52.37,4.89)")
            db.execute('INSERT INTO items VALUES(?,1402,2)',(self.players[0],))
            db.execute("INSERT INTO spawns VALUES(100,'test',25,52.37,4.89,?)",(self.clock+86400000,))
            mon=make_pokemon(p,self.game.species[25],25,35,[15]*3);mon.id=1
            db.execute('INSERT INTO pokemon VALUES(1,?,100,25,?)',(self.players[0],mon.SerializeToString()))
            self.seed=raids.schedule(self.game,db,db.execute("SELECT * FROM forts WHERE id='gym'").fetchone())['seed']

    def tearDown(self):
        for v in self.patches:v.stop()
        self.tmp.cleanup()

    def call(self,method,request,response,index=0):
        self.counter+=1;req=wire.RpcRequest(rpc_id=self.counter);req.requests.add(method=method,body=request.SerializeToString())
        return response.FromString(self.game.rpc(req,self.tickets[index]).payloads[0].payload)

    def join(self,index=0):return self.call(159,p.JoinLobbyProto(gym_id='gym',raid_seed=self.seed,player_lat_degrees=52.37,player_lng_degrees=4.89),p.JoinLobbyOutProto,index)
    def start(self,ids=(1,)):
        self.clock+=21000
        return self.call(165,p.StartRaidBattleProto(gym_id='gym',raid_seed=self.seed,attacking_pokemon_id=ids,player_lat_degrees=52.37,player_lng_degrees=4.89),p.StartRaidBattleOutProto)

    def headers(self,battle):
        with self.db.transaction() as db:token=db.execute('SELECT token FROM raid_tokens').fetchone()[0]
        return {'Authorization':'Bearer '+token,'nbl-battle-id':battle.battle_id,'nbl-player-id':self.players[0][:20]}

    def replacement(self):
        with self.db.transaction() as db:
            db.execute("INSERT OR IGNORE INTO spawns VALUES(101,'replacement',4,52.37,4.89,?)",(self.clock+1000000,))
            mon=make_pokemon(p,self.game.species[4],4,35,[15]*3);mon.id=2
            db.execute('INSERT OR REPLACE INTO pokemon VALUES(2,?,101,4,?)',(self.players[0],mon.SerializeToString()))

    def faint_party(self,battle):
        # Isolated low-HP fixture; damage/faint transitions still run through AI.
        with self.db.transaction() as db:
            b=db.execute('SELECT * FROM raid_battles WHERE id=?',(battle.battle_id,)).fetchone();state=json.loads(b['state'])
            for value in state['party']:value['hp']=1
            raids.persist(db,b['id'],state)
        headers=self.headers(battle)
        for _ in range(60):
            self.clock+=500
            frame=raid_transport.handle(self.game,'/raid/api/get-state',headers)
            if frame.battle_state.state==5:return frame
        self.fail('Fixture party did not faint from server AI')

    def win(self,claim=True):
        self.join();battle=self.start().battle
        for _ in range(20):
            self.clock+=1100;req=p.AttackRaidBattleProto(gym_id='gym',battle_id=battle.battle_id)
            req.attacker_actions.add(type=1,action_start_ms=self.clock,active_pokemon_id=1)
            result=self.call(166,req,p.AttackRaidBattleOutProto)
            if result.battle_update.battle_log.state==2:break
        self.assertEqual(result.battle_update.battle_log.state,2)
        req=p.CompleteRaidBattleProto(gym_id='gym',battle_id=battle.battle_id)
        return req,self.call(3010,req,p.CompleteRaidBattleOutProto) if claim else None

    def test_pass_consumed_at_start_once_leave_capacity_and_restart(self):
        first=self.join();self.assertEqual(first.result,1)
        self.assertEqual(self.join().lobby.creation_ms,first.lobby.creation_ms)
        self.assertEqual(self.join(1).result,13)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT count FROM items WHERE item=1402').fetchone()[0],2)
        self.call(160,p.LeaveLobbyProto(gym_id='gym',raid_seed=self.seed),p.LeaveLobbyOutProto)
        self.assertEqual(self.join().result,1);out=self.start();self.assertEqual(out.result,1)
        self.game=Game(self.config,Database(self.db.path));self.assertEqual(self.start().battle.battle_id,out.battle.battle_id)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT count FROM items WHERE item=1402').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM raid_pass_receipts').fetchone()[0],1)

    def test_real_timed_damage_rewards_bonus_catch_and_restart_retry(self):
        self.join();battle=self.start().battle
        for i in range(20):
            self.clock+=1100
            req=p.AttackRaidBattleProto(gym_id='gym',battle_id=battle.battle_id)
            req.attacker_actions.add(type=1,action_start_ms=self.clock,active_pokemon_id=1)
            result=self.call(166,req,p.AttackRaidBattleOutProto)
            if result.battle_update.battle_log.state==2:break
        self.assertEqual(result.battle_update.battle_log.state,2)
        req=p.CompleteRaidBattleProto(gym_id='gym',battle_id=battle.battle_id)
        reward=self.call(3010,req,p.CompleteRaidBattleOutProto);self.assertTrue(reward.is_victory)
        self.assertEqual(self.call(3010,req,p.CompleteRaidBattleOutProto),reward)
        details=self.call(163,p.GetRaidDetailsProto(gym_id='gym',raid_seed=self.seed,player_lat_degrees=52.37,player_lng_degrees=4.89),p.GetRaidDetailsOutProto)
        self.assertTrue(details.rvn_battle_completed);self.assertTrue(details.rvn_battle_is_victory)
        self.assertEqual(details.throws_remaining,12);self.assertFalse(details.raid_info.complete)
        self.assertEqual(next(a for a in details.raid_battle.battle_log.battle_actions if a.type==8).battle_results,reward.battle_results)
        encounter=reward.battle_results.post_raid_encounter[0]
        caught=self.call(103,p.CatchPokemonProto(encounter_id=encounter.encounter_id,spawn_point_guid=encounter.spawnpoint_id,pokeball=5,hit_pokemon=True),p.CatchPokemonOutProto)
        self.assertEqual(caught.status,1)
        self.game=Game(self.config,Database(self.db.path))
        again=self.call(103,p.CatchPokemonProto(encounter_id=encounter.encounter_id,pokeball=5,hit_pokemon=True),p.CatchPokemonOutProto)
        self.assertEqual(caught,again)
        with self.db.transaction() as db:
            self.assertEqual(raids.history(db,self.players[0],'gym').total_completed_raids,1)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM pokemon').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT balls FROM raid_members').fetchone()[0],11)
            self.assertEqual(db.execute('SELECT xp FROM players WHERE id=?',(self.players[0],)).fetchone()[0],12100)  # Includes the first-species 1,000 XP registration.

    def test_native_summary_has_complete_owned_per_player_result(self):
        req, reward = self.win()
        result = reward.battle_results
        native = result.player_results[0]
        for name in ('participation', 'raid_item_rewards', 'post_raid_encounter', 'gym_badge'):
            self.assertEqual(getattr(native, name), getattr(result, name)[0], name)
        from protocol.opaque import fields
        native_wire=next(value for tag,kind,_,value in fields(result.SerializeToString()) if tag==19 and kind==2)
        self.assertTrue({1,2,3,5,6,8,9} <= {tag for tag,kind,_,value in fields(native_wire)})
        self.assertEqual(native.player_xp_awarded, result.player_xp_awarded[0])
        self.assertGreater(native.attacker.damage_dealt, 0)
        self.assertEqual(native.stats.stat_value, native.attacker.damage_dealt)
        self.assertEqual(result.raid_player_stats.stats[0].stat_id, 2)
        self.assertEqual(self.call(3010, req, p.CompleteRaidBattleOutProto), reward)

    def test_explicit_bonus_breakdown_matches_owned_ball_budget(self):
        from dittobuffer.gameplay.raid_rewards import validate, total_balls
        policy = {'provenance': 'Synthetic unit policy, not official balance', 'gym_ownership': 2,
                  'damage': [{'minimum_fraction': .5, 'balls': 3}],
                  'speed': [{'maximum_fraction': 1, 'balls': 2}]}
        self.game.raids['ball_bonuses'] = policy
        validate(self.game.raids)
        with self.db.transaction() as db:
            from dittobuffer.gameplay.gyms import gym
            gym(db, 'gym')
            definition=json.loads(db.execute('SELECT definition FROM raids WHERE seed=?',(self.seed,)).fetchone()[0])
            definition['_rules']['ball_bonuses']=policy
            db.execute('UPDATE raids SET definition=? WHERE seed=?',(json.dumps(definition),self.seed))
            db.execute('UPDATE trainer_teams SET team=1 WHERE player_id=?', (self.players[0],))
            db.execute("UPDATE gyms SET team=1 WHERE fort_id='gym'")
        req, reward = self.win()
        allocation = reward.battle_results.player_results[0].participation
        self.assertEqual((allocation.base_pokeballs, allocation.gym_ownership_pokeballs,
                          allocation.individual_damage_pokeballs, allocation.speed_completion_pokeballs),
                         (12, 2, 3, 2))
        self.assertEqual(total_balls(allocation), 19)
        self.assertEqual(reward.battle_results.player_results[0].post_raid_encounter.throws_remaining, 19)
        with self.db.transaction() as db:
            self.assertEqual(raids.member(db, self.seed, self.players[0])['balls'], 19)
        encounter = reward.battle_results.post_raid_encounter[0]
        self.call(103, p.CatchPokemonProto(encounter_id=encounter.encounter_id,
            spawn_point_guid=encounter.spawnpoint_id, pokeball=5, hit_pokemon=False), p.CatchPokemonOutProto)
        replay = self.call(3010, req, p.CompleteRaidBattleOutProto)
        self.assertEqual(replay.battle_results.player_results[0].post_raid_encounter.throws_remaining, 18)

    def test_winner_recovers_summary_after_restart_and_global_expiration(self):
        req, _ = self.win(claim=False)
        self.game = Game(self.config, Database(self.db.path))
        self.clock += 3600001
        query = p.GetRaidDetailsProto(gym_id='gym', raid_seed=self.seed,
            player_lat_degrees=52.37, player_lng_degrees=4.89)
        details = self.call(163, query, p.GetRaidDetailsOutProto)
        self.assertEqual(details.result, 1)
        self.assertTrue(details.rvn_battle_completed)
        self.assertTrue(details.rvn_battle_is_victory)
        self.assertFalse(details.player_can_join_lobby)
        self.assertEqual(self.call(163, query, p.GetRaidDetailsOutProto, index=1).result, 4)
        reward = self.call(3010, req, p.CompleteRaidBattleOutProto)
        self.assertTrue(reward.is_victory)
        self.game = Game(self.config, Database(self.db.path))
        self.assertEqual(self.call(3010, req, p.CompleteRaidBattleOutProto), reward)
        self.assertEqual(self.call(163, query, p.GetRaidDetailsOutProto).throws_remaining, 12)

    def test_bonus_policy_rejects_unbounded_and_reversed_schedules(self):
        from dittobuffer.gameplay.raid_rewards import validate
        bad = [None, {'gym_ownership': 2}, {'provenance': ' '},
               {'provenance': 'unit', 'gym_ownership': True},
               {'provenance': 'unit', 'damage': [{'minimum_fraction': float('nan'), 'balls': 1}]},
               {'provenance': 'unit', 'speed': [{'maximum_fraction': .5, 'balls': 1},
                                                {'maximum_fraction': 1, 'balls': 4}]}]
        for policy in bad:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                validate({'ball_bonuses': policy})

    def test_nebula_auth_event_receipt_and_authoritative_damage(self):
        self.join();battle=self.start().battle
        with self.db.transaction() as db:token=db.execute('SELECT token FROM raid_tokens').fetchone()[0]
        headers={'Authorization':'Bearer '+token,'nbl-battle-id':battle.battle_id,'nbl-player-id':self.players[0][:20]}
        before=raid_transport.handle(self.game,'/raid/api/get-state',headers)
        self.assertFalse(before.full_state,'Native update callback must unlock battle input')
        self.assertEqual(before.battle_state.events[0].type,1)
        self.assertEqual(before.battle_state.active_actor_count,1)
        request=p.SendBattleEventProto(battle_id=battle.battle_id,player_id=self.players[0][:20],sequence_id=self.clock,
            battle_event=p.BattleEventProto(type=4,actor_id=self.players[0][:20],attack=p.RaidPbsAttack(attack_type=1)))
        first=raid_transport.handle(self.game,'/raid/api/send-player-event',headers,request.SerializeToString())
        self.assertEqual(first.result,1);self.assertEqual(first,raid_transport.handle(self.game,'/raid/api/send-player-event',headers,request.SerializeToString()))
        request.battle_event.attack.attack_type=2
        with self.assertRaises(RpcError):raid_transport.handle(self.game,'/raid/api/send-player-event',headers,request.SerializeToString())
        self.clock+=1000;after=raid_transport.handle(self.game,'/raid/api/get-state',headers)
        boss=before.battle_state.actors['raid-boss'].active_pokemon_id
        self.assertLess(after.battle_state.pokemon[boss].resources[1].quantity,before.battle_state.pokemon[boss].resources[1].quantity)
        with self.assertRaises(RpcError):raid_transport.handle(self.game,'/raid/api/get-state',headers|{'Authorization':'wrong'})

    def test_bonus_sighting_registration_is_owned_repeat_safe_and_survives_misses(self):
        # Finish the historical import before creating this new encounter.
        self.call(4,p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
        _,reward=self.win();encounter=reward.battle_results.post_raid_encounter[0]
        request=p.GetRaidEncounterDetailsProto(gym_id='gym',raid_seed=self.seed)
        self.assertEqual(self.call(3094,request,p.GetRaidEncounterDetailsOutProto,index=1).result,4)
        self.assertEqual(self.call(3094,request,p.GetRaidEncounterDetailsOutProto).result,1)
        miss=p.CatchPokemonProto(encounter_id=encounter.encounter_id,spawn_point_guid=encounter.spawnpoint_id,pokeball=5,hit_pokemon=False)
        self.assertEqual(self.call(103,miss,p.CatchPokemonOutProto).status,4)
        self.game=Game(self.config,Database(self.db.path))
        for _ in range(3):self.assertEqual(self.call(3094,request,p.GetRaidEncounterDetailsOutProto).throws_remaining,11)
        key='spawn:'+str(encounter.encounter_id)
        with self.db.transaction() as db:
            rows=db.execute('SELECT species,form,caught FROM pokedex_observations WHERE player_id=? AND source_key=?',(self.players[0],key)).fetchall()
            self.assertEqual([tuple(r) for r in rows],[(16,encounter.pokemon.pokemon_display.form,0)])
            self.assertFalse(db.execute('SELECT 1 FROM pokedex_observations WHERE player_id=? AND source_key=?',(self.players[1],key)).fetchone())
        miss.hit_pokemon=True
        self.assertEqual(self.call(103,miss,p.CatchPokemonOutProto).status,1)
        with self.db.transaction() as db:
            rows=db.execute('SELECT caught FROM pokedex_observations WHERE player_id=? AND source_key=?',(self.players[0],key)).fetchall()
            self.assertEqual([r[0] for r in rows],[1])

    def test_background_polls_do_not_rollback_a_gameplay_batch(self):
        self.counter+=1;req=wire.RpcRequest(rpc_id=self.counter)
        req.requests.add(method=815,body=p.AwardFreeRaidTicketProto(gym_id='gym',player_lat_degrees=52.37,player_lng_degrees=4.89).SerializeToString())
        req.requests.add(method=1206);req.requests.add(method=1304)
        out=self.game.rpc(req,self.tickets[0])
        self.assertEqual(p.AwardFreeRaidTicketOutProto.FromString(out.payloads[0].payload).result,1)
        self.assertIn(p.GetRocketBalloonOutProto.FromString(out.payloads[1].payload).status,(3,4))
        self.assertEqual(p.GetVsSeekerStatusOutProto.FromString(out.payloads[2].payload).result,3)
        self.game=Game(self.config,Database(self.db.path))
        again=self.call(815,p.AwardFreeRaidTicketProto(gym_id='gym',player_lat_degrees=52.37,player_lng_degrees=4.89),p.AwardFreeRaidTicketOutProto)
        self.assertEqual(again.result,3)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT count FROM items WHERE item=1401').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM raid_daily_passes').fetchone()[0],1)

    def test_timeout_expiry_no_rewards_and_egg_identity(self):
        with self.db.transaction() as db:
            self.assertEqual(raids.schedule(self.game,db,db.execute("SELECT * FROM forts WHERE id='gym'").fetchone())['seed'],self.seed)
        self.join();battle=self.start().battle
        self.clock=battle.battle_end_ms+1
        out=self.call(3010,p.CompleteRaidBattleProto(gym_id='gym',battle_id=battle.battle_id),p.CompleteRaidBattleOutProto)
        self.assertFalse(out.is_victory)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM encounters').fetchone()[0],0)
            self.assertIsNone(db.execute('SELECT reward FROM raid_members').fetchone()[0])
            self.assertEqual(db.execute('SELECT xp FROM players WHERE id=?',(self.players[0],)).fetchone()[0],10000)
        self.clock+=4000000
        self.assertEqual(self.join().result,3)

    def test_party_defeat_native_reason_and_relobby_keeps_clock(self):
        self.join();battle=self.start().battle
        self.faint_party(battle);self.replacement()
        with self.db.transaction() as db:
            b=db.execute('SELECT * FROM raid_battles').fetchone();state=json.loads(b['state'])
            snapshot=raid_transport.snapshot(b,state)
            self.assertEqual(snapshot.battle_state.events[-1].battle_end.reason,3)
            self.assertEqual(snapshot.battle_state.events[-1].battle_end.result,2)
        self.join();resumed=self.start((2,));self.assertEqual(resumed.result,1)
        self.assertEqual(resumed.battle.battle_end_ms,battle.battle_end_ms)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM raid_pass_receipts').fetchone()[0],1)
            self.assertEqual(json.loads(db.execute('SELECT state FROM raid_battles').fetchone()[0])['results'],'')
            b=db.execute('SELECT * FROM raid_battles').fetchone();state=json.loads(b['state'])
            self.assertEqual(state['generation'],1);self.assertEqual(len(state['previous_parties']),1)
            self.assertEqual(raid_transport.snapshot(b,state).battle_state.events[0].serial,1)

    def test_validation_rejects_early_wrong_party_and_wrong_location(self):
        self.join()
        early=self.call(165,p.StartRaidBattleProto(gym_id='gym',raid_seed=self.seed,attacking_pokemon_id=[1],player_lat_degrees=52.37,player_lng_degrees=4.89),p.StartRaidBattleOutProto)
        self.assertEqual(early.result,9)
        self.assertEqual(self.start((1,1)).result,5)
        self.assertEqual(self.start((999,)).result,5)
        away=self.call(165,p.StartRaidBattleProto(gym_id='gym',raid_seed=self.seed,attacking_pokemon_id=[1],player_lat_degrees=0,player_lng_degrees=0),p.StartRaidBattleOutProto)
        self.assertEqual(away.result,7)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM raid_pass_receipts').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM raid_battles').fetchone()[0],0)

    def test_actual_start_and_party_wire_tags(self):
        for index in range(100):
            self.assertTrue(all(0<=v<9 for v in raids.lobby_code({'battle_id':str(index)})))
        req=p.StartRaidBattleProto(gym_id='g',raid_seed=3,lobby_id=[1],attacking_pokemon_id=[0x0102030405060708],player_lat_degrees=1)
        self.assertIn(bytes.fromhex('2a080807060504030201'),req.SerializeToString())
        self.assertEqual(req.DESCRIPTOR.fields_by_name['lobby_id'].number,4)
        self.assertEqual(req.DESCRIPTOR.fields_by_name['player_lat_degrees'].number,6)
        self.assertEqual(p.LobbyProto.DESCRIPTOR.fields_by_name['rvn_connection'].number,16)
        self.assertEqual(p.GetRaidDetailsOutProto.DESCRIPTOR.fields_by_name['rvn_battle_completed'].number,21)

    def test_storage_capacity_and_exhausted_balls_survive_restart(self):
        _,reward=self.win();enc=reward.battle_results.post_raid_encounter[0]
        self.config['pokemon_capacity']=1
        catch=p.CatchPokemonProto(encounter_id=enc.encounter_id,spawn_point_guid=enc.spawnpoint_id,pokeball=5,hit_pokemon=True)
        with self.assertRaises(RpcError):self.call(103,catch,p.CatchPokemonOutProto)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT balls FROM raid_members').fetchone()[0],12)
        self.config['pokemon_capacity']=300;catch.hit_pokemon=False
        for _ in range(12):self.assertEqual(self.call(103,catch,p.CatchPokemonOutProto).status,4)
        self.game=Game(self.config,Database(self.db.path))
        with self.db.transaction() as db:
            m=db.execute('SELECT * FROM raid_members').fetchone();self.assertEqual(m['balls'],0);self.assertEqual(raids.state_of(db,m),9)
            self.assertEqual(db.execute('SELECT count(*) FROM pokemon').fetchone()[0],1)
        with self.assertRaises(RpcError):self.call(103,catch,p.CatchPokemonOutProto)

    def test_raid_research_progress_is_settled_once(self):
        spec={'template':'LIBRE_TEST_RAID','description':'Win a local raid','event':'raid','target':2,'slot':0,'rewards':[{'type':'xp','amount':100}]}
        with self.db.transaction() as db:
            db.execute('INSERT INTO research_tasks(player_id,id,definition,created_ms,updated_ms) VALUES(?,?,?,?,?)',(self.players[0],'raid-test',json.dumps(spec),self.clock,self.clock))
        req,_=self.win();self.call(3010,req,p.CompleteRaidBattleOutProto)
        self.game=Game(self.config,Database(self.db.path));self.call(3010,req,p.CompleteRaidBattleOutProto)
        with self.db.transaction() as db:
            self.assertEqual(db.execute("SELECT progress FROM research_tasks WHERE id='raid-test'").fetchone()[0],1)
            self.assertEqual(raids.history(db,self.players[0],'gym').total_completed_raids,1)

    def test_replacement_party_has_fresh_native_events_and_sequence_cursor(self):
        self.join();battle=self.start().battle
        with self.db.transaction() as db:token=db.execute('SELECT token FROM raid_tokens').fetchone()[0]
        headers={'Authorization':'Bearer '+token,'nbl-battle-id':battle.battle_id,'nbl-player-id':self.players[0][:20]}
        event=p.SendBattleEventProto(battle_id=battle.battle_id,player_id=self.players[0][:20],sequence_id=self.clock,
            battle_event=p.BattleEventProto(type=4,actor_id=self.players[0][:20],attack=p.RaidPbsAttack(attack_type=1)))
        self.assertEqual(raid_transport.handle(self.game,'/raid/api/send-player-event',headers,event.SerializeToString()).result,1)
        self.faint_party(battle);self.replacement();self.join()
        self.assertEqual(self.start((2,)).result,1)
        with self.assertRaises(RpcError):raid_transport.handle(self.game,'/raid/api/get-state',headers)
        headers=self.headers(battle)
        event.battle_event.ClearField('attack');event.battle_event.type=5;event.battle_event.dodge.direction=1
        self.assertEqual(raid_transport.handle(self.game,'/raid/api/send-player-event',headers,event.SerializeToString()).result,1)
        state=raid_transport.handle(self.game,'/raid/api/get-state',headers)
        self.assertEqual(state.battle_state.actors[self.players[0][:20]].active_pokemon_id,2)
        self.assertNotIn(1,state.battle_state.pokemon)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM raid_pass_receipts').fetchone()[0],1)

    def test_timeout_retry_same_seed_new_session_one_pass_and_one_reward(self):
        first_lobby=self.join().lobby;first=self.start().battle;old_headers=self.headers(first)
        self.clock=first.battle_end_ms+1
        complete=p.CompleteRaidBattleProto(gym_id='gym',battle_id=first.battle_id)
        self.assertFalse(self.call(3010,complete,p.CompleteRaidBattleOutProto).is_victory)
        details=self.call(163,p.GetRaidDetailsProto(gym_id='gym',raid_seed=self.seed,player_lat_degrees=52.37,player_lng_degrees=4.89),p.GetRaidDetailsOutProto)
        self.assertFalse(details.HasField('lobby'));self.assertTrue(details.ticket_used);self.assertTrue(details.player_can_join_lobby)
        # Owned medicine is unnecessary in this fixture: restore its test party.
        self.replacement()
        second_lobby=self.join().lobby
        self.assertNotEqual(first_lobby.raid_battle_id,second_lobby.raid_battle_id)
        self.assertNotEqual(first_lobby.rvn_connection.token,second_lobby.rvn_connection.token)
        self.game=Game(self.config,Database(self.db.path))
        self.assertEqual(self.join().lobby,second_lobby)
        second=self.start((2,)).battle
        self.assertGreater(second.battle_end_ms,first.battle_end_ms)
        self.assertEqual(second.defender.active_pokemon.current_health,60)
        with self.assertRaises(RpcError):raid_transport.handle(self.game,'/raid/api/get-state',old_headers)
        with self.assertRaises(RpcError):raid_transport.handle(self.game,'/raid/api/get-state',old_headers|{'nbl-battle-id':second.battle_id})
        self.assertEqual(self.call(166,p.AttackRaidBattleProto(gym_id='gym',battle_id=first.battle_id),p.AttackRaidBattleOutProto).result,3)
        for _ in range(40):
            self.clock+=1100;req=p.AttackRaidBattleProto(gym_id='gym',battle_id=second.battle_id)
            req.attacker_actions.add(type=1,action_start_ms=self.clock,active_pokemon_id=2)
            out=self.call(166,req,p.AttackRaidBattleOutProto)
            if out.battle_update.battle_log.state==2:break
        victory=p.CompleteRaidBattleProto(gym_id='gym',battle_id=second.battle_id)
        reward=self.call(3010,victory,p.CompleteRaidBattleOutProto);self.assertTrue(reward.is_victory)
        self.assertFalse(self.call(3010,complete,p.CompleteRaidBattleOutProto).is_victory,'Old losses cannot inherit later victory rewards')
        self.game=Game(self.config,Database(self.db.path))
        self.assertEqual(self.call(3010,victory,p.CompleteRaidBattleOutProto),reward)
        self.assertEqual(self.join().result,4)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM raid_battles').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM raid_pass_receipts').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT xp FROM players WHERE id=?',(self.players[0],)).fetchone()[0],11000)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM encounters').fetchone()[0],1)

    def test_surrender_exit_new_attempt_old_code_rejected(self):
        lobby=self.join().lobby;first=self.start().battle;headers=self.headers(first)
        self.call(160,p.LeaveLobbyProto(gym_id='gym',raid_seed=self.seed),p.LeaveLobbyOutProto)
        with self.assertRaises(RpcError):raid_transport.handle(self.game,'/raid/api/get-state',headers)
        fresh=self.join().lobby;self.assertNotEqual(fresh.raid_battle_id,first.battle_id)
        self.clock+=21000
        if list(lobby.lobby_id)!=list(fresh.lobby_id):
            req=p.StartRaidBattleProto(gym_id='gym',raid_seed=self.seed,lobby_id=lobby.lobby_id,attacking_pokemon_id=[1],player_lat_degrees=52.37,player_lng_degrees=4.89)
            self.assertEqual(self.call(165,req,p.StartRaidBattleOutProto).result,9)
        self.assertEqual(self.start().result,1)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM raid_pass_receipts').fetchone()[0],1)

    def test_reward_encounter_uses_configured_iv_floor(self):
        self.win()
        with self.db.transaction() as db:
            row=db.execute('SELECT spawn_id FROM raid_members WHERE seed=? AND player_id=?',
                           (self.seed,self.players[0])).fetchone()
            data=db.execute('SELECT data FROM spawn_attributes WHERE spawn_id=?',(row['spawn_id'],)).fetchone()['data']
        pokemon=p.PokemonProto.FromString(data)
        self.assertTrue(all(10<=value<=15 for value in
                            (pokemon.individual_attack,pokemon.individual_defense,pokemon.individual_stamina)))

    def test_frozen_schedule_rotation_and_development_balance_guard(self):
        with self.db.transaction() as db:
            fort=db.execute("SELECT * FROM forts WHERE id='gym'").fetchone()
            original=raids.schedule(self.game,db,fort)
            self.game.raids['battle_seconds']=321
            self.assertEqual(raids.rules(self.game,original)['battle_seconds'],120)
            del self.game.raids['epoch_ms']
            self.clock=(self.clock//7200000+1)*7200000
            new=raids.schedule(self.game,db,fort);self.assertNotEqual(new['seed'],original['seed'])
            self.assertEqual(new['battle_ms']-new['spawn_ms'],1000)
            self.clock=new['end_ms'];self.assertIsNone(raids.schedule(self.game,db,fort))
        for bad in ({'participation_limit':2},{'daily_free_passes':2},{'development_only':True}):
            config=self.config|{'environment':'live','raids':self.config['raids']|bad}
            with self.assertRaises(ValueError):Game(config,self.db)

    def test_v13_migration_keeps_history_receipts_and_legacy_session(self):
        self.join();battle=self.start().battle;headers=self.headers(battle)
        event=p.SendBattleEventProto(battle_id=battle.battle_id,player_id=self.players[0][:20],sequence_id=self.clock,battle_event=p.BattleEventProto(type=1))
        raid_transport.handle(self.game,'/raid/api/send-player-event',headers,event.SerializeToString())
        db=self.db.connect()
        try:
            db.execute('PRAGMA foreign_keys=OFF')
            db.executescript("""CREATE TABLE raid_battles_old(id TEXT PRIMARY KEY,seed INTEGER UNIQUE NOT NULL REFERENCES raids(seed),player_id TEXT NOT NULL REFERENCES players(id),state TEXT NOT NULL,end_ms INTEGER NOT NULL,settled INTEGER NOT NULL DEFAULT 0);
                INSERT INTO raid_battles_old SELECT id,seed,player_id,state,end_ms,settled FROM raid_battles;
                DROP TABLE raid_battles;ALTER TABLE raid_battles_old RENAME TO raid_battles;
                DELETE FROM raid_combat_sessions;DELETE FROM schema_version WHERE version IN (14,15);
                CREATE TABLE raid_event_receipts_old(battle_id TEXT NOT NULL REFERENCES raid_battles(id),sequence INTEGER NOT NULL,request_hash TEXT NOT NULL,response BLOB NOT NULL,PRIMARY KEY(battle_id,sequence));
                INSERT INTO raid_event_receipts_old SELECT battle_id,sequence,request_hash,response FROM raid_event_receipts;
                DROP TABLE raid_event_receipts;ALTER TABLE raid_event_receipts_old RENAME TO raid_event_receipts;""")
        finally:db.close()
        self.game=Game(self.config,Database(self.db.path))
        self.assertTrue(Path(str(self.db.path)+'.before-v14').exists())
        raid_transport.handle(self.game,'/raid/api/get-state',headers)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM raid_event_receipts').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM raid_combat_sessions').fetchone()[0],1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            db.execute("INSERT INTO raid_battles(id,seed,player_id,state,end_ms) SELECT 'second',seed,player_id,state,end_ms FROM raid_battles LIMIT 1")

    def test_parallel_first_reward_claims_are_serialized(self):
        from concurrent.futures import ThreadPoolExecutor
        req,_=self.win(claim=False)
        def claim(index):
            rpc=wire.RpcRequest(rpc_id=9000+index);rpc.requests.add(method=3010,body=req.SerializeToString())
            return p.CompleteRaidBattleOutProto.FromString(self.game.rpc(rpc,self.tickets[0]).payloads[0].payload)
        with ThreadPoolExecutor(max_workers=4) as pool:out=list(pool.map(claim,range(4)))
        self.assertTrue(all(v.is_victory for v in out));self.assertTrue(all(v==out[0] for v in out))
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM encounters').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT xp FROM players WHERE id=?',(self.players[0],)).fetchone()[0],11000)
            self.assertEqual(db.execute('SELECT score FROM gym_badges').fetchone()[0],1000)

    def test_finished_session_polls_cannot_reapply_healed_injuries(self):
        self.join();battle=self.start().battle;headers=self.headers(battle)
        self.faint_party(battle)
        from dittobuffer.core.management import owned,update_owned
        with self.db.transaction() as db:
            mon=owned(db,self.players[0],1);self.assertEqual(mon.stamina,0)
            mon.stamina=mon.max_stamina;update_owned(self.game,db,self.players[0],mon)
        raid_transport.handle(self.game,'/raid/api/get-state',headers)
        self.clock=battle.battle_end_ms+1
        self.call(3011,p.SyncBattleInventoryProto(),p.SyncBattleInventoryOutProto)
        with self.db.transaction() as db:self.assertEqual(owned(db,self.players[0],1).stamina,mon.max_stamina)

    def test_native_inventory_sync_prepares_relobby_once_without_resetting_battle(self):
        self.join();battle=self.start().battle;headers=self.headers(battle)
        self.faint_party(battle);self.replacement()
        self.assertEqual(self.call(3011,p.SyncBattleInventoryProto(),p.SyncBattleInventoryOutProto).result,1)
        first=self.join().lobby
        self.assertEqual(first.raid_battle_id,battle.battle_id)
        self.assertEqual(first.raid_battle_end_ms,battle.battle_end_ms)
        self.call(3011,p.SyncBattleInventoryProto(),p.SyncBattleInventoryOutProto)
        self.assertEqual(self.join().lobby,first)
        with self.assertRaises(RpcError):raid_transport.handle(self.game,'/raid/api/get-state',headers)
        details=self.call(163,p.GetRaidDetailsProto(gym_id='gym',raid_seed=self.seed,player_lat_degrees=52.37,player_lng_degrees=4.89),p.GetRaidDetailsOutProto)
        self.assertFalse(details.rvn_battle_completed);self.assertFalse(details.HasField('raid_battle'))
        resumed=self.start((2,));self.assertEqual(resumed.result,1)
        self.assertEqual(resumed.battle.battle_end_ms,battle.battle_end_ms)
        self.assertEqual(resumed.battle.defender.active_pokemon.current_health,60)
        self.assertEqual(self.call(166,p.AttackRaidBattleProto(gym_id='gym',battle_id=battle.battle_id),p.AttackRaidBattleOutProto).result,4)

    def test_project_rules_file_overrides_and_live_profile_separation(self):
        relative=Path(self.tmp.name).relative_to(ROOT)/'rules.json'
        (ROOT/relative).write_text(json.dumps({'raids':{'enabled':True,'development_only':True,'pools':self.config['raids']['pools'],'premier_balls':7}}),encoding='utf8')
        cfg=self.config|{'raids':{'rules_file':relative.as_posix(),'epoch_ms':self.clock-2000,'egg_seconds':1,'premier_balls':9}}
        loaded=Game(cfg,self.db);self.assertEqual(loaded.raids['premier_balls'],9)
        self.assertEqual(loaded.raids['pools'][0]['species'],16)
        with self.assertRaises(ValueError):Game(cfg|{'environment':'live'},self.db)
        with self.assertRaises(ValueError):Game(self.config|{'raids':{'rules_file':'../outside.json'}},self.db)
        live=Game(self.config|{'environment':'live','raids':{'rules_file':'src/dittobuffer/gameplay/raid-pools.live.example.json'}},self.db)
        self.assertFalse(live.raids['enabled']);self.assertFalse(live.raids['development_only'])

    def test_native_quit_is_durable_surrender_and_new_attempt_keeps_pass(self):
        self.join();battle=self.start().battle;headers=self.headers(battle)
        request=p.SendBattleEventProto(battle_id=battle.battle_id,player_id=self.players[0][:20],sequence_id=self.clock,battle_event=p.BattleEventProto(type=3))
        self.assertEqual(raid_transport.handle(self.game,'/raid/api/send-player-event',headers,request.SerializeToString()).result,1)
        self.game=Game(self.config,Database(self.db.path))
        frame=raid_transport.handle(self.game,'/raid/api/get-state',headers)
        self.assertEqual(frame.battle_state.events[-1].battle_end.reason,5)
        fresh=self.join().lobby;self.assertNotEqual(fresh.raid_battle_id,battle.battle_id)
        self.assertEqual(self.start().result,1)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM raid_pass_receipts').fetchone()[0],1)

    def test_native_second_charged_slot_projection_validation_and_retry(self):
        with self.db.transaction() as db:
            db.execute('UPDATE players SET stardust=100000 WHERE id=?',(self.players[0],))
            db.execute('INSERT INTO candy VALUES(?,?,100)',(self.players[0],self.game.species[25]['family']))
        unlocked=self.call(1004,p.UnlockPokemonMoveProto(pokemon_id=1),p.UnlockPokemonMoveOutProto)
        self.assertEqual(unlocked.result,1);move=unlocked.unlocked_pokemon.move3
        self.join();battle=self.start().battle;headers=self.headers(battle)
        with self.db.transaction() as db:
            row=db.execute('SELECT * FROM raid_battles WHERE id=?',(battle.battle_id,)).fetchone();state=json.loads(row['state'])
            state['party'][0]['energy']=100;raids.persist(db,battle.battle_id,state)
        projection=raid_transport.pokemon(state['party'][0])
        self.assertEqual(projection.moves[p.BattlePokemonProto.MoveType_Charge2].move,move)
        request=p.SendBattleEventProto(battle_id=battle.battle_id,player_id=self.players[0][:20],sequence_id=self.clock,
            battle_event=p.BattleEventProto(type=4,attack=p.RaidPbsAttack(attack_type=3,move=move,source_pokemon_id=1)))
        result=raid_transport.handle(self.game,'/raid/api/send-player-event',headers,request.SerializeToString())
        self.assertEqual(result.result,1)
        self.assertEqual(result,raid_transport.handle(self.game,'/raid/api/send-player-event',headers,request.SerializeToString()))
        with self.db.transaction() as db:state=json.loads(db.execute('SELECT state FROM raid_battles WHERE id=?',(battle.battle_id,)).fetchone()[0])
        from dittobuffer.gameplay.battle_master import MOVES
        self.assertEqual(state['party'][0]['energy'],100+MOVES[move][3])
        self.assertEqual(sum(p.BattleActionProto.FromString(bytes.fromhex(v)).type==11 for v in state['events']),1)
        request.sequence_id+=1;request.battle_event.attack.move=999999
        with self.assertRaises(RpcError):raid_transport.handle(self.game,'/raid/api/send-player-event',headers,request.SerializeToString())
