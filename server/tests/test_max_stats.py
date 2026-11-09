"""Earned inventory counters; no native tutorial or win is precompleted."""
import json
import unittest
from types import SimpleNamespace

from dittobuffer.gameplay.game import Game,p
from dittobuffer.gameplay import max_stats
from tests.test_max_battle import MaxBattleTests as Base
from tests.test_max_groups import MaxGroupTests as Groups


class MaxStatsTests(unittest.TestCase):
    setUp_resources=Base.setUp_resources
    setUp=Base.setUp
    tearDown=Base.tearDown
    call=Base.call
    create_owned=Base.create_owned
    join=Base.join
    prepare=Base.prepare
    start=Base.start
    begin=Base.begin
    room=Base.room
    combat=Base.combat
    headers=Base.headers
    state=Base.state
    event=Base.event
    attack=Base.attack
    win=Base.win
    complete=Base.complete
    gigantamax_fixture=Groups.gigantamax_fixture

    def stats(self,index=0):
        out=self.call(4,p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto,index)
        items=[p.HoloInventoryItemProto.FromString(v.item) for v in out.inventory_delta.inventory_item]
        return next(v.player_stats for v in items if v.HasField('player_stats'))

    def counts(self,index=0):
        stats=self.stats(index)
        return stats.num_bread_battles_entered,stats.num_bread_battles_won,stats.num_bread_battles_dough_won

    def test_literal_native_max_stat_wire_tags(self):
        # ARM64 writer 0x8BA5F54: offsets178/17C/180 use tags80/81/82.
        stats=p.PlayerStatsProto(num_bread_battles_entered=1,num_bread_battles_won=2,num_bread_battles_dough_won=3)
        self.assertEqual(bytes.fromhex('800501880502900503'),stats.SerializeToString())

    def test_actual_starts_and_wins_project_once_without_precompleting_tutorial(self):
        joined=self.join();self.assertEqual((0,0,0),self.counts())
        prepared=self.prepare(joined.bread_lobby);self.clock=prepared.bread_lobby.bread_battle_start_ms
        started=self.start();self.assertEqual((1,0,0),self.counts())
        self.win(started);self.assertEqual((1,1,0),self.counts())
        result=self.complete(started);self.assertTrue(result.is_victory)
        self.assertEqual(result,self.complete(started));self.assertEqual((1,1,0),self.counts())
        self.assertEqual((0,0,0),self.counts(1))
        with self.db.transaction() as db:
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM tutorials WHERE player_id=? AND value=117',(self.players[0],)).fetchone()[0])
            self.assertEqual(1,db.execute('SELECT COUNT(*) FROM max_victories WHERE player_id=?',(self.players[0],)).fetchone()[0])

    def test_faint_loss_counts_entry_without_winning(self):
        self.game.max_config['development_balance'].update(boss_interval_ms=1000,boss_damage_multiplier=100)
        started=self.begin();self.clock+=1000;self.state(started)
        self.assertEqual(3,self.combat(started)['status'])
        self.assertEqual((1,0,0),self.counts())
        self.assertFalse(self.complete(started).is_victory)
        self.assertEqual((1,0,0),self.counts())

    def test_gmax_subset_uses_frozen_legacy_boss_and_survives_restart(self):
        self.gigantamax_fixture();self.game.max_config['development_balance']['boss_hp']=20
        started=self.begin();self.win(started)
        self.assertEqual((1,1,1),self.counts())
        with self.db.transaction() as db:
            state=json.loads(db.execute('SELECT state FROM max_rooms WHERE id=?',(started.lobby.bread_lobby_id,)).fetchone()[0])
            state.pop('boss_mode');state.pop('station_snapshot')
            db.execute('UPDATE max_rooms SET state=? WHERE id=?',(json.dumps(state),started.lobby.bread_lobby_id))
            db.execute('UPDATE max_stations SET definition=? WHERE id=?',(json.dumps(dict(species=831,mode=1,tier=1,level=10)),self.stations[0]['id']))
        config=dict(self.cfg);config['max_system']=dict(self.game.max_config)
        self.game=Game(config,self.db)
        self.assertEqual((1,1,1),self.counts());self.assertEqual((0,0,0),self.counts(1))

    def test_disabled_profile_never_queries_missing_max_tables(self):
        class NoMaxTables:
            def execute(self,*unused):raise AssertionError('Max-disabled inventory queried a Max table')
        stats=p.PlayerStatsProto(level=21)
        max_stats.apply(SimpleNamespace(config={'max_system':{'enabled':False}}),NoMaxTables(),'ordinary',stats)
        self.assertEqual(p.PlayerStatsProto(level=21),stats)


del Base,Groups

if __name__=='__main__':unittest.main()
