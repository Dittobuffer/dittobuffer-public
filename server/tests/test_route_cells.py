"""Serialized/transactional fixtures; neither Android nor outdoor evidence."""
import unittest
from unittest.mock import patch
from tests import test_routes as support
from dittobuffer.gameplay.game import p,Game
from dittobuffer.core.database import Database
from dittobuffer.gameplay import route_cells
from dittobuffer.gameplay.species import make_pokemon
from dittobuffer.core.management import save,owned,level_of

class RouteCellTests(unittest.TestCase):
    def setUp(self):
        self.f=support.RouteTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.game.routes['cells']={'enabled':True,'daily_limit':3,'capacity':300,'per_route':1}
        value=patch('dittobuffer.gameplay.route_cells.now_ms',lambda:self.f.clock);value.start();self.addCleanup(value.stop)
    def complete(self):
        f=self.f;f.start()
        for i in range(1,26):f.fix(i)
        return f.progress(True)
    def cube(self):
        with self.f.db.transaction() as db:db.execute('INSERT INTO items VALUES(?,651,1)',(self.f.players[0],))
    def test_no_cube_no_cells_and_replay_never_adds_rewards(self):
        earned=self.complete();self.assertFalse(any(v.item==650 for v in earned.route_loot.loot_item))
        self.assertEqual(self.f.progress(True),earned)
        with self.f.db.transaction() as db:self.assertEqual(route_cells.quantity(db,self.f.players[0],650),0)
    def test_completion_cell_receipt_cube_restart_and_bag_exclusion(self):
        f=self.f;self.cube();earned=self.complete()
        self.assertEqual([(v.item,v.count) for v in earned.route_loot.loot_item if v.item==650],[(650,1)])
        self.assertEqual(f.progress(True),earned)
        with f.db.transaction() as db:
            self.assertEqual(route_cells.quantity(db,f.players[0],650),1)
            all_items=db.execute('SELECT SUM(count) FROM items WHERE player_id=?',(f.players[0],)).fetchone()[0]
            self.assertEqual(f.game.bag_count(db,f.players[0]),all_items-2)
            self.assertEqual(db.execute('SELECT SUM(count) FROM route_cell_receipts').fetchone()[0],1)
        f.game.config['routes']['cells']=f.game.routes['cells']
        restarted=Game(f.game.config,Database(f.db.path))
        with restarted.db.transaction() as db:self.assertEqual(route_cells.quantity(db,f.players[0],650),1)
    def test_same_route_daily_limit_and_independent_players(self):
        f=self.f;self.cube();self.complete()
        f.fix(0,advance=200000);self.complete()
        with f.db.transaction() as db:
            self.assertEqual(route_cells.quantity(db,f.players[0],650),1)
            self.assertEqual(route_cells.quantity(db,f.players[1],650),0)
    def test_daily_limit_and_capacity_are_transactional_across_routes(self):
        f=self.f;self.cube()
        with f.db.transaction() as db:
            for index in range(5):
                ident='cell-eligible-'+str(index)
                from dittobuffer.gameplay.routes import put
                route=p.SharedRouteProto();route.CopyFrom(f.route);route.id='distinct-'+str(index)
                put(f.game,db,route,f.points,{'source':'unit-test'})
                db.execute("INSERT INTO route_attempts(id,player,route,version,snapshot,state,reversed,start_ms,expires_ms) VALUES(?,?,?,1,?,'COMPLETED',0,?,?)",(ident,f.players[0],route.id,route.SerializeToString(),f.clock,f.clock+10000))
                attempt=db.execute('SELECT * FROM route_attempts WHERE id=?',(ident,)).fetchone()
                self.assertEqual(route_cells.award(f.game,db,attempt),1 if index<3 else 0)
            self.assertEqual(route_cells.quantity(db,f.players[0],650),3)
            self.assertEqual(sum(v[0] for v in db.execute('SELECT count FROM route_cell_receipts')),3)
    def test_explicit_tuning_required(self):
        self.f.game.routes['cells']={'enabled':True}
        with self.assertRaises(ValueError):route_cells.settings(self.f.game)
    def test_enabled_game_master_has_unique_cell_templates_and_loads(self):
        from dittobuffer.core.master import templates,prelogin
        cfg=dict(self.f.game.config);cfg['routes']=dict(self.f.game.routes)
        rows=templates(cfg);names=[v.template_id for v in rows]
        self.assertEqual(len(names),len(set(names)))
        self.assertTrue(next(v for v in rows if v.template_id=='ITEM_BEANS').item.ignore_inventory_space)
        categories=next(v for v in rows if v.template_id=='ITEM_INVENTORY_UPDATE_SETTINGS').item_inventory_update_settings.category_proto
        self.assertTrue(any(5 in v.category for v in categories))
        self.assertGreater(len(prelogin(cfg)),1000)
    def mon(self,form=2823):
        f=self.f;player=f.players[0];mon=make_pokemon(p,f.game.forms[718,form],718,20,[10,11,12]);mon.id=999
        with f.db.transaction() as db:
            db.execute("INSERT INTO spawns VALUES(999,'zygarde-unit',718,52.08,5.12,0)")
            db.execute('INSERT INTO pokemon VALUES(999,?,999,718,?)',(player,mon.SerializeToString()))
            save(f.game,db,player,mon,20);db.execute('INSERT INTO items VALUES(?,650,250)',(player,))
            db.execute('UPDATE players SET stardust=10000 WHERE id=?',(player,))
            db.execute('INSERT INTO candy VALUES(?,718,100)',(player,))
        return mon
    def test_verified_gm_form_costs_and_native_wire(self):
        rules=route_cells.transitions()
        self.assertEqual(rules[2823,2824]['count'],50);self.assertEqual(rules[2824,2593]['count'],200)
        self.assertEqual((rules[2593,2824]['candy'],rules[2593,2824]['stardust']),(10,2000))
        self.assertNotIn((2591,2592),rules)
        self.assertEqual(p.ChangePokemonFormProto(pokemon_id=1,target_form=2824).SerializeToString(),b'\x09\x01\0\0\0\0\0\0\0\x10\x88\x16')
    def test_forms_preserve_identity_ivs_level_and_rpc_retries(self):
        f=self.f;old=self.mon()
        req=p.ChangePokemonFormProto(pokemon_id=999,target_form=2824)
        out=f.call(1722,req,p.ChangePokemonFormOutProto);self.assertEqual(out.result,1)
        self.assertEqual(out.changed_pokemon.pokemon_display.form,2824)
        self.assertEqual(f.call(1722,req,p.ChangePokemonFormOutProto).result,1)
        with f.db.transaction() as db:
            mon=owned(db,f.players[0],999);self.assertEqual(mon.id,old.id)
            self.assertEqual((mon.individual_attack,mon.individual_defense,mon.individual_stamina),(10,11,12))
            self.assertEqual(level_of(db,mon),20);self.assertEqual(route_cells.quantity(db,f.players[0],650),200)
        req.target_form=2593;self.assertEqual(f.call(1722,req,p.ChangePokemonFormOutProto).result,1)
        with f.db.transaction() as db:self.assertEqual(route_cells.quantity(db,f.players[0],650),0)
    def test_native_preview_projects_stats_without_consuming_or_persisting(self):
        f=self.f;old=self.mon();req=p.ChangePokemonFormProto(pokemon_id=999,target_form=2824)
        preview=f.call(3021,req,p.ChangePokemonFormOutProto)
        self.assertEqual(preview.result,1);self.assertEqual(preview.changed_pokemon.pokemon_display.form,2824)
        self.assertNotEqual(preview.changed_pokemon.cp,old.cp)
        self.assertEqual(f.call(3021,req,p.ChangePokemonFormOutProto,index=1).result,2)
        with f.db.transaction() as db:
            self.assertEqual(owned(db,f.players[0],999).SerializeToString(),old.SerializeToString())
            self.assertEqual(route_cells.quantity(db,f.players[0],650),250)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM zygarde_form_receipts').fetchone()[0],0)
        actual=f.call(1722,req,p.ChangePokemonFormOutProto)
        self.assertEqual(actual,preview)
    def test_form_foreign_owner_insufficient_and_legacy_rejected(self):
        f=self.f;self.mon(2591);req=p.ChangePokemonFormProto(pokemon_id=999,target_form=2592)
        self.assertEqual(f.call(1722,req,p.ChangePokemonFormOutProto,index=1).result,2)
        self.assertEqual(f.call(1722,req,p.ChangePokemonFormOutProto).result,5)
        with f.db.transaction() as db:
            mon=owned(db,f.players[0],999);mon.pokemon_display.form=2823;save(f.game,db,f.players[0],mon,20)
            db.execute('UPDATE items SET count=49 WHERE player_id=? AND item=650',(f.players[0],))
        req.target_form=2824;self.assertEqual(f.call(1722,req,p.ChangePokemonFormOutProto).result,3)

if __name__=='__main__':unittest.main()
