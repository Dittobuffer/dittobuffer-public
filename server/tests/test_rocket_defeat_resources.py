"""Synthetic locator regressions; these are not Android asset/render tests."""
import copy,unittest
from dittobuffer.core.asset_catalog import Catalog
from dittobuffer.gameplay.rocket_resources import COACH_KEYS,COACH_PREFABS,inspect,preservation
from scripts.pokemon_asset_import import catalog_document
from tests.test_asset_catalog import compact_catalog

def fixture(extra=False):
    bundle=dict(sha256='a'*64,url='http://127.0.0.1:8080/assets/client/coach.bundle',
                options=dict(m_Hash='1'*32,m_BundleName='coach',m_BundleSize=7))
    resources=[dict(address=k,path=COACH_PREFABS[k],type='UnityEngine.GameObject',owner=0,bundles=[0]) for k in COACH_KEYS]
    if extra:resources.append(dict(address='BreadBall',path='Assets/BreadBall.prefab',type='UnityEngine.GameObject',owner=0,bundles=[0]))
    doc=catalog_document(compact_catalog(),[bundle],resources,dict(assembly='A',**{'class':'C'}))
    return Catalog(doc)

class Served:
    def __init__(self,missing=False):self.missing=missing
    def read(self,name):return None if self.missing else (b'fixture','application/octet-stream')

class RocketDefeatResourceTests(unittest.TestCase):
    def test_three_exact_coaches_and_complete_bundle_closures(self):
        result=inspect(fixture(),Served());self.assertTrue(result['valid']);self.assertEqual(len(result['coaches']),3)
    def test_registered_key_does_not_hide_unserved_dependency(self):
        result=inspect(fixture(),Served(True));self.assertFalse(result['valid'])
        self.assertTrue(all(v['missing_bundles']==['coach.bundle'] for v in result['coaches']))
    def test_wrong_team_leader_prefab_is_rejected(self):
        cat=fixture();idx=cat.buckets['combat_blanche'][0]
        cat.document['m_InternalIds'][cat.entries[idx][0]]=COACH_PREFABS['combat_candela']
        result=inspect(cat,Served());self.assertFalse(result['valid']);self.assertIn('identity',result['coaches'][0]['error'])
    def test_missing_or_ambiguous_address_is_rejected(self):
        for mode in ('missing','ambiguous'):
            cat=fixture()
            if mode=='missing':del cat.buckets['combat_spark']
            else:cat.buckets['combat_spark']*=2
            with self.subTest(mode=mode):self.assertFalse(inspect(cat,Served())['valid'])
    def test_native_backslash_bundle_address_is_resolved(self):
        cat=fixture();cat.document['m_InternalIds'][0]='{BundleAddress}\\bundles\\Android\\coach.bundle'
        self.assertTrue(inspect(cat,Served())['valid'])
    def test_unrelated_max_prefab_change_is_rejected(self):
        before=fixture(True);after=fixture(True);idx=after.buckets['BreadBall'][0]
        after.document['m_InternalIds'][after.entries[idx][0]]='Assets/WrongBall.prefab'
        with self.assertRaisesRegex(ValueError,'BreadBall'):preservation(before,after)
    def test_shared_dependency_mutation_is_rejected(self):
        before=fixture(True);after=fixture(True);after.document['m_InternalIds'][0]='wrong.bundle'
        with self.assertRaises(ValueError):preservation(before,after)
    def test_unrelated_public_graphs_survive_bucket_relabeling(self):
        before=fixture(True);after=fixture(True)
        before.buckets['libre-dependencies:1:old']=[0];after.buckets['libre-dependencies:1:new']=[0]
        result=preservation(before,after);self.assertGreater(result['all_other_dependency_graphs_preserved'],0)

if __name__=='__main__':unittest.main()
