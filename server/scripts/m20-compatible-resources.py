"""Private, opt-in Mateo/Vivillon resource overlay from an authorized local cache.

Copies only the selected source catalog's exact dependency closure, never modifies
the official app/cache, and never downloads assets. Android compatibility remains
NOT_TESTED until the resulting disposable client is tested on the real device.
"""
import os, shutil
import argparse,base64,collections,copy,hashlib,json,re,shlex,struct,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from dittobuffer.core.asset_catalog import Catalog
from dittobuffer.core.client_resources import ClientResources
from scripts.pokemon_asset_import import catalog_document

def selected_resources(catalog,include_mateo=False):
    selected=[]
    for index in range(len(catalog.entries)):
        loc=catalog.location(index);path=loc['internal_id'].replace('\\','/')
        sprite='/Vivillon/' in path and loc['type'] in ('UnityEngine.Sprite','UnityEngine.Texture2D')
        mateo=include_mateo and path.endswith(('/Mateo/combat_explorer.prefab','/Mateo/dialogue_explorer.prefab')) and loc['type']=='UnityEngine.GameObject'
        if sprite or mateo:selected.append(loc)
    if not selected:raise ValueError('No explicitly supported resource addresses')
    # Serialized AssetReferenceSprite / AssetReferenceGameObject fields use
    # stable GUID aliases, while dynamically requested regional icons use their
    # named addresses. Both must select the exact same reviewed source location.
    # Never import a broad label that also includes unrelated resource entries.
    indices={v['index'] for v in selected};expanded=[]
    aliases={i:[key for key,values in catalog.buckets.items() if re.fullmatch('[a-fA-F0-9]{32}',key) and i in values and set(values)<=indices] for i in indices}
    for loc in selected:
        expanded.append(loc)
        expanded.extend(dict(loc,key=key) for key in aliases[loc['index']])
    return expanded

def graph(catalog,key):
    """Compare semantic graphs independently of compact catalog entry offsets."""
    def one(index):
        loc=catalog.location(index)
        return dict(key=loc['key'],internal_id=loc['internal_id'],provider=loc['provider'],type=loc['type'],options=catalog.options(index),dependencies=[one(i) for i in loc['dependencies']])
    return [one(i) for i in catalog.buckets[key]]

