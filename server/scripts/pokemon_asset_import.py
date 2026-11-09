"""Small offline catalog writer used by explicit model import tools."""
import base64,copy,hashlib,json,struct
from pathlib import Path


def catalog_document(template,bundles,resources,option_type):
    """Keep the owning bundle first; preload its complete verified closure."""
    keys={};entries=[];internal=[];extra=bytearray();types=copy.deepcopy(template['m_resourceTypes'])
    providers=[next(v for v in template['m_ProviderIds'] if v.endswith('AssetBundleProvider')),
               next(v for v in template['m_ProviderIds'] if v.endswith('BundledAssetProvider'))]
    def type_index(name):
        return next(i for i,v in enumerate(types) if v['m_ClassName']==name)
    for row in bundles:
        key='libre-model-bundle:'+row['sha256'];keys[key]=[len(entries)];internal.append(row['url'])
        data=json.dumps(row['options'],separators=(',',':')).encode('utf-16-le')
        assembly=option_type['assembly'].encode();cls=option_type['class'].encode();offset=len(extra)
        extra+=b'\7'+bytes([len(assembly)])+assembly+bytes([len(cls)])+cls+struct.pack('<i',len(data))+data
        entries.append([len(internal)-1,0,None,0,offset,key,type_index('UnityEngine.ResourceManagement.ResourceProviders.IAssetBundleResource')])
    for resource in resources:
        index=len(entries);keys.setdefault(resource['address'],[]).append(index)
        dependency='libre-model-dependencies:'+str(index)
        closure=sorted(set(resource['bundles']))
        if resource['owner'] not in closure:raise ValueError('Resource owner is absent from dependency closure')
        keys[dependency]=[resource['owner']]+[i for i in closure if i!=resource['owner']]
        internal.append(resource['path']);entries.append([len(internal)-1,1,dependency,0,-1,resource['address'],type_index(resource['type'])])
    key_list=list(keys);indices={k:i for i,k in enumerate(key_list)}
    key_data=bytearray(struct.pack('<i',len(keys)));bucket_data=bytearray(struct.pack('<i',len(keys)))
    for key,locations in keys.items():
        raw=key.encode('ascii');bucket_data+=struct.pack('<ii',len(key_data),len(locations))+struct.pack('<'+'i'*len(locations),*locations)
        key_data+=b'\0'+struct.pack('<i',len(raw))+raw
    for entry in entries:entry[2]=indices[entry[2]] if entry[2] else -1;entry[5]=indices[entry[5]]
    result=copy.deepcopy(template);result.update(m_LocatorId='LibrePrivateModelImport',m_InternalIds=internal,m_InternalIdPrefixes=[],m_ProviderIds=providers,m_resourceTypes=types)
    for name,data in [('Key',key_data),('Bucket',bucket_data),('Entry',struct.pack('<i',len(entries))+b''.join(struct.pack('<7i',*v) for v in entries)),('Extra',extra)]:
        result['m_'+name+'DataString']=base64.b64encode(data).decode()
    return result


def write_manifest(root,output,template,nodes,resources,option_type,provenance,metadata=None):
    """Emit the same manifest format consumed by the established local dittobuffer.gameplay."""
    import sys
    if str(root) not in sys.path:sys.path.insert(0,str(root))
    from dittobuffer.core.asset_catalog import Catalog,validate_manifest
    document=catalog_document(template,nodes,resources,option_type);catalog=Catalog(document)
    local=output.with_suffix('.catalog.json');raw=json.dumps(document,separators=(',',':')).encode()
    if output.exists() or local.exists():raise ValueError('Fresh import output required')
    local.write_bytes(raw);content={};bundles=[]
    for index,node in enumerate(nodes):
        location=catalog.location(index);data=(root/node['file']).read_bytes()
        if hashlib.sha256(data).hexdigest()!=node['sha256']:raise ValueError('Import bytes changed')
        content[node['sha256']]=dict(file=node['file'],bytes=len(data))
        bundles.append(dict(**location,status='LOCATED',sha256=node['sha256'],version=node['options']['m_Hash'],bundle_name=node['options']['m_BundleName'],
            expected_bytes=len(data),assets=node['assets'],objects=node.get('objects',{}),unity_versions=node['unity_versions'],serialized_files=node['serialized_files'],
            external_files=node.get('external_files',[]),namespace=node.get('namespace'),source_version=node.get('source_version'),source_bundle_name=node.get('source_bundle_name'),source_sha256=node.get('source_sha256')))
    variants=[]
    for key in dict.fromkeys(r['address'] for r in resources if r['type']=='UnityEngine.GameObject' and r['path'].endswith('.prefab')):
        import re
        required=[i for i in catalog.closure(key) if i<len(nodes)]
        variants.append(dict(address=key,species=int(re.match('pm(\\d+)',key)[1]),model_paths=[r['path'] for r in resources if r['address']==key and r['type']=='UnityEngine.GameObject'],
            bundle_indices=required,missing_dependencies=[],asset_status='DEPENDENCIES LOCATED',android={a:'NOT TESTED' for a in ('bundle_load','render','animation')}))
    from dittobuffer.gameplay.game import p
    import re
    costume_ids={v.name.removeprefix('Costume_'):v.number for v in p.PokemonDisplayProto.Costume.DESCRIPTOR.values}
    costumes={}
    for variant in variants:
        match=re.search(r'\.c([A-Z0-9_]+)',variant['address'])
        if not match or match[1] not in costume_ids:continue
        key=f'{variant["species"]}:{costume_ids[match[1]]}'
        costumes.setdefault(key,dict(species=variant['species'],costume=costume_ids[match[1]],suffix=match[1],addresses=[],evolution='BLOCKED_PENDING_REVIEW'))['addresses'].append(variant['address'])
    result=dict(format=1,provenance=provenance,source_catalog=local.relative_to(root).as_posix(),source_catalog_sha256=hashlib.sha256(raw).hexdigest(),
        local_catalog=local.relative_to(root).as_posix(),local_catalog_sha256=hashlib.sha256(raw).hexdigest(),content=content,bundles=bundles,variants=variants,costumes=costumes,
        counts=dict(located_bundles=len(content),prefab_addresses=len(variants),dependency_complete_addresses=len(variants),dependency_complete_species=len({v['species'] for v in variants}),android_verified=0),
        import_metadata=metadata or {})
    validate_manifest(result,catalog);output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    return result
