import json
import tempfile
import unittest
from pathlib import Path

from dittobuffer.core.database import Database
from dittobuffer.gameplay import rocket
from dittobuffer.gameplay.game import Game, ROOT, p


class RocketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'builds')
        config = json.loads((ROOT / 'config/development.json').read_text(encoding='utf8'))
        config.update(raids={'enabled': False})
        self.db = Database(Path(self.tmp.name) / 'rocket.sqlite3')
        self.game = Game(config, self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_balloon_spawns_shadow_encounter_and_rpc_round_trip(self):
        with self.db.transaction() as db:
            balloon = rocket.spawn(self.game, db, 'leader', 'arlo', 52.37, 4.89, 600)
            row = db.execute('SELECT data FROM spawn_attributes WHERE spawn_id=?',
                             (int(balloon['spawn_id']),)).fetchone()
            mon = p.PokemonProto.FromString(row['data'])
            self.assertEqual(mon.pokemon_display.alignment, 1)
            self.assertTrue(all(10 <= value <= 15 for value in balloon['ivs']))

            out = p.GetRocketBalloonOutProto.FromString(
                self.game.rpc_get_rocket_balloon(
                    db, 'player', p.GetRocketBalloonProto().SerializeToString()
                ).SerializeToString())
            self.assertEqual(out.status, 1)
            self.assertEqual(out.display.incident_display.incident_id,
                             balloon['incident_id'])

            request = p.StartRocketBalloonIncidentProto()
            request.incident_lookup.incident_id = balloon['incident_id']
            started = self.game.rpc_start_rocket_balloon_incident(
                db, 'player', request.SerializeToString())
            self.assertEqual(started.status, 1)
            # The balloon click must produce a renderable incident: the old
            # empty step left the native panel inert and looked like a no-op.
            incident = started.incident
            self.assertEqual(incident.context,
                             p.EnumWrapper.InvasionContext.Value('InvasionContext_ROCKET_BALLOON'))
            self.assertEqual(len(incident.step), 1)
            self.assertTrue(incident.step[0].HasField('invasion_encounter'))
            self.assertFalse(incident.step[0].HasField('invasion_battle'))
            self.assertTrue(incident.completion_display.incident_completed)
            self.assertTrue(incident.completion_display.HasField('invasion_finished'))
            rocket.clear(db, balloon['incident_id'])
            self.assertEqual(rocket.active(db), [])

    def test_invasion_encounter_and_rescue(self):
        with self.db.transaction() as db:
            db.execute('INSERT INTO players(id,name,created_ms,revision) VALUES(?,?,?,?)',
                       ('player', 'Tester', 1, 1))
            balloon = rocket.spawn(self.game, db, 'grunt', 'grunt-fire', 52.37, 4.89, 600)
            request = p.InvasionEncounterProto()
            request.incident_lookup.incident_id = balloon['incident_id']
            out = self.game.rpc_invasion_encounter(db, 'player', request.SerializeToString())
            # The client parses status as the InvasionStatus.Status enum (varint),
            # not as a nested message. A length-delimited status crashes the
            # Rocket reward screen and prevents the encounter from opening.
            self.assertEqual(out.SerializeToString()[:2], b'\x08\x01')
            decoded = p.InvasionEncounterOutProto.FromString(out.SerializeToString())
            self.assertEqual(decoded.status, p.InvasionStatus.Status.Status_Success)
            self.assertEqual(decoded.encounter_pokemon.pokemon_display.alignment, 1)
            record = rocket.encounter(db, 'player', balloon['incident_id'])
            self.assertIsNotNone(record)
            self.assertEqual(decoded.encounter_id, record['spawn_id'])
            self.assertNotEqual(decoded.encounter_id, int(balloon['spawn_id']))
            self.assertEqual(decoded.throws_remaining, 8)
            battle = p.UpdateInvasionBattleProto()
            battle.incident_lookup.incident_id = balloon['incident_id']
            battle.update_type = p.UpdateInvasionBattleProto.UpdateType_WinBattle
            battle_out = self.game.rpc_invasion_battle_update(
                db, 'player', battle.SerializeToString())
            self.assertEqual(battle_out.SerializeToString()[:2], b'\x08\x01')
            self.assertEqual(p.UpdateInvasionBattleOutProto.FromString(
                battle_out.SerializeToString()).status,
                p.InvasionStatus.Status.Status_Success)
            rescued = rocket.rescue(db, 'player', balloon['incident_id'])
            self.assertEqual(rescued['species'], balloon['species'])
            self.assertIsNotNone(db.execute('SELECT 1 FROM pokemon WHERE id=?',
                                            (int(rescued['pokemon_id']),)).fetchone())
            self.assertEqual(rocket.active(db), [])

    def test_rocket_combat_lineup_follows_the_local_rotation(self):
        from dittobuffer.gameplay import rocket_progression
        self.game.rocket.update(leader_level=40,boss_level=40)
        self.game.rocket_leaders=json.loads((ROOT/'src/dittobuffer/gameplay/rocket-leaders.json').read_text())
        definition=rocket_progression.definition(self.game,'arlo')
        self.assertEqual(len(definition['species']),3)
        self.assertGreaterEqual(definition['shields'],1)
        self.assertTrue(all(species in self.game.species for species in definition['species']))
        self.assertEqual(definition,rocket_progression.definition(self.game,'arlo'))

    def test_unknown_character_is_rejected(self):
        with self.db.transaction() as db:
            with self.assertRaises(ValueError):
                rocket.spawn(self.game, db, 'leader', 'not-a-leader', 52.37, 4.89, 600)
