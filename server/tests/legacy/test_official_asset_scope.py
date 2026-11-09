"""Keep official-resource reads and expanded native previews narrowly scoped."""
import importlib.util,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def module(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/file);result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
acquire=module('official_cache','scripts/acquire-official-model-cache.py');preview=module('isolated_preview','scripts/pokemon-model-preview.py')
coverage=module('official_coverage','scripts/report-official-asset-coverage.py')

class OfficialAssetScopeTests(unittest.TestCase):
    def test_only_explicit_resource_cache_files_are_readable(self):
        relative='a'*32+'/'+'b'*32+'/__data'
        self.assertEqual(acquire.resource_name(acquire.DEVICE_ROOT+relative),relative)
        for suffix in ['_monoscripts','_unitybuiltinassets']:
            relative='a'*32+suffix+'/'+'b'*32+'/__data';self.assertEqual(acquire.resource_name(acquire.DEVICE_ROOT+relative),relative)

    def test_account_paths_traversal_and_unrelated_cache_files_are_rejected(self):
        for path in ['/data/user/0/com.nianticlabs.pokemongo/shared_prefs/account.xml',acquire.DEVICE_ROOT+'../DiskCache/data',acquire.DEVICE_ROOT+'a'*32+'/'+'b'*32+'/__info',acquire.DEVICE_ROOT+'a'*32+';cat account/'+'b'*32+'/__data']:
            with self.assertRaises(ValueError):acquire.resource_name(path)

    def test_legacy_preview_keeps_its_original_kanto_scope(self):
        self.assertEqual(preview.permitted_species(dict(profile='m10-8-model-preview',model_preview_species=[462])),set(range(1,152)))

    def test_new_preview_uses_only_explicit_known_species(self):
        self.assertEqual(preview.permitted_species(dict(profile='m10-9-model-preview',model_preview_species=[1,152,462])),{1,152,462})

    def test_new_preview_rejects_missing_empty_or_unknown_species(self):
        for values in [None,[],[0],[1025],[True],['152']]:
            with self.assertRaises(ValueError):preview.permitted_species(dict(profile='m10-9-model-preview',model_preview_species=values))

    def test_reference_or_cache_presence_alone_cannot_certify_a_model_graph(self):
        variant=dict(bundle_indices=[0],model_paths=['pm152.prefab']);row=dict(key='owner',status='LOCATED',sha256='a',assets=[],objects={k:1 for k in ['GameObject','Transform','Mesh','SkinnedMeshRenderer','Material','Texture2D','Animator','AnimationClip']})
        self.assertFalse(coverage.graph_check(variant,{0:row})['structurally_complete'])
        row['assets']=[dict(type='GameObject',path='pm152.prefab')];self.assertTrue(coverage.graph_check(variant,{0:row})['structurally_complete'])
        row['status']='MISSING';self.assertFalse(coverage.graph_check(variant,{0:row})['structurally_complete'])

    def test_external_serialized_file_must_resolve_within_declared_closure(self):
        name='CAB-'+'f'*32;variant=dict(bundle_indices=[0],model_paths=['pm152.prefab']);row=dict(key='owner',status='LOCATED',sha256='a',assets=[dict(type='GameObject',path='pm152.prefab')],external_files=['archive:/'+name+'/'+name],serialized_files=['CAB-'+'a'*32],objects={k:1 for k in ['GameObject','Transform','Mesh','SkinnedMeshRenderer','Material','Texture2D','Animator','AnimationClip']})
        self.assertEqual(coverage.graph_check(variant,{0:row})['unresolved_serialized_file_references'],[name])
        self.assertFalse(coverage.graph_check(variant,{0:row})['structurally_complete'])
        row['serialized_files'].append(name);self.assertTrue(coverage.graph_check(variant,{0:row})['structurally_complete'])

if __name__=='__main__':unittest.main()
