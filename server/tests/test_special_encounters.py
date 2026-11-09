"""Real protobuf/SQLite tests; these do not establish Android playback."""
import json
import unittest
from unittest.mock import patch

from dittobuffer.gameplay import research, special_encounters as special
from dittobuffer.gameplay.game import Game, Database, RpcError, p, wire
from tests import test_research as existing_research_tests


class SpecialEncounterTests(unittest.TestCase):
    setUp = existing_research_tests.ResearchTests.setUp
    tearDown = existing_research_tests.ResearchTests.tearDown
    call = existing_research_tests.ResearchTests.call

    def grant(self, identifier='mew_research', key='special-test', strict=False):
        definition = special.registry()[identifier]
        species = definition['species']
        shiny = definition['shiny_selector'] == 'shiny'
        self.game.shiny_eligible.add((species, self.game.species[species]['form']))
        reward = dict(type='encounter', species=species, level=15, shiny_rate=int(shiny))
        if strict:
            reward['special_encounter'] = identifier
        research.validate_reward(self.game, reward)
        with self.db.transaction() as db:
            research.grant(self.game, db, self.player, key, [reward], encounter_type=4)
            row = dict(db.execute('SELECT * FROM research_encounters WHERE player_id=? AND quest_id=?', (self.player, key)).fetchone())
        return row, reward

    def open(self, row, ticket=None):
        return self.call('QUEST_ENCOUNTER', p.QuestEncounterProto(quest_id=row['client_quest_id'], pokemon_id=row['spawn_id']), p.QuestEncounterOutProto, ticket=ticket)

    def test_all_native_dispatches_and_normal_research_exclusions(self):
        definitions = special.registry()
        self.assertEqual(len(definitions), 12)
        self.assertEqual(len({v['species'] for v in definitions.values()}), 11)
        for identifier, definition in definitions.items():
            shiny = definition['shiny_selector'] == 'shiny'
            self.assertEqual(special.select(definition['species'], shiny, 4)['encounter_id'], identifier)
            for context in (0, 3, 6, 8, 10, 21):
                if identifier=='victini_research' and context in (6,10):
                    self.assertEqual(special.select(494,shiny,context)['controller'],'VictiniEncounter')
                else:self.assertIsNone(special.select(definition['species'], shiny, context))
            row, _ = self.grant(identifier, identifier)
            result = self.open(row)
            self.assertEqual(result.result, 1)
            self.assertEqual(result.pokemon.pokemon_display.shiny, shiny)
            self.assertTrue(definition['required_resources'])
            self.assertEqual(result.pokemon.id, row['spawn_id'])
        self.assertIsNone(special.select(647, False, 4))
        self.assertIsNone(special.select(720, False, 4))
        self.assertIsNone(special.select(893, False, 4))

    def test_free_scripted_ball_miss_final_hit_replay_restart(self):
        row, _ = self.grant()
        self.assertEqual(self.open(row).result, 1)
        with self.db.transaction() as db:
            db.execute('UPDATE items SET count=0 WHERE player_id=? AND item=1', (self.player,))
        body = p.CatchPokemonProto(encounter_id=row['spawn_id'], pokeball=1, hit_pokemon=False)
        self.assertEqual(self.call('CATCH_POKEMON', body, p.CatchPokemonOutProto).status, 4)
        body.hit_pokemon = True
        with patch('dittobuffer.gameplay.game.secrets.randbelow', return_value=999999):
            caught = self.call('CATCH_POKEMON', body, p.CatchPokemonOutProto)
        self.assertEqual(caught.status, 1)
        self.game = Game(self.cfg, Database(self.db.path))
        self.assertEqual(self.call('CATCH_POKEMON', body, p.CatchPokemonOutProto), caught)
        self.assertEqual(self.open(row).result, 3)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1', (self.player,)).fetchone()[0], 0)
            owned, _ = special.owned(db, self.player, row['spawn_id'])
            self.assertEqual(owned['captured_id'], caught.captured_pokemon_id)
            self.assertEqual(owned['playback_state'], 'CAPTURED')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pokemon WHERE player_id=?', (self.player,)).fetchone()[0], 1)

    def test_account_isolation_and_ball_and_berry_restrictions(self):
        row, _ = self.grant()
        with self.assertRaises(RpcError):
            self.call('ENCOUNTER', p.EncounterProto(encounter_id=row['spawn_id'],spawnpoint_id='quest:special-test'), p.EncounterOutProto)
        other = self.game.authenticate(wire.AuthRequest(auth_id='special-other')).ticket
        self.assertEqual(self.open(row, other).result, 2)
        self.open(row)
        with self.assertRaises(RpcError):
            self.call('CATCH_POKEMON', p.CatchPokemonProto(encounter_id=row['spawn_id'], pokeball=3, hit_pokemon=True), p.CatchPokemonOutProto)
        self.assertEqual(self.call('USE_ITEM_ENCOUNTER', p.UseItemEncounterProto(encounter_id=row['spawn_id'], item=701), p.UseItemEncounterOutProto).status, 4)
        with self.assertRaises(RpcError):
            self.call('CATCH_POKEMON', p.CatchPokemonProto(encounter_id=row['spawn_id'], pokeball=1, hit_pokemon=True), p.CatchPokemonOutProto, ticket=other)

    def test_entitlement_recovery_shiny_and_no_reroll(self):
        row, reward = self.grant('shiny_jirachi_research')
        first = self.open(row)
        self.game = Game(self.cfg, Database(self.db.path))
        again = self.open(row)
        self.assertEqual(first.pokemon, again.pokemon)
        self.assertTrue(again.pokemon.pokemon_display.shiny)
        with self.db.transaction() as db:
            research.grant(self.game, db, self.player, row['quest_id'], [reward], encounter_type=4)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM research_encounters WHERE player_id=?', (self.player,)).fetchone()[0], 1)
            state, definition = special.owned(db, self.player, row['spawn_id'])
            self.assertEqual(state['open_count'], 2)
            self.assertEqual(state['playback_state'], 'OPEN_REQUESTED')
            self.assertTrue(definition['shiny'])

    def test_missing_resources_keep_owned_entitlement(self):
        row, _ = self.grant('victini_research', strict=True)
        with patch.object(special, 'resource_status', return_value=dict(ready=False, missing=['VictiniScene'])):
            self.assertEqual(self.open(row).result, 2)
        with patch.object(special, 'resource_status', return_value=dict(ready=True, missing=[])):
            self.assertEqual(self.open(row).result, 1)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM research_encounters WHERE spawn_id=?', (row['spawn_id'],)).fetchone()[0], 1)

    def test_invalid_configuration_does_not_fallback(self):
        for reward in [dict(species=151, special_encounter='invented'), dict(species=151, special_encounter='victini_research'), dict(species=647, special_encounter='shiny_keldeo_research')]:
            with self.assertRaises(ValueError):
                research.validate_reward(self.game, dict(type='encounter', **reward))
        row, reward = self.grant()
        reward['special_encounter'] = 'mew_research'
        with self.assertRaises(ValueError), self.db.transaction() as db:
            research.grant(self.game, db, self.player, 'wrong-source', [reward], encounter_type=6)

    def test_normal_research_keeps_inventory_rules(self):
        with self.db.transaction() as db:
            research.grant(self.game, db, self.player, 'ordinary', [dict(type='encounter', species=16)], encounter_type=6)
            row = dict(db.execute("SELECT * FROM research_encounters WHERE quest_id='ordinary'").fetchone())
            self.assertIsNone(special.capture_rules(db, self.player, row['spawn_id']))
        self.open(row)
        berry=p.UseItemEncounterProto(encounter_id=row['spawn_id'],item=701,spawn_point_guid='unrelated-quest')
        self.assertEqual(self.call('USE_ITEM_ENCOUNTER',berry,p.UseItemEncounterOutProto).status,1)
        berry.spawn_point_guid=row['client_quest_id']
        with self.db.transaction() as db:
            db.execute('INSERT INTO items VALUES(?,701,2) ON CONFLICT(player_id,item) DO UPDATE SET count=2',(self.player,))
        self.assertEqual(self.call('USE_ITEM_ENCOUNTER',berry,p.UseItemEncounterOutProto).status,0)
        self.assertEqual(self.call('USE_ITEM_ENCOUNTER',berry,p.UseItemEncounterOutProto).status,2)
        with self.db.transaction() as db:
            before = db.execute('SELECT count FROM items WHERE player_id=? AND item=1', (self.player,)).fetchone()[0]
            self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=701',(self.player,)).fetchone()[0],1)
        self.call('CATCH_POKEMON', p.CatchPokemonProto(encounter_id=row['spawn_id'], pokeball=1, hit_pokemon=True), p.CatchPokemonOutProto)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1', (self.player,)).fetchone()[0], before-1)

    def test_legacy_entitlement_backfill_preserves_pokemon_bytes(self):
        row, _ = self.grant()
        with self.db.transaction() as db:
            original = db.execute('SELECT data FROM spawn_attributes WHERE spawn_id=?', (row['spawn_id'],)).fetchone()[0]
            db.execute('DELETE FROM special_research_encounters WHERE spawn_id=?', (row['spawn_id'],))
        self.assertEqual(self.open(row).result, 1)
        with self.db.transaction() as db:
            state, definition = special.owned(db, self.player, row['spawn_id'])
            self.assertEqual(definition['encounter_id'], 'mew_research')
            self.assertEqual(state['open_count'], 1)
            self.assertEqual(db.execute('SELECT data FROM spawn_attributes WHERE spawn_id=?', (row['spawn_id'],)).fetchone()[0], original)

    def test_victini_native_generic_research_exception_does_not_generalize(self):
        with self.db.transaction() as db:
            research.grant(self.game,db,self.player,'victini-challenge',[dict(type='encounter',species=494)],encounter_type=6)
            row=dict(db.execute("SELECT * FROM research_encounters WHERE quest_id='victini-challenge'").fetchone())
            self.assertEqual(special.capture_rules(db,self.player,row['spawn_id'])['ball'],1)
            self.assertIsNone(special.select(151,False,6))
        self.assertEqual(self.open(row).result,1)

    def test_native_alternate_forms_are_frozen_across_reopen_and_restart(self):
        for identifier,species,form in [('shaymin_research',492,92),('meloetta_research',648,152)]:
            reward=dict(type='encounter',species=species,form=form,level=15,shiny_rate=0)
            research.validate_reward(self.game,reward)
            with self.db.transaction() as db:
                research.grant(self.game,db,self.player,identifier+'-alternate',[reward],encounter_type=4)
                row=dict(db.execute('SELECT * FROM research_encounters WHERE player_id=? AND quest_id=?',(self.player,identifier+'-alternate')).fetchone())
                _,definition=special.owned(db,self.player,row['spawn_id'])
                self.assertEqual(definition['form'],form)
                self.assertEqual(definition['encounter_id'],identifier)
            first=self.open(row)
            self.assertEqual(first.pokemon.pokemon_display.form,form)
            self.game=Game(self.cfg,Database(self.db.path))
            self.assertEqual(self.open(row).pokemon,first.pokemon)


if __name__ == '__main__':
    unittest.main()