def script_contract(dump,name,namespace,tree):
    """Check referenced script identity and serialized field names, not ABI parity."""
    matches=list(re.finditer(r'^public class '+re.escape(name)+r'\b',dump,re.M))
    block=None
    for match in matches:
        ns=dump[dump.rfind('// Namespace:',0,match.start()):match.start()].splitlines()[0].removeprefix('// Namespace:').strip()
        if ns==namespace:
            end=dump.find('\n// Namespace:',match.end());block=dump[match.start():end if end>=0 else None];break
    if block is None:raise ValueError('Referenced script absent from client namespace: '+namespace+'.'+name)
    field_block=block.split('// Properties',1)[0].split('// Methods',1)[0]
    declared=set(re.findall(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*;\s*// 0x',field_block))
    serialized={key for key in tree if not key.startswith('m_')}
    missing=serialized-declared
    if missing:raise ValueError('Serialized fields absent from client script: '+','.join(sorted(missing)))
    return dict(class_name=name,namespace=namespace,present_in_04292_dump=True,serialized_fields=sorted(serialized),missing_client_fields=[],native_abi_compatibility='NOT_TESTED')

def reference_audit(env):
    files={obj.assets_file.name.casefold():obj.assets_file for obj in env.objects};missing=[];count=0
    for obj in env.objects:
        def walk(value):
            nonlocal count
            if isinstance(value,dict):
                if set(value)=={'m_FileID','m_PathID'} and value['m_PathID']:
                    count+=1;fid=value['m_FileID'];pid=value['m_PathID'];target=None
                    if fid==0:target=obj.assets_file
                    elif 0<fid<=len(obj.assets_file.externals):
                        name=obj.assets_file.externals[fid-1].path.rsplit('/',1)[-1].casefold();target=files.get(name)
                        if target is None and name in ('unity default resources','unity_builtin_extra'):return
                    if target is None or pid not in target.objects:missing.append(dict(source=obj.assets_file.name,path_id=obj.path_id,file_id=fid,target_path_id=pid))
                else:
                    for item in value.values():walk(item)
            elif isinstance(value,list):
                for item in value:walk(item)
        walk(obj.read_typetree())
    return dict(non_null_references=count,missing=missing)

def namespace_bundle(raw,namespace):
    """Use the established namespace-pokemon-assets.py lossless CAB procedure.

    Different Unity builds can reuse the same CAB identity for different shader
    bytes. Derive an isolated, unencrypted copy; never alter an accepted bundle.
    """
    import UnityPy
    from UnityPy.streams import EndianBinaryReader
    pattern=re.compile(rb'CAB-[0-9a-fA-F]{32}')
    def rename(value):return pattern.sub(lambda m:b'CAB-'+hashlib.sha256(b'libre-cab-v1:'+namespace.encode()+b':'+m[0].lower()).hexdigest()[:32].encode(),value)
    env=UnityPy.load(raw);bundle=next(iter(env.files.values()))
    if bundle.signature!='UnityFS' or int(bundle.dataflags)&int(bundle.dataflags.UsesAssetBundleEncryption):raise ValueError('Only readable unencrypted UnityFS dependencies may be isolated')
    before={(o.assets_file.name,o.path_id,o.type.value) for o in env.objects};files={};expected={};changes=[]
    for name,file in bundle.files.items():
        original=file.reader.bytes if hasattr(file,'reader') else file.bytes
        transformed=rename(original);new_name=rename(name.encode()).decode();reader=EndianBinaryReader(transformed);reader.flags=file.flags
        files[new_name]=reader;expected[new_name]=transformed;changes.append(dict(source=name,derived=new_name,reference_replacements=len(pattern.findall(original))))
    bundle.files=files;derived=bundle.save(packer='lz4');check=UnityPy.load(derived);loaded=next(iter(check.files.values()))
    for name,file in loaded.files.items():
        value=file.reader.bytes if hasattr(file,'reader') else file.bytes
        if value!=expected.pop(name):raise ValueError('Derived NPC resource changed undeclared unpacked payload bytes')
    if expected:raise ValueError('Derived NPC resource lost unpacked files')
    after={(o.assets_file.name,o.path_id,o.type.value) for o in check.objects}
    if after!={(rename(name.encode()).decode(),ident,kind) for name,ident,kind in before}:raise ValueError('Derived NPC resource changed object identity')
    return derived,changes

def overlay(component,base,reuse=None,namespace='libre-m202-overlay-dependencies:',component_namespace='libre-m202-component:'):
    """Explicit selected-key precedence with collision-free private graph keys.

    The baseline has already been merged several times. Reusing the generic
    writer's original synthetic-key namespace could shadow those existing keys.
    """
    reuse=reuse or {}
    if any(not component.location(i)['type'].endswith('.IAssetBundleResource') or not base.location(j)['type'].endswith('.IAssetBundleResource') for i,j in reuse.items()):raise ValueError('Only exact reviewed bundle locations may be reused')
    retained=[i for i in range(len(component.entries)) if i not in reuse];component_indices={old:new for new,old in enumerate(retained)}
    catalogs=[component,base];document=dict(base.document);keys={};offsets=[0,len(retained)]
    def mapped(source,index):
        if source==1:return offsets[1]+index
        return offsets[1]+reuse[index] if index in reuse else component_indices[index]
    for source,catalog in enumerate(catalogs):
        for key,indices in catalog.buckets.items():
            if key.startswith('libre-model-') and source==0:key=component_namespace+key
            if key not in keys:keys[key]=[mapped(source,j) for j in indices]
    if any(k.startswith(namespace) for k in keys):raise ValueError('Overlay namespace already exists; use the original baseline')
    for source,catalog in enumerate(catalogs):
        for index in range(len(catalog.entries)):
            if source==0 and index in reuse:continue
            children=catalog.location(index)['dependencies']
            if children:keys[f'{namespace}{source}:{index}']=[mapped(source,j) for j in children]
    key_list=list(keys);ki={k:i for i,k in enumerate(key_list)};providers=[];types=[];internal=[];entries=[];extra=b''
    for source,catalog in enumerate(catalogs):
        io=len(internal);internal.extend(catalog.document['m_InternalIds']);pm=[];tm=[]
        for provider in catalog.document['m_ProviderIds']:
            if provider not in providers:providers.append(provider)
            pm.append(providers.index(provider))
        for kind in catalog.document['m_resourceTypes']:
            if kind not in types:types.append(kind)
            tm.append(types.index(kind))
        for index,(ident,provider,dep,dh,options,primary,kind) in enumerate(catalog.entries):
            if source==0 and index in reuse:continue
            key=catalog.keys[primary]
            if source==0 and key.startswith('libre-model-'):key=component_namespace+key
            entries.append((io+ident,pm[provider],ki[f'{namespace}{source}:{index}'] if dep>=0 else -1,dh,options+len(extra) if options>=0 else -1,ki[key],tm[kind]))
        extra+=catalog.extra
    kd=bytearray(struct.pack('<i',len(keys)));bd=bytearray(struct.pack('<i',len(keys)))
    for key,indices in keys.items():
        raw=key.encode('ascii');bd+=struct.pack('<ii',len(kd),len(indices))+struct.pack('<'+'i'*len(indices),*indices);kd+=b'\0'+struct.pack('<i',len(raw))+raw
    document.update(m_LocatorId='LibreM202PrivateCompatibleResources',m_InternalIds=internal,m_InternalIdPrefixes=[],m_ProviderIds=providers,m_resourceTypes=types)
    for name,raw in [('Key',kd),('Bucket',bd),('Entry',struct.pack('<i',len(entries))+b''.join(struct.pack('<7i',*v) for v in entries)),('Extra',extra)]:document['m_'+name+'DataString']=base64.b64encode(raw).decode()
    return Catalog(document)

def build(args):
    import UnityPy
    if args.namespace_npc_closure and not args.include_mateo:raise ValueError('NPC closure isolation requires --include-mateo')
    out=(ROOT/args.output).resolve();base=(ROOT/args.base).resolve();source=(ROOT/args.catalog).resolve()
    if not out.is_relative_to(ROOT/'builds') or out.exists() or not base.is_relative_to(ROOT) or not source.is_relative_to(ROOT):raise ValueError('Project-local inputs and fresh builds output required')
    original=ClientResources(ROOT,dict(enabled=True,registry=args.base));old=Catalog(json.loads(original.read('catalog_926.json')[0]));catalog=Catalog(json.loads(source.read_bytes()))
    registry=json.loads(base.read_text(encoding='utf8'));selected=selected_resources(catalog,args.include_mateo)
    keys={v['key'] for v in selected};needed=sorted({i for key in keys for i in catalog.closure(key) if catalog.location(i)['type'].endswith('.IAssetBundleResource')})
    npc_indices={i for key in ('combat_explorer','dialogue_explorer') for i in catalog.closure(key) if catalog.location(i)['type'].endswith('.IAssetBundleResource')} if args.include_mateo else set()
    namespace=hashlib.sha256(source.read_bytes()+b':m202-explorer-npc-v1').hexdigest()
    out.mkdir(parents=True);adb=[args.adb,'-s',args.serial];nodes=[];remap={};env=UnityPy.Environment();report=[]
    official_apk=None
    for index in needed:
        location=catalog.location(index);options=copy.deepcopy(catalog.options(index)['value']);download_bytes=options['m_BundleSize']
        name=location['internal_id'].replace('\\','/').rsplit('/',1)[-1]
        if not re.fullmatch(r'[A-Za-z0-9_.-]+\.bundle',name):raise ValueError('Invalid exact bundle name')
        if options['m_Hash']:
            if not re.fullmatch('[a-f0-9]{32}',options['m_Hash']) or not re.fullmatch('[A-Za-z0-9_]+',options['m_BundleName']):raise ValueError('Invalid cache identity')
            origin='/sdcard/Android/data/com.nianticlabs.pokemongo/files/UnityCache/Shared/'+options['m_BundleName']+'/'+options['m_Hash']+'/__data'
            command='cat '+shlex.quote(origin)
        else:
            if not location['internal_id'].startswith('{UnityEngine.AddressableAssets.Addressables.RuntimePath}') or 'monoscripts' not in name:raise ValueError('Unexpected embedded dependency')
            if official_apk is None:
                paths=subprocess.check_output([*adb,'shell','pm','path','com.nianticlabs.pokemongo']).decode().splitlines()
                official_apk=next(v.removeprefix('package:').strip() for v in paths if v.strip().endswith('/base.apk'))
            origin=official_apk+'!assets/aa/Android/'+name
            command='unzip -p '+shlex.quote(official_apk)+' '+shlex.quote('assets/aa/Android/'+name)
        raw=subprocess.check_output([*adb,'exec-out','su','-c',command],timeout=60)
        if not raw.startswith(b'UnityFS\0'):raise ValueError('Exact source resource unavailable: '+origin)
        source_sha=hashlib.sha256(raw).hexdigest();changes=[]
        if args.namespace_npc_closure and index in npc_indices:
            (out/(source_sha+'.source.bundle')).write_bytes(raw)
            raw,changes=namespace_bundle(raw,namespace);options['m_Hash']=hashlib.md5(raw).hexdigest()
        digest=hashlib.sha256(raw).hexdigest();target=out/(digest+'.bundle');target.write_bytes(raw)
        local=UnityPy.load(raw)
        for obj in local.objects:obj.read()
        parsed=env.load_file(str(target))
        assets=[dict(path=k,type=r.type.name) for k,r in local.container.items()]
        serialized=sorted({o.assets_file.name for o in local.objects});external=sorted({e.path for o in local.objects for e in o.assets_file.externals})
        # Cache recompression is possible; preserve actual unchanged bytes and
        # source version, never pad, truncate, invent a hash, or rewrite assets.
        options['m_BundleSize']=len(raw)
        # A private cache namespace keeps this experiment from evicting a prior
        # accepted bundle version in the development client's Unity cache.
        source_bundle_name=options['m_BundleName'];options['m_BundleName']='libre-m202-'+digest[:32]
        options['m_ClearOtherCachedVersionsWhenLoaded']=False
        remap[index]=len(nodes)
        nodes.append(dict(sha256=digest,url='http://127.0.0.1:8080/assets/client/'+digest+'.bundle',options=options,file=target.relative_to(ROOT).as_posix(),assets=assets,serialized_files=serialized,external_files=external,unity_versions=sorted({o.assets_file.unity_version for o in local.objects})))
        report.append(dict(source_catalog_index=index,source=origin,source_bundle_name=source_bundle_name,source_version=catalog.options(index)['value']['m_Hash'],source_download_bytes=download_bytes,source_sha256=source_sha,sha256=digest,bytes=len(raw),transformation='libre-cab-v1: equal-length CAB names/references; all other unpacked bytes and object IDs verified unchanged' if changes else None,namespace=namespace if changes else None,transformation_records=changes,serialized_files=serialized,external_files=external,objects=dict(collections.Counter(o.type.name for o in local.objects)),unity_versions=nodes[-1]['unity_versions']))
        print('Prepared isolated derived dependency' if changes else 'Copied exact source dependency',index,name,len(raw),flush=True)
    cabinets={s.lower() for n in nodes for s in n['serialized_files']};unresolved=[]
    for node in nodes:
        for external in node['external_files']:
            if external.lower() in ('library/unity default resources','resources/unity_builtin_extra'):continue
            if external.rsplit('/',1)[-1].lower() not in cabinets:unresolved.append(dict(bundle=node['file'],external=external))
    script_audit=[]
    dump=(ROOT/'beta_googleplaystore_2026092102_0.429.2_dump.cs').read_text(encoding='utf8') if args.include_mateo else ''
    for obj in env.objects:
        if obj.type.name!='MonoBehaviour':continue
        value=obj.read();script=value.m_Script.read();name=script.m_ClassName;namespace=script.m_Namespace
        contract=script_contract(dump,name,namespace,obj.read_typetree());contract['assembly']=script.m_AssemblyName
        if name=='AvatarModel' and (script.m_AssemblyName!='holo-game' or obj.read_typetree().get('avatarModelName')!=15):raise ValueError('Mateo component does not identify the existing EXPLORER avatar contract')
        script_audit.append(contract)
    references=reference_audit(env)
    audit=dict(source_catalog=source.relative_to(ROOT).as_posix(),source_catalog_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),base_registry=args.base,base_catalog_sha256=hashlib.sha256(original.read('catalog_926.json')[0]).hexdigest(),selected_keys=sorted(keys),dependencies=report,unresolved_externals=unresolved,serialized_references=references,script_classes=script_audit,android_compatibility='NOT_TESTED',original_assets_unchanged=True,redistribution='Excluded: private authorized compatibility inputs only')
    (out/'compatibility-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    if unresolved:raise ValueError('Unresolved external CAB dependencies; audit saved, no runtime registry emitted')
    if references['missing']:raise ValueError('Unresolved serialized object references; audit saved, no runtime registry emitted')
    resources=[]
    for loc in selected:
        closure=[remap[i] for i in catalog.closure(loc['key']) if i in remap]
        owner=remap[loc['dependencies'][0]]
        if not any(a['path'].casefold()==loc['internal_id'].casefold() and a['type']==loc['type'].removeprefix('UnityEngine.') for a in nodes[owner]['assets']):raise ValueError('Exact named asset/type missing: '+loc['key'])
        resources.append(dict(address=loc['key'],path=loc['internal_id'],type=loc['type'],owner=owner,bundles=closure))
    component=Catalog(catalog_document(catalog.document,nodes,resources,catalog.options(needed[0])))
    # Reusing bytes through a different URL/cache namespace can load the same
    # Unity CAB twice. Bind existing, byte-identical versioned dependencies to
    # their actual baseline catalog entry, retaining that entry's exact options
    # and operation identity. Newer or merely similarly named bytes never match.
    reuse={};reused=[]
    for index,node in enumerate(nodes):
        source_options=catalog.options(needed[index])['value']
        for old_index in range(len(old.entries)):
            loc=old.location(old_index)
            if not loc['type'].endswith('.IAssetBundleResource'):continue
            name=loc['internal_id'].replace('\\','/').rsplit('/',1)[-1];entry=registry['files'].get(name)
            if not entry or entry['sha256']!=node['sha256']:continue
            options=old.options(old_index)['value']
            if options['m_Hash']!=source_options['m_Hash'] or options['m_BundleName']!=source_options['m_BundleName'] or options['m_BundleSize']!=len((ROOT/node['file']).read_bytes()):continue
            reuse[index]=old_index
            reused.append(dict(source_catalog_index=needed[index],baseline_catalog_index=old_index,sha256=node['sha256'],internal_id=loc['internal_id'],version=options['m_Hash'],bundle_name=options['m_BundleName']))
            break
    merged=overlay(component,old,reuse)
    preserved=0
    for key in old.keys:
        if key in keys:continue
        if graph(old,key)!=graph(merged,key):raise ValueError('Existing unrelated resource graph changed: '+key)
        preserved+=1
    raw=json.dumps(merged.document,separators=(',',':')).encode();(out/'catalog_926.json').write_bytes(raw);(out/'catalog_926.hash').write_text(hashlib.md5(raw).hexdigest())
    for index,node in enumerate(nodes):
        if index in reuse:continue
        registry['files'][node['sha256']+'.bundle']=dict(file=node['file'],sha256=node['sha256'],bytes=(ROOT/node['file']).stat().st_size)
    for name in ['catalog_926.json','catalog_926.hash']:
        p=out/name;registry['files'][name]=dict(file=p.relative_to(ROOT).as_posix(),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),bytes=p.stat().st_size)
    audit.update(unchanged_public_keys=preserved,overlay_keys=len(keys),reused_exact_dependencies=reused,new_bundle_locations=len(nodes)-len(reuse),catalog_sha256=hashlib.sha256(raw).hexdigest())
    registry['m202_compatible_resources']=audit
    (out/'registry.json').write_text(json.dumps(registry,indent=2)+'\n');(out/'compatibility-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    ClientResources(ROOT,dict(enabled=True,registry=(out/'registry.json').relative_to(ROOT).as_posix()))
    print(json.dumps(dict(registry=(out/'registry.json').relative_to(ROOT).as_posix(),keys=len(keys),bundles=len(nodes),catalog_sha256=audit['catalog_sha256'],android_compatibility='NOT_TESTED')))

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--base',required=True);ap.add_argument('--catalog',required=True);ap.add_argument('--output',required=True);ap.add_argument('--serial',required=True);ap.add_argument('--include-mateo',action='store_true');ap.add_argument('--namespace-npc-closure',action='store_true',help='Explicitly derive isolated NPC CAB identities using the established byte-verified procedure; Vivillon and existing accepted assets stay unchanged');ap.add_argument('--adb',default=os.environ.get('ADB') or shutil.which('adb') or 'adb');build(ap.parse_args())
if __name__=='__main__':main()
