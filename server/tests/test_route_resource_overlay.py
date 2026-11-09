"""Selected private resource overlays must preserve previously merged graphs."""
import copy,importlib.util,unittest
from pathlib import Path
from dittobuffer.core.asset_catalog import Catalog
from scripts.pokemon_asset_import import catalog_document
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('m20_compatible_resources',ROOT/'scripts/m20-compatible-resources.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def fixture(address,url):
    template=dict(m_InternalIdPrefixes=[],m_ProviderIds=['AssetBundleProvider','BundledAssetProvider'],m_resourceTypes=[{'m_ClassName':'UnityEngine.ResourceManagement.ResourceProviders.IAssetBundleResource'},{'m_ClassName':'UnityEngine.GameObject'}])
    nodes=[dict(sha256='a'*64,url=url,options=dict(m_Hash='1'*32,m_BundleName='fixture',m_BundleSize=1))]
    resources=[dict(address=address,path='Assets/'+address+'.prefab',type='UnityEngine.GameObject',owner=0,bundles=[0])]
    return Catalog(catalog_document(template,nodes,resources,dict(assembly='A',**{'class':'C'})))

class RouteResourceOverlayTests(unittest.TestCase):
    def test_exact_dependency_reuse_keeps_one_native_bundle_operation_identity(self):
        baseline=fixture('pm9999','original-operation.bundle');replacement=fixture('combat_explorer','different-url-same-reviewed-bytes.bundle')
        merged=module.overlay(replacement,baseline,{0:0})
        bundle_entries=[i for i in range(len(merged.entries)) if merged.location(i)['type'].endswith('.IAssetBundleResource')]
        self.assertEqual(len(bundle_entries),1)
        native=merged.buckets['combat_explorer'][0];owner=merged.location(native)['dependencies'][0]
        self.assertEqual(owner,bundle_entries[0])
        self.assertEqual(merged.location(owner)['internal_id'],'original-operation.bundle')
        for key in baseline.keys:self.assertEqual(module.graph(merged,key),module.graph(baseline,key))

    def test_selected_resource_keeps_exact_guid_alias_without_broad_labels(self):
        catalog=fixture('combat_explorer','mateo.bundle')
        # The actual source aliases have the same bucket entries. A broad label
        # also containing a bundle must never become an override by accident.
        catalog.document['m_InternalIds'][1]='Assets/AddressableAssets/NPC/AvatarNPC/Mateo/combat_explorer.prefab'
        catalog=Catalog(catalog.document);catalog.buckets['a'*32]=catalog.buckets['combat_explorer'];catalog.buckets['b'*32]=[0,1]
        values=module.selected_resources(catalog,True)
        self.assertEqual({v['key'] for v in values},{'combat_explorer','a'*32})
        for value in values:self.assertEqual(value['internal_id'],'Assets/AddressableAssets/NPC/AvatarNPC/Mateo/combat_explorer.prefab')

    def test_overlay_preserves_nested_catalog_and_preexisting_private_keys(self):
        original=fixture('pm9999','original.bundle')
        baseline,_,_=Catalog.combine([original,fixture('existing_npc','older.bundle')],lambda source,index:True)
        replacement=fixture('archipelago','reviewed.bundle')
        merged=module.overlay(replacement,baseline)
        for key in baseline.keys:self.assertEqual(module.graph(merged,key),module.graph(baseline,key),key)
        root=merged.buckets['archipelago'][0];owner=merged.location(root)['dependencies'][0]
        self.assertEqual(merged.location(owner)['internal_id'],'reviewed.bundle')
        self.assertNotEqual(merged.location(owner)['internal_id'],'original.bundle')
        self.assertEqual(module.graph(original,'pm9999')[0]['dependencies'][0]['internal_id'],'original.bundle')

    def test_overlay_changes_only_explicit_public_address(self):
        baseline=fixture('archipelago','old.bundle');replacement=fixture('archipelago','new.bundle')
        merged=module.overlay(replacement,baseline)
        self.assertEqual(module.graph(merged,'archipelago')[0]['dependencies'][0]['internal_id'],'new.bundle')
        for key in baseline.keys:
            if key!='archipelago':self.assertEqual(module.graph(merged,key),module.graph(baseline,key))

    def test_script_contract_rejects_wrong_namespace_and_missing_fields(self):
        dump='// Namespace: Niantic.Holoholo.Avatar\npublic class AvatarModel : MonoBehaviour\n{\nprivate int avatarModelName; // 0x20\n// Methods\n}\n'
        value=module.script_contract(dump,'AvatarModel','Niantic.Holoholo.Avatar',{'m_Name':'','avatarModelName':15})
        self.assertEqual(value['serialized_fields'],['avatarModelName'])
        self.assertEqual(value['native_abi_compatibility'],'NOT_TESTED')
        with self.assertRaisesRegex(ValueError,'namespace'):module.script_contract(dump,'AvatarModel','Different',{})
        with self.assertRaisesRegex(ValueError,'fields'):module.script_contract(dump,'AvatarModel','Niantic.Holoholo.Avatar',{'unknownField':1})

if __name__=='__main__':unittest.main()
