import json
import random
import unittest
from pathlib import Path
import tests.test_game as fixtures
from dittobuffer.gameplay.game import Game,ROOT,p
from dittobuffer.core.management import owned
from dittobuffer.gameplay.pokemon_sizes import classify,assign,validate
from dittobuffer.gameplay.species import pool_rules,REFERENCE


class SizeCostumeTests(unittest.TestCase):
    setUp=fixtures.GameTests.setUp
    tearDown=fixtures.GameTests.tearDown
    call=fixtures.GameTests.call
    spawn=fixtures.GameTests.spawn
    encounter=fixtures.GameTests.encounter
    catch=fixtures.GameTests.catch

    def owner(self,db):
        return db.execute('SELECT player_id FROM sessions WHERE ticket=?',(self.ticket,)).fetchone()[0]

    def test_native_size_tags_and_threshold_edges(self):
        self.assertEqual(p.PokemonExtendedSettingsProto(unique_id=1,form=163,
            size_settings=p.PokemonSizeSettingsProto(m_lower_bound=.5)).SerializeToString(),
            bytes.fromhex('0801e001a3019204051d0000003f'))
        size=REFERENCE['sizes']['1:163']['bounds']
        for height,want in [(.349,'XXS'),(.35,'XS'),(.524,'XS'),(.525,'M'),(.875,'M'),(.876,'XL'),(1.05,'XL'),(1.051,'XXL')]:
            self.assertEqual(classify(height,size),want)
        for bad in (float('nan'),float('inf'),0,-1):
            with self.assertRaises(ValueError):classify(bad,size)
        with self.assertRaises(ValueError):validate({'pokemon_sizes':{'weights':{'M':1}}})

    def test_all_classes_generated_and_spawn_frozen_after_restart(self):
        self.config['pokemon_sizes']={'enabled':True}
        self.game=Game(self.config,self.database)
        from dittobuffer.gameplay.species import make_pokemon
        rules=self.game.species[1]
        for size in ('XXS','XS','M','XL','XXL'):
            mon=make_pokemon(p,rules,1,5,[10]*3)
            assign(self.game,mon,{'size':size},random.Random(41))
            self.assertEqual(classify(mon.height_m,rules['size_settings']),size)
            self.assertGreater(mon.weight_kg,0)
        self.game.spawn_pools[0]['species'][0]['size']='XXL'
        ident=self.catch(self.encounter()).captured_pokemon_id
        with self.database.transaction() as db:before=owned(db,self.owner(db),ident)
        self.assertEqual(classify(before.height_m,rules['size_settings']),'XXL')
        self.game=Game(self.config,self.database)
        with self.database.transaction() as db:self.assertEqual(before,owned(db,self.owner(db),ident))

    def test_enabling_sizes_does_not_resize_existing_owned_records(self):
        ident=self.catch(self.encounter()).captured_pokemon_id
        with self.database.transaction() as db:before=owned(db,self.owner(db),ident).SerializeToString()
        self.config['pokemon_sizes']={'enabled':True}
        self.game=Game(self.config,self.database)
        with self.database.transaction() as db:self.assertEqual(before,owned(db,self.owner(db),ident).SerializeToString())

    def test_costume_requires_mapping_and_rejects_incompatible_forms_and_evolution(self):
        # The missing-mapping case must not inherit the normal profile's
        # optional local asset registry.
        self.config['pokemon_assets']={'enabled':False}
        self.game=Game(self.config,self.database)
        with self.assertRaises(ValueError):pool_rules(self.game,{'species':6,'costume':14})
        manifest=Path(self.tmp.name)/'assets.json'
        manifest.write_text(json.dumps({'format':1,'costumes':{'6:14':{'addresses':['pm6.cSPRING_2020_NOEVOLVE']}}}))
        self.config['pokemon_assets']={'enabled':True,'manifest':manifest.relative_to(ROOT).as_posix()}
        self.config['spawn_pools']=[{'species':[{'id':6,'weight':1,'costume':14}]}]
        self.game=Game(self.config,self.database)
        with self.assertRaises(ValueError):pool_rules(self.game,{'species':6,'costume':999999})
        with self.assertRaises(ValueError):pool_rules(self.game,{'species':6,'costume':14,'form':p.PokemonDisplayProto.Form.Value('Form_CHARIZARD_MEGA_X')})
        ident=self.catch(self.encounter()).captured_pokemon_id
        with self.database.transaction() as db:before=owned(db,self.owner(db),ident)
        self.assertEqual(before.pokemon_display.costume,14)
        self.assertEqual(self.call(125,p.EvolvePokemonProto(pokemon_id=ident),p.EvolvePokemonOutProto).result,4)
        with self.database.transaction() as db:self.assertEqual(before,owned(db,self.owner(db),ident))

    def test_sinnoh_evolution_item_retry_and_size_origin_persistence(self):
        self.config['pokemon_sizes']={'enabled':True}
        self.config['spawn_pools']=[{'species':[{'id':112,'weight':1,'size':'XL','gender':2}]}]
        self.game=Game(self.config,self.database)
        ident=self.catch(self.encounter()).captured_pokemon_id
        with self.database.transaction() as db:
            player=self.owner(db);before=owned(db,player,ident)
            db.execute('UPDATE candy SET count=250 WHERE player_id=?',(player,))
            db.execute('INSERT INTO items VALUES(?,?,1)',(player,1106))
        bad=p.EvolvePokemonProto(pokemon_id=ident,evolution_item_requirement=1102)
        self.assertNotEqual(self.call(125,bad,p.EvolvePokemonOutProto).result,1)
        req=p.EvolvePokemonProto(pokemon_id=ident,evolution_item_requirement=1106,target_pokemon_id=464,target_pokemon_form=852)
        out=self.call(125,req,p.EvolvePokemonOutProto,rpc_id=900)
        self.assertEqual(out.result,1)
        self.assertEqual(out,self.call(125,req,p.EvolvePokemonOutProto,rpc_id=900))
        self.assertNotEqual(self.call(125,req,p.EvolvePokemonOutProto).result,1)
        self.game=Game(self.config,self.database)
        with self.database.transaction() as db:
            after=owned(db,player,ident)
            self.assertEqual((after.id,after.pokemon_id,after.pokemon_display.gender),(ident,464,2))
            self.assertEqual(after.creation_time_ms,before.creation_time_ms)
            self.assertEqual(after.origin_detail,before.origin_detail)
            self.assertEqual(classify(after.height_m,self.game.species[464]['size_settings']),'XL')
            self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1106',(player,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count FROM candy WHERE player_id=? AND family=?',(player,self.game.species[112]['family'])).fetchone()[0],151)
