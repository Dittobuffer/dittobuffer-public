import json
import tempfile
import unittest
from pathlib import Path

from dittobuffer.core.database import Database
from dittobuffer.gameplay.game import Game, ROOT, p


class RandomSpawnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'builds')
        self.config = json.loads((ROOT / 'config/development.json').read_text(encoding='utf8'))
        self.config.update(
            random_spawns={'enabled': True, 'refresh_seconds': 1800,
                           'level_range': [5, 35], 'iv_range': [0, 15],
                           'shiny_rate': 1 / 64, 'special_weight': 0.05,
                           'points_per_tile': 10},
            spawn_neighbor_tiles=0,
        )
        self.db = Database(Path(self.tmp.name) / 'spawns.sqlite3')
        self.game = Game(self.config, self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rotating_pool_excludes_legendaries_and_respects_bounds(self):
        self.assertTrue(self.game.random_spawns['enabled'])
        self.assertEqual(self.game.spawn_seconds, 1800)
        self.assertAlmostEqual(self.game.random_spawns['shiny_rate'], 1 / 64)
        pool = self.game.spawn_pools[0]['species']
        self.assertNotIn({'id': 890, 'form': 2580}, [{'id': e['id'], 'form': e.get('form')}
                                                       for e in pool])
        self.assertNotIn({'id': 718, 'form': 2592}, [{'id': e['id'], 'form': e.get('form')}
                                                       for e in pool])
        for entry in pool:
            form = entry.get('form', self.game.species[entry['id']]['form'])
            rules = self.game.forms[entry['id'], form]
            self.assertNotIn(rules.get('class'), (1, 2, 3), rules.get('name'))
            self.assertNotIn('SHADOW', rules.get('name', ''))
        with self.db.transaction() as db:
            spawn_ids = self.game.seed(db, 52.37, 4.89)
            self.assertEqual(len(spawn_ids), 10)
            for spawn_id in spawn_ids:
                row = db.execute('SELECT a.data,a.level FROM spawn_attributes a '
                                 'WHERE a.spawn_id=?', (spawn_id,)).fetchone()
                mon = p.PokemonProto.FromString(row['data'])
                self.assertTrue(5 <= row['level'] <= 35)
                self.assertEqual(mon.pokemon_display.alignment, 0)
                self.assertFalse(mon.pokemon_display.shiny and
                                 (mon.pokemon_id, mon.pokemon_display.form) not in
                                 self.game.shiny_eligible)
                self.assertTrue(all(0 <= iv <= 15 for iv in
                                    (mon.individual_attack, mon.individual_defense,
                                     mon.individual_stamina)))

    def test_percentage_mix_controls_absolute_shares(self):
        from dittobuffer.gameplay import liveops, random_spawns
        base = [entry['id'] for entry in self.game.spawn_pools[0]['species']]
        first, second, third = base[0], base[len(base) // 3], base[-1]
        self.game.liveops = dict(liveops.DEFAULTS,
                                 spawn_percent={str(first): 20, str(second): 35, str(third): 6})
        pools, _ = random_spawns.build_pool(self.game)
        entries = pools[0]['species']
        total = sum(float(entry['weight']) for entry in entries)
        share = lambda species: sum(float(entry['weight']) for entry in entries
                                    if entry['id'] == species) / total
        self.assertAlmostEqual(share(first), 0.20, places=3)
        self.assertAlmostEqual(share(second), 0.35, places=3)
        self.assertAlmostEqual(share(third), 0.06, places=3)
        self.assertAlmostEqual(1 - share(first) - share(second) - share(third), 0.39, places=3)
        self.assertGreater(len(entries), 3)

    def test_percentage_mix_over_one_hundred_is_rejected(self):
        from dittobuffer.gameplay import liveops
        with self.assertRaises(ValueError):
            liveops.normalise({'spawn_percent': {'16': 60, '66': 60}})

    def test_default_is_on_for_profiles_without_a_block(self):
        from dittobuffer.gameplay.random_spawns import settings
        self.assertTrue(settings({})['enabled'])
        self.assertTrue(settings({'random_spawns': {'enabled': True}})['enabled'])
        self.assertFalse(settings({'random_spawns': {'enabled': False}})['enabled'])

    def test_disabled_uses_the_explicit_pool_and_duration(self):
        self.config = json.loads((ROOT / 'config/development.json').read_text(encoding='utf8'))
        self.config.update(random_spawns={'enabled': False})
        game = Game(self.config, self.db)
        self.assertFalse(game.random_spawns['enabled'])
        self.assertEqual(game.spawn_seconds, self.config['spawn_duration_seconds'])
        self.assertEqual(game.spawn_pools[0]['species'][0]['id'], 1)
