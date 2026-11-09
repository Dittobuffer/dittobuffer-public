"""First species registration XP: native score, durable facts and retry safety."""
import unittest
from tests import test_game as fixture
from dittobuffer.gameplay.game import Game,p

class PokedexBonusTests(unittest.TestCase):
    setUp=fixture.GameTests.setUp
    tearDown=fixture.GameTests.tearDown
    call=fixture.GameTests.call
    spawn=fixture.GameTests.spawn
    encounter=fixture.GameTests.encounter
    catch=fixture.GameTests.catch
    inventory=fixture.GameTests.inventory
    def test_first_catch_adds_1000_and_replay_restart_transfer_do_not_repeat(self):
        spawn=self.encounter();result=self.catch(spawn)
        awards=dict(zip(result.scores.activity_type,result.scores.exp))
        self.assertEqual(awards[p.HoloActivityType_ActivityPokedexEntryNew],1000)
        self.assertEqual(sum(result.scores.exp),self.config['catch_xp']+1000)
        self.assertEqual(self.catch(spawn),result)
        # Transfer cannot erase lifetime Pok?dex registration.
        self.assertEqual(self.call(112,p.ReleasePokemonProto(pokemon_id=result.captured_pokemon_id),p.ReleasePokemonOutProto).status,1)
        self.game=Game(self.config,self.database)
        self.config['spawn_generation']=2
        second=self.catch(self.encounter())
        self.assertEqual(second.status,1);self.assertNotIn(p.HoloActivityType_ActivityPokedexEntryNew,second.scores.activity_type)
        with self.database.transaction() as db:
            self.assertEqual(db.execute('SELECT xp FROM players').fetchone()[0],2*self.config['catch_xp']+1000)
    def test_seen_only_is_not_registered_and_reward_is_account_specific(self):
        one=self.encounter()
        result=self.catch(one)
        self.assertIn(p.HoloActivityType_ActivityPokedexEntryNew,result.scores.activity_type)
        from dittobuffer.gameplay.game import wire
        other=self.game.authenticate(wire.AuthRequest(auth_id='second-dex-trainer')).ticket
        request=p.EncounterProto(encounter_id=one.encounter_id,spawnpoint_id=one.spawnpoint_id,player_lat_degrees=one.latitude,player_lng_degrees=one.longitude)
        self.call(102,request,p.EncounterOutProto,ticket=other)
        caught=self.call(103,p.CatchPokemonProto(encounter_id=one.encounter_id,spawn_point_guid=one.spawnpoint_id,pokeball=1,hit_pokemon=True),p.CatchPokemonOutProto,ticket=other)
        self.assertIn(p.HoloActivityType_ActivityPokedexEntryNew,caught.scores.activity_type)

    def test_concurrent_same_species_catches_register_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from dittobuffer.gameplay.game import wire,now_ms
        self.config['spawn_points_per_tile']=2;self.game.spawn_points_per_tile=2;self.config['spawn_generation']=10
        with self.database.transaction() as db:
            ids=self.game.seed(db,52.37,4.89)
            rows=[dict(db.execute('SELECT * FROM spawns WHERE id=?',(sid,)).fetchone()) for sid in ids[:2]]
        self.assertEqual(len(rows),2)
        for row in rows:self.call(102,p.EncounterProto(encounter_id=row['id'],spawnpoint_id=row['spawnpoint'],player_lat_degrees=row['lat'],player_lng_degrees=row['lng']),p.EncounterOutProto)
        def capture(pair):
            i,row=pair;req=wire.RpcRequest(rpc_id=10000+i)
            req.requests.add(method=103,body=p.CatchPokemonProto(encounter_id=row['id'],spawn_point_guid=row['spawnpoint'],pokeball=1,hit_pokemon=True).SerializeToString())
            return p.CatchPokemonOutProto.FromString(self.game.rpc(req,self.ticket).payloads[0].payload)
        with ThreadPoolExecutor(2) as workers:results=list(workers.map(capture,enumerate(rows)))
        self.assertTrue(all(r.status==1 for r in results))
        self.assertEqual(sum(p.HoloActivityType_ActivityPokedexEntryNew in r.scores.activity_type for r in results),1)
