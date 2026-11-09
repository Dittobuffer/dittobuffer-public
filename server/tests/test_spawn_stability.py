import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from s2sphere import CellId,LatLng
from dittobuffer.core.database import Database
from dittobuffer.gameplay.game import Game,ROOT,p,wire

class SpawnStabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds')
        self.addCleanup(self.tmp.cleanup)
        self.config=json.loads((ROOT/'config/development.json').read_text())
        self.config.update(spawn_neighbor_tiles=0,spawn_points_per_tile=1)
        self.db=Database(Path(self.tmp.name)/'game.sqlite3')
        self.game=Game(self.config,self.db)
        self.ticket=self.game.authenticate(wire.AuthRequest(auth_id='stable-map')).ticket
        self.player=None
        with self.db.transaction() as db:self.player=db.execute('SELECT player_id FROM sessions WHERE ticket=?',(self.ticket,)).fetchone()[0]
        self.cell=CellId.from_lat_lng(LatLng.from_degrees(52.37,4.89)).parent(12).id()

    def world(self,lat=52.37,lng=4.89,cells=None,since=None):
        with self.db.transaction() as db:
            return self.game.rpc_get_map_objects(db,self.player,p.GetMapObjectsProto(
                cell_id=[self.cell] if cells is None else cells,since_time_ms=since or [],
                player_lat=lat,player_lng=lng).SerializeToString())

    def test_unexpired_identity_attributes_and_modified_time_survive_poll_and_restart(self):
        with patch('dittobuffer.gameplay.game.now_ms',return_value=1800000010000):first=self.world()
        old=first.map_cell[0].wild_pokemon[0]
        self.game=Game(self.config,self.db)
        with patch('dittobuffer.gameplay.game.now_ms',return_value=1800000025000):second=self.world(since=[first.map_cell[0].as_of_time_ms])
        new=next(v for v in second.map_cell[0].wild_pokemon if v.encounter_id==old.encounter_id)
        self.assertEqual(old.last_modified_ms,new.last_modified_ms)
        self.assertEqual(old.pokemon,new.pokemon)
        self.assertEqual(old.time_till_hidden_ms-new.time_till_hidden_ms,15000)
        self.assertEqual(old.spawn_point_id,new.spawn_point_id)

    def test_full_cell_does_not_delete_spawn_at_gps_radius_boundary(self):
        with patch('dittobuffer.gameplay.game.now_ms',return_value=1800000010000):
            first=self.world();original=first.map_cell[0].wild_pokemon[0]
            # Same requested S2 cell, different GPS fix >250m from this spawn.
            second=self.world(lat=52.374)
        self.assertIn(original.encounter_id,[v.encounter_id for v in second.map_cell[0].wild_pokemon])

    def test_partial_cell_request_never_emits_empty_unrequested_cells(self):
        with patch('dittobuffer.gameplay.game.now_ms',return_value=1800000010000):
            first=self.world()
            other=CellId.from_lat_lng(LatLng.from_degrees(52.4,4.95)).parent(15).id()
            partial=self.world(cells=[other])
            again=self.world()
        self.assertEqual([c.s2_cell_id for c in partial.map_cell],[other])
        self.assertEqual(first.map_cell[0].catchable_pokemon,again.map_cell[0].catchable_pokemon)

    def test_expired_and_caught_records_are_removed(self):
        with patch('dittobuffer.gameplay.game.now_ms',return_value=1800000010000):
            first=self.world();original=first.map_cell[0].wild_pokemon[0]
            with self.db.transaction() as db:
                db.execute("INSERT INTO encounters(player_id,spawn_id,state) VALUES(?,?,'caught')",(self.player,original.encounter_id))
            self.assertNotIn(original.encounter_id,[v.encounter_id for c in self.world().map_cell for v in c.wild_pokemon])
        with patch('dittobuffer.gameplay.game.now_ms',return_value=1800001000000):
            self.assertNotIn(original.encounter_id,[v.encounter_id for c in self.world().map_cell for v in c.wild_pokemon])

    def test_near_expiry_encounter_returns_its_saved_deadline_to_android(self):
        self.config['encounter_duration_seconds']=300
        with patch('dittobuffer.gameplay.game.now_ms',return_value=1799500):
            value=self.world().map_cell[0].catchable_pokemon[0]
            with self.db.transaction() as db:
                out=self.game.rpc_encounter(db,self.player,p.EncounterProto(encounter_id=value.encounter_id,
                    spawnpoint_id=value.spawnpoint_id,player_lat_degrees=value.latitude,
                    player_lng_degrees=value.longitude).SerializeToString())
            self.assertEqual(out.status,1)
            self.assertEqual(out.pokemon.time_till_hidden_ms,300000)
            self.assertEqual(value.expiration_time_ms-1799500,500)

if __name__=='__main__':unittest.main()
