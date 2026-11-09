"""Register exact Route bundles from an existing authorized Pixel cache, read-only.

Never downloads resources, alters the original cache/catalog, or fabricates assets.
The resulting local registry is for private research, not asset redistribution.
"""
import os, shutil
import argparse,hashlib,json,re,shlex,struct,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from dittobuffer.core.asset_catalog import Catalog
from dittobuffer.core.client_resources import ClientResources

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--base',required=True);ap.add_argument('--output',required=True);ap.add_argument('--serial',required=True);ap.add_argument('--include-mateo',action='store_true',help='Also inspect the exact native Mateo/Explorer catalog dependency closure');ap.add_argument('--allow-cache-recompression',action='store_true',help='Explicitly accept structurally valid UnityFS files from the exact versioned cache path when catalog CRC is disabled; record both sizes and local SHA');ap.add_argument('--include-vivillon',action='store_true',help='Also inspect exact regional medal and habitat UI resources');ap.add_argument('--adb',default=os.environ.get('ADB') or shutil.which('adb') or 'adb');args=ap.parse_args()
    out=(ROOT/args.output).resolve();base=(ROOT/args.base).resolve()
    if not out.is_relative_to(ROOT/'builds') or out.exists() or not base.is_relative_to(ROOT):raise ValueError('Local base and fresh builds output required')
    resources=ClientResources(ROOT,dict(enabled=True,registry=args.base));document=resources.read('catalog_926.json')[0];catalog=Catalog(json.loads(document));registry=json.loads(base.read_text());out.mkdir(parents=True)
    roots=[];bundles={}
    for index in range(len(catalog.entries)):
        loc=catalog.location(index)
        native=loc['internal_id'].replace('\\','/').lower()
        mateo=args.include_mateo and any(token in native for token in ('npcmateo_','npcicons_assets_mateo_','npcavatars_assets_dialogue_explorer_','npcavatars_assets_combat_explorer_'))
        vivillon=args.include_vivillon and ('vivillon' in native or 'vivillon' in str(loc['key']).lower())
        if not mateo and not vivillon and 'route' not in str(loc['key']).lower() and '/route' not in native:continue
        roots.append(loc['key'])
        for dependency in catalog.closure(loc['key']):
            value=catalog.location(dependency)
            if not value['type'].endswith('.IAssetBundleResource'):continue
            name=value['internal_id'].replace('\\','/').rsplit('/',1)[-1]
            bundles[name]={**catalog.options(dependency)['value'],'source_internal_id':value['internal_id']}
    added=[];missing=[];embedded=[];adb=[args.adb,'-s',args.serial]
    for name,options in sorted(bundles.items()):
        if name in resources.files:continue
        if not options['m_Hash'] and options['source_internal_id'].startswith('{UnityEngine.AddressableAssets.Addressables.RuntimePath}'):
            path=ROOT/'builds/client-base/assets/aa/Android'/name
            if path.is_file():
                embedded.append(dict(name=name,file=path.relative_to(ROOT).as_posix(),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),bytes=path.stat().st_size));continue
        if not re.fullmatch(r'[A-Za-z0-9_.-]+\.bundle',name) or not re.fullmatch(r'[a-f0-9]{32}',options['m_Hash']) or not re.fullmatch(r'[A-Za-z0-9_]+',options['m_BundleName']):raise ValueError('Invalid catalog cache identity')
        source='/sdcard/Android/data/com.nianticlabs.pokemongo/files/UnityCache/Shared/'+options['m_BundleName']+'/'+options['m_Hash']+'/__data'
        result=subprocess.run([*adb,'exec-out','su','-c','cat '+shlex.quote(source)],capture_output=True)
        if result.returncode:missing.append(dict(name=name,cache_version=options['m_Hash'],expected_bytes=options['m_BundleSize']));continue
        raw=result.stdout
        recompressed=len(raw)!=options['m_BundleSize']
        if not raw.startswith(b'UnityFS\0') or recompressed and (not args.allow_cache_recompression or options['m_Crc'] or options['m_UseCrcForCachedBundles']):
            missing.append(dict(name=name,cache_version=options['m_Hash'],expected_bytes=options['m_BundleSize'],actual_bytes=len(raw),reason='Cache bytes need separate compatibility/recompression review'));continue
        # Parse the unchanged file instead of trusting a matching folder name.
        import UnityPy
        env=UnityPy.load(raw);object_count=sum(1 for _ in env.objects)
        if recompressed:
            # Unity caches can recompress a download. BundleSize describes its
            # download size, not a cryptographic content identity. Require the
            # exact catalog bundle/version directory, a consistent UnityFS
            # header and readable assets; never pad/truncate to imitate it.
            cursor=12
            for _ in range(2):cursor=raw.index(b'\0',cursor)+1
            if struct.unpack_from('>Q',raw,cursor)[0]!=len(raw) or not object_count or not env.container:raise ValueError('Invalid recompressed UnityFS cache resource')
            for obj in env.objects:obj.read()
        path=out/name;path.write_bytes(raw);row=dict(file=path.relative_to(ROOT).as_posix(),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
        registry['files'][name]=row;added.append(dict(name=name,source=source,cache_version=options['m_Hash'],objects=object_count,catalog_download_bytes=options['m_BundleSize'],recompressed_cache=recompressed,**row))
    registry['m20_route_resources']=dict(source='Exact existing official cache versions; read-only import',added=added,embedded=embedded,unresolved=missing,catalog_sha256=hashlib.sha256(document).hexdigest(),catalog_unchanged=True,roots=len(set(roots)),required_bundles=len(bundles),redistribution='Not included; private authorized research assets only')
    registry['missing_bundles']=[v for v in registry.get('missing_bundles',[]) if v not in {a['name'] for a in added}]
    target=out/'registry.json';target.write_text(json.dumps(registry,indent=2)+'\n');check=ClientResources(ROOT,dict(enabled=True,registry=target.relative_to(ROOT).as_posix()))
    assert check.read('catalog_926.json')[0]==document
    (out/'route-resource-report.json').write_text(json.dumps(registry['m20_route_resources'],indent=2)+'\n')
    print(json.dumps(dict(registry=target.relative_to(ROOT).as_posix(),added=len(added),unresolved=len(missing),catalog_unchanged=True)))
if __name__=='__main__':main()
