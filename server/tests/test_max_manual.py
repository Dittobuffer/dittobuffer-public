"""Hand-placed Power Spots stay at explicit locations and survive rotation."""
import gc
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from dittobuffer.admin.admin import Admin
from dittobuffer.core.database import Database
from dittobuffer.gameplay import max_system as mx
from dittobuffer.gameplay.game import Game, ROOT, now_ms, p, wire
from dittobuffer.gameplay.species import catalog as species_catalog


class ManualPowerSpotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'builds')
        self.path = Path(self.tmp.name)
        self.clock = now_ms()
        self.patches = [patch('dittobuffer.gameplay.game.now_ms', lambda: self.clock),
                        patch('dittobuffer.gameplay.max_system.now_ms', lambda: self.clock)]
        for entry in self.patches:
            entry.start()
        cfg = json.loads((ROOT / 'config/family.json').read_text(encoding='utf8'))
        cfg.update(authentication={'mode': 'local-development'}, profile='m18-unit',
                   world={'enabled': False}, weather={'enabled': False}, social={'enabled': False},
                   rocket={'enabled': False},
                   raids={**cfg.get('raids', {}), 'participation_limit': 1, 'multiplayer': False})
        templates = {}
        for name, value in [
            ('MP_SHARED_SETTINGS', p.GameMasterClientTemplateProto(template_id='MP_SHARED_SETTINGS', mp_settings=p.MpSharedSettingsProto(mp_capacity=1500, mp_base_daily_limit=800))),
            ('BREAD_FEATURE_FLAGS', p.GameMasterClientTemplateProto(template_id='BREAD_FEATURE_FLAGS')),
            ('BREAD_SHARED_SETTINGS', p.GameMasterClientTemplateProto(template_id='BREAD_SHARED_SETTINGS')),
            ('BREAD_MOVE_MAPPING_SETTINGS', p.GameMasterClientTemplateProto(template_id='BREAD_MOVE_MAPPING_SETTINGS')),
            ('BREAD_MOVE_LEVEL_SETTINGS_GROUP_1', p.GameMasterClientTemplateProto(template_id='BREAD_MOVE_LEVEL_SETTINGS_GROUP_1')),
        ]:
            templates[name] = value.SerializeToString().hex()
        self.reference = self.path / 'ref.json'
        default_form = species_catalog(ROOT / 'data/static/gmts.binpb.gz')[831]['form']
        self.reference.write_text(json.dumps(dict(
            templates=templates, species_groups={f'831:{default_form}': 1, '831:0': 1})))
        self.candidates = self.path / 'candidates.sqlite3'
        with closing(sqlite3.connect(self.candidates)) as db:
            db.executescript('CREATE TABLE metadata(key TEXT,value TEXT);'
                             'CREATE TABLE candidates(id TEXT,name TEXT,lat REAL,lng REAL);')
            db.execute('INSERT INTO metadata VALUES(?,?)', ('source', json.dumps({'license': 'ODbL-1.0'})))
            db.execute('INSERT INTO candidates VALUES(?,?,?,?)', ('osm:n1', 'Reviewed place', 52.08, 5.12))
            db.commit()
        self.base = cfg
        self.base['max_system'] = dict(enabled=True, reference=str(self.reference),
                                       candidates=str(self.candidates), active_probability=1,
                                       pools=[dict(species=831, tier=1, level=10, mode=1, weight=1)])
        self.game, self.db = self.build('manual.sqlite3')

    def build(self, filename, **values):
        cfg = json.loads(json.dumps(self.base))
        cfg['max_system'].update(values)
        database = Database(self.path / filename)
        return Game(cfg, database), database

    def tearDown(self):
        for entry in self.patches:
            entry.stop()
        gc.collect()
        self.tmp.cleanup()

    def test_hand_placed_spot_is_served_at_its_exact_location(self):
        with self.db.transaction() as db:
            placed = mx.place(self.game, db, 52.081234, 5.121234, 'Corner shop',
                              {'species': 831, 'mode': 1}, 3600)
            row = mx.lookup(self.game, db, placed['id'])
            self.assertEqual((row['name'], row['lat'], row['lng']), ('Corner shop', 52.081234, 5.121234))
            self.assertTrue(row['source_id'].startswith('manual:'))
            self.assertEqual(json.loads(row['definition'])['species'], 831)
            self.assertEqual(mx.station(self.game, db, 'manual-player', row).name, 'Corner shop')

    def test_map_reply_carries_the_hand_placed_spot(self):
        from s2sphere import CellId, LatLng
        ticket = self.game.authenticate(wire.AuthRequest(auth_id='manual-map-1')).ticket
        with self.db.transaction() as db:
            placed = mx.place(self.game, db, 52.0791, 5.1191, 'Map spot',
                              {'species': 831, 'mode': 1}, 3600)
        cell = CellId.from_lat_lng(LatLng.from_degrees(52.0791, 5.1191)).parent(12).id()
        request = wire.RpcRequest(rpc_id=99)
        request.requests.add(method=106, body=p.GetMapObjectsProto(
            cell_id=[cell], player_lat=52.0791, player_lng=5.1191).SerializeToString())
        response = self.game.rpc(request, ticket, run='manual-map')
        self.assertEqual(response.status, 1)
        out = p.GetMapObjectsOutProto.FromString(response.payloads[0].payload)
        self.assertIn(placed['id'], [station.id for c in out.map_cell for station in c.stations])

    def test_placement_requires_a_released_boss_and_real_coordinates(self):
        with self.db.transaction() as db:
            with self.assertRaisesRegex(ValueError, 'released Max pool'):
                mx.place(self.game, db, 52.08, 5.12, 'Off-pool', {'species': 25, 'mode': 1}, 3600)
            with self.assertRaisesRegex(ValueError, 'released Max pool'):
                mx.place(self.game, db, 52.08, 5.12, 'Gmax', {'species': 831, 'mode': 2}, 3600)
            for lat, lng, duration in ((95, 5.12, 3600), (52.08, 5.12, 5)):
                with self.assertRaises(ValueError):
                    mx.place(self.game, db, lat, lng, 'Bad', {'species': 831, 'mode': 1}, duration)

    def test_scheduled_rotation_never_retires_a_hand_placed_spot(self):
        game, database = self.build(
            'scheduled.sqlite3', rotation_seconds=60, active_seconds=30,
            schedule=dict(enabled=True, timezone='Europe/Amsterdam',
                          regular={'rotation_seconds': 60,
                                   'pool': [{'species': 831, 'tier': 1, 'level': 10, 'mode': 1}]},
                          events=[]))
        with database.transaction() as db:
            placed = mx.place(game, db, 52.0805, 5.1205, 'Fixed spot', {'species': 831, 'mode': 1}, 7200)
            original = db.execute('SELECT end_ms FROM max_stations WHERE id=?', (placed['id'],)).fetchone()[0]
            self.clock += 120000  # Every rotating window in this area has closed.
            mx.materialize(game, db, [52, 53, 5, 6])
            after = db.execute('SELECT end_ms FROM max_stations WHERE id=?', (placed['id'],)).fetchone()
            self.assertIsNotNone(after)
            self.assertEqual(after['end_ms'], original)

    def test_admin_places_and_deletes_a_spot_at_coordinates(self):
        admin = Admin.__new__(Admin)
        admin.config_path = self.path / 'config.json'
        admin.config_path.write_text(json.dumps(self.base), encoding='utf8')
        admin.config = self.base
        admin.db_path = self.db.path
        admin.lock = threading.Lock()
        admin.port, admin.host = 0, '127.0.0.1'
        created = admin.max_action({'action': 'place_station', 'lat': 52.079, 'lng': 5.119,
                                    'name': 'Admin spot', 'species': 831,
                                    'mode': 1, 'tier': 1, 'level': 10, 'duration_s': 3600})
        with closing(admin.connect()) as db:
            row = db.execute('SELECT name,lat,lng,source_id FROM max_stations WHERE id=?', (created['id'],)).fetchone()
            self.assertEqual((row['name'], row['lat'], row['lng']), ('Admin spot', 52.079, 5.119))
            self.assertTrue(row['source_id'].startswith('manual:'))
        self.assertTrue(admin.max_action({'action': 'end_station', 'id': created['id']})['ended'])
        deleted = admin.max_action({'action': 'delete_station', 'id': created['id']})
        self.assertTrue(deleted['deleted'])
        with closing(admin.connect()) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM max_stations WHERE id=?', (created['id'],)).fetchone())

    def test_admin_pool_save_accepts_only_max_reference_forms(self):
        admin = Admin.__new__(Admin)
        admin.config_path = self.path / 'config.json'
        admin.config_path.write_text(json.dumps(self.base), encoding='utf8')
        admin.config = self.base
        admin.db_path = self.db.path
        admin.lock = threading.Lock()
        base = {**self.base['max_system']}
        saved = admin.save_max({'config': {**base,
                                          'pools': [dict(species=831, mode=1, tier=1, level=10, weight=1)]}})
        self.assertEqual(saved['config']['pools'][0]['form'], 0)
        with self.assertRaisesRegex(ValueError, 'Unsupported explicit Max'):
            admin.save_max({'config': {**base,
                                      'pools': [dict(species=831, form=9999, mode=1, tier=1, level=10, weight=1)]}})

    def test_admin_actions_reject_unknown_spots(self):
        admin = Admin.__new__(Admin)
        admin.config_path = self.path / 'config.json'
        admin.config_path.write_text(json.dumps(self.base), encoding='utf8')
        admin.config = self.base
        admin.db_path = self.db.path
        admin.lock = threading.Lock()
        for action in ('delete_station', 'end_station', 'extend_station'):
            with self.assertRaisesRegex(ValueError, 'Unknown Power Spot'):
                admin.max_action({'action': action, 'id': 'power-missing'})


if __name__ == '__main__':
    unittest.main()
