import unittest
import tests.test_game as fixtures
from dittobuffer.gameplay.game import Game,p
from dittobuffer.core.management import owned
from dittobuffer.gameplay.species import rules_for,make_pokemon

class PokemonCatalogTests(unittest.TestCase):
    setUp=fixtures.GameTests.setUp
    tearDown=fixtures.GameTests.tearDown
    call=fixtures.GameTests.call
    spawn=fixtures.GameTests.spawn
    encounter=fixtures.GameTests.encounter
    catch=fixtures.GameTests.catch

    def captured(self):
        pokemon_id=self.catch(self.encounter()).captured_pokemon_id
        with self.database.transaction() as db:
            db.execute('UPDATE players SET stardust=100000')
            db.execute('UPDATE candy SET count=100')
            for item in (1201,1202,1203,1204):db.execute('INSERT INTO items VALUES(?,?,3)',(self.game.player(db,db.execute('SELECT player_id FROM sessions WHERE ticket=?',(self.ticket,)).fetchone()[0])['id'],item))
        return pokemon_id

    def snapshot(self,ident):
        with self.database.transaction() as db:
            player=db.execute('SELECT player_id FROM sessions WHERE ticket=?',(self.ticket,)).fetchone()[0]
            return owned(db,player,ident),db.execute('SELECT stardust FROM players WHERE id=?',(player,)).fetchone()[0],dict(db.execute('SELECT item,count FROM items WHERE player_id=?',(player,)))

    def test_fixed64_writers_independent_literal(self):
        self.assertEqual(p.UseItemMoveRerollProto(pokemon_id=1).SerializeToString(),b'\x11\x01'+b'\0'*7)
        self.assertEqual(p.UnlockPokemonMoveProto(pokemon_id=1).SerializeToString(),b'\x09\x01'+b'\0'*7)
        self.assertEqual(p.PokedexEntryProto(captured_costumes=[1],captured_forms=[163],captured_genders=[1]).SerializeToString(),b'\x32\x01\x01\x3a\x02\xa3\x01\x42\x01\x01')
        self.assertEqual(p.EditPokemonTagProto(tag_to_edit=[p.PokemonTagProto(id=1)]).SerializeToString(),b'\x12\x02\x08\x01')
        self.assertEqual(p.EditPokemonTagOutProto(edit_result=[1]).SerializeToString(),b'\x12\x01\x01')

    def dex(self):
        out=self.call(4,p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
        return [value.pokedex_entry for item in out.inventory_delta.inventory_item
            if (value:=p.HoloInventoryItemProto.FromString(item.item)).HasField('pokedex_entry')]

    def test_dex_seen_then_caught_and_transfer_restart_preserves_registration(self):
        spawn=self.encounter();entry=self.dex()[0]
        self.assertEqual((entry.times_encountered,entry.times_captured),(1,0))
        ident=self.catch(spawn).captured_pokemon_id
        before=self.snapshot(ident)[0]
        self.assertEqual(self.dex()[0].times_captured,1)
        self.assertEqual(self.snapshot(ident)[0],before)
        self.assertEqual(self.call(112,p.ReleasePokemonProto(pokemon_ids=[ident]),p.ReleasePokemonOutProto).status,1)
        self.game=Game(self.config,self.database)
        entry=self.dex()[0];self.assertEqual((entry.times_encountered,entry.times_captured),(1,1))
        self.assertIn(before.pokemon_display.form,entry.captured_forms)

    def test_dex_repeated_encounter_and_inventory_do_not_duplicate_counts(self):
        spawn=self.encounter()
        self.call(102,p.EncounterProto(encounter_id=spawn.encounter_id,spawnpoint_id=spawn.spawnpoint_id,
            player_lat_degrees=spawn.latitude,player_lng_degrees=spawn.longitude),p.EncounterOutProto)
        self.catch(spawn)
        for _ in range(3):self.assertEqual(self.dex()[0].times_captured,1)

    def test_dex_import_retained_history_does_not_rewrite_owned(self):
        ident=self.captured();before=self.snapshot(ident)[0]
        with self.database.transaction() as db:
            db.execute('DELETE FROM pokedex_observations');db.execute('DELETE FROM pokedex_imports')
        self.assertEqual(self.dex()[0].times_captured,1)
        self.assertEqual(self.snapshot(ident)[0],before)

    def test_regular_tms_consume_once_and_survive_restart(self):
        ident=self.captured();before=self.snapshot(ident)[0]
        request=p.UseItemMoveRerollProto(item=1201,pokemon_id=ident)
        out=self.call(813,request,p.UseItemMoveRerollOutProto,rpc_id=700)
        self.assertEqual(out.result,1);self.assertNotEqual(before.move1,out.updated_pokemon.move1)
        self.assertEqual(self.call(813,request,p.UseItemMoveRerollOutProto,rpc_id=700),out)
        self.game=Game(self.config,self.database)
        self.assertEqual(self.snapshot(ident)[2][1201],2)
        charged=self.call(813,p.UseItemMoveRerollProto(item=1202,pokemon_id=ident),p.UseItemMoveRerollOutProto)
        self.assertEqual(charged.result,1);self.assertNotEqual(before.move2,charged.updated_pokemon.move2)

    def test_invalid_elite_move_and_locked_slot_do_not_consume(self):
        ident=self.captured();before=self.snapshot(ident)
        for request,code in [(p.UseItemMoveRerollProto(item=1203,pokemon_id=ident,target_elite_move=209),10),
            (p.UseItemMoveRerollProto(item=1202,pokemon_id=ident,reroll_unlocked_move=True),8)]:
            self.assertEqual(self.call(813,request,p.UseItemMoveRerollOutProto).result,code)
        self.assertEqual(before,self.snapshot(ident))

    def test_second_attack_cost_restart_and_new_request_retry(self):
        ident=self.captured();before=self.snapshot(ident)
        request=p.UnlockPokemonMoveProto(pokemon_id=ident)
        out=self.call(1004,request,p.UnlockPokemonMoveOutProto)
        self.assertEqual(out.result,1);self.assertNotEqual(out.unlocked_pokemon.move2,out.unlocked_pokemon.move3)
        after=self.snapshot(ident);self.assertEqual(before[1]-after[1],10000)
        self.assertEqual(self.call(1004,request,p.UnlockPokemonMoveOutProto).result,4)
        self.game=Game(self.config,self.database)
        self.assertEqual(self.snapshot(ident),after)
        with self.database.transaction() as db:self.assertEqual(db.execute('SELECT count FROM candy WHERE family=1').fetchone()[0],75)

    def test_missing_currency_and_owner_are_atomic(self):
        ident=self.captured()
        with self.database.transaction() as db:db.execute('UPDATE players SET stardust=0')
        before=self.snapshot(ident)
        self.assertEqual(self.call(1004,p.UnlockPokemonMoveProto(pokemon_id=ident),p.UnlockPokemonMoveOutProto).result,5)
        self.assertEqual(before,self.snapshot(ident))
        self.assertEqual(self.call(813,p.UseItemMoveRerollProto(item=1201,pokemon_id=999999),p.UseItemMoveRerollOutProto).result,2)

    def test_full_forms_have_distinct_stats_types_and_moves(self):
        self.assertGreater(len(self.game.species),900);self.assertGreater(len(self.game.forms),2300)
        normal=self.game.forms[26,49];alola=self.game.forms[26,50]
        self.assertEqual(alola['types'],[13,14]);self.assertNotEqual(normal['attack'],alola['attack'])
        pokemon=make_pokemon(p,alola,26,20,[15]*3)
        self.assertEqual(rules_for(self.game,pokemon),alola)
        self.assertEqual(pokemon.pokemon_display.form,50)
        self.assertTrue(self.game.forms[25,894]['is_costume'])

    def test_regional_catch_preserves_display_and_form_statistics(self):
        self.game.spawn_pools=[{'species':[{'id':26,'form':50,'weight':1,'gender':2}]}]
        ident=self.captured();pokemon=self.snapshot(ident)[0]
        self.assertEqual(pokemon.pokemon_id,26);self.assertEqual(pokemon.pokemon_display.form,50)
        self.assertEqual(pokemon.pokemon_display.gender,2)
        self.assertIn(pokemon.move1,self.game.forms[26,50]['fast_moves'])
        self.game=Game(self.config,self.database)
        self.assertEqual(self.snapshot(ident)[0],pokemon)

    def test_item_evolution_consumes_required_item_and_preserves_second_slot(self):
        ident=self.captured();rules=self.game.species[61]
        value=make_pokemon(p,rules,61,5,[15,12,11],shiny=True,gender=2);value.id=ident;value.move3=rules['charged_moves'][1]
        with self.database.transaction() as db:
            player=db.execute('SELECT player_id FROM pokemon WHERE id=?',(ident,)).fetchone()[0]
            db.execute('UPDATE pokemon SET species=61,data=? WHERE id=?',(value.SerializeToString(),ident))
            db.execute('INSERT INTO candy VALUES(?,?,250)',(player,rules['family']))
            db.execute('INSERT INTO items VALUES(?,?,1)',(player,1102))
        req=p.EvolvePokemonProto(pokemon_id=ident,target_pokemon_id=186,evolution_item_requirement=1102)
        out=self.call(125,req,p.EvolvePokemonOutProto)
        self.assertEqual(out.result,1);self.assertEqual(out.evolved_pokemon.id,ident)
        self.assertTrue(out.evolved_pokemon.pokemon_display.shiny);self.assertTrue(out.evolved_pokemon.move3)
        with self.database.transaction() as db:self.assertEqual(db.execute('SELECT count FROM items WHERE item=1102').fetchone()[0],0)

    def test_gender_evolution_failure_does_not_charge(self):
        ident=self.captured();rules=self.game.species[415]
        value=make_pokemon(p,rules,415,5,[15]*3,gender=1);value.id=ident
        with self.database.transaction() as db:
            player=db.execute('SELECT player_id FROM pokemon WHERE id=?',(ident,)).fetchone()[0]
            db.execute('UPDATE pokemon SET species=415,data=? WHERE id=?',(value.SerializeToString(),ident))
            db.execute('INSERT INTO candy VALUES(?,?,250)',(player,rules['family']))
        before=self.snapshot(ident)
        self.assertEqual(self.call(125,p.EvolvePokemonProto(pokemon_id=ident),p.EvolvePokemonOutProto).result,4)
        self.assertEqual(before,self.snapshot(ident))

    def test_unimplemented_evolution_condition_fails_without_charging(self):
        ident=self.captured();value=self.snapshot(ident)[0]
        rules=rules_for(self.game,value)
        rules['evolution_branches']=[{1:2,3:25,24:100}]
        before=self.snapshot(ident)
        with self.database.transaction() as db:
            candy_before=list(db.execute('SELECT player_id,family,count FROM candy'))
        self.assertEqual(self.call(125,p.EvolvePokemonProto(pokemon_id=ident),p.EvolvePokemonOutProto).result,4)
        self.assertEqual(before,self.snapshot(ident))
        with self.database.transaction() as db:self.assertEqual(candy_before,list(db.execute('SELECT player_id,family,count FROM candy')))

    def test_purified_evolution_uses_native_cost_and_retry_is_idempotent(self):
        self.game.spawn_pools=[{'species':[{'id':19,'weight':1}]}]
        ident=self.captured();value=self.snapshot(ident)[0];rules=rules_for(self.game,value)
        self.assertEqual(rules['evolution_branches'][0][19],22)
        value.pokemon_display.alignment=2
        with self.database.transaction() as db:
            player=db.execute('SELECT player_id FROM pokemon WHERE id=?',(ident,)).fetchone()[0]
            db.execute('UPDATE pokemon SET data=? WHERE id=?',(value.SerializeToString(),ident))
            db.execute('UPDATE candy SET count=22 WHERE player_id=? AND family=?',(player,rules['family']))
        req=p.EvolvePokemonProto(pokemon_id=ident)
        preview=self.call(125,p.EvolvePokemonProto(pokemon_id=ident,preview=True),p.EvolvePokemonOutProto)
        self.assertEqual(preview.result,1)
        out=self.call(125,req,p.EvolvePokemonOutProto,rpc_id=986)
        self.assertEqual(out.result,1);self.assertEqual(out.evolved_pokemon.pokemon_display.alignment,2)
        self.assertEqual(out,self.call(125,req,p.EvolvePokemonOutProto,rpc_id=986))
        self.game=Game(self.config,self.database)
        with self.database.transaction() as db:
            self.assertEqual(db.execute('SELECT count FROM candy WHERE player_id=? AND family=?',(player,rules['family'])).fetchone()[0],1)
            self.assertEqual(owned(db,player,ident),out.evolved_pokemon)

    def test_unreviewed_costume_evolution_cannot_strip_the_costume(self):
        ident=self.captured();value=make_pokemon(p,self.game.forms[25,894],25,5,[10]*3);value.id=ident
        with self.database.transaction() as db:
            db.execute('UPDATE pokemon SET species=25,data=? WHERE id=?',(value.SerializeToString(),ident))
            before=[tuple(r) for r in db.execute('SELECT * FROM candy')]
        snapshot=self.snapshot(ident)
        self.assertEqual(self.call(125,p.EvolvePokemonProto(pokemon_id=ident,target_pokemon_id=26),p.EvolvePokemonOutProto).result,4)
        self.assertEqual(snapshot,self.snapshot(ident))
        with self.database.transaction() as db:self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM candy')])

    def test_protected_mass_transfer_is_atomic(self):
        ident=self.captured();value=self.snapshot(ident)[0]
        self.game.config['spawn_generation']=self.game.config.get('spawn_generation',1)+1
        ordinary_id=self.catch(self.encounter()).captured_pokemon_id
        value.pokemon_display.shiny=True
        with self.database.transaction() as db:
            player=db.execute('SELECT player_id FROM pokemon WHERE id=?',(ident,)).fetchone()[0]
            db.execute('UPDATE pokemon SET data=? WHERE id=?',(value.SerializeToString(),ident))
            before=[tuple(r) for r in db.execute('SELECT * FROM pokemon ORDER BY id')]
            candy_before=[tuple(r) for r in db.execute('SELECT * FROM candy')]
        self.assertEqual(self.call(112,p.ReleasePokemonProto(pokemon_ids=[ordinary_id,ident]),p.ReleasePokemonOutProto).status,3)
        with self.database.transaction() as db:
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM pokemon ORDER BY id')])
            self.assertEqual(candy_before,[tuple(r) for r in db.execute('SELECT * FROM candy')])

    def test_original_background_is_frozen_through_catch_and_restart(self):
        self.config['pokemon_backgrounds']={'enabled':True,'cards':[{'id':32001,'type':2,
            'image_url':'http://127.0.0.1:8080/assets/dittobuffer-stop.png','sources':['wild'],'probability':1}]}
        self.game=Game(self.config,self.database)
        ident=self.captured();before=self.snapshot(ident)[0]
        self.assertEqual(before.pokemon_display.location_card.location_card,32001)
        self.config['pokemon_backgrounds']['enabled']=False;self.game=Game(self.config,self.database)
        self.assertEqual(self.snapshot(ident)[0],before)
        self.assertEqual(list(self.dex()[0].location_cards),[32001])

    def test_background_gate_and_source_validation(self):
        from dittobuffer.gameplay.backgrounds import templates,assign
        import random
        card={'id':32001,'type':2,'image_url':'http://127.0.0.1:8080/assets/dittobuffer-stop.png','sources':['raid'],'species':[1],'probability':1}
        self.config['pokemon_backgrounds']={'enabled':True,'cards':[card]}
        values=templates(self.config);gate=next(t for t in values if t.HasField('feature_gate'))
        self.assertEqual(gate.feature_gate.sub_feature_gate_list[0].name,'BACKGROUNDS_PDP_RENDERER_SUB_FEATURE_GATE')
        self.assertEqual(gate.feature_gate.sub_feature_gate_list[0].status,1)
        self.game=Game(self.config,self.database)
        value=make_pokemon(p,self.game.species[1],1,5,[1]*3)
        assign(self.game,value,random.Random(1),'wild');self.assertFalse(value.pokemon_display.HasField('location_card'))
        assign(self.game,value,random.Random(1),'raid');self.assertEqual(value.pokemon_display.location_card.location_card,32001)
        card['image_url']='https://unapproved.invalid/art.png'
        with self.assertRaises(ValueError):templates(self.config)

    def test_storage_tags_native_wire_retries_restart_and_delete(self):
        ident=self.captured();self.game.config['pokemon_tags']={'minimum_level':1}
        methods={v:k for k,v in self.game.methods.items()}
        self.assertEqual(p.PokemonTagProto(id=1,name='A',color=2,sort_index=3).SerializeToString(),b'\x08\x01\x12\x01A\x18\x02\x20\x03')
        req=p.CreatePokemonTagProto(name='Favorites later',color=1)
        out=self.call(methods['CREATE_POKEMON_TAG'],req,p.CreatePokemonTagOutProto,rpc_id=900)
        self.assertEqual(out.result,1);tag=out.created_tag.id
        self.assertEqual(out,self.call(methods['CREATE_POKEMON_TAG'],req,p.CreatePokemonTagOutProto,rpc_id=900))
        self.assertEqual(self.call(methods['CREATE_POKEMON_TAG'],req,p.CreatePokemonTagOutProto).result,3)
        req=p.SetPokemonTagsForPokemonProto();req.tag_changes.add(pokemon_id=ident,tags_to_add=[tag])
        for _ in range(2):self.assertEqual(self.call(methods['SET_POKEMON_TAGS_FOR_POKEMON'],req,p.SetPokemonTagsForPokemonOutProto).status,1)
        self.assertEqual(list(self.snapshot(ident)[0].pokemon_tag_ids),[tag])
        edit=p.EditPokemonTagProto(tag_to_edit=[p.PokemonTagProto(id=tag,name='Reviewed',color=3,sort_index=0)])
        for _ in range(2):self.assertEqual(list(self.call(methods['EDIT_POKEMON_TAG'],edit,p.EditPokemonTagOutProto,rpc_id=901).edit_result),[1])
        self.config['pokemon_tags']={'minimum_level':1};self.game=Game(self.config,self.database)
        saved=self.call(methods['GET_POKEMON_TAGS'],p.GetPokemonTagsProto(),p.GetPokemonTagsOutProto).tag[0]
        self.assertEqual((saved.id,saved.name,saved.color),(tag,'Reviewed',3))
        self.assertEqual(self.call(methods['DELETE_POKEMON_TAG'],p.DeletePokemonTagProto(tag_id=tag),p.DeletePokemonTagOutProto).result,1)
        self.assertFalse(self.snapshot(ident)[0].pokemon_tag_ids)

    def test_storage_tag_batch_validation_is_atomic(self):
        ident=self.captured();self.game.config['pokemon_tags']={'minimum_level':1}
        methods={v:k for k,v in self.game.methods.items()}
        tag=self.call(methods['CREATE_POKEMON_TAG'],p.CreatePokemonTagProto(name='Battle',color=3),p.CreatePokemonTagOutProto).created_tag.id
        before=self.snapshot(ident)[0]
        req=p.SetPokemonTagsForPokemonProto();req.tag_changes.add(pokemon_id=ident,tags_to_add=[tag]);req.tag_changes.add(pokemon_id=999999,tags_to_add=[tag])
        self.assertEqual(self.call(methods['SET_POKEMON_TAGS_FOR_POKEMON'],req,p.SetPokemonTagsForPokemonOutProto).status,3)
        self.assertEqual(before,self.snapshot(ident)[0])
        req=p.SetPokemonTagsForPokemonProto();req.tag_changes.add(pokemon_id=ident,tags_to_add=[999999])
        self.assertEqual(self.call(methods['SET_POKEMON_TAGS_FOR_POKEMON'],req,p.SetPokemonTagsForPokemonOutProto).status,4)
        self.assertEqual(before,self.snapshot(ident)[0])

    def test_second_charged_gym_action_uses_its_own_move_and_energy(self):
        from dittobuffer.gameplay import gym_battle
        from dittobuffer.gameplay.battle_master import MOVES
        a=make_pokemon(p,self.game.species[19],19,10,[10]*3);a.id=1;a.move3=131
        d=make_pokemon(p,self.game.species[143],143,10,[10]*3);d.id=2
        state={'party':[gym_battle.entry(a)],'active':0,'defender':gym_battle.entry(d),'events':[],'pending':[]}
        state['party'][0]['energy']=100
        self.assertTrue(gym_battle.schedule(self.game,state,0,11,1000))
        self.assertEqual(state['party'][0]['energy'],100+MOVES[131][3])
        event=p.BattleActionProto.FromString(bytes.fromhex(state['events'][-1]));self.assertEqual(event.type,11)
        self.assertEqual(state['pending'][0]['damage'],gym_battle.damage(self.game,state['party'][0],state['defender'],131))
        a.move3=0;state['party'][0]=gym_battle.entry(a);state['party'][0]['energy']=100
        self.assertFalse(gym_battle.schedule(self.game,state,0,11,5000))

    def test_dex_native_shiny_category_uses_persistent_observations(self):
        self.game.spawn_pools[0]['shiny_rate']=1
        self.catch(self.encounter());entry=self.dex()[0]
        categories={v.value.pokedex_category:v.value for v in entry.category_status}
        self.assertTrue(entry.captured_shiny);self.assertTrue(categories[11].acquired)
        self.assertTrue(categories[1].acquired);self.assertGreater(entry.last_capture_timestamp_ms,0)

    def test_event_sources_preserve_explicit_regional_identity(self):
        from dittobuffer.gameplay import eggs, research, raids
        from dittobuffer.gameplay.game import now_ms
        variant={'species':26,'form':50,'gender':2}
        self.config['eggs']={'pools':[{'km':2,'species':[variant]}],'stop_drop_probability':1}
        self.config['raids']={'enabled':True,'epoch_ms':now_ms()-10000,'pools':[variant|{'tier':1,'hp':100,'cp':500,'weight':1}]}
        self.game=Game(self.config,self.database);self.spawn()
        with self.database.transaction() as db:
            player=db.execute('SELECT player_id FROM sessions WHERE ticket=?',(self.ticket,)).fetchone()[0]
            db.execute("INSERT INTO forts VALUES('test-stop',1,'Test Stop',52.37,4.89)")
            db.execute("INSERT INTO forts VALUES('test-gym',0,'Test Gym',52.37,4.89)")
            stop=db.execute('SELECT * FROM forts WHERE kind=1').fetchone();gym=db.execute('SELECT * FROM forts WHERE kind=0').fetchone()
            eggs.award(self.game,db,player,stop)
            egg=p.PokemonProto.FromString(db.execute('SELECT data FROM eggs WHERE player_id=?',(player,)).fetchone()[0])
            research.grant(self.game,db,player,'regional-proof',[variant|{'type':'encounter'}])
            reward=p.PokemonProto.FromString(db.execute('SELECT a.data FROM spawn_attributes a JOIN research_encounters e ON e.spawn_id=a.spawn_id WHERE e.player_id=?',(player,)).fetchone()[0])
            boss=p.PokemonProto.FromString(raids.schedule(self.game,db,gym)['pokemon'])
        for mon in (egg,reward,boss):
            self.assertEqual(mon.pokemon_id,26);self.assertEqual(mon.pokemon_display.form,50);self.assertEqual(mon.pokemon_display.gender,2)
        from dittobuffer.gameplay.species import pool_rules
        with self.assertRaises(ValueError):pool_rules(self.game,{'species':144,'gender':1})

if __name__=='__main__':unittest.main()
