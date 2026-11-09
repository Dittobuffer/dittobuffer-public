"""Opt-in recovery of exact native FTUE avatar resources into a fresh private folder.

The accepted manifest, catalog and linked assets are read-only inputs. Supply
independently obtained bundles with --cache; --download explicitly permits the
public location encoded by the supplied 0.429.2 client. Never publishes payloads.
"""
import argparse,copy,hashlib,json,re,sys,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from dittobuffer.core.asset_catalog import Catalog,load_manifest,validate_manifest
BASE='https://asset-cdn-rel.nianticstatic.com/Avatars/catalog_avatars_88/bundles/Android/'

def restore(base,cache,output,download=False):
    import UnityPy
    manifest=copy.deepcopy(load_manifest(ROOT,base));catalog=Catalog(json.loads((ROOT/manifest['local_catalog']).read_text()))
    output=output.resolve();cache=cache.resolve()
    if not output.is_relative_to(ROOT/'.private') or output.exists():raise ValueError('Fresh ignored PRIVATE output required; shared asset tree is read-only')
    bundles={v['index']:v for v in manifest['bundles']};output.mkdir(parents=True)
    evidence=[];required=set()
    def acquire(index):
        row=bundles[index]
        if row['status']=='LOCATED':return ROOT/manifest['content'][row['sha256']]['file']
        key=row['key']
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+\.bundle',key) or not 0<row['expected_bytes']<=20000000:raise ValueError('Invalid exact bundle identity')
        source=cache/key
        if source.is_file():raw=source.read_bytes();origin='independently supplied local cache'
        elif download:
            with urllib.request.urlopen(BASE+key,timeout=30) as response:raw=response.read(row['expected_bytes']+1)
            origin=BASE+key
        else:raise ValueError('Missing exact input: '+key)
        if len(raw)!=row['expected_bytes'] or not raw.startswith(b'UnityFS\0'):raise ValueError('Catalog size/UnityFS mismatch: '+key)
        sha=hashlib.sha256(raw).hexdigest();path=output/(sha+'.bundle');path.write_bytes(raw);env=UnityPy.load(str(path))
        row.update(status='LOCATED',sha256=sha,serialized_files=sorted({o.assets_file.name for o in env.objects}),unity_versions=sorted({o.assets_file.unity_version for o in env.objects}))
        manifest['content'][sha]=dict(file=path.relative_to(ROOT).as_posix(),bytes=len(raw))
        catalog.document['m_InternalIds'][catalog.entries[index][0]]='http://127.0.0.1:8080/assets/avatar/bundles/'+sha+'.bundle'
        evidence.append(dict(key=key,sha256=sha,bytes=len(raw),source=origin));return path
    def closure(address):
        if address not in catalog.buckets:raise ValueError('Missing native preset reference: '+address)
        for i in catalog.closure(address):
            if i in bundles:required.add(i);acquire(i)
    for i in range(9):closure('n_avatarpreset_ftue_'+str(i))
    preset=next(b for b in bundles.values() if b['key'].startswith('neutralavatarpresets_assets_all_'))
    for obj in UnityPy.load(str(acquire(preset['index']))).objects:
        if obj.type.name!='MonoBehaviour':continue
        for key,value in obj.read_typetree().items():
            if 'BackingField' in key and isinstance(value,str) and value:closure(value)
    raw=json.dumps(catalog.document,separators=(',',':')).encode();local=output/'catalog.json';local.write_bytes(raw)
    manifest['local_catalog']=local.relative_to(ROOT).as_posix();manifest['local_catalog_sha256']=hashlib.sha256(raw).hexdigest()
    for variant in manifest['variants']:
        variant['missing_dependencies']=[bundles[i]['key'] for i in variant['bundle_indices'] if bundles[i]['status']!='LOCATED']
    manifest['counts'].update(located_bundles=len(manifest['content']),dependency_complete_addresses=sum(not v['missing_dependencies'] for v in manifest['variants']))
    manifest.pop('portable_restoration',None);validate_manifest(manifest,Catalog(catalog.document))
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    report=dict(base_manifest=base,preserved_content_count=len(manifest['content'])-len(evidence),recovered=evidence,required_bundle_indices=sorted(required),catalog_sha256=manifest['local_catalog_sha256'])
    (output/'restoration.json').write_text(json.dumps(report,indent=2)+'\n');return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',default='config/resources/avatar-assets.json');parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--download',action='store_true');args=parser.parse_args()
    result=restore(args.base,args.cache,args.output,args.download)
    print(json.dumps(dict(preserved=result['preserved_content_count'],restored=len(result['recovered']),bytes=sum(v['bytes'] for v in result['recovered']))))
if __name__=='__main__':main()
