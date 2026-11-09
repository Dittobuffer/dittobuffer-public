import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from dittobuffer.gameplay.game import Game,Database,ROOT,p,wire,RpcError
from dittobuffer.gameplay import eggs

class EggTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds')
        self.cfg=json.loads((ROOT/'config/development.json').read_text())
        self.cfg['profile']='m5-tests';self.cfg['walking']['allow_debug_fixture']=True
        self.cfg['eggs'].update(stop_drop_probability=1,initial_limited_incubators=1,pools=[{'km':2,'species':[10],'weight':1}])
        self.cfg['fort_cooldown_seconds']=0
        self.db=Database(Path(self.tmp.name)/'eggs.sqlite3');self.game=Game(self.cfg,self.db)
        self.ticket=self.game.authenticate(wire.AuthRequest(auth_id='egg-tests')).ticket;self.counter=0
        with self.db.transaction() as db:
            self.game.seed(db,52.37,4.89)
            db.execute("INSERT INTO forts VALUES('test-stop',1,'Test Stop',52.37,4.89)")
            self.fort=dict(db.execute('SELECT * FROM forts WHERE kind=1').fetchone())
        self.player_id=None
        self.inventory()
        with self.db.transaction() as db:self.player_id=db.execute('SELECT id FROM players').fetchone()[0]

    def tearDown(self):self.tmp.cleanup()
    def call(self,method,body,response,rpc_id=None,ticket=None):
        self.counter+=1;req=wire.RpcRequest(rpc_id=rpc_id or self.counter);req.requests.add(method=method,body=body.SerializeToString())
        return response.FromString(self.game.rpc(req,ticket or self.ticket).payloads[0].payload)
    def inventory(self):
        out=self.call(4,p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
        return [p.HoloInventoryItemProto.FromString(v.item) for v in out.inventory_delta.inventory_item]
    def spin(self,rpc_id=None):
        return self.call(101,p.FortSearchProto(id=self.fort['id'],player_lat_degrees=self.fort['lat'],player_lng_degrees=self.fort['lng']),p.FortSearchOutProto,rpc_id)
    def assign(self,identifier,item=901):
        with self.db.transaction() as db:inc=db.execute('SELECT id FROM incubators WHERE player_id=? AND item=?',(self.player_id,item)).fetchone()[0]
        return self.call(140,p.UseItemEggIncubatorProto(item_id=inc,pokemond_id=identifier),p.UseItemEggIncubatorOutProto)
    def sample(self,seconds,delta=0,**overrides):
        stamp=1800000000000+seconds*1000
        fix=dict(lat=52.37+delta,lng=4.89,accuracy=1,time=stamp/1000,provider=3,source='debug');fix.update(overrides)
        with patch('dittobuffer.gameplay.eggs.now_ms',return_value=stamp):
            with self.db.transaction() as db:eggs.sample(self.game,db,self.player_id,fix)

    def test_literal_egg_wire_and_shared_ids(self):
        self.assertEqual(p.GetHatchedEggsOutProto(pokemon_id=[1]).SerializeToString(),b'\x12\x08\x01'+b'\0'*7)
        self.assertEqual(p.UseItemEggIncubatorProto(pokemond_id=1).SerializeToString(),b'\x10\x01')
        first=self.spin(500).egg_pokemon;again=self.spin(500).egg_pokemon
        self.assertEqual(first,again);self.assertTrue(first.is_egg);self.assertEqual(first.egg_km_walked_target,2)
        self.assertEqual(first.egg_slot_type,0)
        with self.db.transaction() as db:
            self.assertNotEqual(eggs.storage_id(db),first.id)
            self.assertEqual(eggs.count(db,self.player_id),1)
        self.game=Game(self.cfg,Database(self.db.path))
        self.assertEqual(next(v.pokemon for v in self.inventory() if v.HasField('pokemon')),first)

    def test_capacity_and_incubator_ownership_and_assignment(self):
        self.cfg['eggs']['capacity']=1
        # Assignment after prior walking must keep target/start absolute:
        # the real client's EggKmTotal subtracts them to choose its model.
        with self.db.transaction() as db:db.execute('UPDATE walking SET total_m=12345 WHERE player_id=?',(self.player_id,))
        first=self.spin().egg_pokemon
        self.assertFalse(self.spin().HasField('egg_pokemon'))
        assigned=self.assign(first.id)
        self.assertEqual(assigned.result,1)
        egg=next(v.pokemon for v in self.inventory() if v.HasField('pokemon'))
        self.assertAlmostEqual(egg.egg_km_walked_start,12.345)
        self.assertAlmostEqual(egg.egg_km_walked_target,14.345)
        self.assertAlmostEqual(egg.egg_km_walked_target-egg.egg_km_walked_start,2)
        self.assertEqual(self.assign(first.id),assigned)
        self.assertEqual(self.assign(first.id,902).result,6)
        other=self.game.authenticate(wire.AuthRequest(auth_id='other-owner')).ticket
        self.assertEqual(self.call(140,p.UseItemEggIncubatorProto(item_id=assigned.egg_incubator.item_id,pokemond_id=first.id),p.UseItemEggIncubatorOutProto,ticket=other).result,2)
        self.game=Game(self.cfg,self.db)
        self.assertEqual(self.assign(first.id),assigned)

    def test_validated_distance_rejects_duplicates_stale_jumps_and_noise(self):
        self.sample(0)
        self.sample(10,0.001) # ~111 m in 10 seconds: implausible.
        self.sample(10,0.0001) # Duplicate fix timestamp is already rejected.
        self.sample(20,0.0001,accuracy=100)
        self.sample(20,0.0001,provider=2)
        self.sample(20,0.0001,time=1)
        self.sample(20,0.00001,accuracy=10) # Accuracy-scale jitter.
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT total_m FROM walking').fetchone()[0],0)
        self.sample(30,0.0001)
        with self.db.transaction() as db:
            actual=db.execute('SELECT total_m FROM walking').fetchone()[0]
            self.assertAlmostEqual(actual,11.1195,places=3)
        self.game=Game(self.cfg,self.db)
        self.sample(30,0.0001)
        self.sample(200,0.01) # Long gap adopts a fresh baseline without credit.
        with self.db.transaction() as db:self.assertAlmostEqual(db.execute('SELECT total_m FROM walking').fetchone()[0],actual)
        self.cfg['walking']['allow_debug_fixture']=False
        self.sample(220,0.0101)
        with self.db.transaction() as db:self.assertAlmostEqual(db.execute('SELECT total_m FROM walking').fetchone()[0],actual)

    def test_hatching_is_atomic_persistent_and_rewards_once(self):
        egg=self.spin().egg_pokemon;self.assign(egg.id,902)
        # Isolated fixture: stage the last 10 m, then feed paced valid fixes.
        with self.db.transaction() as db:db.execute('UPDATE walking SET total_m=1990')
        self.sample(0);self.sample(10,0.0001)
        self.game=Game(self.cfg,self.db)
        self.assertTrue(any(v.HasField('pokemon') and v.pokemon.is_egg for v in self.inventory()))
        failed=wire.RpcRequest(rpc_id=900)
        failed.requests.add(method=126);failed.requests.add(method=999999)
        with self.assertRaises(RpcError):self.game.rpc(failed,self.ticket)
        with self.db.transaction() as db:
            self.assertEqual(eggs.count(db,self.player_id),1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM hatches').fetchone()[0],0)
        result=self.call(126,p.GetHatchedEggsProto(),p.GetHatchedEggsOutProto,rpc_id=500)
        with self.db.transaction() as db:
            self.assertEqual(eggs.count(db,self.player_id),0)
            row=db.execute('SELECT * FROM pokemon WHERE id=?',(egg.id,)).fetchone()
            self.assertTrue(p.PokemonProto.FromString(row['data']).hatched_from_egg)
            self.assertEqual(db.execute('SELECT uses,egg_id FROM incubators WHERE item=902').fetchone()[:],(2,None))
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
            xp=db.execute('SELECT xp FROM players').fetchone()[0]
            self.assertEqual(xp,250)
        self.assertEqual(list(result.pokemon_id),[egg.id]);self.assertEqual(list(result.candy_awarded),[5])
        self.assertEqual(self.call(126,p.GetHatchedEggsProto(),p.GetHatchedEggsOutProto,rpc_id=500),result)
        self.game=Game(self.cfg,self.db);self.sample(30,0.0002)
        self.assertFalse(self.call(126,p.GetHatchedEggsProto(),p.GetHatchedEggsOutProto).pokemon_id)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT xp FROM players').fetchone()[0],xp)
            self.assertEqual(db.execute('SELECT count(*) FROM hatches').fetchone()[0],1)

    def test_multiple_eggs_keep_independent_incubation_progress(self):
        first=self.spin().egg_pokemon;second=self.spin().egg_pokemon
        self.assertNotEqual(first.id,second.id)
        self.assign(first.id);self.assign(second.id,902)
        self.sample(0);self.sample(10,0.0001)
        self.game=Game(self.cfg,self.db)
        records=[v.pokemon for v in self.inventory() if v.HasField('pokemon')]
        self.assertEqual(len(records),2);self.assertTrue(all(v.is_egg and v.egg_incubator_id for v in records))
        stats=next(v.player_stats for v in self.inventory() if v.HasField('player_stats'))
        self.assertAlmostEqual(stats.km_walked,0.0111195,places=5)
        self.assertEqual(stats.num_eggs_hatched,0)

    def test_initial_and_level_reward_incubators_are_granted_once(self):
        self.game=Game(self.cfg,self.db);self.inventory();self.inventory()
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM incubators').fetchone()[0],2)
            db.execute('UPDATE players SET xp=1000')
        self.cfg['level_up_rewards']['2']={'902':1}
        self.call(128,p.LevelUpRewardsProto(level=2),p.LevelUpRewardsOutProto)
        self.call(128,p.LevelUpRewardsProto(level=2),p.LevelUpRewardsOutProto)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM incubators WHERE item=902').fetchone()[0],2)
            self.assertIsNone(db.execute('SELECT 1 FROM items WHERE item=902').fetchone())

    def test_final_finite_use_exhausts_without_consuming_infinite(self):
        first=self.spin().egg_pokemon;second=self.spin().egg_pokemon
        with self.db.transaction() as db:db.execute('UPDATE incubators SET uses=1 WHERE item=902')
        self.assign(first.id,902)
        with self.db.transaction() as db:db.execute('UPDATE walking SET total_m=1990')
        self.sample(0);self.sample(10,0.0001)
        self.call(126,p.GetHatchedEggsProto(),p.GetHatchedEggsOutProto)
        self.assertEqual(self.assign(second.id,902).result,7)
        inv=next(v.egg_incubators for v in self.inventory() if v.HasField('egg_incubators'))
        self.assertEqual([(v.item,v.uses_remaining) for v in inv.egg_incubator],[(901,-1)])

if __name__=='__main__':unittest.main()
