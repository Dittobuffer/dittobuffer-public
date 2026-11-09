"""Transitions across the existing gym, raid, health and reward boundaries."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_gyms as gym_cases
import test_raids as raid_cases
from dittobuffer.gameplay import gyms, raids, raid_transport, gym_battle
from dittobuffer.core.database import Database
from dittobuffer.gameplay.game import Game, p
from dittobuffer.gameplay.game import RpcError, wire
from dittobuffer.core.management import owned


class GymRaidBoundaryTests(unittest.TestCase):
    call = gym_cases.GymTests.call
    deploy = gym_cases.GymTests.deploy

    def setUp(self):
        gym_cases.GymTests.setUp(self)
        self.raid_clock = patch('server.raids.now_ms', lambda: self.clock)
        self.raid_clock.start()

    def tearDown(self):
        self.raid_clock.stop()
        gym_cases.GymTests.tearDown(self)

    def enable_raid(self, gym_id='gym'):
        self.config['raids'] = dict(enabled=True, epoch_ms=self.clock, egg_seconds=2,
            active_seconds=3600, rotation_seconds=7200, gym_ids=[gym_id],
            pools=[dict(species=16, tier=1, hp=60, cp=1000, weight=1)])
        self.game = Game(self.config, Database(self.db.path))

    def battle(self):
        return self.call(157, p.GymStartSessionProto(gym_id='gym',
            attacking_pokemon_id=[5, 6], defending_pokemon_id=1,
            player_lat_degrees=52.37, player_lng_degrees=4.89),
            p.GymStartSessionOutProto, 1)

    def test_feed_applies_elapsed_decay_before_restoration(self):
        self.assertEqual(self.deploy().result, 1)
        with self.db.transaction() as db:
            original = owned(db, self.players[0], 1).SerializeToString()
        self.clock += 4 * 3600000
        result = self.call(164, p.GymFeedPokemonProto(gym_id='gym', pokemon_id=1,
            item=701, starting_quantity=10, player_lat_degrees=52.37,
            player_lng_degrees=4.89), p.GymFeedPokemonOutProto)
        self.assertEqual(result.result, 1)
        self.assertAlmostEqual(result.gym_status_and_defenders.gym_defender[0]
                               .motivated_pokemon.motivation_now, .8)
        self.game = Game(self.config, Database(self.db.path))
        with self.db.transaction() as db:
            self.assertAlmostEqual(gyms.defenders(db, 'gym')[0]['motivation'], .8)
            self.assertEqual(owned(db, self.players[0], 1).SerializeToString(), original)
            self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=701',
                                       (self.players[0],)).fetchone()[0], 9)

    def test_motivated_gym_projection_matches_native_battle_nameplate_without_owned_rewrite(self):
        self.assertEqual(self.deploy().result, 1)
        with self.db.transaction() as db:
            original = owned(db, self.players[0], 1).SerializeToString()
            base_cp = owned(db, self.players[0], 1).cp
            db.execute('UPDATE gym_defenders SET motivation=.5 WHERE pokemon_id=1')
            shown = gyms.status(self.game, db, gyms.gym(db, 'gym')).gym_defender[0].motivated_pokemon
            self.assertEqual(shown.pokemon.cp, shown.cp_now)
            self.assertEqual(shown.cp_when_deployed, base_cp)
            self.assertEqual(owned(db, self.players[0], 1).SerializeToString(), original)
        battle = self.battle().battle
        self.assertEqual(battle.defender.active_pokemon.pokemon.cp, shown.pokemon.cp)

    def test_raid_egg_allows_deployment_but_active_raid_blocks_gym_actions(self):
        self.enable_raid()
        self.assertEqual(self.deploy().result, 1)
        self.clock += 2000
        self.assertEqual(self.deploy(8, 2).result, 19)
        result = self.call(164, p.GymFeedPokemonProto(gym_id='gym', pokemon_id=1,
            item=701, starting_quantity=10, player_lat_degrees=52.37,
            player_lng_degrees=4.89), p.GymFeedPokemonOutProto)
        self.assertEqual(result.result, 12)
        self.assertEqual(self.battle().result, 15)
        with self.db.transaction() as db:
            self.assertEqual(len(gyms.defenders(db, 'gym')), 1)
            self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=701',
                                       (self.players[0],)).fetchone()[0], 10)
        self.clock += 3600000
        self.assertEqual(self.deploy(8, 2).result, 1)
        self.assertEqual(self.battle().result, 1)

    def test_raid_hatch_releases_existing_gym_battle_without_victory_rewards(self):
        self.enable_raid()
        self.assertEqual(self.deploy().result, 1)
        start = self.battle()
        self.assertEqual(start.result, 1)
        self.clock += 2000
        response = self.call(158, p.GymBattleAttackProto(gym_id='gym',
            battle_id=start.battle.battle_id, player_lat_degrees=52.37,
            player_lng_degrees=4.89), p.GymBattleAttackOutProto, 1)
        self.assertEqual(response.result, 5)
        with self.db.transaction() as db:
            row = db.execute('SELECT settled,state FROM gym_battles').fetchone()
            state = json.loads(row['state'])
            self.assertEqual(row['settled'], 1)
            self.assertEqual(state['status'], 3)
            self.assertTrue(state['raid_interrupted'])
            self.assertFalse(gyms.battle_busy(db, self.players[1], [5, 6]))
            self.assertEqual(db.execute('SELECT xp FROM players WHERE id=?',
                                       (self.players[1],)).fetchone()[0], 10000)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gym_returns').fetchone()[0], 0)

    def test_gym_cannot_start_while_same_trainer_is_in_another_raid(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO forts VALUES('raid',0,'Raid',52.37,4.89)")
            db.execute('INSERT INTO items VALUES(?,1402,1)', (self.players[1],))
        self.enable_raid('raid')
        self.assertEqual(self.deploy().result, 1)
        self.clock += 2000
        with self.db.transaction() as db:
            raid = raids.schedule(self.game, db, gyms.gym(db, 'raid'))
        joined = self.call(159, p.JoinLobbyProto(gym_id='raid', raid_seed=raid['seed'],
            player_lat_degrees=52.37, player_lng_degrees=4.89), p.JoinLobbyOutProto, 1)
        self.assertEqual(joined.result, 1)
        self.clock += 21000
        started = self.call(165, p.StartRaidBattleProto(gym_id='raid', raid_seed=raid['seed'],
            attacking_pokemon_id=[5, 6], player_lat_degrees=52.37,
            player_lng_degrees=4.89), p.StartRaidBattleOutProto, 1)
        self.assertEqual(started.result, 1)
        self.assertEqual(self.battle().result, 9)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gym_battles').fetchone()[0], 0)

    def test_concurrent_returns_share_one_daily_cap_and_keep_owned_rows(self):
        self.config['gyms']['coin_minutes'] = 10
        self.assertEqual(self.deploy().result, 1)
        with self.db.transaction() as db:
            db.execute("INSERT INTO forts VALUES('second',0,'Second',52.37,4.89)")
        self.assertEqual(self.call(155, p.GymDeployProto(fort_id='second', pokemon_id=2,
            player_lat_degrees=52.37, player_lng_degrees=4.89), p.GymDeployOutProto).result, 1)
        self.clock += 400 * 60000
        with self.db.transaction() as db:
            defenders = list(db.execute('SELECT * FROM gym_defenders'))
        def returned(row):
            with self.db.transaction() as db:
                gyms.return_defender(self.game, db, row)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(returned, defenders))
        self.game = Game(self.config, Database(self.db.path))
        with self.db.transaction() as db:
            self.assertEqual(sorted(r[0] for r in db.execute('SELECT coins FROM gym_returns')), [10, 40])
            self.assertEqual(gyms.team(db, self.players[0])['coins'], 50)
            for row in defenders:
                gyms.return_defender(self.game, db, row)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pokemon').fetchone()[0], 9)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gym_returns').fetchone()[0], 2)
            self.assertTrue(all(not owned(db, self.players[0], pid).deployed_fort_id
                                and owned(db, self.players[0], pid).stamina == 0 for pid in [1, 2]))


class RaidRewardBoundaryTests(unittest.TestCase):
    setUp = raid_cases.RaidTests.setUp
    tearDown = raid_cases.RaidTests.tearDown
    call = raid_cases.RaidTests.call
    join = raid_cases.RaidTests.join
    start = raid_cases.RaidTests.start
    win = raid_cases.RaidTests.win

    def configure_rewards(self):
        rewards = {'101': 2, '201': 1, '706': 1, '1201': 1, '1202': 1, '1301': 1}
        self.config['item_capacity'] = 1
        self.config['raids']['pools'][0]['items'] = rewards
        self.game = Game(self.config, Database(self.db.path))
        # Create a fresh schedule, without editing any persisted raid definition.
        self.config['raids']['epoch_ms'] -= 1000
        self.game = Game(self.config, Database(self.db.path))
        with self.db.transaction() as db:
            self.seed = raids.schedule(self.game, db, gyms.gym(db, 'gym'))['seed']
        return rewards

    def amounts(self):
        with self.db.transaction() as db:
            return dict(db.execute('SELECT item,count FROM items WHERE player_id=?', (self.players[0],)))

    def test_supported_regular_rewards_overflow_full_bag_once_and_restart(self):
        rewards = self.configure_rewards()
        before = self.amounts()
        request, reward = self.win()
        self.assertTrue(reward.is_victory)
        loot = reward.battle_results.raid_item_rewards[0].loot_item
        self.assertEqual({str(v.item): v.count for v in loot if v.item}, rewards)
        after = self.amounts()
        for item, count in rewards.items():
            self.assertEqual(after[int(item)] - before.get(int(item), 0), count)
        self.assertGreater(sum(after.values()), self.config['item_capacity'])
        self.assertEqual(self.call(3010, request, p.CompleteRaidBattleOutProto), reward)
        self.assertEqual(self.amounts(), after)
        self.game = Game(self.config, Database(self.db.path))
        self.assertEqual(self.call(3010, request, p.CompleteRaidBattleOutProto), reward)
        self.assertEqual(self.amounts(), after)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM raid_members WHERE reward IS NOT NULL').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT xp FROM players WHERE id=?', (self.players[0],)).fetchone()[0], 11000)

    def test_interrupted_reward_batch_rolls_back_then_genuine_claim_succeeds(self):
        self.configure_rewards()
        request, _ = self.win(claim=False)
        before = self.amounts()
        rpc = wire.RpcRequest(rpc_id=900001)
        rpc.requests.add(method=3010, body=request.SerializeToString())
        rpc.requests.add(method=999999, body=b'')
        with self.assertRaises(RpcError):
            self.game.rpc(rpc, self.tickets[0])
        self.assertEqual(self.amounts(), before)
        with self.db.transaction() as db:
            member = raids.member(db, self.seed, self.players[0])
            self.assertIsNone(member['reward'])
            self.assertFalse(member['spawn_id'])
        self.game = Game(self.config, Database(self.db.path))
        result = self.call(3010, request, p.CompleteRaidBattleOutProto)
        self.assertTrue(result.is_victory)
        self.assertEqual(self.call(3010, request, p.CompleteRaidBattleOutProto), result)

    def test_reward_definitions_match_pinned_source_and_reject_unrelated_items(self):
        from dittobuffer.core import master
        from dittobuffer.gameplay.raid_items import DEFINITIONS
        self.assertEqual(DEFINITIONS['source_sha256'], '5c947ac64d1de8859bea1b3bf044609d74b6e8429d53f8cb1aa30a72a46dce84')
        generated = {t.item.unique_id: t for t in master.templates(self.config) if t.HasField('item')}
        categories = next(t.item_inventory_update_settings for t in master.templates(self.config)
                          if t.HasField('item_inventory_update_settings'))
        visible = {category for row in categories.category_proto for category in row.category}
        self.assertTrue({14, 15} <= visible, 'Raid Candy and Pass items must have actual Android bag sections')
        golden, candy = generated[706].item, generated[1301].item
        self.assertEqual((golden.item_type, golden.category, golden.drop_trainer_level), (6, 2, 10))
        self.assertEqual(list(golden.food.item_effect), [1014, 1016])
        self.assertEqual(list(golden.food.item_effect_percent), [2.5, 1])
        self.assertEqual((candy.item_type, candy.category, candy.drop_trainer_level), (15, 14, 5))
        self.config['raids']['pools'][0]['items'] = {'1410': 200}
        with self.assertRaisesRegex(ValueError, 'Unsupported raid reward item'):
            Game(self.config, Database(self.db.path))


class RaidReconnectBoundaryTests(unittest.TestCase):
    setUp = raid_cases.RaidTests.setUp
    tearDown = raid_cases.RaidTests.tearDown
    call = raid_cases.RaidTests.call
    join = raid_cases.RaidTests.join
    start = raid_cases.RaidTests.start
    headers = raid_cases.RaidTests.headers
    replacement = raid_cases.RaidTests.replacement

    def test_cold_reconnect_ignores_recommended_reordering_and_keeps_combat(self):
        self.replacement();self.join();battle = self.start((1, 2)).battle
        old_headers = self.headers(battle)
        event = p.SendBattleEventProto(battle_id=battle.battle_id, player_id=self.players[0][:20], sequence_id=1,
            battle_event=p.BattleEventProto(type=4, actor_id=self.players[0][:20], attack=p.RaidPbsAttack(attack_type=1)))
        self.assertEqual(raid_transport.handle(self.game, '/raid/api/send-player-event', old_headers, event.SerializeToString()).result, 1)
        self.clock += 1000
        raid_transport.handle(self.game, '/raid/api/get-state', old_headers)
        with self.db.transaction() as db:
            before = json.loads(db.execute('SELECT state FROM raid_battles WHERE id=?', (battle.battle_id,)).fetchone()[0])
        self.assertLess(before['defender']['hp'], 60)
        self.game = Game(self.config, Database(self.db.path))
        first, retry = self.join().lobby, self.join().lobby
        self.assertEqual(first.rvn_connection.token, retry.rvn_connection.token)
        # The restarted Android client recommends a different order. This must
        # resume the saved team, rather than replacing/healing it or error 43.
        out = self.call(165, p.StartRaidBattleProto(gym_id='gym', raid_seed=self.seed,
            attacking_pokemon_id=[2, 1], player_lat_degrees=52.37, player_lng_degrees=4.89), p.StartRaidBattleOutProto)
        self.assertEqual(out.result, 1)
        self.assertEqual(out.battle.battle_id, battle.battle_id)
        self.assertEqual((out.battle.battle_start_ms, out.battle.battle_end_ms), (before['start'], before['end']))
        self.assertEqual(out.battle.attacker.active_pokemon.pokemon.id, 1)
        with self.db.transaction() as db:
            after = json.loads(db.execute('SELECT state FROM raid_battles WHERE id=?', (battle.battle_id,)).fetchone()[0])
            self.assertEqual(after['defender'], before['defender'])
            self.assertEqual(after['party'], before['party'])
            self.assertEqual(after['pending'], before['pending'])
            self.assertEqual(after['generation'], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM raid_pass_receipts').fetchone()[0], 1)
        with self.assertRaises(RpcError):raid_transport.handle(self.game, '/raid/api/get-state', old_headers)
        # New native directors restart their sequence numbers. Their new
        # generation may reuse sequence 1 without replaying the old attack.
        event.battle_event.ClearField('attack');event.battle_event.type=5
        new_headers = self.headers(battle)
        self.assertEqual(raid_transport.handle(self.game, '/raid/api/send-player-event', new_headers, event.SerializeToString()).result, 1)
        self.assertEqual(raid_transport.handle(self.game, '/raid/api/send-player-event', new_headers, event.SerializeToString()).result, 1)

    def test_active_reconnect_projects_original_deadline_in_lobby(self):
        self.join();battle = self.start().battle
        self.clock += 7000
        lobby = self.join().lobby
        self.assertEqual(lobby.raid_battle_end_ms, battle.battle_end_ms)
