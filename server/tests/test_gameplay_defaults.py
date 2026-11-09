"""Integration guards for the accepted model identities used by new spawns."""
import json
from pathlib import Path
import unittest
from dittobuffer.gameplay.game import ROOT,p
from dittobuffer.gameplay.species import catalog,form_catalog,make_pokemon,rules_for


class GameplayDefaultTests(unittest.TestCase):
    def test_all_evidenced_overrides_have_exact_stat_bearing_forms(self):
        path=ROOT/'data/static/gmts.binpb.gz';defaults=catalog(path);forms=form_catalog(path)
        overrides=json.loads((ROOT/'src/dittobuffer/gameplay/pokemon-defaults.json').read_text())['mappings']
        self.assertEqual(len(defaults),958);self.assertEqual(len(overrides),30)
        for entry in overrides:
            with self.subTest(species=entry['species']):
                self.assertEqual(defaults[entry['species']],forms[entry['species'],entry['form']])
                self.assertFalse(defaults[entry['species']]['is_costume'])
                mon=make_pokemon(p,defaults[entry['species']],entry['species'],10,[7,9,11])
                self.assertEqual(mon.pokemon_display.form,entry['form'])
                self.assertEqual(mon.pokemon_id,entry['species'])

    def test_normal_spawn_distribution_and_existing_default_identities_preserved(self):
        cfg=json.loads((ROOT/'config/development.json').read_text())
        self.assertEqual([r['id'] for r in cfg['spawn_pools'][0]['species']],[1,4,7,10,16,25])
        defaults=catalog(ROOT/'data/static/gmts.binpb.gz')
        self.assertEqual([defaults[i]['form'] for i in [1,4,7,10,16,25]],[163,172,181,953,962,598])
        self.assertEqual(defaults[849]['form'],2463)
        self.assertEqual(defaults[978]['form'],2991)

    def test_legacy_form_zero_record_remains_readable_without_rewrite(self):
        path=ROOT/'data/static/gmts.binpb.gz';defaults=catalog(path);forms=form_catalog(path)
        class Catalog:
            pass
        game=Catalog();game.species=defaults;game.forms=forms
        legacy=make_pokemon(p,forms[327,0],327,10,[10]*3)
        original=legacy.SerializeToString()
        self.assertEqual(rules_for(game,legacy),forms[327,0])
        self.assertEqual(legacy.SerializeToString(),original)
        self.assertEqual(defaults[327]['form'],37)


if __name__=='__main__':unittest.main()
