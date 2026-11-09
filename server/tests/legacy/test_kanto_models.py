"""Keep model availability and actual Android acceptance separate."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('kanto_model_status',ROOT/'scripts/kanto-model-status.py')
STATUS=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STATUS)


def native_record(address='pm25'):
    sample=dict(viewId=25,assetName=address+'(Clone)',ready=True,shiny=address.endswith('.s'),costume='',gender=2 if '.g2' in address else 1,
        normalizedTime=.2,animator=dict(initialized=True,enabled=True,clips=[dict(name='wait',length=2)]),
        renderers=[dict(type='SkinnedMeshRenderer',enabled=True,visible=True,vertices=100,bones=20,nullBones=0,
            materials=[dict(shader='Pokemon',textures=[dict(name='body',width=256,height=256)])])])
    second=copy.deepcopy(sample);second['normalizedTime']=.7
    return dict(samples=[sample,second])


def manifest(address='pm25',located=True):
    path='Packages/Pokemon/pm0025_00/pm25.prefab'
    return dict(content={'a':dict(file='fixture.bundle')},bundles=[dict(index=0,key='exact-bundle',status='LOCATED' if located else 'MISSING',
        version='1'*32,expected_bytes=123,sha256='a',assets=[dict(type='GameObject',path=path)])],
        variants=[dict(address=address,species=25,bundle_indices=[0],model_paths=[path])])


class KantoModelStatusTests(unittest.TestCase):
    def test_wrong_identity_and_stale_variant_flags_fail(self):
        for address in ('pm25','pm25.s','pm25.g2','pm25.g2.s'):
            good=native_record(address)
            self.assertTrue(all(STATUS.native_checks(address,good).values()))
            for key,value in [('viewId',26),('assetName','pm26(Clone)'),('shiny',not address.endswith('.s')),('costume','OLD_HAT')]:
                bad=copy.deepcopy(good);bad['samples'][0][key]=value
                self.assertFalse(all(STATUS.native_checks(address,bad).values()),key)
        bad=native_record('pm25.g2');bad['samples'][1]['gender']=1
        self.assertFalse(STATUS.native_checks('pm25.g2',bad)['variant_properties'])

    def test_bad_rig_shader_texture_and_frozen_animation_fail(self):
        good=native_record()
        for key,value in [('vertices',0),('bones',0),('nullBones',1)]:
            bad=copy.deepcopy(good);bad['samples'][0]['renderers'][0][key]=value
            self.assertFalse(STATUS.native_checks('pm25',bad)['rig'],key)
        bad=copy.deepcopy(good);bad['samples'][0]['renderers'][0]['materials'][0]['shader']='Hidden/InternalErrorShader'
        self.assertFalse(STATUS.native_checks('pm25',bad)['materials'])
        bad=copy.deepcopy(good);bad['samples'][0]['renderers'][0]['materials'][0]['textures'][0]['width']=0
        self.assertFalse(STATUS.native_checks('pm25',bad)['materials'])
        bad=copy.deepcopy(good);bad['samples'][1]['normalizedTime']=.2
        self.assertFalse(STATUS.native_checks('pm25',bad)['animation'])

    def test_catalog_reference_and_human_tick_cannot_certify_android(self):
        result=STATUS.build(manifest(),review={'pm25':dict(rendered=True)})
        self.assertTrue(result['variants']['pm25']['dependencies_complete'])
        self.assertFalse(result['variants']['pm25']['rendered_and_animated'])
        result=STATUS.build(manifest(located=False),{'pm25':native_record()},{'pm25':dict(rendered=True)})
        self.assertFalse(result['variants']['pm25']['rendered_and_animated'])
        self.assertEqual(result['species'][24]['missing_base_dependencies'][0]['key'],'exact-bundle')

    def test_native_success_still_requires_visual_review(self):
        result=STATUS.build(manifest(),{'pm25':native_record()})
        self.assertEqual(result['variants']['pm25']['status'],'PREFAB SUCCESSFULLY LOADED')
        self.assertEqual(result['counts']['base_android_verified'],0)
        result=STATUS.build(manifest(),{'pm25':native_record()},{'pm25':dict(rendered=True)})
        self.assertEqual(result['counts']['base_android_verified'],1)

    def test_gender_sibling_cannot_fill_missing_base(self):
        result=STATUS.build(manifest('pm25.g2'),{'pm25.g2':native_record('pm25.g2')},{'pm25.g2':dict(rendered=True)})
        self.assertEqual(result['counts']['gender']['rendered'],1)
        self.assertEqual(result['counts']['base_available'],0)
        self.assertFalse(result['species'][24]['base_rendered'])

    def test_scope_excludes_max_and_later_species(self):
        for key in ('pm0','pm152','pm25.fBREAD','pm6.fGIGANTAMAX','pm25.fDYNAMAX'):
            self.assertIsNone(STATUS.group(key))
        self.assertEqual(STATUS.group('pm9.fMEGA.s'),'mega')

    def test_review_cannot_certify_a_different_prefab_version(self):
        approval=dict(rendered=True,accepted_prefab_sha256='different')
        result=STATUS.build(manifest(),{'pm25':native_record()},{'pm25':approval})
        self.assertFalse(result['variants']['pm25']['rendered_and_animated'])
        approval['accepted_prefab_sha256']='a'
        result=STATUS.build(manifest(),{'pm25':native_record()},{'pm25':approval})
        self.assertTrue(result['variants']['pm25']['rendered_and_animated'])

    def test_native_evidence_must_match_the_loaded_prefab_bytes(self):
        proof=native_record();proof['prefab_sha256']='old-candidate'
        approval={'pm25':dict(rendered=True,accepted_prefab_sha256='a')}
        result=STATUS.build(manifest(),{'pm25':proof},approval)
        self.assertFalse(result['variants']['pm25']['native_prefab_matches'])
        self.assertFalse(result['variants']['pm25']['rendered_and_animated'])
        proof['prefab_sha256']='a'
        self.assertTrue(STATUS.build(manifest(),{'pm25':proof},approval)['variants']['pm25']['rendered_and_animated'])

    def test_loading_exception_blocks_acceptance_but_known_transport_warning_does_not(self):
        proof=native_record();proof['errors']=[dict(message='Error getting web [credential omitted]')]
        self.assertTrue(all(STATUS.native_checks('pm25',proof).values()))
        proof['errors'].append(dict(message='Failed to load AssetBundle: mismatched serialized file'))
        self.assertFalse(STATUS.native_checks('pm25',proof)['loading_errors'])


if __name__=='__main__':unittest.main()
