"""Real inventory/capture transactions; synthetic pins are not native evidence."""
import unittest
from unittest.mock import patch
from tests import test_world_postcards as support
from dittobuffer.gameplay import vivillon as v,world_postcards as w,gifts
from dittobuffer.core import management
from dittobuffer.gameplay.game import p,Game,RpcError
from dittobuffer.core.database import Database

class VivillonTests(unittest.TestCase):
    def setUp(self):
        self.fixture=support.WorldPostcardTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.f=self.fixture.f;self.seq=10000
        with self.f.db.transaction() as db:
            self.f.game.config['pokemon_capacity']=2000
            db.execute('UPDATE items SET count=1000 WHERE player_id=? AND item=1',(self.f.players[0],))
    def pin(self,region='OCEAN',player=0,own=False):
        self.seq+=1;f=self.f;origin=next(o for o in w.registry()['origins'] if o['region']==region)
        with f.db.transaction() as db:
            if own:
                db.execute('INSERT INTO social_gifts(id,owner,fort_id,name,image,lat,lng,created_ms) VALUES(?,?,?,?,?,?,?,?)',(self.seq,f.players[player],origin['id'],'Synthetic own test','https://example.invalid/image.png',origin['lat'],origin['lng'],f.clock))
            else:
                # Distinct declared synthetic exchange days, with the prior
                # received Gift opened; retain the real social uniqueness rules.
                db.execute('UPDATE social_gifts SET opened_ms=? WHERE receiver=?',(f.clock,f.players[player]))
                db.execute('INSERT INTO social_gifts(id,owner,receiver,fort_id,name,image,lat,lng,created_ms,sent_day) VALUES(?,?,?,?,?,?,?,?,?,?)',(self.seq,f.players[1-player],f.players[player],origin['id'],'Synthetic received test','https://example.invalid/image.png',origin['lat'],origin['lng'],f.clock,str(self.seq)))
            return self.seq,gifts.postcard(f.game,db,f.players[player],p.CreatePostcardProto(giftbox_id=self.seq).SerializeToString())
    def encounter(self,region='OCEAN',player=0):
        f=self.f
        with f.db.transaction() as db:return v.encounter(f.game,db,f.players[player],p.ButterflyCollectorRewardEncounterProtoRequest(region=w.PATTERNS[region]).SerializeToString())
    def catch(self,region='OCEAN',player=0):
        f=self.f;enc=self.encounter(region,player);self.assertEqual(enc.result,1)
        request=p.CatchPokemonProto(encounter_id=enc.encounter_id,pokeball=1,hit_pokemon=True)
        with patch('dittobuffer.gameplay.game.secrets.randbelow',return_value=0):
            with f.db.transaction() as db:out=f.game.rpc_catch_pokemon(db,f.players[player],request.SerializeToString())
        self.assertEqual(out.status,1)
        with f.db.transaction() as db:
            before=f.game.player(db,f.players[player])['captures']
            replay=f.game.rpc_catch_pokemon(db,f.players[player],request.SerializeToString());self.assertEqual(replay,out)
            self.assertEqual(f.game.player(db,f.players[player])['captures'],before)
        return out.captured_pokemon_id
    def test_thresholds_pending_repin_restart_and_catch_replay(self):
        f=self.f
        for goal in (3,9,15):
            self.assertEqual(self.encounter().result,3)
            for i in range(goal):
                gift,out=self.pin();self.assertEqual(out.result,1);self.assertEqual(out.butterfly_collector_updated_region.progress,i+1)
            enc=self.encounter();self.assertEqual(enc.result,1);self.assertEqual(self.encounter(),enc)
            gift,extra=self.pin();self.assertEqual(extra.butterfly_collector_updated_region.progress,goal)
            with f.db.transaction() as db:
                card=extra.postcard.postcard_id
                self.assertEqual(gifts.delete_postcards(f.game,db,f.players[0],p.DeletePostcardsProto(postcard_ids=[card]).SerializeToString()).result,1)
                self.assertEqual(gifts.postcard(f.game,db,f.players[0],p.CreatePostcardProto(giftbox_id=gift).SerializeToString()).result,1)
                self.assertEqual(db.execute('SELECT progress FROM vivillon_regions WHERE player=?',(f.players[0],)).fetchone()[0],goal)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM vivillon_regions WHERE player=?',(f.players[1],)).fetchone()[0],0)
            f.game=Game(f.game.config,Database(f.db.path));self.assertEqual(self.encounter(),enc)
            ident=self.catch()
            with f.db.transaction() as db:
                mon=management.owned(db,f.players[0],ident);self.assertEqual(mon.pokemon_display.form,v.form_for(w.PATTERNS['OCEAN'],664))
                row=db.execute('SELECT * FROM vivillon_regions WHERE player=?',(f.players[0],)).fetchone();self.assertEqual(row['progress'],0);self.assertIsNone(row['pending'])
        with f.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM vivillon_entitlements WHERE caught_pokemon IS NOT NULL').fetchone()[0],3)
    def test_all_eighteen_real_catches_evolve_with_distinct_forms(self):
        f=self.f;forms=set()
        for name,region in w.PATTERNS.items():
            for _ in range(3):self.pin(name)
            ident=self.catch(name)
            with f.db.transaction() as db:
                mon=management.owned(db,f.players[0],ident);rules=f.game.forms[664,mon.pokemon_display.form]
                self.assertEqual(rules['evolution_branches'][0][3],25)
                management.add_candy(db,f.players[0],rules['family'],125)
                for species,cost in [(665,25),(666,100)]:
                    before=management.candy(db,f.players[0],rules['family'])
                    evolved=management.evolve(f.game,db,f.players[0],p.EvolvePokemonProto(pokemon_id=ident).SerializeToString())
                    self.assertEqual(evolved.result,1,(name,species));mon=evolved.evolved_pokemon
                    self.assertEqual(mon.pokemon_id,species);self.assertEqual(mon.pokemon_display.form,v.form_for(region,species))
                    self.assertEqual(management.candy(db,f.players[0],rules['family']),before-cost+1)
                forms.add(mon.pokemon_display.form)
        self.assertEqual(len(forms),18)
        restarted=Game(f.game.config,Database(f.db.path))
        with restarted.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pokemon WHERE player_id=? AND species=666',(f.players[0],)).fetchone()[0],18)
            self.assertEqual(len({p.PokemonProto.FromString(r[0]).pokemon_display.form for r in db.execute('SELECT data FROM pokemon WHERE player_id=? AND species=666',(f.players[0],))}),18)
    def test_foreign_and_full_storage_cannot_consume_entitlement(self):
        f=self.f
        for _ in range(3):self.pin()
        self.assertEqual(self.encounter(player=1).result,3)
        f.game.config['pokemon_capacity']=0
        self.assertEqual(self.encounter().result,2)
        f.game.config['pokemon_capacity']=2000
        enc=self.encounter()
        with self.assertRaises(RpcError):
            with f.db.transaction() as db:f.game.rpc_catch_pokemon(db,f.players[1],p.CatchPokemonProto(encounter_id=enc.encounter_id,pokeball=1,hit_pokemon=True).SerializeToString())
        self.assertEqual(self.encounter(),enc)
    def test_pattern_survives_management_two_consent_trade_and_restart(self):
        # Backend transactions only: synthetic pin/Candy resources are declared
        # test fixtures, and do not establish Android or multiplayer acceptance.
        from dittobuffer.gameplay import trading,pokedex
        f=self.f;a,b=f.players
        for _ in range(3):self.pin('OCEAN');self.pin('RIVER',player=1)
        ocean=self.catch('OCEAN');river=self.catch('RIVER',player=1)
        with f.db.transaction() as db:
            db.execute('UPDATE players SET xp=2000000,stardust=2000000')
            first,last=sorted(f.players)
            db.execute('INSERT INTO social_pairs(a,b,created_ms,points) VALUES(?,?,?,1)',(first,last,f.clock))
            for player in f.players:
                db.execute('INSERT INTO social_locations VALUES(?,?,?,?,?,?)',(player,52.08,5.12,2,trading.now_ms(),trading.now_ms()))
            family=f.game.forms[664,v.form_for(w.PATTERNS['OCEAN'],664)]['family']
            management.add_candy(db,a,family,130)
            for _ in range(2):
                self.assertEqual(management.evolve(f.game,db,a,p.EvolvePokemonProto(pokemon_id=ocean).SerializeToString()).result,1)
            self.assertEqual(management.nickname(f.game,db,a,p.NicknamePokemonProto(pokemon_id=ocean,nickname='Ocean Test').SerializeToString()).result,1)
            mon=management.owned(db,a,ocean)
            upgraded=management.upgrade(f.game,db,a,p.UpgradePokemonProto(pokemon_id=ocean,pokemon_current_cp=mon.cp,number_of_upgrades=1).SerializeToString())
            self.assertEqual(upgraded.result,1)
            self.assertEqual(upgraded.upgraded_pokemon.pokemon_display.form,v.form_for(w.PATTERNS['OCEAN'],666))
        for index,other in ((0,b),(1,a)):
            self.assertEqual(f.call(970,p.OpenTradingProto(player_id=other),p.OpenTradingOutProto,index).result,1)
        self.assertEqual(f.call(971,p.UpdateTradingProto(player_id=b,pokemon_id=ocean),p.UpdateTradingOutProto).result,1)
        proposed=f.call(971,p.UpdateTradingProto(player_id=a,pokemon_id=river),p.UpdateTradingOutProto,1)
        self.assertEqual(proposed.result,1);token=proposed.trading.transaction_log
        first=f.call(972,p.ConfirmTradingProto(player_id=b,transaction_log=token),p.ConfirmTradingOutProto)
        self.assertEqual(first.result,1);self.assertEqual(first.trading.state,4)
        completed=f.call(972,p.ConfirmTradingProto(player_id=a,transaction_log=token),p.ConfirmTradingOutProto,1)
        self.assertEqual(completed.result,1);self.assertEqual(completed.trading.state,5)
        f.game=Game(f.game.config,Database(f.db.path))
        with f.db.transaction() as db:
            moved=management.owned(db,b,ocean);self.assertIsNone(management.owned(db,a,ocean))
            self.assertEqual(moved.pokemon_id,666);self.assertEqual(moved.nickname,'Ocean Test')
            self.assertEqual(moved.pokemon_display.form,v.form_for(w.PATTERNS['OCEAN'],666))
            self.assertEqual(management.owned(db,a,river).pokemon_display.form,v.form_for(w.PATTERNS['RIVER'],664))
            entry=next(item.pokedex_entry for item in pokedex.inventory(f.game,db,b) if item.pokedex_entry.pokedex_entry_number==666)
            self.assertIn(v.form_for(w.PATTERNS['OCEAN'],666),entry.captured_forms)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM social_trade_history').fetchone()[0],1)
            # Trading the caught Pokemon does not reissue its original medal
            # entitlement or erase the originating account's permanent progress.
            region=db.execute('SELECT * FROM vivillon_regions WHERE player=? AND region=?',(a,w.PATTERNS['OCEAN'])).fetchone()
            self.assertEqual((region['rank'],region['goal'],region['pending']),(1,9,None))
    def test_own_pin_daily_limit_and_native_wire_tag(self):
        for _ in range(3):self.pin(own=True)
        with self.f.db.transaction() as db:
            self.assertEqual(db.execute('SELECT progress FROM vivillon_regions WHERE player=?',(self.f.players[0],)).fetchone()[0],2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM vivillon_pin_credits WHERE reason='own_daily_limit'").fetchone()[0],1)
        # Independently audited native WriteTo 0x8962B1C: tag 0x31.
        self.assertEqual(p.ButterflyCollectorRewardEncounterProtoResponse(encounter_id=1).SerializeToString(),b'\x31\x01\x00\x00\x00\x00\x00\x00\x00')
    def test_counted_card_delete_repin_capacity_and_transaction_rollback(self):
        f=self.f;gift,out=self.pin();card=out.postcard
        with f.db.transaction() as db:
            self.assertEqual(gifts.delete_postcards(f.game,db,f.players[0],p.DeletePostcardsProto(postcard_ids=[card.postcard_id]).SerializeToString()).result,1)
            pinned=gifts.postcard(f.game,db,f.players[0],p.CreatePostcardProto(giftbox_id=gift).SerializeToString());self.assertEqual(pinned.result,1)
            self.assertEqual(db.execute('SELECT progress FROM vivillon_regions WHERE player=?',(f.players[0],)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vivillon_pin_credits WHERE player=?',(f.players[0],)).fetchone()[0],1)
        f.game.config['social']['postcard_capacity']=1
        gift,full=self.pin('TUNDRA');self.assertEqual(full.result,5)
        with f.db.transaction() as db:self.assertFalse(db.execute('SELECT 1 FROM vivillon_regions WHERE region=?',(w.PATTERNS['TUNDRA'],)).fetchone())
        f.game.config['social']['postcard_capacity']=350
        with self.assertRaisesRegex(RuntimeError,'declared rollback'):
            with f.db.transaction() as db:
                result=gifts.postcard(f.game,db,f.players[0],p.CreatePostcardProto(giftbox_id=gift).SerializeToString());self.assertEqual(result.result,1)
                raise RuntimeError('declared rollback')
        with f.db.transaction() as db:self.assertFalse(db.execute('SELECT 1 FROM vivillon_pin_credits WHERE gift=?',(gift,)).fetchone())

if __name__=='__main__':unittest.main()
