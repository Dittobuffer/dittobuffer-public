import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dittobuffer.core.database import Database
from dittobuffer.gameplay.game import Game,ROOT,p,wire,now_ms
from dittobuffer.gameplay.species import make_pokemon
from dittobuffer.core.management import owned
from dittobuffer.gameplay import gym_battle, gyms

class GymTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds')
        self.config=json.loads((ROOT/'config/development.json').read_text(encoding='utf8'))
        self.config['gyms']={'motivation_loss':1,'coin_minutes':.01}
        self.db=Database(Path(self.tmp.name)/'gyms.sqlite3');self.game=Game(self.config,self.db)
        self.tickets=[self.game.authenticate(wire.AuthRequest(auth_id='gym-'+str(i))).ticket for i in range(3)]
        self.players=[];self.counter=0;self.clock=now_ms()
        self.patches=[patch(name,lambda:self.clock) for name in ('dittobuffer.gameplay.game.now_ms','dittobuffer.gameplay.gyms.now_ms','dittobuffer.gameplay.gym_battle.now_ms')]
        for item in self.patches:item.start()
        with self.db.transaction() as db:
            db.execute("INSERT INTO forts VALUES('gym',0,'Gym',52.37,4.89)")
            for index,ticket in enumerate(self.tickets):
                player=db.execute('SELECT player_id FROM sessions WHERE ticket=?',(ticket,)).fetchone()[0];self.players.append(player)
                db.execute('UPDATE players SET xp=10000 WHERE id=?',(player,))
                for j,species in enumerate((7,1,4)):
                    spawn=100+index*10+j
                    db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',(spawn,str(spawn),species,52.37,4.89,self.clock+86400000))
                    mon=make_pokemon(p,self.game.species[species],species,15,(10,10,10))
                    mon.id=db.execute('INSERT INTO pokemon(player_id,spawn_id,species,data) VALUES(?,?,?,?)',(player,spawn,species,b'')).lastrowid
                    db.execute('UPDATE pokemon SET data=? WHERE id=?',(mon.SerializeToString(),mon.id))
        self.call(405,p.SetPlayerTeamProto(team=1),p.SetPlayerTeamOutProto,0)
        self.call(405,p.SetPlayerTeamProto(team=2),p.SetPlayerTeamOutProto,1)
        self.call(405,p.SetPlayerTeamProto(team=1),p.SetPlayerTeamOutProto,2)

    def tearDown(self):
        for item in self.patches:item.stop()
        self.tmp.cleanup()

    def call(self,method,message,response,index=0):
        self.counter+=1;req=wire.RpcRequest(rpc_id=self.counter)
        req.requests.add(method=method,body=message.SerializeToString())
        return response.FromString(self.game.rpc(req,self.tickets[index]).payloads[0].payload)

    def deploy(self,pid=1,index=0):
        return self.call(155,p.GymDeployProto(fort_id='gym',pokemon_id=pid,player_lat_degrees=52.37,player_lng_degrees=4.89),p.GymDeployOutProto,index)

    def start(self,party=(5,6)):
        self.assertEqual(self.deploy().result,1)
        out=self.call(157,p.GymStartSessionProto(gym_id='gym',attacking_pokemon_id=party,defending_pokemon_id=1,
            player_lat_degrees=52.37,player_lng_degrees=4.89),p.GymStartSessionOutProto,1)
        self.assertEqual(out.result,1);self.clock=out.battle.battle_start_ms
        return out.battle

    def attack(self,battle,kind=None,**kwargs):
        request=p.GymBattleAttackProto(gym_id='gym',battle_id=battle.battle_id,player_lat_degrees=52.37,player_lng_degrees=4.89,timestamp_ms=self.clock)
        if kind:request.attacker_actions.add(type=kind,action_start_ms=self.clock,**kwargs)
        return self.call(158,request,p.GymBattleAttackOutProto,1)

    def test_team_selection_level_validation_and_restart(self):
        self.assertEqual(self.call(405,p.SetPlayerTeamProto(team=3),p.SetPlayerTeamOutProto).status,2)
        with self.db.transaction() as db:
            db.execute('UPDATE trainer_teams SET team=0 WHERE player_id=?',(self.players[2],))
            db.execute('UPDATE players SET xp=0 WHERE id=?',(self.players[2],))
        self.assertEqual(self.call(405,p.SetPlayerTeamProto(team=3),p.SetPlayerTeamOutProto,2).status,3)
        self.game=Game(self.config,Database(self.db.path))
        self.assertEqual(self.call(2,p.GetPlayerProto(),p.GetPlayerOutProto,1).player.team,2)

    def test_three_round_order_and_notifications_are_persistent_and_idempotent(self):
        self.game.config['gyms'].update(motivation_loss=1/3,motivation_decay_per_hour=0)
        self.assertEqual(self.deploy(1).result,1)
        self.assertEqual(self.deploy(8,2).result,1)
        for round_index in range(3):
            for ident,next_id in ((1,8),(8,0)):
                start=self.call(157,p.GymStartSessionProto(gym_id='gym',attacking_pokemon_id=[5,6],defending_pokemon_id=ident,
                    player_lat_degrees=52.37,player_lng_degrees=4.89),p.GymStartSessionOutProto,1)
                self.assertEqual(start.result,1)
                with self.db.transaction() as db:
                    row=db.execute('SELECT * FROM gym_battles WHERE id=?',(start.battle.battle_id,)).fetchone()
                    state=json.loads(row['state']);state['status']=2;state['defender']['hp']=0
                    gym_battle.settle(self.game,db,row,state);gym_battle.persist(db,row['id'],state)
                    result=p.BattleResultsProto.FromString(bytes.fromhex(state['results']))
                    self.assertEqual(result.next_defender_pokemon_id,next_id)
                    before=db.execute('SELECT SUM(xp) FROM players').fetchone()[0]
                    gym_battle.settle(self.game,db,row,state)
                    self.assertEqual(db.execute('SELECT SUM(xp) FROM players').fetchone()[0],before)
                self.game=Game(self.config,Database(self.db.path));self.clock+=1000
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gym_defenders').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gym_returns').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gym_notifications').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pokemon').fetchone()[0],9)
        proxy=self.call(5017,p.ProxyRequestProto(action=10105),p.ProxyResponseProto)
        inbox=p.GetNotificationInboxOutProto.FromString(proxy.payload)
        self.assertEqual(len(inbox.inbox.notifications),1)
        notice=inbox.inbox.notifications[0]
        self.assertEqual(notice.category,'GYM_REMOVAL')
        self.assertEqual({v.name:v.literal for v in notice.variables}['POKEMON_ID'],'1')
        request=p.UpdateNotificationProto(notification_ids=[notice.notification_id],create_timestamp_ms=[notice.create_timestamp_ms],state=2)
        foreign=self.call(5012,p.ProxyRequestProto(action=10103,payload=request.SerializeToString()),p.ProxyResponseProto,1)
        self.assertEqual(len(p.UpdateNotificationOutProto.FromString(foreign.payload).notification_ids),0)
        self.call(5012,p.ProxyRequestProto(action=10103,payload=request.SerializeToString()),p.ProxyResponseProto)
        request.state=1 # A stale acknowledgement cannot reopen a dismissed row.
        self.call(5012,p.ProxyRequestProto(action=10103,payload=request.SerializeToString()),p.ProxyResponseProto)
        self.game=Game(self.config,Database(self.db.path))
        self.assertEqual(len(self.call(809,p.GetInboxV2Proto(),p.GetNotificationInboxOutProto).inbox.notifications),0)
        history=self.call(809,p.GetInboxV2Proto(is_history=True),p.GetNotificationInboxOutProto)
        self.assertEqual(len(history.inbox.notifications),1)
        self.assertEqual(len(history.inbox.notifications[0].labels),0)

    def test_passive_decay_never_restores_low_motivation(self):
        self.deploy()
        with self.db.transaction() as db:db.execute('UPDATE gym_defenders SET motivation=.1')
        self.clock+=3600000
        self.call(156,p.GymGetInfoProto(gym_id='gym'),p.GymGetInfoOutProto)
        with self.db.transaction() as db:self.assertAlmostEqual(db.execute('SELECT motivation FROM gym_defenders').fetchone()[0],.1)

    def test_full_six_defender_capacity_and_distinct_owners(self):
        for index,species in enumerate((7,1,4,16,10,19,13)):
            ticket=self.game.authenticate(wire.AuthRequest(auth_id='capacity-'+str(index))).ticket
            with self.db.transaction() as db:
                player=db.execute('SELECT player_id FROM sessions WHERE ticket=?',(ticket,)).fetchone()[0]
                db.execute('UPDATE players SET xp=10000 WHERE id=?',(player,))
                db.execute('UPDATE trainer_teams SET team=1 WHERE player_id=?',(player,))
                spawn=1000+index
                db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',(spawn,str(spawn),species,52.37,4.89,self.clock+86400000))
                mon=make_pokemon(p,self.game.species[species],species,15,(10,10,10))
                mon.id=db.execute('INSERT INTO pokemon(player_id,spawn_id,species,data) VALUES(?,?,?,?)',(player,spawn,species,b'')).lastrowid
                db.execute('UPDATE pokemon SET data=? WHERE id=?',(mon.SerializeToString(),mon.id))
            self.tickets.append(ticket)
            self.assertEqual(self.deploy(mon.id,len(self.tickets)-1).result,1 if index<6 else 4)
        with self.db.transaction() as db:
            self.assertEqual(len(gyms.defenders(db,'gym')),6)
            self.assertEqual(len({r['owner_id'] for r in gyms.defenders(db,'gym')}),6)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_deploy_identity_restrictions_and_duplicate_species(self):
        self.assertEqual(self.deploy().result,1)
        self.assertEqual(self.deploy().result,2)
        self.assertEqual(self.deploy(4,1).result,3)
        self.assertEqual(self.deploy(7,2).result,14)
        self.assertEqual(self.deploy(8,2).result,1)
        result=self.call(112,p.ReleasePokemonProto(pokemon_id=1),p.ReleasePokemonOutProto)
        self.assertEqual(result.status,2)
        self.game=Game(self.config,Database(self.db.path))
        details=self.call(156,p.GymGetInfoProto(gym_id='gym'),p.GymGetInfoOutProto)
        self.assertEqual(details.gym_status_and_defenders.pokemon_fort_proto.team,1)
        self.assertEqual(len(details.gym_status_and_defenders.gym_defender),2)
        with self.db.transaction() as db:
            self.assertEqual(owned(db,self.players[0],1).deployed_fort_id,'gym')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pokemon').fetchone()[0],9)

    def test_berry_consumption_replay_and_wrong_team(self):
        self.deploy()
        request=p.GymFeedPokemonProto(gym_id='gym',pokemon_id=1,item=701,starting_quantity=10,
            player_lat_degrees=52.37,player_lng_degrees=4.89)
        self.assertEqual(self.call(164,request,p.GymFeedPokemonOutProto,1).result,7)
        self.assertEqual(self.call(164,request,p.GymFeedPokemonOutProto).result,1)
        self.assertEqual(self.call(164,request,p.GymFeedPokemonOutProto).result,8)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT berries FROM gym_defenders').fetchone()[0],1)

    def test_fast_charge_damage_victory_return_and_reward_once(self):
        battle=self.start();initial=battle.defender.active_pokemon.current_health
        self.assertEqual(self.attack(battle,3).result,2) # no free charged attacks
        result=None
        for i in range(80):
            with self.db.transaction() as db:state=json.loads(db.execute('SELECT state FROM gym_battles').fetchone()[0])
            if state['status']!=1:break
            value=state['party'][state['active']]
            self.clock=max(self.clock,value['ready'])
            kind=3 if value['energy']>=50 else 1
            result=self.attack(battle,kind)
            self.assertEqual(result.result,1)
            self.clock+=1000;result=self.attack(battle)
        self.assertEqual(result.battle_update.battle_log.state,2)
        self.assertGreater(initial,0)
        with self.db.transaction() as db:
            state=json.loads(db.execute('SELECT state FROM gym_battles').fetchone()[0])
            kinds=[p.BattleActionProto.FromString(bytes.fromhex(v)).type for v in state['events']]
            self.assertIn(3,kinds);self.assertIn(8,kinds)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gym_returns').fetchone()[0],1)
            self.assertEqual(owned(db,self.players[0],1).stamina,0)
            self.assertEqual(owned(db,self.players[0],1).deployed_fort_id,'')
            xp=db.execute('SELECT xp FROM players WHERE id=?',(self.players[1],)).fetchone()[0]
        self.game=Game(self.config,Database(self.db.path));self.attack(battle)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT xp FROM players WHERE id=?',(self.players[1],)).fetchone()[0],xp)
        self.assertEqual(self.call(112,p.ReleasePokemonProto(pokemon_id=1),p.ReleasePokemonOutProto).status,1)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT defender_id,settled FROM gym_battles').fetchone()[:],(1,1))
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_old_battle_schema_migration_preserves_session(self):
        battle=self.start()
        with self.db.transaction() as db:
            ddl=db.execute("SELECT sql FROM sqlite_master WHERE name='gym_battles'").fetchone()[0]
            db.execute('ALTER TABLE gym_battles RENAME TO old_battles')
            db.execute(ddl.replace('defender_id INTEGER NOT NULL,','defender_id INTEGER NOT NULL REFERENCES pokemon(id),'))
            db.execute('INSERT INTO gym_battles SELECT * FROM old_battles')
            db.execute('DROP TABLE old_battles')
            db.execute('DELETE FROM schema_version WHERE version=10')
        self.game=Game(self.config,Database(self.db.path))
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT id FROM gym_battles').fetchone()[0],battle.battle_id)
            self.assertFalse(any(r['table']=='pokemon' for r in db.execute('PRAGMA foreign_key_list(gym_battles)')))
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_pending_damage_cannot_follow_an_outgoing_attacker(self):
        battle=self.start()
        with self.db.transaction() as db:state=json.loads(db.execute('SELECT state FROM gym_battles').fetchone()[0])
        state['ai_due']=state['end']+1
        self.assertTrue(gym_battle.schedule(self.game,state,0,1,self.clock))
        state['active']=1
        before=state['defender']['hp']
        gym_battle.advance(self.game,state,self.clock+2000)
        self.assertEqual(state['defender']['hp'],before)

    def test_invalid_timing_range_ownership_and_replayed_actions(self):
        battle=self.start();self.assertEqual(self.attack(battle,1).result,1)
        with self.db.transaction() as db:saved=db.execute('SELECT state FROM gym_battles').fetchone()[0]
        self.assertEqual(self.attack(battle,1).result,1) # exact intention retry, new RPC id
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT state FROM gym_battles').fetchone()[0],saved)
        self.assertEqual(self.attack(battle,3,duration_ms=1,energy_delta=100).result,2)
        request=p.GymBattleAttackProto(gym_id='gym',battle_id=battle.battle_id,player_lat_degrees=52.37,player_lng_degrees=4.89)
        request.attacker_actions.add(type=1,action_start_ms=self.clock+3000)
        self.assertEqual(self.call(158,request,p.GymBattleAttackOutProto,1).result,2)

    def test_party_locks_and_live_hp_checkpoint_survive_restart(self):
        battle=self.start();self.clock+=2400
        result=self.attack(battle)
        hp=result.battle_update.active_attacker.current_health
        self.assertLess(hp,battle.attacker.active_pokemon.current_health)
        self.game=Game(self.config,Database(self.db.path))
        with self.db.transaction() as db:self.assertEqual(owned(db,self.players[1],5).stamina,hp)
        out=self.call(112,p.ReleasePokemonProto(pokemon_id=5),p.ReleasePokemonOutProto,1)
        self.assertEqual(out.status,2)
        request=p.UseItemPotionProto(item=101,pokemon_id=5)
        self.assertNotEqual(self.call(113,request,p.UseItemPotionOutProto,1).result,1)

    def test_faint_reports_zero_before_swap_and_expiration_recovers(self):
        battle=self.start();self.clock+=40000
        first=self.attack(battle)
        self.assertEqual(first.battle_update.active_attacker.pokemon.id,5)
        self.assertEqual(first.battle_update.active_attacker.current_health,0)
        self.clock+=2000;second=self.attack(battle)
        self.assertEqual(second.battle_update.active_attacker.pokemon.id,6)
        swaps=[a for a in second.battle_update.battle_log.battle_actions if a.type==4]
        self.assertTrue(any(a.active_pokemon_id==5 and a.target_pokemon_id==6 for a in swaps))
        self.clock=battle.battle_end_ms+1
        self.call(2,p.GetPlayerProto(),p.GetPlayerOutProto,1)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT settled FROM gym_battles').fetchone()[0],1)

    def test_badge_and_wallet_wire_and_daily_coin_cap(self):
        self.deploy()
        self.clock+=600000
        with self.db.transaction() as db:
            row=db.execute('SELECT * FROM gym_defenders').fetchone()
            gyms.return_defender(self.game,db,row);gyms.return_defender(self.game,db,row)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gym_returns').fetchone()[0],1)
            self.assertEqual(gyms.team(db,self.players[0])['coins'],50)
        wallet=self.call(310001,p.GetAvailableSkusAndBalancesProto(),p.GetAvailableSkusAndBalancesOutProto)
        self.assertEqual(wallet.balance[0].quantity,50)
        badges=self.call(811,p.ListGymBadgesProto(),p.ListGymBadgesOutProto)
        self.assertEqual(badges.gym_badge[0].gym_badge_stats.num_deploys,1)
        profile=self.call(121,p.PlayerProfileProto(),p.PlayerProfileOutProto)
        self.assertEqual(profile.gym_badges.total,1)
        self.assertEqual(profile.gym_badges.gym_badge[0].fort_id,'gym')
        self.assertEqual(p.GymBadges(total=1).SerializeToString(),b'\x10\x01')
        self.assertEqual(self.deploy(2).result,1)
        self.clock+=600000
        with self.db.transaction() as db:
            gyms.return_defender(self.game,db,db.execute('SELECT * FROM gym_defenders').fetchone())
            self.assertEqual(gyms.team(db,self.players[0])['coins'],50)
            self.assertEqual(db.execute('SELECT coins FROM gym_returns WHERE pokemon_id=2').fetchone()[0],0)
        self.clock=(self.clock//86400000+1)*86400000
        self.assertEqual(self.deploy(3).result,1)
        self.clock+=600000
        with self.db.transaction() as db:
            gyms.return_defender(self.game,db,db.execute('SELECT * FROM gym_defenders').fetchone())
            self.assertEqual(gyms.team(db,self.players[0])['coins'],100)

    def test_dodge_substitution_and_timeout_persist(self):
        battle=self.start()
        self.clock+=1900;self.assertEqual(self.attack(battle,2).result,1)
        self.clock+=600;result=self.attack(battle)
        self.assertEqual(result.result,1)
        self.assertLess(result.battle_update.active_attacker.current_health,battle.attacker.active_pokemon.current_health)
        # Passive defender AI must be able to faint the active attacker and substitute.
        self.clock=battle.battle_end_ms
        result=self.attack(battle)
        self.assertIn(result.battle_update.battle_log.state,(3,4))
        with self.db.transaction() as db:
            state=json.loads(db.execute('SELECT state FROM gym_battles').fetchone()[0]);self.assertTrue(state['active']>0 or state.get('pending_swap') is not None)
            self.assertEqual(db.execute('SELECT settled FROM gym_battles').fetchone()[0],1)

    def test_wire_fixed64_and_server_client_balance_agree(self):
        self.assertEqual(p.GymDeployProto(pokemon_id=1).SerializeToString(),b'\x11\x01'+b'\x00'*7)
        from dittobuffer.gameplay.battle_master import MOVES
        from dittobuffer.core.master import template_bytes
        from protocol.opaque import fields
        seen=[];sequences={}
        # Check the final transmitted master, where a later legacy override
        # previously replaced Vine Whip and Sludge Bomb with dummy settings.
        for raw in template_bytes(self.config):
            data={n:v for n,k,_,v in fields(raw)}
            if 5 in data:
                sequences[data[1].decode()]=list(p.MoveSequenceSettingsProto.FromString(data[5]).sequence)
            if 4 in data:
                move=p.MoveSettingsProto.FromString(data[4]);seen.append(move.unique_id)
                self.assertEqual((move.type,move.power,move.duration_ms,move.energy_delta,move.damage_window_start_ms),MOVES[move.unique_id])
                self.assertIn(move.animation_id,(4,5))
                self.assertTrue(move.vfx_name)
        self.assertEqual(set(seen),set(MOVES));self.assertEqual(len(seen),len(set(seen)))
        from dittobuffer.gameplay.battle_master import PINNED
        self.assertEqual(sequences,PINNED['sequences'])
        for name in ('sequence_default','sequence_seq_return','sequence_seq_faint','sequence_seq_fort_faint'):
            self.assertEqual(sequences[name][-1],'sys complete')
        self.assertEqual(self.game.species[1]['types'],[12,4])
