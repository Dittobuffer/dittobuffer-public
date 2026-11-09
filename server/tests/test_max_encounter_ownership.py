"""Authenticated Max reward ownership regressions; no Android acceptance claim."""
import unittest

from dittobuffer.core.database import Database
from dittobuffer.core.management import owned
from dittobuffer.gameplay.game import Game, RpcError, p, wire
from tests import test_max_battle as support


class MaxEncounterOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.fixture = support.MaxBattleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def reward(self):
        fixture = self.fixture
        started = fixture.begin()
        fixture.win(started)
        result = fixture.complete(started)
        self.assertEqual(result.result, 1)
        self.assertTrue(result.is_victory)
        return started, result.battle_results.post_battle_encounter

    @staticmethod
    def encounter_request(encounter):
        return p.EncounterProto(encounter_id=encounter.encounter_id,
                                spawnpoint_id=encounter.spawnpoint_id,
                                player_lat_degrees=52.08001,
                                player_lng_degrees=5.12)

    def snapshot(self):
        fixture = self.fixture
        with fixture.db.transaction() as connection:
            return {
                table: [tuple(row) for row in connection.execute(query)]
                for table, query in {
                    'encounters': 'SELECT * FROM encounters ORDER BY player_id,spawn_id',
                    'pokemon': 'SELECT * FROM pokemon ORDER BY id',
                    'items': 'SELECT * FROM items ORDER BY player_id,item',
                    'members': 'SELECT * FROM max_members ORDER BY room_id,player_id',
                    'wallet': 'SELECT * FROM max_wallet ORDER BY player_id',
                    'ledger': 'SELECT * FROM max_ledger ORDER BY player_id,source',
                }.items()
            }

    def test_foreign_reward_cannot_enter_generic_encounter_then_catch(self):
        fixture = self.fixture
        unused, reward = self.reward()
        before = self.snapshot()
        request = self.encounter_request(reward)
        # A successful generic 102 used to create a foreign encounter row,
        # letting 103 treat the private Max Pokemon as a shared wild spawn.
        with self.assertRaisesRegex(RpcError, 'does not belong'):
            fixture.call(102, request, p.EncounterOutProto, index=1)
        self.assertEqual(self.snapshot(), before)
        theft = p.CatchPokemonProto(encounter_id=reward.encounter_id,
                                    spawn_point_guid=reward.spawnpoint_id,
                                    pokeball=1, hit_pokemon=True)
        with self.assertRaisesRegex(RpcError, 'does not belong'):
            fixture.call(103, theft, p.CatchPokemonOutProto, index=1)
        self.assertEqual(self.snapshot(), before)

    def test_stale_foreign_encounter_row_cannot_bypass_direct_catch_ownership(self):
        fixture = self.fixture
        unused, reward = self.reward()
        # Explicitly reproduce the stale row a pre-fix successful 102 could
        # leave behind; the fix must not trust that row as a reward grant.
        with fixture.db.transaction() as connection:
            connection.execute('INSERT INTO encounters(player_id,spawn_id) VALUES(?,?)',
                               (fixture.players[1], reward.encounter_id))
        before = self.snapshot()
        for enabled in (True, False):
            fixture.game.config['max_system'] = {'enabled': enabled}
            with self.subTest(enabled=enabled), self.assertRaisesRegex(RpcError, 'does not belong'):
                fixture.call(103, p.CatchPokemonProto(encounter_id=reward.encounter_id,
                    spawn_point_guid=reward.spawnpoint_id, pokeball=1, hit_pokemon=True),
                    p.CatchPokemonOutProto, index=1)
            self.assertEqual(self.snapshot(), before)

    def test_existing_private_reward_stays_private_when_max_is_disabled(self):
        fixture = self.fixture
        unused, reward = self.reward()
        fixture.game.config['max_system'] = {'enabled': False}
        before = self.snapshot()
        with self.assertRaisesRegex(RpcError, 'does not belong'):
            fixture.call(102, self.encounter_request(reward), p.EncounterOutProto, index=1)
        self.assertEqual(self.snapshot(), before)

    def test_owner_can_reopen_catch_and_restart_without_duplicate_rewards(self):
        fixture = self.fixture
        started, reward = self.reward()
        owner = fixture.players[0]
        opened = fixture.call(102, self.encounter_request(reward), p.EncounterOutProto)
        self.assertEqual(opened.status, 1)
        self.assertEqual(opened.pokemon.pokemon.pokemon_display.bread_mode_enum, 1)
        fixture.game.config['catch_probability'] = 1
        request = p.CatchPokemonProto(encounter_id=reward.encounter_id,
                                     spawn_point_guid=reward.spawnpoint_id,
                                     pokeball=5, hit_pokemon=True)
        caught = fixture.call(103, request, p.CatchPokemonOutProto)
        self.assertEqual(caught.status, 1)
        before = self.snapshot()
        configuration = dict(fixture.cfg)
        configuration['max_system'] = dict(fixture.game.max_config)
        fixture.game = Game(configuration, fixture.db)
        self.assertEqual(fixture.call(103, request, p.CatchPokemonOutProto), caught)
        self.assertEqual(self.snapshot(), before)
        with fixture.db.transaction() as connection:
            pokemon = owned(connection, owner, caught.captured_pokemon_id)
            self.assertEqual(pokemon.pokemon_display.bread_mode_enum, 1)
            self.assertIsNone(owned(connection, fixture.players[1], caught.captured_pokemon_id))
            self.assertEqual(connection.execute(
                'SELECT balls FROM max_members WHERE room_id=? AND player_id=?',
                (started.lobby.bread_lobby_id, owner)).fetchone()[0], 4)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM max_victories').fetchone()[0], 1)

    def test_absent_max_tables_do_not_prevent_two_players_catching_shared_wild(self):
        fixture = self.fixture
        configuration = dict(fixture.cfg)
        configuration.update(max_system={'enabled': False}, catch_probability=1)
        database = Database(fixture.path / 'without-max.sqlite3')
        fixture.game = Game(configuration, database)
        fixture.db = database
        fixture.tickets = [fixture.game.authenticate(wire.AuthRequest(
            auth_id='ordinary-shared-wild-' + str(index))).ticket for index in range(2)]
        with database.transaction() as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='max_members'").fetchone())
            fixture.game.seed(connection, 52.08, 5.12)
            spawn = dict(connection.execute(
                'SELECT s.* FROM spawns s JOIN spawn_generations g ON g.spawn_id=s.id '
                'WHERE s.expires_ms>? ORDER BY s.id LIMIT 1', (fixture.clock,)).fetchone())
        request = p.EncounterProto(encounter_id=spawn['id'], spawnpoint_id=spawn['spawnpoint'],
                                   player_lat_degrees=spawn['lat'], player_lng_degrees=spawn['lng'])
        capture = p.CatchPokemonProto(encounter_id=spawn['id'], spawn_point_guid=spawn['spawnpoint'],
                                     pokeball=1, hit_pokemon=True)
        storage_ids = []
        for index in range(2):
            self.assertEqual(fixture.call(102, request, p.EncounterOutProto, index=index).status, 1)
            caught = fixture.call(103, capture, p.CatchPokemonOutProto, index=index)
            self.assertEqual(caught.status, 1)
            storage_ids.append(caught.captured_pokemon_id)
        self.assertNotEqual(*storage_ids)
