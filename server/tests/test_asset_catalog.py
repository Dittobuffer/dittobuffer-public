import base64
import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from dittobuffer.core.asset_catalog import AssetStore, Catalog, validate_manifest
ROOT=Path(__file__).resolve().parents[1]


def compact_catalog():
    keys=['bundle-A', 'pm9999']
    key_data=struct.pack('<i',len(keys));offsets=[]
    for key in keys:
        offsets.append(len(key_data));raw=key.encode()
        key_data+=b'\0'+struct.pack('<i',len(raw))+raw
    buckets=struct.pack('<i',2)+b''.join(struct.pack('<iii',offset,1,i) for i,offset in enumerate(offsets))
    options=json.dumps({'m_Hash':'1'*32,'m_BundleName':'bundle-A','m_BundleSize':7}).encode('utf-16-le')
    extra=b'\7'+bytes([1])+b'A'+bytes([1])+b'C'+struct.pack('<i',len(options))+options
    entries=struct.pack('<i',2)+struct.pack('<7i',0,0,-1,0,0,0,0)+struct.pack('<7i',1,1,0,0,-1,1,1)
    return {'m_InternalIds':['source.bundle','Packages/pm9999.prefab'],
        'm_ProviderIds':['AssetBundleProvider','BundledAssetProvider'],
        'm_resourceTypes':[{'m_ClassName':'UnityEngine.ResourceManagement.ResourceProviders.IAssetBundleResource'},
                           {'m_ClassName':'UnityEngine.GameObject'}],
        **{'m_'+name+'DataString':base64.b64encode(data).decode() for name,data in
           [('Key',key_data),('Bucket',buckets),('Entry',entries),('Extra',extra)]}}


