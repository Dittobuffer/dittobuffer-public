"""Mega raids reuse the ordinary engine and atomic raid reward receipt."""
import json
import unittest
from unittest.mock import patch
import test_raids as regular
from dittobuffer.gameplay import mega, raids, raid_transport
from dittobuffer.gameplay.game import Game,p,wire,RpcError
from dittobuffer.core.database import Database


class MegaRaidTests(unittest.TestCase):
    setUp=regular.RaidTests.setUp
    tearDown=regular.RaidTests.tearDown
    call=regular.RaidTests.call
    join=regular.RaidTests.join
    start=regular.RaidTests.start
    win=regular.RaidTests.win
    headers=regular.RaidTests.headers
    replacement=regular.RaidTests.replacement
    faint_party=regular.RaidTests.faint_party

    def configure(self):
        boss=dict(species=3,tier=6,mega_evolution=1,weight=1,hp=20,cp=1000,xp=1000,dust=500,
            items={'101':2},reward_policy='Isolated source-cost delivery, not official raid loot',
            mega_energy_rewards=[dict(max_seconds=30,amount=200),dict(max_seconds=300,amount=100)])
        self.config['raids']['pools']=[boss]
        self.game=Game(self.config,self.db)
        with self.db.transaction() as db:
            db.execute('DELETE FROM raids')
            self.seed=raids.schedule(self.game,db,db.execute("SELECT * FROM forts WHERE id='gym'").fetchone())['seed']
        return boss

    def test_mega_boss_base_bonus_one_energy_grant_and_restart(self):
        self.configure()
        with self.db.transaction() as db:
            row=db.execute('SELECT * FROM raids').fetchone()
            boss=p.PokemonProto.FromString(row['pokemon'])
            self.assertEqual(boss.pokemon_display.current_temp_evolution,1)
            self.assertEqual(boss.pokemon_id,3)
            db.execute('INSERT INTO items VALUES(?,1401,1)',(self.players[0],))
        req,out=self.win()
        self.assertTrue(out.is_victory)
        self.assertEqual(out.battle_results.participation[-1].speed_completion_mega_resource,200)
        bonus=out.battle_results.post_raid_encounter[0]
        self.assertEqual((bonus.pokemon.pokemon_id,bonus.pokemon.pokemon_display.current_temp_evolution),(3,0))
        self.game=Game(self.config,Database(self.db.path))
        self.assertEqual(self.call(3010,req,p.CompleteRaidBattleOutProto),out)
        caught=self.call(103,p.CatchPokemonProto(encounter_id=bonus.encounter_id,spawn_point_guid=bonus.spawnpoint_id,pokeball=5,hit_pokemon=True),p.CatchPokemonOutProto)
        self.assertEqual(caught.status,1)
        with self.db.transaction() as db:
            self.assertEqual(mega.balance(db,self.players[0],3,1),200)
            self.assertEqual(db.execute('SELECT count FROM items WHERE item=1401').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count FROM items WHERE item=1402').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM mega_ledger WHERE source LIKE 'raid:%'").fetchone()[0],1)
            owned=p.PokemonProto.FromString(db.execute('SELECT data FROM pokemon WHERE id=?',(caught.captured_pokemon_id,)).fetchone()[0])
            self.assertEqual((owned.pokemon_id,owned.pokemon_display.current_temp_evolution),(3,0))

    def test_interrupted_reward_rollback_then_retry_and_time_based_energy(self):
        self.configure();req,_=self.win(claim=False)
        with self.db.transaction() as db:
            row=db.execute('SELECT * FROM raid_battles').fetchone();state=json.loads(row['state'])
            state['clock']=state['start']+45000
            raids.persist(db,row['id'],state)
        self.counter+=1;batch=wire.RpcRequest(rpc_id=self.counter)
        batch.requests.add(method=3010,body=req.SerializeToString());batch.requests.add(method=999999)
        with self.assertRaises(RpcError):self.game.rpc(batch,self.tickets[0])
        with self.db.transaction() as db:
            self.assertEqual(mega.balance(db,self.players[0],3,1),0)
            self.assertIsNone(db.execute('SELECT reward FROM raid_members').fetchone()[0])
        self.game=Game(self.config,Database(self.db.path))
        out=self.call(3010,req,p.CompleteRaidBattleOutProto)
        self.assertEqual(out.battle_results.participation[-1].speed_completion_mega_resource,100)
        self.assertEqual(self.call(3010,req,p.CompleteRaidBattleOutProto),out)

    def test_invalid_mega_selection_and_missing_reward_source_are_rejected(self):
        for change in ({'species':16},{'mega_evolution':4},{'mega_energy_rewards':[]},{'reward_policy':''}):
            self.configure();self.config['raids']['pools'][0].update(change)
            with self.assertRaises(ValueError):Game(self.config,self.db)

    def test_defeat_never_grants_energy(self):
        self.configure();self.join();battle=self.start().battle
        self.clock=battle.battle_end_ms+1
        out=self.call(3010,p.CompleteRaidBattleProto(gym_id='gym',battle_id=battle.battle_id),p.CompleteRaidBattleOutProto)
        self.assertFalse(out.is_victory)
        with self.db.transaction() as db:self.assertEqual(mega.balance(db,self.players[0],3,1),0)

    def test_mega_relobby_and_restart_keep_boss_clock_and_one_pass(self):
        self.configure();self.join();battle=self.start().battle
        self.faint_party(battle);self.replacement()
        with self.db.transaction() as db:
            state=json.loads(db.execute('SELECT state FROM raid_battles').fetchone()[0])
            boss_hp=state['defender']['hp']
            boss=p.PokemonProto.FromString(bytes.fromhex(state['defender']['pokemon']))
            self.assertEqual((state['status'],boss.pokemon_display.current_temp_evolution),(3,1))
        self.game=Game(self.config,Database(self.db.path))
        self.join();resumed=self.start((2,))
        self.assertEqual(resumed.result,1)
        self.assertEqual((resumed.battle.battle_id,resumed.battle.battle_end_ms),(battle.battle_id,battle.battle_end_ms))
        with self.db.transaction() as db:
            state=json.loads(db.execute('SELECT state FROM raid_battles').fetchone()[0])
            self.assertEqual((state['defender']['hp'],state['generation']),(boss_hp,1))
            self.assertEqual(db.execute('SELECT count(*) FROM raid_pass_receipts').fetchone()[0],1)
            self.assertEqual(mega.balance(db,self.players[0],3,1),0)
        self.clock+=1100
        attack=p.SendBattleEventProto(battle_id=battle.battle_id,player_id=self.players[0][:20],sequence_id=self.clock,
            battle_event=p.BattleEventProto(type=4,actor_id=self.players[0][:20],attack=p.RaidPbsAttack(attack_type=1)))
        self.assertEqual(raid_transport.handle(self.game,'/raid/api/send-player-event',self.headers(battle),attack.SerializeToString()).result,1)
