"""Migration contracts on isolated data; not fresh Android acceptance."""
import copy,json,unittest
from pathlib import Path
from dittobuffer.paths import ROOT
from dittobuffer.gameplay.game import Game,p
from dittobuffer.core import master
from tests import test_rocket_native as rocket_support

class WorkspaceMigrationTests(unittest.TestCase):
    def test_native_rocket_coexists_with_routes_max_pvp_and_legacy_balloon_tables(self):
        fixture=rocket_support.RocketTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        config=copy.deepcopy(fixture.cfg)
        family=json.loads((ROOT/'config/family.json').read_text())
        config['pvp']=family['pvp'];config['max_system']=family['max_system']
        config['routes']=json.loads((ROOT/'src/dittobuffer/gameplay/routes.example.json').read_text())
        config['routes'].update(enabled=True);config['routes']['cells']['enabled']=True
        config['vivillon']={'enabled':True}
        fixture.game=Game(config,fixture.db)
        self.assertTrue(fixture.game.rocket_native_enabled)
        templates=master.templates(config);names=[v.template_id for v in templates]
        self.assertEqual(len(names),len(set(names)))
        self.assertTrue({'CHARACTER_GRASS_GRUNT_MALE','ROUTE_BADGE_SETTINGS','BUTTERFLY_COLLECTOR_SETTINGS','BREAD_SHARED_SETTINGS','COMBAT_LEAGUE_DEFAULT_GREAT'}<=set(names))
        battle=fixture.start();self.assertTrue(battle.combat_id.startswith('rocket-battle-'))
        fixture.clock+=500
        self.assertEqual(fixture.action(battle).combat.combat_id,battle.combat_id)
        from dittobuffer.gameplay import rocket
        with fixture.db.transaction() as db:
            from dittobuffer.gameplay.gyms import battle_busy
            self.assertTrue(battle_busy(db,fixture.players[0],fixture.parties[0]))
            rocket.ensure(db)
            legacy={row[1] for row in db.execute('PRAGMA table_info(rocket_balloons)')}
            native={row[1] for row in db.execute('PRAGMA table_info(rocket_native_balloons)')}
            self.assertIn('encounter_spawn_id',legacy);self.assertIn('slot',native)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_native_shadow_cost_overlay_preserves_unknown_fields(self):
        from dittobuffer.gameplay.rocket_shadow import augment_template
        from protocol.opaque import byte_field,fields
        raw=byte_field(1,b'V0001_POKEMON_BULBASAUR')+byte_field(2,p.PokemonSettingsProto(unique_id=1).SerializeToString())+byte_field(501,b'preserve-native-unknown')
        result=augment_template(raw,dict(dust=3000,candy=3,return_move=323,frustration=322))
        value=p.GameMasterClientTemplateProto.FromString(result)
        self.assertEqual(getattr(value,value.DESCRIPTOR.fields_by_number[2].name).shadow.purification_candy_needed,3)
        self.assertIn((501,2,b'preserve-native-unknown'),[(n,k,v) for n,k,w,v in fields(result)])

    def test_native_rocket_and_optional_feature_gm_epochs_are_distinct(self):
        versions=set()
        for native in (False,True):
            for pvp in (False,True):
                for routes in (False,True):
                    config={'rocket':{'enabled':True,'server_combat':native},'pvp':{'enabled':pvp},'routes':{'enabled':routes}}
                    versions.add(master.version(config))
        self.assertEqual(len(versions),8)
