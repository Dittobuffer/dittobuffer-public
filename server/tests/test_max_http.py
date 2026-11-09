"""Real loopback Max HTTP/PBS regression; not Android acceptance evidence.

The existing Max fixture supplies explicitly unearned owned Pokemon, particles,
reviewed-schema references and custom deterministic battle tuning. Requests use
the production HTTP handler, RPC authentication and combat/reward transactions.
"""
from copy import deepcopy
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dittobuffer.gameplay.game import ROOT, p, wire
from dittobuffer.gameplay.battle_master import MOVES
from dittobuffer.runtime.http import create_server
from tests import test_max_battle as battle_support


class MaxHttpTests(unittest.TestCase):
    def setUp(self):
        self.fixture = battle_support.MaxBattleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        # Range validation and combat share the same explicitly controlled
        # fixture clock; a future synthetic fix must not depend on wall time.
        clock = patch('dittobuffer.gameplay.friends.now_ms', lambda: self.fixture.clock)
        clock.start()
        self.addCleanup(clock.stop)
        self.listeners = []
        self.addCleanup(self.close_listeners)
        self.counter = 10000
        self.sequence = 0
        self.config = deepcopy(self.fixture.cfg)
        self.config.update(host='127.0.0.1', port=0, network={'mode': 'loopback'},
                           database=self.fixture.db.path.relative_to(ROOT).as_posix(),
                           catch_probability=1, pokemon_assets={'enabled': False},
                           avatar_assets={'enabled': False}, client_resources={'enabled': False})
        self.config['max_system'] = deepcopy(self.fixture.game.max_config)
        self.config_path = Path(self.fixture.tmp.name) / 'max-http.json'
        self.start_listener()

    def start_listener(self):
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')
        server = create_server(self.config_path.relative_to(ROOT).as_posix(), port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.listeners.append((server, worker))
        self.base = 'http://127.0.0.1:' + str(server.server_address[1])

    def close_listeners(self):
        while self.listeners:
            server, worker = self.listeners.pop()
            server.shutdown()
            server.server_close()
            worker.join(5)
            self.assertFalse(worker.is_alive())

    def request(self, path, response_type, *, body=None, headers=None):
        request = Request(self.base + path, data=body, headers=headers or {},
                          method='GET' if body is None else 'POST')
        with urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['Content-Type'], 'application/x-protobuf')
            raw = response.read()
            self.assertEqual(int(response.headers['Content-Length']), len(raw))
            if path.startswith('/max/'):
                self.assertGreater(int(response.headers['nbl-gs-rsp-ts']), 0)
            return response_type.FromString(raw)

    def rpc(self, method, request, response_type, player=0):
        self.counter += 1
        envelope = wire.RpcRequest(rpc_id=self.counter)
        envelope.requests.add(method=method, body=request.SerializeToString())
        response = self.request('/rpc', wire.RpcResponse, body=envelope.SerializeToString(),
                                headers={'Authorization': self.fixture.tickets[player],
                                         'x-dittobuffer-session': 'max-http-regression'})
        self.assertEqual(response.status, 1)
        self.assertEqual(response.payloads[0].method, method)
        return response_type.FromString(response.payloads[0].payload)

    def begin(self):
        fixture = self.fixture
        spot = fixture.stations[0]
        info = self.rpc(3051, p.GetStationInfoProto(station_id=spot['id']), p.GetStationInfoOutProto)
        self.assertEqual(info.result, 1)
        joined = self.rpc(2450, p.JoinBreadLobbyProto(station_id=spot['id'],
                          bread_battle_seed=info.station_proto.battle_details.bread_battle_seed),
                          p.JoinBreadLobbyOutProto)
        self.assertEqual(joined.result, 1)
        prepared = self.rpc(2453, p.PrepareBreadLobbyProto(station_id=spot['id'],
                            bread_lobby_id=joined.bread_lobby.bread_lobby_id), p.PrepareBreadLobbyOutProto)
        self.assertEqual(prepared.result, 1)
        fixture.clock = prepared.bread_lobby.bread_battle_start_ms
        started = self.rpc(2456, p.StartBreadBattleProto(station_id=spot['id'],
                           bread_battle_seed=info.station_proto.battle_details.bread_battle_seed,
                           attacking_pokemon_id=[fixture.parties[0].id]), p.StartBreadBattleOutProto)
        self.assertEqual(started.result, 1)
        return started

    def event(self, started, kind=1, sequence=None):
        self.sequence += 1
        fixture = self.fixture
        event = p.SendBattleEventProto(battle_id=str(started.lobby.bread_lobby_id),
                    player_id=fixture.players[0][:20], sequence_id=self.sequence if sequence is None else sequence,
                    battle_event=p.BattleEventProto(type=4, actor_id=fixture.players[0][:20],
                         attack=p.RaidPbsAttack(attack_type=kind, source_pokemon_id=fixture.parties[0].id)))
        return event, self.request('/max/api/send-player-event', p.SendBattleEventOutProto,
                                  body=event.SerializeToString(), headers=fixture.headers(started))

    def collection(self):
        fixture = self.fixture
        with fixture.db.transaction() as connection:
            player = tuple(connection.execute('SELECT xp,stardust FROM players WHERE id=?',
                                               (fixture.players[0],)).fetchone())
            items = [tuple(row) for row in connection.execute(
                'SELECT item,count FROM items WHERE player_id=? ORDER BY item', (fixture.players[0],))]
            return player, items

    def test_native_paths_drive_tap_loot_battle_rewards_catch_and_restart(self):
        fixture = self.fixture
        spot = fixture.stations[0]
        details = self.rpc(2462, p.GetStationedPokemonDetailsProto(station_id=spot['id']),
                           p.GetStationedPokemonDetailsOutProto)
        self.assertEqual(details.result, 1)
        loot_request = p.LootStationProto(station_id=spot['id'], station_lat_degrees=spot['lat'],
                                         station_lng_degrees=spot['lng'])
        loot = self.rpc(2461, loot_request, p.LootStationOutProto)
        self.assertEqual(loot.status, 1)
        self.assertEqual(self.rpc(2461, loot_request, p.LootStationOutProto), loot)
        summary = self.rpc(2467, p.GetMpSummaryProto(), p.GetMpSummaryOutProto)
        self.assertEqual(summary.mp_collected_today, 120)
        started = self.begin()
        headers = fixture.headers(started)
        self.assertEqual(self.request('/max/api/get-time', p.BattleClockSyncOutProto,
                         body=b'', headers=headers).result, 1)
        self.assertEqual(self.request('/max/api/get-state', p.BattleStateOutProto,
                         headers=headers).battle_state.state, 3)
        self.assertEqual(self.request('/max/api/get-state', p.BattleStateOutProto,
                         body=b'', headers=headers).battle_state.state, 3)
        for _ in range(40):
            state = fixture.combat(started)
            if state['status'] == 2:
                break
            self.assertEqual(state['status'], 1)
            if state['phase'] in (1, 3):
                fixture.clock = max(fixture.clock, state['phase_due'])
                self.request('/max/api/get-state', p.BattleStateOutProto, headers=headers)
                continue
            self.assertEqual(self.event(started, 5 if state['phase'] == 2 else 1)[1].result, 1)
            fixture.clock += max(2500, MOVES[fixture.parties[0].move1][2])
        else:
            self.fail('HTTP attacks did not defeat the explicitly tuned fixture boss')
        complete_request = p.CompleteBreadBattleProto(station_id=spot['id'],
                                        bread_battle_id=str(started.lobby.bread_lobby_id))
        result = self.rpc(2473, complete_request, p.CompleteBreadBattleOutProto)
        self.assertTrue(result.is_victory)
        self.assertEqual(result.result, 1)
        once = self.collection()
        self.assertEqual(self.rpc(2473, complete_request, p.CompleteBreadBattleOutProto), result)
        self.assertEqual(self.collection(), once)
        encounter = result.battle_results.post_battle_encounter
        caught = self.rpc(103, p.CatchPokemonProto(encounter_id=encounter.encounter_id,
                           spawn_point_guid=encounter.spawnpoint_id, pokeball=5, hit_pokemon=True),
                           p.CatchPokemonOutProto)
        self.assertEqual(caught.status, 1)
        after_catch = self.collection()
        self.close_listeners()
        self.start_listener()
        inventory = self.rpc(4, p.GetHoloholoInventoryProto(), p.GetHoloholoInventoryOutProto)
        records = [p.HoloInventoryItemProto.FromString(value.item)
                   for value in inventory.inventory_delta.inventory_item]
        self.assertIn(caught.captured_pokemon_id, [value.pokemon.id for value in records
                                                  if value.HasField('pokemon')])
        balance = next(value.item.count for value in records if value.HasField('item') and value.item.item == 1506)
        self.assertEqual(balance, 870)
        self.assertEqual(self.rpc(2473, complete_request, p.CompleteBreadBattleOutProto)
                         .battle_results.post_battle_encounter.throws_remaining, 4)
        self.assertEqual(self.collection(), after_catch)
        with fixture.db.transaction() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM max_victories').fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM max_ledger WHERE source LIKE 'battle:%'").fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM pokemon WHERE spawn_id=?',
                                               (encounter.encounter_id,)).fetchone()[0], 1)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_native_transport_rejects_bad_credentials_owners_and_sequence_reuse(self):
        fixture = self.fixture
        started = self.begin()
        good = fixture.headers(started)
        for headers in ({}, {**good, 'Authorization': 'Bearer invalid'},
                        {**good, 'nbl-player-id': fixture.players[1][:20]},
                        {**good, 'nbl-battle-id': str(started.lobby.bread_lobby_id + 1)}):
            with self.subTest(headers=list(headers)), self.assertRaises(HTTPError) as error:
                self.request('/max/api/get-state', p.BattleStateOutProto, headers=headers)
            self.assertEqual(error.exception.code, 403)
        event, first = self.event(started, sequence=44)
        self.assertEqual(first.result, 1)
        before = fixture.combat(started)
        retry = self.request('/max/api/send-player-event', p.SendBattleEventOutProto,
                             body=event.SerializeToString(), headers=good)
        self.assertEqual(retry, first)
        self.assertEqual(fixture.combat(started), before)
        event.battle_event.attack.attack_type = 2
        with self.assertRaises(HTTPError) as error:
            self.request('/max/api/send-player-event', p.SendBattleEventOutProto,
                         body=event.SerializeToString(), headers=good)
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(fixture.combat(started), before)
        with self.assertRaises(HTTPError) as error:
            self.request('/max/api/send-player-event/unknown', p.SendBattleEventOutProto,
                         body=b'', headers=good)
        self.assertEqual(error.exception.code, 404)

    def test_disabled_max_and_peer_allowlist_fail_closed(self):
        self.close_listeners()
        self.config['max_system']['enabled'] = False
        self.start_listener()
        for body in (None, b''):
            with self.subTest(body=body), self.assertRaises(HTTPError) as error:
                self.request('/max/api/get-state', p.BattleStateOutProto, body=body)
            self.assertEqual(error.exception.code, 403)
        self.close_listeners()
        self.config['max_system']['enabled'] = True
        self.config['network']['allowed_networks'] = []
        self.start_listener()
        for body in (None, b''):
            with self.subTest(body=body), self.assertRaises(HTTPError) as error:
                self.request('/max/api/get-state', p.BattleStateOutProto, body=body)
            self.assertEqual(error.exception.code, 403)

    def location_headers(self, prefix, session, *, player=0, latitude=52.08001):
        return {'Authorization': self.fixture.tickets[player],
                prefix+'session': session, prefix+'lat': str(latitude),
                prefix+'lng': '5.12001', prefix+'acc': '4',
                prefix+'fix-time': str(self.fixture.clock/1000),
                prefix+'provider': '3', prefix+'location-source': 'device'}

    def test_legacy_and_current_prefixes_keep_location_authentication_and_session_rollover(self):
        fixture = self.fixture
        fixture.clock += 1000
        request = wire.RpcRequest(rpc_id=55555)
        request.requests.add(method=3051, body=p.GetStationInfoProto(
            station_id=fixture.stations[0]['id']).SerializeToString())
        for index, prefix in enumerate(('x-librenlp-', 'x-dittobuffer-')):
            fixture.clock += 1000
            headers = self.location_headers(prefix, 'cold-run-'+str(index))
            # A new native session can reuse an RPC id with a different body.
            request.requests[0].body = p.GetStationInfoProto(
                station_id=fixture.stations[index]['id']).SerializeToString()
            reply = self.request('/rpc', wire.RpcResponse,
                                 body=request.SerializeToString(), headers=headers)
            self.assertEqual(p.GetStationInfoOutProto.FromString(reply.payloads[0].payload)
                             .station_proto.id, fixture.stations[index]['id'])
            with fixture.db.transaction() as connection:
                location = connection.execute('SELECT * FROM social_locations WHERE player_id=?',
                                               (fixture.players[0],)).fetchone()
                self.assertEqual(location['lat'], 52.08001)
                self.assertEqual(location['lng'], 5.12001)
                self.assertEqual(location['fix_ms'], fixture.clock)
                receipt = connection.execute('SELECT COUNT(*) FROM rpc_receipts WHERE rpc_id=? AND session_run=?',
                                               (request.rpc_id, 'cold-run-'+str(index))).fetchone()[0]
                self.assertEqual(receipt, 1)
            # The observed location must satisfy the actual collection range
            # gate, rather than merely appearing in a location readback.
            station = fixture.stations[index]
            loot_request = wire.RpcRequest(rpc_id=55556)
            loot_request.requests.add(method=2461, body=p.LootStationProto(
                station_id=station['id'], station_lat_degrees=station['lat'],
                station_lng_degrees=station['lng']).SerializeToString())
            loot = self.request('/rpc', wire.RpcResponse,
                                body=loot_request.SerializeToString(), headers=headers)
            self.assertEqual(p.LootStationOutProto.FromString(loot.payloads[0].payload).status, 1)
        fixture.clock += 1000
        request.rpc_id += 1
        other = self.location_headers('x-librenlp-', 'other-player', player=1, latitude=52.08002)
        self.request('/rpc', wire.RpcResponse, body=request.SerializeToString(), headers=other)
        with fixture.db.transaction() as connection:
            locations = {row['player_id']: row['lat'] for row in connection.execute('SELECT * FROM social_locations')}
            self.assertEqual(locations[fixture.players[0]], 52.08001)
            self.assertEqual(locations[fixture.players[1]], 52.08002)
            before = [tuple(row) for row in connection.execute('SELECT * FROM social_locations ORDER BY player_id')]
        request.rpc_id += 1
        with self.assertRaises(HTTPError) as error:
            self.request('/rpc', wire.RpcResponse, body=request.SerializeToString(),
                         headers={**other, 'Authorization': 'invalid-local-session'})
        self.assertEqual(error.exception.code, 400)
        with fixture.db.transaction() as connection:
            self.assertEqual([tuple(row) for row in connection.execute(
                'SELECT * FROM social_locations ORDER BY player_id')], before)

    def test_matching_prefixes_work_but_conflicts_are_rejected_before_mutation(self):
        fixture = self.fixture
        fixture.clock += 1000
        request = wire.RpcRequest(rpc_id=60000)
        request.requests.add(method=3051, body=p.GetStationInfoProto(
            station_id=fixture.stations[0]['id']).SerializeToString())
        current = self.location_headers('x-dittobuffer-', 'same-session')
        legacy = self.location_headers('x-librenlp-', 'same-session')
        matching = {**current, **legacy}
        reply = self.request('/rpc', wire.RpcResponse,
                             body=request.SerializeToString(), headers=matching)
        self.assertEqual(reply.status, 1)
        spot = fixture.stations[0]
        request.requests[0].method = 2461
        request.requests[0].body = p.LootStationProto(station_id=spot['id'],
                    station_lat_degrees=spot['lat'], station_lng_degrees=spot['lng']).SerializeToString()
        with fixture.db.transaction() as connection:
            before = [tuple(row) for row in connection.execute('SELECT * FROM social_locations ORDER BY player_id')]
            balance = connection.execute('SELECT balance FROM max_wallet WHERE player_id=?',
                                         (fixture.players[0],)).fetchone()[0]
        for suffix, conflict in (('lat', '52.08002'), ('session', 'different-session'), ('rpc-id', '2')):
            request.rpc_id += 1
            headers = {**matching, 'x-librenlp-'+suffix: conflict}
            if suffix == 'rpc-id':
                headers['x-dittobuffer-rpc-id'] = '1'
            with self.subTest(suffix=suffix), self.assertRaises(HTTPError) as error:
                self.request('/rpc', wire.RpcResponse, body=request.SerializeToString(), headers=headers)
            self.assertEqual(error.exception.code, 400)
            with fixture.db.transaction() as connection:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM rpc_receipts WHERE rpc_id=?',
                                                    (request.rpc_id,)).fetchone()[0], 0)
                self.assertEqual(connection.execute('SELECT balance FROM max_wallet WHERE player_id=?',
                                                    (fixture.players[0],)).fetchone()[0], balance)
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM max_claims').fetchone()[0], 0)
                self.assertEqual([tuple(row) for row in connection.execute(
                    'SELECT * FROM social_locations ORDER BY player_id')], before)


if __name__ == '__main__':
    unittest.main()
