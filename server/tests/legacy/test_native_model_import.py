"""Reject unsafe model selections, conflicting identities and broad previews."""
import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


importer = module('native_model_import', 'scripts/import-native-models.py')
preview = module('m11_preview', 'scripts/pokemon-model-preview.py')
planner = module('model_plan', 'scripts/plan-native-model-integration.py')
native_checks = module('m11_native_checks', 'scripts/native_model_checks.py')
publisher = module('m11_publisher', 'scripts/publish-native-models.py')


class NativeModelImportTests(unittest.TestCase):
    def test_original_prefab_spelling_must_match_exact_source_path_and_identity(self):
        record=dict(expected_prefab_name='pm800.fDUSKMANE.s',source_prefab_path='Packages/Pokemon/pm800.fDUSK_MANE/pm800.fDUSKMANE.s.prefab',
            samples=[dict(viewId=800,assetName='pm800.fDUSKMANE.s(Clone)',ready=True)]*2)
        self.assertTrue(native_checks.native_checks('pm800.fDUSK_MANE.s',record)['exact_prefab'])
        for field,value in [('source_prefab_path','Packages/other.prefab'),('expected_prefab_name','pm799.fDUSKMANE.s'),('expected_prefab_name','pm800.fDUSKMANE')]:
            invalid=copy.deepcopy(record);invalid[field]=value
            self.assertFalse(native_checks.native_checks('pm800.fDUSK_MANE.s',invalid)['exact_source_prefab_name'])

    def test_publication_requires_reviewed_cold_load_and_a_different_warm_process(self):
        row=dict(android_visually_verified=True,restart_verified=True,android_verified_in_m11=True,prefab_sha256='exact',
            review=dict(rendered=True,restart_rendered=True,accepted_prefab_sha256='exact',reviewed_evidence=['cold.json']),
            proofs=[dict(complete_run=True,prefab_sha256='exact',checks=dict(rig=True,materials=True),pid=100,role='warm',source='warm.json'),dict(complete_run=True,prefab_sha256='exact',checks=dict(rig=True,materials=True),pid=200,role='cold',source='cold.json')])
        self.assertTrue(publisher.accepted(row))
        for field,value in [('restart_rendered',False),('accepted_prefab_sha256','other'),('reviewed_evidence',[])]:
            invalid=copy.deepcopy(row);invalid['review'][field]=value;self.assertFalse(publisher.accepted(invalid))
        for field,value in [('pid',100),('complete_run',False),('prefab_sha256','other'),('checks',dict(materials=False))]:
            invalid=copy.deepcopy(row);invalid['proofs'][1][field]=value;self.assertFalse(publisher.accepted(invalid))

    def test_explicit_new_kanto_scope_cannot_replace_an_existing_complete_key(self):
        importer.validate_addresses(['pm25.cEXAMPLE'],{25},{'pm25','pm25.s'})
        with self.assertRaises(ValueError):importer.validate_addresses(['pm25'],{25},{'pm25','pm25.s'})

    def test_alias_cannot_bypass_species_or_shiny_identity(self):
        self.assertTrue(native_checks.native_checks('pm487',{},'pm487.fALTERED')['exact_catalog_alias'])
        for alias,original in [('pm487','pm150'),('pm487.s','pm487.fALTERED'),('pm487','pm487.fLOAF'),('pm487.fA','pm487.fB')]:
            self.assertFalse(native_checks.native_checks(alias,{},original)['exact_catalog_alias'])

    def test_additive_import_preserves_kanto_and_rejects_unknown_ids(self):
        for key in ['pm1', 'pm151.s', 'pm9999', 'pm249/../../account', 'pm249;cat file']:
            with self.assertRaises(ValueError):
                importer.validate_addresses([key], {1, 151, 249})
        importer.validate_addresses(['pm249', 'pm249.s'], {249})

    def test_max_and_invalid_or_duplicate_selections_are_rejected(self):
        for keys in [[], None, ['pm249', 'pm249'], [True], [{}], ['pm249.fBREAD'], ['pm249.fGMAX'], ['pm249.fLOAF.s'], ['pm249.dMax'], ['pm249.fETERNAMAX']]:
            with self.assertRaises(ValueError):
                importer.validate_addresses(keys, {249})

    def test_same_internal_identity_cannot_name_different_bytes(self):
        row = dict(status='LOCATED', sha256='one', serialized_files=['CAB-A'])
        importer.identity_conflicts([row, dict(row)])
        with self.assertRaises(ValueError):
            importer.identity_conflicts([row, dict(row, sha256='two', serialized_files=['cab-a'])])

    def test_m11_preview_requires_explicit_known_species(self):
        self.assertEqual(preview.permitted_species(dict(profile='m10-11-model-preview', model_preview_species=[1, 249, 778])), {1, 249, 778})
        for requested in [None, [], [True], [9999]]:
            with self.assertRaises(ValueError):
                preview.permitted_species(dict(profile='m10-11-model-preview', model_preview_species=requested))

    def test_form_companion_is_preserved_instead_of_fabricating_a_root_cry(self):
        keys={'pm849.fAMPED.cry','pm849.fLOW_KEY.cry','pm778.cry'}
        self.assertEqual(importer.cry_address('pm849.fAMPED.s',keys),'pm849.fAMPED.cry')
        self.assertEqual(importer.cry_address('pm778.fDISGUISED.s',keys),'pm778.cry')
        with self.assertRaises(ValueError):importer.cry_address('pm849.fUNKNOWN',keys)

    def test_explicit_female_form_keeps_its_actual_display_gender(self):
        reference={'genders':{'876':{'malePercent':50,'femalePercent':50}}}
        self.assertEqual(preview.preview_gender('pm876.fFEMALE.s',reference),2)
        self.assertEqual(preview.preview_gender('pm876.fMALE',reference),1)

    def test_a_shared_template_does_not_make_the_first_equivalent_form_a_default(self):
        base=dict(form=0,stamina=100,attack=80,defense=70,height=.3,weight=2,types=[1,0],is_costume=False)
        forms={1:dict(base),2:dict(base)}
        result=planner.choose_default(base,forms,{0:'pm999',1:'pm999.fA',2:'pm999.fB'},{'pm999.fA','pm999.fB'})
        self.assertFalse(result['resolved']);self.assertEqual(len(result['matching_forms']),2)

    def test_unique_form_mapping_uses_template_identity_rather_than_list_order(self):
        base=dict(form=0,stamina=100,attack=80,defense=70,height=.3,weight=2,types=[1,0],is_costume=False)
        forms={1:dict(base,weight=3),2:dict(base)}
        result=planner.choose_default(base,forms,{0:'pm999',1:'pm999.fA',2:'pm999.fB'},{'pm999.fA','pm999.fB'})
        self.assertTrue(result['resolved']);self.assertEqual(result['address'],'pm999.fB');self.assertEqual(result['form'],2)


if __name__ == '__main__':
    unittest.main()
