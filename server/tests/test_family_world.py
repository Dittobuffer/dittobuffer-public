"""Independent account mutations against shared world identities; backend only."""
import json,unittest
from concurrent.futures import ThreadPoolExecutor
from tests import test_family_accounts as base
from dittobuffer.gameplay.game import Game,p,wire,RpcError,now_ms
from dittobuffer.core.database import Database
from dittobuffer.gameplay.species import make_pokemon
from dittobuffer.gameplay import eggs, mega
from dittobuffer.core import management
from s2sphere import CellId,LatLng

class FamilyWorldTests(unittest.TestCase):
    setUp=base.FamilyAccountsTests.setUp
    tearDown=base.FamilyAccountsTests.tearDown
    account=base.FamilyAccountsTests.account
    call=base.FamilyAccountsTests.call
    def seed_mon(self,player,species=1):
        with self.db.transaction() as db:
            ident=db.execute('SELECT COALESCE(MAX(id),0)+1 FROM pokemon').fetchone()[0];spawn=150000+ident
            db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',(spawn,str(spawn),species,52.37,4.89,now_ms()+86400000))
            mon=make_pokemon(p,self.game.species[species],species,10,[10]*3);mon.id=ident
            db.execute('INSERT INTO pokemon VALUES(?,?,?,?,?)',(ident,player,spawn,species,mon.SerializeToString()))
            return ident
    def spin(self,ticket):return self.call(ticket,101,p.FortSearchProto(id='shared-stop',player_lat_degrees=52.37,player_lng_degrees=4.89),p.FortSearchOutProto)
    def test_shared_stop_independent_cooldowns_eggs_research_receipts_and_restart(self):
        a=self.account();b=self.account('bobby');self.cfg['eggs']['stop_drop_probability']=1
        with self.db.transaction() as db:db.execute("INSERT INTO forts VALUES('shared-stop',1,'Shared Stop',52.37,4.89)")
        for account in [a,b]:self.call(account[2],4,p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
        with ThreadPoolExecutor(2) as ex:out=list(ex.map(lambda account:self.spin(account[2]),[a,b]))
        self.assertEqual([v.result for v in out],[1,1]);self.assertNotEqual(out[0].egg_pokemon.id,out[1].egg_pokemon.id)
        self.assertEqual(self.spin(a[2]).result,3);self.assertEqual(self.spin(b[2]).result,3)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fort_visits').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM eggs').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(DISTINCT player_id) FROM research_tasks').fetchone()[0],2)
            foreign=db.execute('SELECT id FROM incubators WHERE player_id=?',(a[0],)).fetchone()[0]
        result=self.call(b[2],140,p.UseItemEggIncubatorProto(item_id=foreign,pokemond_id=out[0].egg_pokemon.id),p.UseItemEggIncubatorOutProto);self.assertEqual(result.result,2)
        self.game=Game(self.cfg,Database(self.db.path));self.assertEqual(self.spin(a[2]).result,3)
    def test_wild_identity_shared_catch_outcome_and_inventory_private(self):
        a=self.account();b=self.account('bobby');self.cfg['catch_probability']=1
        cell=CellId.from_lat_lng(LatLng.from_degrees(52.37,4.89)).parent(12).id()
        req=p.GetMapObjectsProto(cell_id=[cell],player_lat=52.37,player_lng=4.89)
        def world(t):return self.call(t,106,req,p.GetMapObjectsOutProto)
        wa,wb=world(a[2]),world(b[2]);sa=[s for c in wa.map_cell for s in c.catchable_pokemon];sb=[s for c in wb.map_cell for s in c.catchable_pokemon]
        self.assertEqual([s.encounter_id for s in sa],[s.encounter_id for s in sb]);spawn=sa[0]
        enc=p.EncounterProto(encounter_id=spawn.encounter_id,spawnpoint_id=spawn.spawnpoint_id,player_lat_degrees=spawn.latitude,player_lng_degrees=spawn.longitude)
        for t in [a[2],b[2]]:self.assertEqual(self.call(t,102,enc,p.EncounterOutProto).status,1)
        catch=p.CatchPokemonProto(encounter_id=spawn.encounter_id,spawn_point_guid=spawn.spawnpoint_id,pokeball=1,hit_pokemon=True)
        result=self.call(a[2],103,catch,p.CatchPokemonOutProto);self.assertEqual(result.status,1)
        self.assertIn(spawn.encounter_id,[s.encounter_id for c in world(b[2]).map_cell for s in c.catchable_pokemon])
        self.assertNotIn(spawn.encounter_id,[s.encounter_id for c in world(a[2]).map_cell for s in c.catchable_pokemon])
        for account,expected in [(a,1),(b,0)]:
            inv=self.call(account[2],4,p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
            mons=[p.HoloInventoryItemProto.FromString(v.item).pokemon for v in inv.inventory_delta.inventory_item if p.HoloInventoryItemProto.FromString(v.item).HasField('pokemon')]
            self.assertEqual(len([v for v in mons if not v.is_egg]),expected)
    def test_cross_account_management_mutations_never_change_owned_state(self):
        a=self.account();b=self.account('bobby');ident=self.seed_mon(a[0],3)
        cases=[(148,p.SetFavoritePokemonProto(pokemon_id=ident,is_favorite=True),p.SetFavoritePokemonOutProto),
               (149,p.NicknamePokemonProto(pokemon_id=ident,nickname='Stolen'),p.NicknamePokemonOutProto),
               (147,p.UpgradePokemonProto(pokemon_id=ident),p.UpgradePokemonOutProto),
               (125,p.EvolvePokemonProto(pokemon_id=ident),p.EvolvePokemonOutProto),
               (112,p.ReleasePokemonProto(pokemon_id=ident),p.ReleasePokemonOutProto),
               (152,p.SetBuddyPokemonProto(pokemon_id=ident),p.SetBuddyPokemonOutProto),
               (1502,p.MegaEvolvePokemonProto(pokemon_id=ident,temp_evo_id=1),p.MegaEvolvePokemonOutProto)]
        with self.db.transaction() as db:before=bytes(db.execute('SELECT data FROM pokemon WHERE id=?',(ident,)).fetchone()[0])
        for method,req,typ in cases:
            response=self.call(b[2],method,req,typ)
            code=response.result if 'result' in response.DESCRIPTOR.fields_by_name else response.status
            self.assertNotEqual(code,1,(method,response))
        with self.db.transaction() as db:self.assertEqual(before,db.execute('SELECT data FROM pokemon WHERE id=?',(ident,)).fetchone()[0])
    def test_concurrent_gym_placement_uses_shared_team_capacity_and_one_species(self):
        a=self.account();b=self.account('bobby');ids=[self.seed_mon(t[0]) for t in [a,b]]
        with self.db.transaction() as db:
            db.execute('UPDATE players SET xp=10000');db.execute('UPDATE trainer_teams SET team=1');db.execute("INSERT INTO forts VALUES('shared-gym',0,'Shared Gym',52.37,4.89)")
        def place(pair):
            account,ident=pair
            return self.call(account[2],155,p.GymDeployProto(fort_id='shared-gym',pokemon_id=ident,player_lat_degrees=52.37,player_lng_degrees=4.89),p.GymDeployOutProto).result
        with ThreadPoolExecutor(2) as ex:results=list(ex.map(place,zip([a,b],ids)))
        self.assertEqual(results.count(1),1)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gym_defenders').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pokemon').fetchone()[0],2)
        self.game=Game(self.cfg,Database(self.db.path))
        self.assertEqual(self.call(a[2]).player.team,1);self.assertEqual(self.call(b[2]).player.team,1)

    def test_location_samples_are_per_principal_without_other_player_coordinates(self):
        a=self.account();b=self.account('bobby');stamp=now_ms()
        for index,(account,location) in enumerate([(a,(52.0817613,5.1238533)),(b,(52.3728,4.8936))]):
            req=wire.RpcRequest(rpc_id=900+index);req.requests.add(method=2)
            out=self.game.rpc(req,account[2],location=dict(lat=location[0],lng=location[1],accuracy=5,time=stamp/1000,provider=3,source='device'))
            self.assertEqual(len(out.payloads),1)
        with self.db.transaction() as db:
            rows={r['player_id']:(r['lat'],r['lng'],r['total_m']) for r in db.execute('SELECT * FROM walking')}
        self.assertEqual(rows[a[0]],(52.0817613,5.1238533,0));self.assertEqual(rows[b[0]],(52.3728,4.8936,0))

    def test_foreign_shop_offer_medicine_research_and_preferences_isolation(self):
        a=self.account();b=self.account('bobby');ident=self.seed_mon(a[0]);methods={v:k for k,v in self.game.methods.items()}
        with self.db.transaction() as db:
            db.execute('UPDATE trainer_teams SET coins=50');db.execute("INSERT INTO forts VALUES('shared-stop',1,'Shared Stop',52.37,4.89)")
        catalog=self.call(a[2],310001,p.GetAvailableSkusAndBalancesProto(),p.GetAvailableSkusAndBalancesOutProto);offer=catalog.available_sku[0]
        buy=p.PurchaseSkuProto(sku_id=offer.id,offer_id=offer.offer_id)
        self.assertEqual(self.call(b[2],310000,buy,p.PurchaseSkuOutProto).status,6)
        self.assertEqual(self.call(a[2],310000,buy,p.PurchaseSkuOutProto).status,1)
        self.assertEqual(self.call(a[2],310000,buy,p.PurchaseSkuOutProto).status,1)
        potion=self.call(b[2],methods['USE_ITEM_POTION'],p.UseItemPotionProto(pokemon_id=ident,item=101),p.UseItemPotionOutProto);self.assertNotEqual(potion.result,1)
        task=self.spin(a[2]).challenge_quest.quest.quest_id
        claimed=self.call(b[2],methods['COMPLETE_QUEST'],p.CompleteQuestProto(quest_id=task),p.CompleteQuestOutProto);self.assertNotEqual(claimed.status,1)
        setting=p.SavePlayerPreferencesProto(player_preferences_proto=p.PlayerPreferencesProto(opt_out_of_sponsored_gifts=True))
        self.assertEqual(self.call(a[2],1652,setting,p.SavePlayerPreferencesOutProto).result,1)
        self.assertFalse(self.call(b[2]).player.player_preferences.opt_out_of_sponsored_gifts)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT coins FROM trainer_teams WHERE player_id=?',(a[0],)).fetchone()[0],40)
            self.assertEqual(db.execute('SELECT coins FROM trainer_teams WHERE player_id=?',(b[0],)).fetchone()[0],50)

if __name__=='__main__':unittest.main()
