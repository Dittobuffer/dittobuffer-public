"""Combined optional features share the new package and isolated database."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from dittobuffer.paths import ROOT, GAMEPLAY
from dittobuffer.core.database import Database
from dittobuffer.core import master
from dittobuffer.gameplay.game import Game


class StructureIntegrationTests(unittest.TestCase):
    def test_routes_templates_and_persistence_coexist_with_new_main_features(self):
        config = json.loads((ROOT / 'config/family.json').read_text(encoding='utf-8'))
        routes = json.loads((GAMEPLAY / 'routes.example.json').read_text(encoding='utf-8'))
        routes['enabled'] = routes['cells']['enabled'] = True
        config.update(world={'enabled': False}, weather={'enabled': False},
                      rocket={'enabled': True}, routes=routes, vivillon={'enabled': True})
        with tempfile.TemporaryDirectory(dir=ROOT / 'builds') as folder:
            path = Path(folder) / 'coexist.sqlite3'
            game = Game(config, Database(path))
            templates = master.templates(config)
            names = [value.template_id for value in templates]
            self.assertEqual(len(names), len(set(names)))
            for required in ('ROUTE_BADGE_SETTINGS', 'BUTTERFLY_COLLECTOR_SETTINGS',
                             'BREAD_SHARED_SETTINGS', 'COMBAT_LEAGUE_DEFAULT_GREAT'):
                self.assertIn(required, names)
            # Reopening the same isolated database must preserve all schemas,
            # without optional modules replacing the core collection tables.
            reopened = Game(config, Database(path))
            with reopened.db.transaction() as connection:
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({'pokemon', 'routes', 'pvp_battles', 'max_wallet'} <= tables)
                self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
            # Rocket creates its encounter tables lazily on a real incident.
            self.assertTrue(callable(reopened.rpc_invasion_encounter))
            self.assertTrue(callable(reopened.rpc_invasion_open_combat_session))

    def test_optional_gm_variants_do_not_reuse_the_pvp_epoch(self):
        config = json.loads((ROOT / 'config/family.json').read_text(encoding='utf-8'))
        versions = set()
        for route, cells, mateo, vivillon in (
                (False, False, False, False), (True, False, False, False),
                (True, True, False, False), (True, False, True, False),
                (True, True, True, True)):
            variant = copy.deepcopy(config)
            variant.update(routes={'enabled': route, 'cells': {'enabled': cells},
                                   'mateo': {'enabled': mateo}},
                           vivillon={'enabled': vivillon})
            versions.add(master.version(variant))
        self.assertEqual(len(versions), 5)

    def test_pvp_revision_and_routes_have_independent_cache_bits(self):
        config = {'pvp': {'enabled': True, 'season': 1, 'template_revision': 0}}
        versions = set()
        for revision in (0, 1, 2):
            for routes in (False, True):
                variant = copy.deepcopy(config)
                variant['pvp']['template_revision'] = revision
                variant['routes'] = {'enabled': routes}
                versions.add(master.version(variant))
        self.assertEqual(len(versions), 6)
