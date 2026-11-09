"""Mega ownership, spending, projected identity and authoritative lifecycle."""
import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from dittobuffer.gameplay.game import Game,p,wire,ROOT,RpcError
from dittobuffer.core.database import Database
from dittobuffer.gameplay.species import make_pokemon
from dittobuffer.gameplay import mega


class MegaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds')
        self.cfg=json.loads((ROOT/'config/development.json').read_text())
        self.cfg['mega']=dict(enabled=True,development_only=True,duration_ms=10000,cooldown_ms=40000)
        self.db=Database(Path(self.tmp.name)/'mega.sqlite3')
        self.clock=1800000000000
        self.patches=[patch('dittobuffer.gameplay.mega.now_ms',lambda:self.clock),patch('dittobuffer.gameplay.game.now_ms',lambda:self.clock)]
        for v in self.patches:v.start()
        self.game=Game(self.cfg,self.db)
        self.ticket=self.game.authenticate(wire.AuthRequest(auth_id='mega-owner')).ticket
        self.other=self.game.authenticate(wire.AuthRequest(auth_id='mega-other')).ticket
        with self.db.transaction() as db:
            self.player=db.execute('SELECT player_id FROM sessions WHERE ticket=?',(self.ticket,)).fetchone()[0]
            self.original={}
            for i,species in enumerate([3,6,9,94,384,16],1):
                spawn=910000000000000000+i
                db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',(spawn,str(spawn),species,52.091,4.263,self.clock+86400000))
                pokemon=make_pokemon(p,self.game.species[species],species,25,[15]*3)
                pokemon.id=i
                db.execute('INSERT INTO pokemon VALUES(?,?,?,?,?)',(i,self.player,spawn,species,pokemon.SerializeToString()))
                db.execute('INSERT INTO pokemon_levels VALUES(?,25)',(i,))
                self.original[i]=pokemon.SerializeToString()
        self.rpc_id=0

    def tearDown(self):
        for v in self.patches:v.stop()
        self.tmp.cleanup()

    def test_mega_raid_reward_encounter_is_the_ordinary_species(self):
        from dittobuffer.gameplay import raids
        variant=self.game.species[150]
        boss={'species':150,'tier':6,'mega_evolution':2,'level':25}
        pokemon=make_pokemon(p,variant,150,25,[15]*3)
        plain=pokemon.cp
        raids.apply_mega_reward(self.game,pokemon,variant,boss,self.clock+600000)
        # The boss is Mega in combat, but the catch is not left Mega-evolved for
        # the encounter window (and cannot later show a negative timer).
        self.assertEqual(pokemon.pokemon_display.current_temp_evolution,0)
        self.assertFalse(pokemon.pokemon_display.temp_evolution_is_locked)
        self.assertEqual(pokemon.pokemon_display.locked_temp_evolution,0)
        self.assertEqual(pokemon.temp_evo_cp,0)
        self.assertTrue(pokemon.has_mega_evolved)
        self.assertIn(2,list(pokemon.mega_evolved_forms))
        self.assertEqual(pokemon.cp,plain)

    def call(self,pokemon=1,evo=1,ticket=None,rpc_id=None,preview=False):
        self.rpc_id+=1
        request=wire.RpcRequest(rpc_id=rpc_id or self.rpc_id)
        request.requests.add(method=1502,body=p.MegaEvolvePokemonProto(pokemon_id=pokemon,temp_evo_id=evo,preview=preview).SerializeToString())
        return p.MegaEvolvePokemonOutProto.FromString(self.game.rpc(request,ticket or self.ticket).payloads[0].payload)

    def grant(self,species=3,evo=1,amount=1000):
        with self.db.transaction() as db:mega.grant(self.game,db,self.player,species,evo,amount,'fixture:'+str(species)+':'+str(evo))

    def test_native_wire_and_first_evolution_preserve_owned_bytes(self):
        self.assertEqual(p.MegaEvolvePokemonProto(pokemon_id=1).SerializeToString(),bytes.fromhex('090100000000000000'))
        self.assertEqual(p.CaptureScoreProto(candy_from_active_mega=1,experience_from_active_mega=50).SerializeToString(),bytes.fromhex('30013832'))
        self.grant();out=self.call()
        self.assertEqual(out.result,1)
        self.assertEqual(out.evolved_pokemon.pokemon_id,3)
        self.assertEqual(out.evolved_pokemon.pokemon_display.current_temp_evolution,1)
        self.assertGreater(out.evolved_pokemon.temp_evo_cp,out.evolved_pokemon.cp)
        self.assertEqual(out.evolved_pokemon.pokemon_display.mega_evolution_level.level,1)
        with self.db.transaction() as db:
            self.assertEqual(mega.balance(db,self.player,3,1),800)
            self.assertEqual(db.execute('SELECT data FROM pokemon WHERE id=1').fetchone()[0],self.original[1])

    def test_rayquaza_shared_bonus_types_do_not_enable_primal_gameplay(self):
        with self.db.transaction() as db:
            ray=p.PokemonProto.FromString(self.original[5]);ray.move2=384
            db.execute('UPDATE pokemon SET data=? WHERE id=5',(ray.SerializeToString(),))
        self.grant(384);self.assertEqual(self.call(pokemon=5).result,1)
        psychic=make_pokemon(p,self.game.species[63],63,25,[15]*3)
        grass=p.PokemonProto.FromString(self.original[1])
        with self.db.transaction() as db:
            self.assertEqual(mega.catch_bonus(self.game,db,self.player,psychic),(1,0,0))
            self.assertEqual(mega.catch_bonus(self.game,db,self.player,grass),(0,0,0))
            self.assertEqual(mega.catch_bonus(self.game,db,self.player,grass,True),(1,0,0))
        shared=next(t.primal_evo_settings for t in mega.templates(self.cfg) if t.template_id=='PRIMAL_EVO_SETTINGS')
        self.assertEqual([v.pokemon_id for v in shared.type_boosts],[384])
        self.assertEqual(set(shared.type_boosts[0].boost_type),{3,14,16})
        self.assertFalse(shared.HasField('common_temp_settings'))
        self.assertIsNone(mega.branch(382,4));self.assertIsNone(mega.branch(383,4))

    def test_invalid_ownership_species_form_and_insufficient_are_atomic(self):
        self.assertEqual(self.call(ticket=self.other).result,2)
        self.assertEqual(self.call(pokemon=6).result,4)
        self.assertEqual(self.call(evo=3).result,4)
        self.assertEqual(self.call().result,3)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mega_active').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mega_progress').fetchone()[0],0)

    def test_preview_is_read_only_and_replay_cannot_charge_again(self):
        self.grant();out=self.call(preview=True)
        self.assertEqual(out.result,1);self.assertGreater(out.preview.evolving_pokemon_default_cp,0)
        self.assertGreater(out.preview.default_cp_range.min_cp,out.preview.evolving_pokemon_default_cp)
        self.assertEqual(out.evolved_pokemon.pokemon_display.current_temp_evolution,1)
        first=self.call(rpc_id=20);again=self.call(rpc_id=20)
        self.assertEqual(first.SerializeToString(),again.SerializeToString())
        self.assertEqual(self.call().result,7)
        with self.db.transaction() as db:self.assertEqual(mega.balance(db,self.player,3,1),800)

    def test_expiry_offline_restart_repeat_and_free_boundary(self):
        self.grant();self.call();self.clock+=10000
        self.game=Game(self.cfg,Database(self.db.path))
        with self.db.transaction() as db:
            mega.expire(self.game,db)
            pokemon=p.PokemonProto.FromString(self.original[1])
            self.assertIsNone(mega.active(db,1));self.assertEqual(mega.project(self.game,db,pokemon).pokemon_display.current_temp_evolution,0)
            self.assertEqual(mega.cost(self.game,db,pokemon,1),40)
        self.assertEqual(self.call().result,1)
        self.clock+=50000
        with self.db.transaction() as db:
            mega.expire(self.game,db)
            self.assertEqual(mega.cost(self.game,db,pokemon,1),0)
        self.assertEqual(self.call().result,1)
        with self.db.transaction() as db:self.assertEqual(mega.balance(db,self.player,3,1),760)

    def test_switching_and_charizard_branches_are_separate(self):
        self.grant();self.grant(6,2);self.grant(6,3)
        self.call();self.assertEqual(self.call(2,2).result,1)
        self.assertEqual(self.call(2,3).result,1)
        with self.db.transaction() as db:
            self.assertIsNone(mega.active(db,1));self.assertEqual(mega.active(db,2)['temp_evo'],3)
            self.assertEqual(mega.balance(db,self.player,6,2),800);self.assertEqual(mega.balance(db,self.player,6,3),800)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mega_active').fetchone()[0],1)
            from dittobuffer.gameplay.medals import counters
            self.assertEqual(counters(self.game,db,self.player)['mega_unique'],2,'Charizard X/Y are one species for the native Guru medal')

    def test_daily_progression_and_thresholds(self):
        self.grant(amount=10000);self.call()
        for i in range(6):
            self.clock+=86400000;self.call()
        with self.db.transaction() as db:
            self.assertEqual(mega.progress(db,1,1)['points'],7)
            self.assertEqual(mega.progress(db,1,1)['level'],2)
        self.clock+=10000;self.call()
        with self.db.transaction() as db:self.assertEqual(mega.progress(db,1,1)['points'],7)

    def test_rayquaza_does_not_bypass_dragon_ascent(self):
        self.grant(384);self.assertEqual(self.call(5).result,6)

    def test_compound_failure_rolls_back_and_concurrent_requests_charge_once(self):
        self.grant()
        request=wire.RpcRequest(rpc_id=100)
        request.requests.add(method=1502,body=p.MegaEvolvePokemonProto(pokemon_id=1,temp_evo_id=1).SerializeToString())
        request.requests.add(method=999999)
        with self.assertRaises(RpcError):self.game.rpc(request,self.ticket)
        with self.db.transaction() as db:self.assertEqual(mega.balance(db,self.player,3,1),1000)
        def run(index):
            q=wire.RpcRequest(rpc_id=index)
            q.requests.add(method=1502,body=p.MegaEvolvePokemonProto(pokemon_id=1,temp_evo_id=1).SerializeToString())
            return p.MegaEvolvePokemonOutProto.FromString(self.game.rpc(q,self.ticket).payloads[0].payload).result
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,[201,202]))
        self.assertEqual(sorted(results),[1,7])
        with self.db.transaction() as db:self.assertEqual(mega.balance(db,self.player,3,1),800)

    def rpc(self,method,request,response):
        self.rpc_id+=1
        batch=wire.RpcRequest(rpc_id=self.rpc_id)
        batch.requests.add(method=method,body=request.SerializeToString())
        return response.FromString(self.game.rpc(batch,self.ticket).payloads[0].payload)

    def test_energy_inventory_legacy_and_split_branches_and_cap(self):
        self.grant(amount=60000);self.grant(6,2);self.grant(6,3)
        inventory=self.rpc(4,p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
        values=[p.HoloInventoryItemProto.FromString(i.item) for i in inventory.inventory_delta.inventory_item]
        self.assertEqual([(v.mega_evolve_species.pokemon_species_id,v.mega_evolve_species.energy_count) for v in values if v.HasField('mega_evolve_species')],[(3,50000)])
        self.assertEqual({(v.pokemon_temp_evo_resource.temp_evo_pokemon_branch.pokedex_id,v.pokemon_temp_evo_resource.temp_evo_pokemon_branch.temp_evo_id):v.pokemon_temp_evo_resource.energy_count for v in values if v.HasField('pokemon_temp_evo_resource')},{(3,1):50000,(6,2):1000,(6,3):1000})
        with self.db.transaction() as db:
            self.assertEqual(mega.grant(self.game,db,self.player,3,1,60000,'fixture:3:1'),50000)
            self.assertEqual(mega.balance(db,self.player,3,1),50000)
            with self.assertRaises(ValueError):mega.grant(self.game,db,self.player,6,2,1000,'fixture:3:1')

    def test_native_level_cost_upgrade_stale_request_and_restart(self):
        self.grant();self.call()
        preview=self.rpc(3080,p.GetMegaLevelUpPreviewProto(pokemon_id=1,temp_evo_id=1,current_mega_level=1),p.GetMegaLevelUpPreviewOutProto)
        self.assertEqual([(r.target_mega_level,r.total_mega_energy_cost) for r in preview.bulk_level_up_cost_table],[(2,480),(3,1160)])
        req=p.MegaLevelUpProto(pokemon_id=1,temp_evo_id=1,current_mega_level=1,target_mega_level=2)
        upgraded=self.rpc(3079,req,p.MegaLevelUpOutProto)
        self.assertEqual((upgraded.result,upgraded.mega_level_after,upgraded.mega_energy_balance_after),(1,2,320))
        self.game=Game(self.cfg,Database(self.db.path))
        self.assertEqual(self.rpc(3079,req,p.MegaLevelUpOutProto).result,5)
        self.assertEqual(upgraded.updated_pokemon.pokemon_display.mega_evolution_level.level,2)
        with self.db.transaction() as db:
            self.assertEqual(mega.progress(db,1,1)['points'],7)
            self.assertEqual(db.execute('SELECT data FROM pokemon WHERE id=1').fetchone()[0],self.original[1])

    def test_temporary_battle_stats_lock_and_ordinary_hp(self):
        from dittobuffer.gameplay.combat import damage_value
        self.grant();self.call()
        ordinary=p.PokemonProto.FromString(self.original[1]);enemy=p.PokemonProto.FromString(self.original[3])
        with self.db.transaction() as db:boosted=mega.project(self.game,db,ordinary,combat=True)
        self.assertGreater(boosted.cp,ordinary.cp)
        self.assertGreater(damage_value(boosted,enemy,self.game.species,ordinary.move1,forms=self.game.forms),damage_value(ordinary,enemy,self.game.species,ordinary.move1,forms=self.game.forms))
        expected=damage_value(boosted,enemy,self.game.species,ordinary.move1,forms=self.game.forms)
        self.clock+=10001
        self.assertEqual(damage_value(boosted,enemy,self.game.species,ordinary.move1,forms=self.game.forms),expected)
        self.assertEqual(mega.ordinary_hp(boosted,0),0)
        self.assertEqual(mega.ordinary_hp(boosted,boosted.max_stamina),ordinary.max_stamina)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT data FROM pokemon WHERE id=1').fetchone()[0],self.original[1])

    def test_bonus_eligibility_levels_expiry_and_xl_level_gate(self):
        matching=make_pokemon(p,self.game.species[1],1,20,[15]*3)
        other=make_pokemon(p,self.game.species[4],4,20,[15]*3)
        with self.db.transaction() as db:self.assertEqual(mega.catch_bonus(self.game,db,self.player,matching),(0,0,0))
        self.grant();self.call()
        with self.db.transaction() as db:
            self.assertEqual(mega.catch_bonus(self.game,db,self.player,matching),(1,0,0))
            self.assertEqual(mega.catch_bonus(self.game,db,self.player,other),(0,0,0))
            self.assertEqual(mega.catch_bonus(self.game,db,self.player,other,True),(1,0,0))
            db.execute('UPDATE mega_progress SET level=3,points=30 WHERE pokemon_id=1')
            with patch('dittobuffer.gameplay.mega.secrets.randbelow',return_value=0):
                self.assertEqual(mega.catch_bonus(self.game,db,self.player,matching),(2,100,0))
                db.execute('UPDATE players SET xp=20000000 WHERE id=?',(self.player,))
                self.assertEqual(mega.catch_bonus(self.game,db,self.player,matching),(2,100,1))
            self.assertEqual(db.execute('SELECT count FROM mega_catch_xl WHERE player_id=? AND family=1',(self.player,)).fetchone()[0],1)
        self.clock+=10000
        with self.db.transaction() as db:self.assertEqual(mega.catch_bonus(self.game,db,self.player,matching,True),(0,0,0))

    def test_real_catch_bonus_and_new_request_replay_are_atomic(self):
        self.cfg.update(catch_probability=1,spawn_pools=[{'species':[{'id':1,'weight':1}]}],spawn_points_per_tile=1,spawn_neighbor_tiles=0)
        self.game=Game(self.cfg,self.db);self.grant();self.call()
        from s2sphere import CellId,LatLng
        cell=CellId.from_lat_lng(LatLng.from_degrees(52.37,4.89)).parent(12).id()
        world=self.rpc(106,p.GetMapObjectsProto(cell_id=[cell],player_lat=52.37,player_lng=4.89),p.GetMapObjectsOutProto)
        spawn=next(s for c in world.map_cell for s in c.catchable_pokemon)
        self.rpc(102,p.EncounterProto(encounter_id=spawn.encounter_id,spawnpoint_id=spawn.spawnpoint_id,player_lat_degrees=spawn.latitude,player_lng_degrees=spawn.longitude),p.EncounterOutProto)
        req=p.CatchPokemonProto(encounter_id=spawn.encounter_id,spawn_point_guid=spawn.spawnpoint_id,pokeball=1,hit_pokemon=True)
        first=self.rpc(103,req,p.CatchPokemonOutProto)
        self.assertEqual(first.status,1);self.assertEqual(first.scores.temp_evo_score_info.candy_from_active_temp_evo,1)
        self.assertEqual(first.scores.candy_from_active_mega,0,'Do not send two bonus representations')
        self.game=Game(self.cfg,Database(self.db.path))
        self.assertEqual(self.rpc(103,req,p.CatchPokemonOutProto),first)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT count FROM candy WHERE player_id=? AND family=1',(self.player,)).fetchone()[0],self.cfg['catch_candy']+1)
            self.assertEqual(db.execute('SELECT captures FROM players WHERE id=?',(self.player,)).fetchone()[0],1)

    def test_compact_game_master_preserves_existing_species_wire(self):
        import gzip
        from protocol.opaque import fields
        for n,k,_,raw in fields(gzip.decompress((ROOT/'data/static/gmts.binpb.gz').read_bytes())):
            if n!=2 or k!=2:continue
            payload=next((v for a,b,_,v in fields(raw) if a==2 and b==2),None)
            if payload is None:continue
            entries=list(fields(payload));species=next((v for a,b,_,v in entries if a==1),None)
            if species not in mega.SPECIES:continue
            updated=mega.augment_template(raw,self.cfg)
            data=next(v for a,b,_,v in fields(updated) if a==2 and b==2)
            self.assertTrue(data.startswith(payload),'All original species fields must remain byte-identical')
            self.assertEqual(mega.augment_template(updated,self.cfg),updated,'Registration must be idempotent')

    def test_active_restrictions_and_invalid_appearances(self):
        self.grant()
        for change in ('form','costume','alignment','fainted','deployed'):
            mon=p.PokemonProto.FromString(self.original[1])
            if change=='form':mon.pokemon_display.form=172
            elif change=='costume':mon.pokemon_display.costume=1
            elif change=='alignment':mon.pokemon_display.alignment=1
            elif change=='fainted':mon.stamina=0
            else:mon.deployed_fort_id='gym'
            with self.db.transaction() as db:db.execute('UPDATE pokemon SET data=? WHERE id=1',(mon.SerializeToString(),))
            self.assertIn(self.call().result,(4,5))
        with self.db.transaction() as db:db.execute('UPDATE pokemon SET data=? WHERE id=1',(self.original[1],))
        self.call()
        out=self.rpc(112,p.ReleasePokemonProto(pokemon_id=1),p.ReleasePokemonOutProto)
        self.assertEqual(out.status,2)
        self.assertEqual(self.rpc(147,p.UpgradePokemonProto(pokemon_id=1),p.UpgradePokemonOutProto).result,5)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT data FROM pokemon WHERE id=1').fetchone()[0],self.original[1])

    def test_max_level_and_source_specific_fourth_level_unlock(self):
        self.grant(amount=5000);self.call()
        out=self.rpc(3079,p.MegaLevelUpProto(pokemon_id=1,temp_evo_id=1,current_mega_level=1,target_mega_level=3),p.MegaLevelUpOutProto)
        self.assertEqual((out.result,out.mega_level_after,out.total_mega_energy_cost),(1,3,1160))
        self.assertEqual(self.rpc(1506,p.UnlockTemporaryEvolutionLevelProto(pokemon_id=1,temp_evo_id=1,temp_evo_level=4),p.UnlockTemporaryEvolutionLevelOutProto).result,5)
        # Level four exists only for selected source-backed species; never
        # synthesize it for every Mega or allow it before the required level.
        self.assertIsNone(mega.level_settings(3,1,4))
        self.assertEqual(mega.level_settings(26,2,4).mega_energy_cost_to_unlock,5000)

    def test_disabled_and_accelerated_production_configuration_rejected(self):
        disabled=copy.deepcopy(self.cfg);disabled['mega']={'enabled':False}
        self.game=Game(disabled,self.db);self.assertEqual(self.call().result,4)
        invalid=copy.deepcopy(self.cfg);invalid['mega']['development_only']=False
        with self.assertRaises(ValueError):Game(invalid,self.db)

    def test_mega_achievement_receipts_survive_transfer(self):
        from dittobuffer.gameplay.medals import counters,definitions
        self.grant();self.call();self.clock+=50000
        self.assertEqual(self.rpc(112,p.ReleasePokemonProto(pokemon_id=1),p.ReleasePokemonOutProto).status,1)
        with self.db.transaction() as db:
            values=counters(self.game,db,self.player)
            self.assertEqual((values['mega_total'],values['mega_unique']),(1,1))
            self.assertEqual(mega.balance(db,self.player,3,1),800)
        rules={r['counter']:r for r in definitions()}
        self.assertEqual(rules['mega_total']['targets'],[1,50,500,1000])
        self.assertEqual(rules['mega_unique']['targets'],[1,24,36,46])
