"""Model import invariants; Android visual acceptance remains a separate gate."""
import importlib.util,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from pokemon_asset_import import catalog_document
from dittobuffer.core.asset_catalog import Catalog
try:
    import numpy as np
    spec=importlib.util.spec_from_file_location('fbx_conversion',ROOT/'scripts/convert-pokemon-fbx.py');CONVERT=importlib.util.module_from_spec(spec);spec.loader.exec_module(CONVERT)
except ImportError:CONVERT=None

class ResearchCatalogTests(unittest.TestCase):
    def test_owning_bundle_precedes_global_dependencies(self):
        types=[dict(m_AssemblyName='x',m_ClassName=n) for n in ['UnityEngine.ResourceManagement.ResourceProviders.IAssetBundleResource','UnityEngine.GameObject']]
        template=dict(m_ProviderIds=['x.AssetBundleProvider','x.BundledAssetProvider'],m_resourceTypes=types)
        nodes=[dict(sha256=str(i)*64,url='http://127.0.0.1:8080/assets/pokemon/'+str(i),options={}) for i in range(3)]
        resources=[dict(address='pm14',path='Assets/pm14.prefab',type='UnityEngine.GameObject',owner=2,bundles=[0,2,1,0])]
        catalog=Catalog(catalog_document(template,nodes,resources,dict(assembly='x',**{'class':'Options'})))
        index=catalog.buckets['pm14'][0]
        dependencies=catalog.buckets[catalog.keys[catalog.entries[index][2]]]
        self.assertEqual(dependencies,(2,0,1))
        with self.assertRaises(ValueError):catalog_document(template,nodes,[dict(resources[0],bundles=[0,1])],dict(assembly='x',**{'class':'Options'}))

@unittest.skipIf(CONVERT is None,'Optional model conversion dependencies are not installed')
class SourceTransformTests(unittest.TestCase):
    def test_matrix_decomposition_preserves_rotation_scale_translation(self):
        matrix=[0,-3,0,4,2,0,0,5,0,0,4,6,0,0,0,1]
        t,q,s=CONVERT.decompose(matrix)
        self.assertEqual(list(t.values()),[4,5,6]);self.assertEqual(list(s.values()),[2,3,4])
        self.assertAlmostEqual(q['z'],math.sqrt(.5));self.assertAlmostEqual(q['w'],math.sqrt(.5))
        with self.assertRaises(ValueError):CONVERT.decompose([1,.5,0,0,0,1,0,0,0,0,1,0,0,0,0,1])

    def test_animation_quaternion_order_and_shortest_arc(self):
        q=CONVERT.sample([[0,[1,0,0,0]],[1,[0,0,0,1]]],.5,True)
        np.testing.assert_allclose(q,[0,0,math.sqrt(.5),math.sqrt(.5)],atol=1e-6)
        q=CONVERT.sample([[0,[1,0,0,0]],[1,[-1,0,0,0]]],.5,True)
        np.testing.assert_allclose(q,[0,0,0,1],atol=1e-6)
        self.assertEqual(CONVERT.sample([[0,[1,2,3]],[1,[3,4,5]]],.5),[2,3,4])

class ConvertedBundleTests(unittest.TestCase):
    def test_private_shader_has_unique_identity_and_unchanged_gpu_program(self):
        path=ROOT/'builds/m107-venomoth-isolated-shader.json'
        if not path.exists():self.skipTest('Pinned private shader fixture is not present')
        try:import UnityPy
        except ImportError:self.skipTest('Optional Unity importer is not installed')
        m=json.loads(path.read_text());row=next(b for b in m['bundles'] if b.get('source_sha256'));env=UnityPy.load(str(ROOT/m['content'][row['sha256']]['file']))
        copied=next(o.read_typetree() for o in env.objects if o.type.name=='Shader');change=m['import_metadata']['shader_changes'][0]
        source=next(o.read_typetree() for o in UnityPy.load(str(ROOT/m['content'][change['source_bundle_sha256']]['file'])).objects if str(o.path_id)==change['source_object'])
        self.assertNotEqual(copied['m_AssetGUID'],source['m_AssetGUID']);self.assertEqual(copied['compressedBlob'],source['compressedBlob'])
        for sub in copied['m_ParsedForm']['m_SubShaders']:
            for p in sub['m_Passes']:self.assertEqual(p['m_State']['zWrite']['val'],0);self.assertEqual(p['m_State']['zTest']['val'],4);self.assertEqual(p['m_State']['culling']['val'],2)

    def test_native_metadata_root_and_no_template_species_visual_payload(self):
        path=ROOT/'builds/m107-converted-kakuna-v3.json'
        if not path.exists():self.skipTest('Pinned local research fixture is not present')
        try:import UnityPy
        except ImportError:self.skipTest('Optional Unity importer is not installed')
        manifest=json.loads(path.read_text());row=next(b for b in manifest['bundles'] if b.get('source_sha256'));env=UnityPy.load(str(ROOT/manifest['content'][row['sha256']]['file']))
        bundles=[o for o in env.objects if o.type.name=='AssetBundle'];self.assertEqual([o.path_id for o in bundles],[1])
        models=[o.read_typetree()['ViewID'] for o in env.objects if o.type.name=='MonoBehaviour' and 'ViewID' in o.read_typetree()];self.assertEqual(models,[14])
        names=[o.read_typetree()['m_Name'] for o in env.objects if o.type.name in ('Mesh','Texture2D','AnimationClip')]
        self.assertFalse(any('127' in n for n in names),names)
        self.assertEqual({o.read_typetree()['m_Name'] for o in env.objects if o.type.name=='AnimationClip'},{'waitA01','tokusyu01','damageS01','land01'})

    def test_converted_pixels_match_pinned_source_images(self):
        path=ROOT/'builds/m107-converted-kakuna-v3.json'
        if not path.exists():self.skipTest('Pinned local research fixture is not present')
        try:import UnityPy
        except ImportError:self.skipTest('Optional Unity importer is not installed')
        from PIL import Image
        m=json.loads(path.read_text());row=next(b for b in m['bundles'] if b.get('source_sha256'));env=UnityPy.load(str(ROOT/m['content'][row['sha256']]['file']))
        for obj in env.objects:
            if obj.type.name!='Texture2D':continue
            tex=obj.read();source=ROOT/'builds/m107-sources/kakuna-fbx'/(tex.m_Name+'.png')
            self.assertTrue(source.exists(),tex.m_Name)
            self.assertEqual(tex.image.convert('RGBA').tobytes(),Image.open(source).convert('RGBA').tobytes())

if __name__=='__main__':unittest.main()
