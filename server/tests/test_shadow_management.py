import json
import tempfile
import unittest
from pathlib import Path

from dittobuffer.core import management
from dittobuffer.core.database import Database
from dittobuffer.gameplay.game import Game, ROOT, p
from dittobuffer.gameplay.species import apply_stats, make_pokemon


class ShadowManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'builds')
        config = json.loads((ROOT / 'config/development.json').read_text(encoding='utf8'))
        config.update(database=str(Path(self.tmp.name) / 'management.sqlite3'), raids={'enabled': False})
        self.game = Game(config, Database(config['database']))
        with self.game.db.transaction() as db:
            db.execute('INSERT INTO players(id,name,created_ms,revision,xp,stardust) '
                       'VALUES(?,?,?,?,?,?)', ('player', 'Tester', 1, 1, 999_999_999, 999_999))
            db.execute("INSERT INTO spawns VALUES(900,'point',25,52.37,4.89,9999999999999)")
            db.execute('INSERT INTO items VALUES(?,?,?)', ('player', 1201, 10))
            family = self.game.species[25]['family']
            db.execute('INSERT INTO candy VALUES(?,?,?)', ('player', family, 999))
            db.execute('INSERT INTO mega_catch_xl VALUES(?,?,?)', ('player', family, 999))
            self.mon = make_pokemon(p, self.game.species[25], 25, 40, [10, 10, 10])
            apply_stats(self.mon, self.game.species[25], 40)
            self.mon.id = 123
            self.mon.pokemon_display.alignment = 1
            self.mon.move2 = 322  # Frustration
            db.execute('INSERT INTO pokemon(id,player_id,spawn_id,species,data) VALUES(?,?,?,?,?)',
                       (123, 'player', 900, 25, self.mon.SerializeToString()))
            db.execute('INSERT INTO pokemon_levels VALUES(?,?)', (123, 40))

    def tearDown(self):
        self.tmp.cleanup()

    def test_shadow_pokemon_can_learn_a_tm_move(self):
        with self.game.db.transaction() as db:
            request = p.UseItemMoveRerollProto(pokemon_id=123, item=1201)
            out = self.game.rpc_use_item_move_reroll(db, 'player', request.SerializeToString())
            self.assertEqual(out.result, 1)
            self.assertEqual(out.updated_pokemon.id, 123)
            self.assertEqual(out.updated_pokemon.pokemon_display.alignment, 1)

    def test_shadow_pokemon_can_unlock_a_second_charged_attack(self):
        with self.game.db.transaction() as db:
            request = p.UnlockPokemonMoveProto(pokemon_id=123)
            out = self.game.rpc_unlock_special_move(db, 'player', request.SerializeToString())
            self.assertEqual(out.result, 1)
            self.assertNotEqual(out.unlocked_pokemon.move3, 0)
            self.assertEqual(out.unlocked_pokemon.pokemon_display.alignment, 1)

    def test_level_41_power_up_is_available_with_xl_candy(self):
        with self.game.db.transaction() as db:
            request = p.UpgradePokemonProto(pokemon_id=123, number_of_upgrades=1, preview=True)
            out = management.upgrade(self.game, db, 'player', request.SerializeToString())
            self.assertEqual(out.result, 1)
            self.assertTrue(out.bulk_upgrades_cost_table)
            # Level 41's exact reference CPM; the old formula reported 0.802.
            self.assertAlmostEqual(out.next_upgraded_pokemon.cp_multiplier, 0.7953, places=6)

    def test_purification_updates_alignment_ivs_and_costs(self):
        with self.game.db.transaction() as db:
            request = p.PurifyPokemonProto(pokemon_id=123)
            out = management.purify(self.game, db, 'player', request.SerializeToString())
            self.assertEqual(out.status, p.PurifyPokemonOutProto.Status.Value('Status_Success'))
            self.assertEqual(out.purified_pokemon.pokemon_display.alignment, 2)
            self.assertEqual(out.purified_pokemon.individual_attack, 12)
            self.assertEqual(out.purified_pokemon.move2, 323)
            self.assertTrue(out.purified_pokemon.move2_is_purified_exclusive)
            self.assertEqual(db.execute('SELECT stardust FROM players WHERE id=?', ('player',)).fetchone()[0],
                             998_999)
            self.assertEqual(db.execute('SELECT count FROM candy WHERE player_id=? AND family=?',
                                        ('player', self.game.species[25]['family'])).fetchone()[0], 974)


if __name__ == '__main__':
    unittest.main()
