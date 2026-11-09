import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from google.protobuf.message import DecodeError
from dittobuffer.core.database import Database
from dittobuffer.gameplay.game import Game, ROOT, RpcError, p, wire, now_ms, distance
from protocol.opaque import fields,byte_field,merge
from s2sphere import CellId, LatLng

class GameTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds')
        config=json.loads((ROOT/'config/development.json').read_text())
        config['catch_probability']=1
        # Existing transaction cases use an explicit single-species fixture.
        config['spawn_pools']=[{'species':[{'id':1,'weight':1}]}]
        config['spawn_points_per_tile']=1
        config['spawn_neighbor_tiles']=0
        config['encounter_duration_seconds']=0
        self.config=config
        self.database=Database(Path(self.tmp.name)/'test.sqlite3')
        self.game=Game(config,self.database)
        self.ticket=self.game.authenticate(wire.AuthRequest(auth_id='test-player')).ticket
        self.counter=0

    def tearDown(self):
        self.tmp.cleanup()

    def call(self,method,message,response_type,ticket=None,rpc_id=None):
        self.counter+=1
        request=wire.RpcRequest(rpc_id=self.counter if rpc_id is None else rpc_id)
        request.requests.add(method=method,body=message.SerializeToString())
        response=self.game.rpc(request,ticket or self.ticket)
        self.assertEqual(response.status,1)
        self.assertEqual(len(response.payloads),1)
        return response_type.FromString(response.payloads[0].payload)

    def spawn(self):
        cell=CellId.from_lat_lng(LatLng.from_degrees(52.37,4.89)).parent(12).id()
        out=self.call(106,p.GetMapObjectsProto(cell_id=[cell],player_lat=52.37,player_lng=4.89),p.GetMapObjectsOutProto)
        self.assertGreater(len(out.map_cell[0].catchable_pokemon),0)
        return out.map_cell[0].catchable_pokemon[0]

    def encounter(self):
        spawn=self.spawn()
        out=self.call(102,p.EncounterProto(encounter_id=spawn.encounter_id,spawnpoint_id=spawn.spawnpoint_id,
                    player_lat_degrees=spawn.latitude,player_lng_degrees=spawn.longitude),p.EncounterOutProto)
        self.assertEqual(out.status,1)
        return spawn

    def catch(self,spawn,**kwargs):
        request=p.CatchPokemonProto(encounter_id=spawn.encounter_id,spawn_point_guid=spawn.spawnpoint_id,pokeball=1,hit_pokemon=True)
        return self.call(103,request,p.CatchPokemonOutProto,**kwargs)

    def inventory(self):
        out=self.call(4,p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
        return [p.HoloInventoryItemProto.FromString(i.item) for i in out.inventory_delta.inventory_item]

    def test_schema_corrections(self):
        self.assertEqual(p.InventoryItemProto.DESCRIPTOR.fields_by_name['item'].type,12)
        self.assertEqual(p.DownloadSettingsResponseProto.DESCRIPTOR.fields_by_name['values'].type,12)
        self.assertEqual(p.GetMapObjectsOutProto.DESCRIPTOR.fields_by_name['map_cell'].message_type.name,'ClientMapCellProto')
        self.assertEqual(p.CatchPokemonOutProto.Status_CatchSuccess,1)
        # Literal tags from this APK's serializers, independent of our decoder.
        self.assertEqual(p.EncounterProto(encounter_id=1).SerializeToString(),b'\x09\x01'+b'\x00'*7)
        self.assertEqual(p.CatchPokemonProto(encounter_id=1).SerializeToString(),b'\x09\x01'+b'\x00'*7)
        self.assertEqual(p.CatchPokemonOutProto(captured_pokemon_id=1).SerializeToString(),b'\x19\x01'+b'\x00'*7)
        self.assertEqual(p.ItemInventoryUpdateSettingsProto.DESCRIPTOR.fields_by_name['category_proto'].number,2)
        self.assertEqual(p.ItemSettingsProto(ignore_inventory_space=True).SerializeToString(),b'\x98\x01\x01')
        self.assertEqual(p.GlobalSettingsProto(minimum_client_version='x').SerializeToString(),b'\x32\x01x')
        self.assertEqual(p.EnabledPokemonSettingsProto(enabled_pokemon_range=[p.EnabledPokemonSettingsProto.Range(start=1,end=7)]).SerializeToString(),b'\x1a\x04\x08\x01\x10\x07')
        self.assertEqual(p.NeutralAvatarSettingsProto(default_neutral_avatar=p.PlayerNeutralAvatarProto()).SerializeToString(),b'\x52\x00')
        self.assertEqual(p.PreviewProto(default_cp_range=p.CpRange(min_cp=1,max_cp=2)).SerializeToString(),b'\x0a\x04\x08\x01\x10\x02')
        self.assertEqual(p.LevelUpRewardsOutProto(pokecoins=1).SerializeToString(),b'\x30\x01')
        self.assertEqual(p.UseItemPotionProto(pokemon_id=1).SerializeToString(),b'\x11\x01'+b'\x00'*7)
        self.assertEqual(p.UseItemEncounterProto(encounter_id=1).SerializeToString(),b'\x11\x01'+b'\x00'*7)
        self.assertEqual(p.UseItemBulkHealProto(pokemon_id=[1]).SerializeToString(),b'\x12\x08\x01'+b'\x00'*7)
        self.assertEqual(p.PlayerLevelSettingsProto(default_level_cap=40).SerializeToString(),b'\x60\x28')
        self.assertEqual(p.PlayerStatsSnapshotProto(reason=1,stats=p.PlayerStatsProto(level=5)).SerializeToString(),b'\x08\x01\x12\x02\x08\x05')
        for message in (p.UpgradePokemonProto,p.EvolvePokemonProto):
            self.assertEqual(message(pokemon_id=1).SerializeToString(),b'\x09\x01'+b'\x00'*7)

    def test_spawn_attributes_persist_across_pool_change(self):
        spawn=self.spawn()
        with self.database.transaction() as db:
            before=bytes(db.execute('SELECT data FROM spawn_attributes WHERE spawn_id=?',(spawn.encounter_id,)).fetchone()[0])
        self.config['spawn_pools']=[{'species':[{'id':4,'weight':1}]}]
        self.game=Game(self.config,self.database)
        self.spawn()
        with self.database.transaction() as db:
            after=bytes(db.execute('SELECT data FROM spawn_attributes WHERE spawn_id=?',(spawn.encounter_id,)).fetchone()[0])
            species=db.execute('SELECT species FROM spawns WHERE id=?',(spawn.encounter_id,)).fetchone()[0]
        self.assertEqual(before,after)
        self.assertEqual(species,1)

    def test_three_species_use_own_models_moves_and_candy(self):
        self.config['spawn_pools']=[{'species':[{'id':s,'weight':1}]} for s in (1,4,7)]
        self.config['spawn_points_per_tile']=3;self.config['shiny_rate']=1
        self.game=Game(self.config,self.database)
        cell=CellId.from_lat_lng(LatLng.from_degrees(52.37,4.89)).parent(12).id()
        world=self.call(106,p.GetMapObjectsProto(cell_id=[cell],player_lat=52.37,player_lng=4.89),p.GetMapObjectsOutProto)
        spawns=[value for c in world.map_cell for value in c.catchable_pokemon]
        self.assertEqual({s.pokedex_type_id for s in spawns},{1,4,7})
        for spawn in spawns:
            out=self.call(102,p.EncounterProto(encounter_id=spawn.encounter_id,spawnpoint_id=spawn.spawnpoint_id,
                player_lat_degrees=spawn.latitude,player_lng_degrees=spawn.longitude),p.EncounterOutProto)
            self.assertTrue(out.pokemon.pokemon.pokemon_display.shiny)
            self.assertEqual(out.pokemon.pokemon.move1,self.game.species[spawn.pokedex_type_id]['moves'][0])
            caught=self.catch(spawn)
            self.assertEqual(caught.status,1)
        with self.database.transaction() as db:
            self.assertEqual(dict(db.execute('SELECT family,count FROM candy')),{1:3,4:3,7:3})
            self.assertEqual(db.execute('SELECT count(*) FROM pokemon_levels').fetchone()[0],3)

    def test_avatar_record_persists(self):
        avatar=next(value.neutral_avatar for value in self.inventory() if value.HasField('neutral_avatar'))
        self.game=Game(self.config,self.database)
        again=next(value.neutral_avatar for value in self.inventory() if value.HasField('neutral_avatar'))
        self.assertEqual(avatar,again)
        self.assertEqual(avatar.articles.shirt.article_id,'n_shirt_default-new_0')

    def test_saved_legacy_and_custom_appearances_are_preserved(self):
        from dittobuffer.gameplay.avatar import default_avatar,legacy_default_avatar
        self.inventory()
        with self.database.transaction() as db:
            player=db.execute('SELECT player_id FROM sessions WHERE ticket=?',(self.ticket,)).fetchone()[0]
            db.execute('UPDATE player_appearance SET data=? WHERE player_id=?',(legacy_default_avatar(p).SerializeToString(),player))
            upgraded=self.game.appearance(db,player)
            self.assertEqual(upgraded,legacy_default_avatar(p))
            custom=legacy_default_avatar(p);custom.body_blend.size=0.31
            db.execute('UPDATE player_appearance SET data=? WHERE player_id=?',(custom.SerializeToString(),player))
            self.assertEqual(self.game.appearance(db,player),custom)
        self.game=Game(self.config,Database(self.database.path))
        with self.database.transaction() as db:self.assertEqual(self.game.appearance(db,player),custom)

    def test_favorite_nickname_and_transfer_guard_persist(self):
        owned=self.catch(self.encounter()).captured_pokemon_id
        self.assertEqual(self.call(148,p.SetFavoritePokemonProto(pokemon_id=owned,is_favorite=True),p.SetFavoritePokemonOutProto).result,1)
        self.assertEqual(self.call(149,p.NicknamePokemonProto(pokemon_id=owned,nickname='LibreLeaf'),p.NicknamePokemonOutProto).result,1)
        self.game=Game(self.config,self.database)
        value=next(v.pokemon for v in self.inventory() if v.HasField('pokemon'))
        self.assertTrue(value.favorite);self.assertEqual(value.nickname,'LibreLeaf')
        self.assertEqual(self.call(112,p.ReleasePokemonProto(pokemon_id=owned),p.ReleasePokemonOutProto).status,3)
        self.assertEqual(self.call(148,p.SetFavoritePokemonProto(pokemon_id=owned,is_favorite=False),p.SetFavoritePokemonOutProto).result,1)
        self.assertEqual(self.call(149,p.NicknamePokemonProto(pokemon_id=owned,nickname='x'*13),p.NicknamePokemonOutProto).result,2)
        self.assertEqual(self.call(149,p.NicknamePokemonProto(pokemon_id=owned,nickname='bad\x00name'),p.NicknamePokemonOutProto).result,2)
        self.assertEqual(self.call(149,p.NicknamePokemonProto(pokemon_id=owned,nickname=''),p.NicknamePokemonOutProto).result,1)
        self.assertEqual(self.call(148,p.SetFavoritePokemonProto(pokemon_id=999),p.SetFavoritePokemonOutProto).result,2)
        self.assertEqual(self.call(112,p.ReleasePokemonProto(pokemon_id=owned),p.ReleasePokemonOutProto).status,1)

    def test_discard_validation_retry_and_zero_inventory(self):
        for count in (-1,0,101):
            self.assertEqual(self.call(137,p.RecycleItemProto(item=1,count=count),p.RecycleItemOutProto).result,2)
        request=p.RecycleItemProto(item=1,count=100)
        result=self.call(137,request,p.RecycleItemOutProto,rpc_id=500)
        self.assertEqual(result.new_count,0)
        self.assertEqual(self.call(137,request,p.RecycleItemOutProto,rpc_id=500),result)
        self.assertEqual(self.call(137,request,p.RecycleItemOutProto).result,2)
        self.game=Game(self.config,self.database)
        self.assertEqual(next(v.item.count for v in self.inventory() if v.HasField('item') and v.item.item==1),0)

    def test_berry_persists_boosts_and_survives_miss(self):
        self.config['catch_probability']=0.4
        # This persistence test must not lose its encounter when the generated
        # map spawn happens to cross a real-time rotation boundary.
        self.config['encounter_duration_seconds']=300
        spawn=self.encounter()
        request=p.UseItemEncounterProto(item=701,encounter_id=spawn.encounter_id,spawn_point_guid=spawn.spawnpoint_id)
        response=self.call(154,request,p.UseItemEncounterOutProto,rpc_id=500)
        self.assertEqual(response.status,0);self.assertAlmostEqual(response.capture_probability.capture_probability[0],0.6)
        self.assertEqual(self.call(154,request,p.UseItemEncounterOutProto,rpc_id=500),response)
        self.game=Game(self.config,self.database)
        self.assertEqual(self.call(154,request,p.UseItemEncounterOutProto).status,2)
        miss=p.CatchPokemonProto(encounter_id=spawn.encounter_id,spawn_point_guid=spawn.spawnpoint_id,pokeball=1)
        self.assertEqual(self.call(103,miss,p.CatchPokemonOutProto).status,4)
        self.assertEqual(self.call(154,request,p.UseItemEncounterOutProto).status,2)
        miss.hit_pokemon=True
        with patch('dittobuffer.gameplay.game.secrets.randbelow',return_value=999999):
            self.assertEqual(self.call(103,miss,p.CatchPokemonOutProto).status,2)
        self.assertEqual(self.call(154,request,p.UseItemEncounterOutProto).status,0)
        with self.database.transaction() as db:self.assertEqual(db.execute('SELECT count FROM items WHERE item=701').fetchone()[0],8)

    def test_medicine_validation_consumption_bulk_and_retry(self):
        owned=self.catch(self.encounter()).captured_pokemon_id
        request=p.UseItemPotionProto(item=101,pokemon_id=owned)
        self.assertEqual(self.call(113,request,p.UseItemPotionOutProto).result,3)
        with self.database.transaction() as db:
            value=p.PokemonProto.FromString(db.execute('SELECT data FROM pokemon').fetchone()[0]);value.stamina=1
            db.execute('UPDATE pokemon SET data=?',(value.SerializeToString(),))
        healed=self.call(113,request,p.UseItemPotionOutProto,rpc_id=500)
        self.assertEqual(healed.result,1);self.assertEqual(healed.stamina,min(value.max_stamina,21))
        self.assertEqual(self.call(113,request,p.UseItemPotionOutProto,rpc_id=500),healed)
        revive=p.UseItemReviveProto(item=201,pokemon_id=owned)
        self.assertEqual(self.call(116,revive,p.UseItemReviveOutProto).result,3)
        with self.database.transaction() as db:
            value.stamina=0;db.execute('UPDATE pokemon SET data=?',(value.SerializeToString(),))
        self.assertEqual(self.call(113,request,p.UseItemPotionOutProto).result,3)
        result=self.call(173,p.UseItemBulkHealProto(item=201,pokemon_id=[owned,owned,999]),p.UseItemBulkHealOutProto)
        self.assertEqual([v.result for v in result.heal_results],[1,2])
        self.assertEqual(result.remaining_item_count,4)
        self.game=Game(self.config,self.database)
        value=next(v.pokemon for v in self.inventory() if v.HasField('pokemon'))
        self.assertEqual(value.stamina,max(1,value.max_stamina//2))

    def test_started_encounter_survives_map_expiration(self):
        self.config['encounter_duration_seconds']=300
        spawn=self.encounter()
        with self.database.transaction() as db:
            old=db.execute('SELECT expires_ms FROM spawns WHERE id=?',(spawn.encounter_id,)).fetchone()[0]
            db.execute('UPDATE spawns SET expires_ms=0 WHERE id=?',(spawn.encounter_id,))
            deadline=db.execute('SELECT deadline_ms FROM encounter_deadlines').fetchone()[0]
        self.assertGreaterEqual(deadline,old)
        self.assertEqual(self.catch(spawn).status,1)

    def test_tile_boundary_does_not_hide_nearby_previous_spawns(self):
        cell=CellId.from_lat_lng(LatLng.from_degrees(52.37049,4.89)).parent(12).id()
        def world(lat):
            out=self.call(106,p.GetMapObjectsProto(cell_id=[cell],player_lat=lat,player_lng=4.89),p.GetMapObjectsOutProto)
            return {value.encounter_id for c in out.map_cell for value in c.catchable_pokemon}
        before=world(52.37049);after=world(52.37051)
        self.assertTrue(before.issubset(after))
        self.assertGreater(len(after),len(before))

    def test_neighbor_loading_epoch_and_fort_identity(self):
        self.config['spawn_neighbor_tiles']=1
        cell=CellId.from_lat_lng(LatLng.from_degrees(52.37,4.89)).parent(12).id()
        request=p.GetMapObjectsProto(cell_id=[cell,cell],player_lat=52.37,player_lng=4.89)
        with self.database.transaction() as db:
            db.execute("INSERT INTO forts VALUES('test-stop',1,'Test Stop',52.37,4.89)")
            db.execute("INSERT INTO forts VALUES('test-gym',0,'Test Gym',52.37,4.89)")
        first=self.call(106,request,p.GetMapObjectsOutProto)
        ids={v.encounter_id for c in first.map_cell for v in c.catchable_pokemon}
        self.assertEqual(len(first.map_cell),1);self.assertEqual(len(ids),9)
        with self.database.transaction() as db:
            forts=[tuple(r) for r in db.execute('SELECT * FROM forts ORDER BY id')]
            self.assertEqual(len(forts),2)
        self.config['spawn_generation']=2
        second=self.call(106,request,p.GetMapObjectsOutProto)
        self.assertTrue(ids.isdisjoint({v.encounter_id for c in second.map_cell for v in c.catchable_pokemon}))
        with self.database.transaction() as db:self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM forts ORDER BY id')],forts)

    def test_transfer_tombstone_reward_and_retry(self):
        owned=self.catch(self.encounter()).captured_pokemon_id
        request=p.ReleasePokemonProto(pokemon_ids=[owned,owned])
        out=self.call(112,request,p.ReleasePokemonOutProto,rpc_id=300)
        again=self.call(112,request,p.ReleasePokemonOutProto,rpc_id=300)
        self.assertEqual(out,again);self.assertEqual(out.candy_awarded,1)
        with self.database.transaction() as db:
            self.assertEqual(db.execute('SELECT count FROM candy WHERE family=1').fetchone()[0],4)
            self.assertEqual(db.execute('SELECT count(*) FROM pokemon').fetchone()[0],0)
        inventory=self.call(4,p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
        # Route play deletions use the same inventory delta but are not Pokemon
        # tombstones; only non-zero Pokemon keys belong to this assertion.
        keys=[key.pokemon_id for item in inventory.inventory_delta.inventory_item
              if item.deleted_item_key
              for key in [p.HoloInventoryKeyProto.FromString(item.deleted_item_key)]
              if key.pokemon_id]
        self.assertEqual(keys,[owned])
        self.assertEqual(self.call(112,request,p.ReleasePokemonOutProto).status,3)

    def test_bulk_transfer_is_atomic(self):
        owned=self.catch(self.encounter()).captured_pokemon_id
        out=self.call(112,p.ReleasePokemonProto(pokemon_ids=[owned,9999]),p.ReleasePokemonOutProto)
        self.assertEqual(out.status,3)
        with self.database.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM pokemon').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count FROM candy WHERE family=1').fetchone()[0],3)

    def test_power_up_preview_resource_cost_and_persistence(self):
        owned=self.catch(self.encounter()).captured_pokemon_id
        request=p.UpgradePokemonProto(pokemon_id=owned,preview=True,number_of_upgrades=1)
        preview=self.call(147,request,p.UpgradePokemonOutProto)
        self.assertEqual(preview.result,1)
        with self.database.transaction() as db:
            initial=bytes(db.execute('SELECT data FROM pokemon').fetchone()[0])
            self.assertEqual(db.execute('SELECT stardust FROM players').fetchone()[0],100)
        request.preview=False
        actual=self.call(147,request,p.UpgradePokemonOutProto,rpc_id=400)
        retry=self.call(147,request,p.UpgradePokemonOutProto,rpc_id=400)
        self.assertEqual(actual,retry)
        self.assertEqual(actual.upgraded_pokemon,preview.upgraded_pokemon)
        with self.database.transaction() as db:
            self.assertGreater(actual.upgraded_pokemon.cp,p.PokemonProto.FromString(initial).cp)
            self.assertEqual(db.execute('SELECT stardust FROM players').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count FROM candy').fetchone()[0],2)
        self.game=Game(self.config,self.database)
        self.assertEqual(next(v.pokemon for v in self.inventory() if v.HasField('pokemon')),actual.upgraded_pokemon)
        self.assertEqual(self.call(147,request,p.UpgradePokemonOutProto).result,3)

    def test_evolve_preview_cost_preserves_identity_and_family(self):
        owned=self.catch(self.encounter()).captured_pokemon_id
        request=p.EvolvePokemonProto(pokemon_id=owned,target_pokemon_id=2)
        self.assertEqual(self.call(125,request,p.EvolvePokemonOutProto).result,3)
        with self.database.transaction() as db:db.execute('UPDATE candy SET count=25')
        request.preview=True;preview=self.call(125,request,p.EvolvePokemonOutProto)
        self.assertEqual(preview.preview.default_cp_range.min_cp,preview.evolved_pokemon.cp)
        self.assertEqual(preview.preview.default_cp_range.max_cp,preview.evolved_pokemon.cp)
        with self.database.transaction() as db:
            self.assertEqual(db.execute('SELECT species FROM pokemon').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count FROM candy').fetchone()[0],25)
        request.preview=False
        evolved=self.call(125,request,p.EvolvePokemonOutProto,rpc_id=500)
        self.assertEqual(self.call(125,request,p.EvolvePokemonOutProto,rpc_id=500),evolved)
        self.assertEqual(evolved.evolved_pokemon.id,owned)
        self.assertEqual(evolved.evolved_pokemon.pokemon_id,2)
        self.assertEqual(evolved.evolved_pokemon.pokemon_display.form,166)
        self.assertEqual(evolved.evolved_pokemon,preview.evolved_pokemon)
        with self.database.transaction() as db:
            self.assertEqual(db.execute('SELECT count FROM candy WHERE family=1').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT xp FROM players').fetchone()[0],1600)
        self.game=Game(self.config,self.database)
        self.assertEqual(next(v.pokemon for v in self.inventory() if v.HasField('pokemon')),evolved.evolved_pokemon)

    def test_level_rewards_require_earned_level_and_persist_claim(self):
        request=p.LevelUpRewardsProto(level=2)
        with self.assertRaises(RpcError):self.call(128,request,p.LevelUpRewardsOutProto)
        with self.database.transaction() as db:db.execute('UPDATE players SET xp=1000')
        reward=self.call(128,request,p.LevelUpRewardsOutProto,rpc_id=500)
        self.assertEqual(reward.result,1)
        self.assertEqual([(v.item,v.item_count) for v in reward.items],[(1,10)])
        self.assertEqual(self.call(128,request,p.LevelUpRewardsOutProto,rpc_id=500),reward)
        self.game=Game(self.config,self.database)
        self.assertEqual(self.call(128,request,p.LevelUpRewardsOutProto).result,2)
        with self.database.transaction() as db:
            self.assertEqual(db.execute('SELECT count FROM items WHERE item=1').fetchone()[0],110)

    def test_nonuniform_progression_bounds_and_inventory_revisions(self):
        self.config['level_requirements']=[0,1000,2000,4000]
        from dittobuffer.gameplay.progression import bounds
        self.assertEqual(bounds(self.config,2999),(2,1000,3000))
        self.assertEqual(bounds(self.config,3000),(3,3000,7000))
        from dittobuffer.core.master import templates
        settings=next(value.player_level for value in templates(self.config) if value.HasField('player_level'))
        self.assertEqual(list(settings.required_exp),[0,1000,3000,7000])
        with self.database.transaction() as db:db.execute('UPDATE players SET xp=3000')
        first=self.call(4,p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
        stats=next(p.HoloInventoryItemProto.FromString(v.item).player_stats for v in first.inventory_delta.inventory_item if p.HoloInventoryItemProto.FromString(v.item).HasField('player_stats'))
        self.assertEqual((stats.level,stats.prev_level_exp,stats.next_level_exp),(3,3000,7000))
        second=self.call(4,p.GetHoloholoInventoryProto(timestamp_millis=first.inventory_delta.new_timestamp),p.GetHoloholoInventoryOutProto)
        self.assertEqual(len(second.inventory_delta.inventory_item),0)

    def test_level_up_snapshots_preserve_previous_and_current_stats(self):
        # Bootstrap the existing account's real baseline, then cross a level.
        with self.database.transaction() as db:db.execute('UPDATE players SET xp=950')
        self.inventory()
        self.catch(self.encounter())
        histories=next(v.player_stats_snapshots for v in self.inventory() if v.HasField('player_stats_snapshots'))
        self.assertEqual([(s.reason,s.stats.level,s.stats.experience,s.stats.num_pokemon_captured) for s in histories.snap_shot],[(2,1,950,0),(1,3,2050,1)])
        self.game=Game(self.config,self.database)
        self.assertEqual(next(v.player_stats_snapshots for v in self.inventory() if v.HasField('player_stats_snapshots')),histories)

    def test_account_restart_persistence(self):
        first=self.call(2,p.GetPlayerProto(),p.GetPlayerOutProto)
        self.game=Game(self.config,self.database)
        self.ticket=self.game.authenticate(wire.AuthRequest(auth_id='test-player')).ticket
        second=self.call(2,p.GetPlayerProto(),p.GetPlayerOutProto)
        self.assertEqual(first,second)
        with self.database.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM players').fetchone()[0],1)

    def test_prevent_account_creation(self):
        with self.assertRaises(RpcError):
            self.game.authenticate(wire.AuthRequest(auth_id='unknown',prevent_account_creation=True))

    def test_bootstrap_and_settings(self):
        for method,request,response in [(7,p.GetRemoteConfigVersionsProto(),p.GetRemoteConfigVersionsOutProto),
                (11,p.GetServerTimeProto(),p.GetServerTimeOutProto)]:
            self.call(method,request,response)
        settings=self.call(5,p.DownloadSettingsActionProto(),p.DownloadSettingsResponseProto)
        values={n:v for n,k,_,v in fields(settings.values) if k==2}
        self.assertEqual(values[6],b'0.429.2')
        self.assertEqual(p.MapSettingsProto.FromString(values[3]).encounter_range_meters,100)

    def test_inventory_bootstrap_singletons(self):
        items=self.inventory()
        for field in ('applied_items','egg_incubators','inventory_upgrades','player_camera'):
            self.assertEqual(sum(item.HasField(field) for item in items),1)

    def test_same_spawnpoint_can_host_next_encounter(self):
        first=self.encounter()
        owned=self.catch(first)
        location=CellId.from_token(first.spawnpoint_id).to_lat_lng()
        self.assertLess(distance(first.latitude,first.longitude,location.lat().degrees,location.lng().degrees),10)
        later=now_ms()+self.config['spawn_duration_seconds']*1000
        with patch('dittobuffer.gameplay.game.now_ms',return_value=later):
            second=self.spawn()
        self.assertNotEqual(first.encounter_id,second.encounter_id)
        self.assertEqual(first.spawnpoint_id,second.spawnpoint_id)
        with self.database.transaction() as db:
            self.assertEqual(db.execute('SELECT id FROM pokemon').fetchone()[0],owned.captured_pokemon_id)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_master_preserves_asset_and_form_definitions(self):
        import gzip
        from dittobuffer.core.master import template_bytes
        path=ROOT/'data/static/gmts.binpb.gz'
        if not path.exists():self.skipTest('Static snapshot is not installed')
        def index(values):
            return {next(v.decode() for n,k,_,v in fields(raw) if n==1 and k==2):raw for raw in values}
        original=index([v for n,k,_,v in fields(gzip.decompress(path.read_bytes())) if n==2 and k==2])
        current=index(template_bytes(self.config))
        for name in ('V0001_POKEMON_BULBASAUR','V0001_POKEMON_BULBASAUR_NORMAL','FORMS_V0001_POKEMON_BULBASAUR'):
            if not name.startswith('V'):
                self.assertEqual(current[name],original[name])
                continue
            def body(template):
                return {n:v for n,k,_,v in fields(next(v for n,k,_,v in fields(template) if n==2))}
            before=body(original[name]);after=body(current[name])
            # The compact exporter omits ShadowAttributes (field 45); the
            # local server deliberately adds purification metadata to Pokemon
            # templates. Every supplied field must stay byte-identical.
            shadow=after.pop(45,None);before.pop(45,None)
            self.assertEqual(after,before)
            self.assertIsNotNone(shadow)
            self.assertIn(1,{n for n,k,_,v in fields(shadow)})
            self.assertIn(2,{n for n,k,_,v in fields(shadow)})

    def test_opaque_settings_keep_unknown_fields(self):
        base=byte_field(2,b'old')+byte_field(600,b'opaque-config')
        override=byte_field(2,b'new')
        self.assertEqual(merge(base,override),byte_field(600,b'opaque-config')+override)
        with self.assertRaises(ValueError):
            list(fields(b'\x12\x20short'))

    def test_tutorial_persists(self):
        result=self.call(406,p.MarkTutorialCompleteProto(tutorial_complete=[8]),p.MarkTutorialCompleteOutProto)
        self.assertIn(8,result.player.tutorial_complete)
        self.game=Game(self.config,self.database)
        self.assertIn(8,self.call(2,p.GetPlayerProto(),p.GetPlayerOutProto).player.tutorial_complete)

    def test_catch_persists_and_rewards_once(self):
        spawn=self.encounter()
        result=self.catch(spawn)
        again=self.catch(spawn)
        self.assertEqual(result,again)
        self.game=Game(self.config,Database(self.database.path))
        items=self.inventory()
        self.assertEqual(sum(i.HasField('pokemon') for i in items),1)
        self.assertEqual(next(i.item.count for i in items if i.HasField('item') and i.item.item==1),99)
        self.assertEqual(next(i.player_stats.experience for i in items if i.HasField('player_stats')),1100)

    def test_same_rpc_retry_is_idempotent(self):
        spawn=self.encounter()
        first=self.catch(spawn,rpc_id=1000)
        second=self.catch(spawn,rpc_id=1000)
        self.assertEqual(first,second)

    def test_parallel_retry_cannot_duplicate_catch(self):
        spawn=self.encounter()
        request=wire.RpcRequest(rpc_id=1000)
        request.requests.add(method=103,body=p.CatchPokemonProto(encounter_id=spawn.encounter_id,spawn_point_guid=spawn.spawnpoint_id,pokeball=1,hit_pokemon=True).SerializeToString())
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.game.rpc(request,self.ticket).SerializeToString(),range(4)))
        self.assertEqual(len(set(results)),1)
        self.assertEqual(sum(i.HasField('pokemon') for i in self.inventory()),1)

    def test_encounter_ownership_required(self):
        spawn=self.encounter()
        other=self.game.authenticate(wire.AuthRequest(auth_id='other-player')).ticket
        with self.assertRaises(RpcError):
            self.catch(spawn,ticket=other)

    def test_expired_encounter_cannot_consume_ball(self):
        self.config['encounter_duration_seconds']=300
        spawn=self.encounter()
        with self.database.transaction() as db:
            db.execute('UPDATE spawns SET expires_ms=0')
            db.execute('UPDATE encounter_deadlines SET deadline_ms=0')
        with self.assertRaises(RpcError):
            self.catch(spawn)
        self.assertEqual(next(i.item.count for i in self.inventory() if i.HasField('item') and i.item.item==1),100)

    def test_miss_consumes_one_ball_and_does_not_reward(self):
        spawn=self.encounter()
        result=self.call(103,p.CatchPokemonProto(encounter_id=spawn.encounter_id,spawn_point_guid=spawn.spawnpoint_id,pokeball=1),p.CatchPokemonOutProto)
        self.assertEqual(result.status,4)
        items=self.inventory()
        self.assertEqual(sum(i.HasField('pokemon') for i in items),0)
        self.assertEqual(next(i.item.count for i in items if i.HasField('item') and i.item.item==1),99)

    def test_batch_failure_rolls_back_gameplay(self):
        spawn=self.encounter()
        request=wire.RpcRequest(rpc_id=1000)
        request.requests.add(method=103,body=p.CatchPokemonProto(encounter_id=spawn.encounter_id,spawn_point_guid=spawn.spawnpoint_id,pokeball=1,hit_pokemon=True).SerializeToString())
        request.requests.add(method=999999)
        with self.assertRaises(RpcError):
            self.game.rpc(request,self.ticket)
        self.assertEqual(sum(i.HasField('pokemon') for i in self.inventory()),0)

    def test_fort_cooldown_and_rewards(self):
        self.spawn()
        with self.database.transaction() as db:
            db.execute("INSERT INTO forts VALUES('test-stop',1,'Test Stop',52.37,4.89)")
            fort=db.execute('SELECT * FROM forts WHERE kind=1').fetchone()
        request=p.FortSearchProto(id=fort['id'],player_lat_degrees=fort['lat'],player_lng_degrees=fort['lng'])
        first=self.call(101,request,p.FortSearchOutProto)
        second=self.call(101,request,p.FortSearchOutProto)
        self.assertEqual(first.result,1)
        self.assertEqual(second.result,3)
        self.assertEqual(next(i.item.count for i in self.inventory() if i.HasField('item') and i.item.item==1),103)

    def test_malformed_and_unauthenticated(self):
        with self.assertRaises(DecodeError):
            wire.RpcRequest.FromString(b'\xff')
        with self.assertRaises(RpcError):
            self.game.rpc(wire.RpcRequest(), 'invalid')

if __name__=='__main__':
    unittest.main()
