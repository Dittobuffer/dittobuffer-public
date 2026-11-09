import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from dittobuffer.world import world_rewards
from tests import test_game as base


class StopLootTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((base.ROOT / 'config/family.json').read_text(encoding='utf8'))
        self.original = world_rewards.LOOT_PATH
        self.folder = tempfile.TemporaryDirectory(dir=base.ROOT / 'builds')
        world_rewards.LOOT_PATH = Path(self.folder.name) / 'stop-loot.json'
        world_rewards._cache.update(key=None, table=None)

    def tearDown(self):
        world_rewards.LOOT_PATH = self.original
        world_rewards._cache.update(key=None, table=None)
        self.folder.cleanup()

    def test_defaults_cover_every_published_stop_item(self):
        table = world_rewards.default(self.config)
        self.assertEqual({row['item'] for row in table['drops']},
                         {row['id'] for row in world_rewards.RULES['items']} | {4})
        self.assertEqual(table['guaranteed'], [{'item': 1, 'count': 1}])
        self.assertEqual(table['passes'], {'premium': 0.025, 'remote': 0.025})
        master = next(row for row in table['drops'] if row['item'] == 4)
        self.assertEqual(master['weight'], 0.5)

    def test_save_round_trip_and_awards_use_custom_counts(self):
        table = {'count': 1, 'guaranteed': [{'item': 1402, 'count': 2}],
                 'drops': [{'item': 1408, 'weight': 1, 'min_level': 1, 'min_count': 3, 'max_count': 3}],
                 'passes': {'premium': 0, 'remote': 0}}
        saved = world_rewards.save(self.config, table)
        self.assertEqual(saved['guaranteed'][0]['count'], 2)
        game = SimpleNamespace(config=self.config)
        self.assertEqual(world_rewards.awards(game, 'player', 'stop', 7, 50), {1402: 2, 1408: 3})
        self.assertEqual(world_rewards.max_awards(self.config, 50), 5)

    def test_invalid_rows_rejected(self):
        for table in ({'count': 21, 'guaranteed': [], 'drops': world_rewards.default(self.config)['drops']},
                      {'count': 1, 'guaranteed': [], 'drops': [{'item': 1, 'weight': 0, 'min_level': 1,
                                                                'min_count': 1, 'max_count': 1}]},
                      {'count': 1, 'guaranteed': [], 'drops': [{'item': 1, 'weight': 5, 'min_level': 1,
                                                                'min_count': 5, 'max_count': 2}]},
                      {'count': 1, 'guaranteed': [], 'drops': world_rewards.default(self.config)['drops'],
                       'passes': {'premium': 1.5, 'remote': 0}}):
            with self.assertRaises(ValueError):
                world_rewards.normalise(self.config, table)

    def test_level_filter_excludes_locked_rows(self):
        table = {'count': 5, 'guaranteed': [],
                 'drops': [{'item': 3, 'weight': 1, 'min_level': 40, 'min_count': 1, 'max_count': 1}],
                 'passes': {'premium': 0, 'remote': 0}}
        world_rewards.save(self.config, table)
        game = SimpleNamespace(config=self.config)
        self.assertEqual(world_rewards.awards(game, 'low', 'stop', 1, 5), {})
        self.assertEqual(world_rewards.awards(game, 'high', 'stop', 1, 40), {3: 5})
