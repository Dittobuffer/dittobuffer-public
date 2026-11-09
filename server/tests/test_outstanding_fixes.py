import contextlib
import json
import tempfile
import unittest
from pathlib import Path

from dittobuffer.admin.admin import Admin
from dittobuffer.core.database import Database
from dittobuffer.gameplay import mega, rocket
from dittobuffer.gameplay.game import Game, ROOT, p
from dittobuffer.gameplay.species import apply_stats, make_pokemon


class FixRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'builds')
        config = json.loads((ROOT / 'config/development.json').read_text(encoding='utf8'))
        config.update(database=str(Path(self.tmp.name) / 'fixes.sqlite3'),
                      world={'enabled': False}, raids={'enabled': False},
                      catch_probability=1.0)
        self.config = config
        self.game = Game(config, Database(config['database']))
        with self.game.db.transaction() as db:
            db.execute('INSERT INTO players(id,name,created_ms,revision,xp,stardust) '
                       'VALUES(?,?,?,?,?,?)',
                       ('player', 'Tester', 1, 1, 999_999_999, 999_999))
            db.execute("INSERT INTO spawns VALUES(900,'point',25,52.37,4.89,9999999999999)")
            self.mon = make_pokemon(p, self.game.species[25], 25, 40, [10, 10, 10])
            apply_stats(self.mon, self.game.species[25], 40)
            self.mon.id = 123
            self.mon.cp_multiplier = .796  # pre-fix formula value
            db.execute('INSERT INTO pokemon(id,player_id,spawn_id,species,data) VALUES(?,?,?,?,?)',
                       (123, 'player', 900, 25, self.mon.SerializeToString()))
            db.execute('INSERT INTO pokemon_levels VALUES(?,?)', (123, 40))

    def tearDown(self):
        self.tmp.cleanup()

    def test_balloon_shadow_catch_accepts_the_invasion_ball(self):
        with self.game.db.transaction() as db:
            balloon = rocket.spawn(self.game, db, 'leader', 'cliff', 52.37, 4.89, 600)
            request = p.InvasionEncounterProto()
            request.incident_lookup.incident_id = balloon['incident_id']
            encounter = self.game.rpc_invasion_encounter(
                db, 'player', request.SerializeToString())
            self.assertEqual(encounter.status, 1)
            catch = p.CatchPokemonProto(
                encounter_id=encounter.encounter_id,
                pokeball=encounter.invasion_ball,
                hit_pokemon=True,
            )
            # Native invasion encounters do not carry a WildPokemon spawn guid.
            out = self.game.rpc_catch_pokemon(db, 'player', catch.SerializeToString())
            self.assertEqual(out.status, 1)
            self.assertNotEqual(out.captured_pokemon_id, 0)
            self.assertEqual(db.execute('SELECT state FROM encounters WHERE player_id=? AND spawn_id=?',
                                        ('player', encounter.encounter_id)).fetchone()[0], 'caught')

    def test_balloon_catch_survives_a_missing_invasion_encounter_row(self):
        with self.game.db.transaction() as db:
            balloon = rocket.spawn(self.game, db, 'leader', 'cliff', 52.37, 4.89, 600)
            request = p.InvasionEncounterProto()
            request.incident_lookup.incident_id = balloon['incident_id']
            encounter = self.game.rpc_invasion_encounter(
                db, 'player', request.SerializeToString())
            # Reproduce the deferred-client flow that reaches CATCH_POKEMON
            # without the intermediate encounter-ownership row.
            db.execute('DELETE FROM encounter_deadlines WHERE player_id=? AND spawn_id=?',
                       ('player', encounter.encounter_id))
            db.execute('DELETE FROM encounters WHERE player_id=? AND spawn_id=?',
                       ('player', encounter.encounter_id))
            catch = p.CatchPokemonProto(encounter_id=encounter.encounter_id,
                                        pokeball=encounter.invasion_ball,
                                        hit_pokemon=True)
            out = self.game.rpc_catch_pokemon(db, 'player', catch.SerializeToString())
            self.assertEqual(out.status, 1, out)
            self.assertNotEqual(out.captured_pokemon_id, 0)
            self.assertIsNone(db.execute('SELECT 1 FROM rocket_balloons').fetchone())

    def test_purify_accepts_fixed64_wire_id_from_the_client(self):
        from dittobuffer.core import management
        body = bytes([0x09]) + (123).to_bytes(8, 'little')
        with self.game.db.transaction() as db:
            mon = p.PokemonProto.FromString(
                db.execute('SELECT data FROM pokemon WHERE id=123').fetchone()[0])
            mon.pokemon_display.alignment = 1
            db.execute('UPDATE pokemon SET data=? WHERE id=123', (mon.SerializeToString(),))
            db.execute('INSERT OR IGNORE INTO candy VALUES(?,?,?)',
                       ('player', self.game.species[25]['family'], 999))
            out = management.purify(self.game, db, 'player', body)
            self.assertEqual(out.status, p.PurifyPokemonOutProto.Status.Value('Status_Success'))
            self.assertEqual(out.purified_pokemon.id, 123)

    def test_game_master_has_shadow_and_shared_move_settings(self):
        from dittobuffer.core import master
        from protocol.opaque import fields
        templates = {}
        for payload in master.template_bytes(self.config):
            fields_in = {number: value for number, kind, _, value in fields(payload)}
            if 1 in fields_in:
                templates[fields_in[1].decode()] = payload
        self.assertIn('SHARED_MOVE_SETTINGS', templates)
        shared = p.GameMasterClientTemplateProto.FromString(templates['SHARED_MOVE_SETTINGS'])
        self.assertGreater(shared.shared_move_settings.shadow_third_move_unlock_candy_multiplier, 1)
        upgrade = p.GameMasterClientTemplateProto.FromString(templates['POKEMON_UPGRADE_SETTINGS'])
        self.assertEqual(upgrade.pokemon_upgrades.default_cp_boost_additional_level, 1)
        self.assertEqual(list(upgrade.pokemon_upgrades.candy_cost[40:]), [0] * 10)
        pokemon = {number: value for number, kind, _, value in fields(templates['V0150_POKEMON_MEWTWO_NORMAL'])}
        inner = {number: value for number, kind, _, value in fields(pokemon[2])}
        self.assertIn(45, inner)
        shadow = {number: value for number, kind, _, value in fields(inner[45])}
        self.assertEqual(shadow[1], 1000)
        self.assertEqual(shadow[2], 25)

    def test_legacy_cp_multiplier_is_reconciled_for_power_up(self):
        from dittobuffer.core import management
        with self.game.db.transaction() as db:
            projected = mega.project(self.game, db, self.mon)
            self.assertAlmostEqual(projected.cp_multiplier, 0.7903, places=6)
            self.assertGreaterEqual(projected.cp, 1)
            # Inventory shows the corrected CP; the upgrade must compare against
            # that same value instead of the stale stored CP.
            request = p.UpgradePokemonProto(pokemon_id=123, preview=True,
                                            number_of_upgrades=1,
                                            pokemon_current_cp=projected.cp)
            out = management.upgrade(self.game, db, 'player', request.SerializeToString())
            self.assertEqual(out.result, 1, out)
            self.assertGreater(out.upgraded_pokemon.cp, projected.cp)

    def test_expired_temporary_evolution_is_cleared_from_inventory(self):
        with self.game.db.transaction() as db:
            stale = p.PokemonProto()
            stale.CopyFrom(self.mon)
            stale.pokemon_display.current_temp_evolution = 1
            stale.pokemon_display.temporary_evolution_finish_ms = 1
            stale.pokemon_display.temp_evolution_is_locked = True
            db.execute('UPDATE pokemon SET data=? WHERE id=?',
                       (stale.SerializeToString(), 123))
            projected = mega.project(self.game, db, stale)
            self.assertEqual(projected.pokemon_display.current_temp_evolution, 0)
            self.assertEqual(projected.pokemon_display.temporary_evolution_finish_ms, 0)
            self.assertFalse(projected.pokemon_display.temp_evolution_is_locked)

    def test_clear_admin_spawns_expires_and_unmarks(self):
        config_path = Path(self.tmp.name) / 'config.json'
        config_path.write_text(json.dumps(self.config), encoding='utf8')
        admin = Admin(config_path, 0, '127.0.0.1')
        with contextlib.closing(admin.connect()) as db:
            db.execute('INSERT OR REPLACE INTO admin_spawns VALUES(?,?,?)', (900, 1, 'test'))
            db.execute('INSERT INTO encounters(player_id,spawn_id) VALUES(?,?)', ('player', 900))
            db.commit()
        result = admin.clear_admin_spawns()
        self.assertEqual(result['deleted'], 1)
        with contextlib.closing(admin.connect()) as db:
            expiry = db.execute('SELECT expires_ms FROM spawns WHERE id=900').fetchone()[0]
            self.assertLessEqual(expiry, __import__('time').time_ns() // 1_000_000)
            self.assertIsNone(db.execute('SELECT 1 FROM admin_spawns').fetchone())
            self.assertIsNone(db.execute('SELECT 1 FROM encounters').fetchone())


if __name__ == '__main__':
    unittest.main()