class AssetCatalogTests(unittest.TestCase):
    def complete_manifest(self):
        doc=compact_catalog();sha='a'*64
        doc['m_InternalIds'][0]='http://127.0.0.1:8080/assets/pokemon/bundles/'+sha+'.bundle'
        manifest={'content':{sha:{'bytes':7,'file':'fixture.bundle'}},
            'bundles':[{'index':0,'status':'LOCATED','sha256':sha,'version':'1'*32,'bundle_name':'bundle-A','serialized_files':['CAB-fixture']}],
            'variants':[{'address':'pm9999','bundle_indices':[0],'missing_dependencies':[]}]}
        return manifest,doc

    def test_manifest_rejects_wrong_cache_versions_or_nonlocal_delivery(self):
        manifest,doc=self.complete_manifest()
        self.assertEqual(validate_manifest(manifest,Catalog(doc))['prefabs'],1)
        manifest['bundles'][0]['version']='2'*32
        with self.assertRaisesRegex(ValueError,'cache version'):validate_manifest(manifest,Catalog(doc))
        manifest,doc=self.complete_manifest();doc['m_InternalIds'][0]='https://invalid.example/bundle'
        with self.assertRaisesRegex(ValueError,'Nonlocal'):validate_manifest(manifest,Catalog(doc))

    def test_manifest_rejects_incomplete_or_wrong_dependency_claims(self):
        manifest,doc=self.complete_manifest();manifest['variants'][0]['bundle_indices']=[]
        with self.assertRaisesRegex(ValueError,'dependency graph'):validate_manifest(manifest,Catalog(doc))
        manifest,doc=self.complete_manifest();manifest['bundles'][0]['status']='MISSING'
        with self.assertRaisesRegex(ValueError,'unavailable'):validate_manifest(manifest,Catalog(doc))
        manifest['variants'][0]['missing_dependencies']=['bundle-A']
        validate_manifest(manifest,Catalog(doc))

    def test_manifest_rejects_duplicate_unity_file_identities(self):
        manifest,doc=self.complete_manifest();catalog=Catalog(doc)
        merged,_,offsets=Catalog.combine([catalog,catalog],lambda source,index:True)
        # Two different readable bundles cannot share one internal CAB file.
        raw=copy.deepcopy(merged.document);raw['m_InternalIds'][2]=raw['m_InternalIds'][2].replace('a'*64,'b'*64)
        manifest['content']['b'*64]={'bytes':7,'file':'another.bundle'}
        row=dict(manifest['bundles'][0],index=offsets[1],sha256='b'*64,bundle_name='bundle-B')
        manifest['bundles'].append(row)
        options=Catalog(raw).options(offsets[1]);options['value']['m_BundleName']='bundle-B'
        merged=Catalog(raw).rewrite_bundle_options({offsets[1]:options})
        with self.assertRaisesRegex(ValueError,'Unity file identity'):validate_manifest(manifest,merged)

    def test_merged_sources_require_declared_unity_identities(self):
        manifest,doc=self.complete_manifest();manifest['source_imports']=['old.json','new.json']
        validate_manifest(manifest,Catalog(doc))
        manifest['bundles'][0].pop('serialized_files')
        with self.assertRaisesRegex(ValueError,'verified Unity file identities'):validate_manifest(manifest,Catalog(doc))

    def test_compact_keys_dependencies_and_exact_version(self):
        catalog=Catalog(compact_catalog())
        self.assertEqual(catalog.closure('pm9999'),[0,1])
        self.assertEqual(catalog.location(1)['dependencies'],[0])
        self.assertEqual(catalog.options(0)['value']['m_Hash'],'1'*32)
        self.assertEqual(catalog.closure('absent'),[])

    def test_rejects_truncation_and_dangling_entries(self):
        for name in ('Bucket','Key','Entry','Extra'):
            doc=compact_catalog();field='m_'+name+'DataString'
            doc[field]=base64.b64encode(base64.b64decode(doc[field])[:2]).decode()
            with self.assertRaises((ValueError,IndexError)):
                c=Catalog(doc);c.options(0)
        doc=compact_catalog();raw=bytearray(base64.b64decode(doc['m_EntryDataString']))
        struct.pack_into('<i',raw,4+28+8,9999)
        doc['m_EntryDataString']=base64.b64encode(raw).decode()
        with self.assertRaises(ValueError):Catalog(doc)

    def test_dependency_cycle_rejected(self):
        doc=compact_catalog();raw=bytearray(base64.b64decode(doc['m_EntryDataString']))
        struct.pack_into('<i',raw,4+8,1)
        doc['m_EntryDataString']=base64.b64encode(raw).decode()
        with self.assertRaisesRegex(ValueError,'Cyclic'):Catalog(doc).closure('pm9999')

    def test_combine_prefers_complete_source_and_preserves_exact_dependency(self):
        a=compact_catalog();b=copy.deepcopy(a);b['m_InternalIds'][0]='different-version.bundle'
        merged,chosen,offsets=Catalog.combine([Catalog(a),Catalog(b)],lambda source,index:source==1 or index!=0)
        self.assertEqual(chosen['pm9999'],1)
        root=merged.buckets['pm9999'][0];children=merged.location(root)['dependencies']
        self.assertEqual(children,[offsets[1]])
        self.assertEqual(merged.location(children[0])['internal_id'],'different-version.bundle')
        merged,chosen,_=Catalog.combine([Catalog(a),Catalog(b)],lambda source,index:True)
        self.assertEqual(chosen['pm9999'],0)
        self.assertEqual(merged.location(merged.location(merged.buckets['pm9999'][0])['dependencies'][0])['internal_id'],'source.bundle')

    def test_rewritten_bundle_version_retains_keys_and_dependency_graph(self):
        original=Catalog(compact_catalog());replacement=original.options(0)
        replacement['value']['m_Hash']='2'*32
        rewritten=original.rewrite_bundle_options({0:replacement})
        self.assertEqual(rewritten.options(0)['value']['m_Hash'],'2'*32)
        self.assertEqual(original.options(0)['value']['m_Hash'],'1'*32)
        self.assertEqual(rewritten.keys,original.keys)
        self.assertEqual(rewritten.location(1),original.location(1))

    def store(self,root):
        data=b'fixture';sha=hashlib.sha256(data).hexdigest()
        (root/'bundle').write_bytes(data);catalog=b'{}';(root/'catalog.json').write_bytes(catalog)
        manifest={'format':1,'local_catalog':'catalog.json','local_catalog_sha256':hashlib.sha256(catalog).hexdigest(),
            'content':{sha:{'file':'bundle','bytes':len(data)}}}
        (root/'manifest.json').write_text(json.dumps(manifest))
        return AssetStore(root,{'enabled':True,'manifest':'manifest.json'}),sha

    def test_allowlist_integrity_unknown_paths_and_disabled(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'builds') as directory:
            root=Path(directory);store,sha=self.store(root)
            path='/assets/pokemon/bundles/'+sha+'.bundle'
            self.assertEqual(store.read(path)[0],b'fixture')
            for bad in ('/assets/pokemon/bundles/../../bundle',path+'?x=1',path.replace(sha,'0'*64)):
                self.assertIsNone(store.read(bad))
            self.assertIsNone(AssetStore(root,{}).read(path))
            (root/'bundle').write_bytes(b'tamper!')
            with self.assertRaisesRegex(ValueError,'integrity'):store.read(path)

    def test_catalog_hash_and_project_boundary(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'builds') as directory:
            root=Path(directory);store,sha=self.store(root)
            self.assertEqual(store.read('/assets/pokemon/catalog.hash')[0],hashlib.md5(b'{}').hexdigest().encode())
            with self.assertRaises(ValueError):store.project_path('../outside')
            (root/'catalog.json').write_bytes(b'[]')
            with self.assertRaisesRegex(ValueError,'integrity'):store.read('/assets/pokemon/catalog.json')
