"""Registered base-form Power Spots through real game RPCs; not Android proof."""
import copy
import json
import unittest

from s2sphere import CellId, LatLng
from dittobuffer.core.database import Database
from dittobuffer.gameplay.game import Game, ROOT, p, wire
from dittobuffer.gameplay import max_system as mx
from tests import test_max_manual as support


class MaxFormCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = support.ManualPowerSpotTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        # The reviewed catalog contains both zero base forms and explicit
        # NORMAL defaults. This synthetic backend reference declares those
        # identities, not replacement models or invented aliases.
        reference = json.loads(self.fixture.reference.read_text())
        for species in (*range(1, 13), 25, 831):
            reference['species_groups'][f'{species}:0'] = 1
            reference['species_groups'][f"{species}:{self.fixture.game.species[species]['form']}"] = 1
        shared = p.GameMasterClientTemplateProto(template_id='BREAD_SHARED_SETTINGS')
        shared.bread_settings.allowed_sourdough_pokemon.add(
            pokemon_id=6, form=[p.PokemonDisplayProto.Form_CHARIZARD_NORMAL], bread_mode=2)
        reference['templates'][shared.template_id] = shared.SerializeToString().hex()
        self.fixture.reference.write_text(json.dumps(reference))
        mx.reference.cache_clear()
        self.addCleanup(mx.reference.cache_clear)
        self.configuration = copy.deepcopy(self.fixture.base)
        balance = json.loads((ROOT / 'config/family.json').read_text(encoding='utf-8'))['max_system']['development_balance']
        self.configuration['max_system'].update(battle_enabled=True, development_balance=balance,
            pools=[{'species': 25, 'form': 0, 'mode': 1, 'tier': 1, 'level': 10},
                   {'species': 831, 'form': 0, 'mode': 1, 'tier': 1, 'level': 10},
                   {'species': 6, 'form': p.PokemonDisplayProto.Form_CHARIZARD_NORMAL,
                    'mode': 2, 'tier': 7, 'level': 10}])
        self.db = Database(self.fixture.path / 'form-rpcs.sqlite3')
        self.game = Game(self.configuration, self.db)
        self.ticket = self.game.authenticate(wire.AuthRequest(auth_id='max-base-form-unit')).ticket
        self.counter = 0

    def call(self, method, request, response):
        self.counter += 1
        envelope = wire.RpcRequest(rpc_id=self.counter)
        envelope.requests.add(method=method, body=request.SerializeToString())
        reply = self.game.rpc(envelope, self.ticket, run='max-base-form-unit')
        self.assertEqual(reply.status, 1)
        return response.FromString(reply.payloads[0].payload)

    def place(self, species, form, mode=1):
        with self.db.transaction() as connection:
            return mx.place(self.game, connection, 52.08, 5.12, 'Explicit base-form test',
                            {'species': species, 'form': form, 'mode': mode}, 3600)

    def test_station_info_3051_accepts_registered_explicit_zero_form(self):
        for species in (25, 831):
            with self.subTest(species=species):
                placed = self.place(species, 0)
                response = self.call(3051, p.GetStationInfoProto(station_id=placed['id'],
                    station_lat_degrees=placed['lat'], station_lng_degrees=placed['lng']),
                    p.GetStationInfoOutProto)
                self.assertEqual(response.result, 1)
                pokemon = response.station_proto.battle_details.battle_pokemon
                self.assertEqual((pokemon.pokemon_id, pokemon.pokemon_display.form,
                                  pokemon.pokemon_display.bread_mode_enum), (species, 0, 1))

    def test_map_106_serializes_registered_zero_form_station_without_exception(self):
        ids = {self.place(species, 0)['id'] for species in (25, 831)}
        cell = CellId.from_lat_lng(LatLng.from_degrees(52.08, 5.12)).parent(12).id()
        response = self.call(106, p.GetMapObjectsProto(cell_id=[cell], player_lat=52.08,
            player_lng=5.12), p.GetMapObjectsOutProto)
        stations = [station for value in response.map_cell for station in value.stations]
        self.assertTrue(ids <= {station.id for station in stations})
        for station in stations:
            self.assertEqual(station.battle_details.battle_pokemon.pokemon_display.bread_mode_enum,
                             2 if station.battle_details.battle_pokemon.pokemon_id == 6 else 1)
        self.assertEqual(p.ClientMapCellProto.DESCRIPTOR.fields_by_name['stations'].number, 17)
        self.assertEqual(p.ClientMapCellProto.DESCRIPTOR.fields_by_name['routes'].number, 20)

    def test_zero_form_pools_do_not_abort_map_and_inventory_batch(self):
        # The failing migrated family profile explicitly released these 13
        # base identities. One station used to abort the complete RPC batch,
        # leaving wild Pokemon, forts and inventory updates undelivered.
        species = [*range(1, 13), 25]
        self.configuration['max_system']['pools'] = [
            {'species': value, 'form': 0, 'mode': 1} for value in species]
        self.game = Game(self.configuration, self.db)
        ids = {self.place(value, 0)['id'] for value in species}
        # main removed generated tile POIs; own this regression's explicit fort.
        with self.db.transaction() as db:
            db.execute("INSERT INTO forts VALUES('zero-form-fixture',1,'Fixture Stop',52.08,5.12)")
        cell = CellId.from_lat_lng(LatLng.from_degrees(52.08, 5.12)).parent(12).id()
        envelope = wire.RpcRequest(rpc_id=1)
        envelope.requests.add(method=106, body=p.GetMapObjectsProto(
            cell_id=[cell], player_lat=52.08, player_lng=5.12).SerializeToString())
        envelope.requests.add(method=4, body=p.GetHoloholoInventoryProto().SerializeToString())
        reply = self.game.rpc(envelope, self.ticket, run='zero-base-map-inventory')
        self.assertEqual(reply.status, 1)
        self.assertEqual(len(reply.payloads), 2)
        world = p.GetMapObjectsOutProto.FromString(reply.payloads[0].payload)
        self.assertEqual(world.status, 1)
        self.assertTrue(ids <= {v.id for c in world.map_cell for v in c.stations})
        self.assertTrue(any(c.catchable_pokemon for c in world.map_cell))
        self.assertTrue(any(c.fort for c in world.map_cell))
        inventory = p.GetHoloholoInventoryOutProto.FromString(reply.payloads[1].payload)
        self.assertTrue(inventory.success)

    def test_unset_form_preserves_legacy_omitted_pool_default(self):
        self.game.max_released_pools = [{'species': 831, 'mode': 1}]
        unset = p.PokemonProto(pokemon_id=831)
        self.assertEqual(unset.pokemon_display.form, 0)
        self.assertTrue(mx.capable(self.game, unset))
        self.assertEqual(mx.attach(self.game, unset).pokemon_display.form, 0)
        explicit = p.PokemonProto(pokemon_id=831)
        explicit.pokemon_display.form = self.game.species[831]['form']
        self.assertTrue(mx.capable(self.game, explicit))

    def test_nonzero_forms_and_gmax_source_allowlist_are_not_broadened(self):
        self.game.max_released_pools = [{'species': 25, 'form': 0, 'mode': 1},
            {'species': 6, 'form': p.PokemonDisplayProto.Form_CHARIZARD_NORMAL, 'mode': 2}]
        pikachu = p.PokemonProto(pokemon_id=25)
        pikachu.pokemon_display.form = self.game.species[25]['form']
        self.assertNotEqual(pikachu.pokemon_display.form, 0)
        self.assertFalse(mx.capable(self.game, pikachu))
        with self.assertRaisesRegex(ValueError, 'not explicitly released'):
            mx.attach(self.game, pikachu)
        charizard = p.PokemonProto(pokemon_id=6)
        charizard.pokemon_display.form = p.PokemonDisplayProto.Form_CHARIZARD_NORMAL
        self.assertTrue(mx.capable(self.game, charizard, 2))
        placed = self.place(6, p.PokemonDisplayProto.Form_CHARIZARD_NORMAL, 2)
        response = self.call(3051, p.GetStationInfoProto(station_id=placed['id']), p.GetStationInfoOutProto)
        self.assertEqual(response.station_proto.battle_details.battle_pokemon.pokemon_display.form,
                         p.PokemonDisplayProto.Form_CHARIZARD_NORMAL)
        with self.assertRaisesRegex(ValueError, 'not source-allowlisted'):
            mx.boss_definition(self.game, {'species': 6, 'form': 0, 'mode': 2})
