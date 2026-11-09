"""Isolated native compatibility probe: exact NPC resource in the runtime locator.

Preserve every other runtime address. Native built-in CAB resolution remains an
explicit experiment until actual prefab, component and animation checks pass.
"""
import argparse,base64,hashlib,importlib.util,json,struct,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from dittobuffer.core.asset_catalog import Catalog

def served_bundle_entry(catalog, files, version):
 """Reuse a served canonical location, never a retained stale URL alias."""
 matches=[i for i in range(len(catalog.entries))
          if catalog.location(i)['type'].endswith('.IAssetBundleResource')
          and catalog.options(i)['value']['m_Hash']==version]
 return next((i for i in matches if catalog.location(i)['internal_id'].replace('\\','/').rsplit('/',1)[-1] in files),None)

def normalized_material(obj):
 """Compare resolved PPtrs, independent of external-table slot numbering."""
 def visit(value):
  if isinstance(value,dict):
   if set(value)=={'m_FileID','m_PathID'}:
    cab=obj.assets_file.name if not value['m_FileID'] else obj.assets_file.externals[value['m_FileID']-1].path.rsplit('/',1)[-1]
    # The M18 audited built-in alias preserves every native shader/texture byte.
    if cab=='CAB-05206ddec906fdf7b8747fb4996dcb98':cab='CAB-4efd892a7d487a9fcc11fd5bb94ffad6'
    return (cab.lower(),value['m_PathID']) if value['m_PathID'] else ('null',0)
   return {k:visit(v) for k,v in value.items()}
  if isinstance(value,(list,tuple)):return [visit(v) for v in value]
  return value
 return visit(obj.read_typetree())

def compatible_ball_shared(oldenv,newenv,name):
 """Two exact reviewed shared materials; reject any other content change."""
 if not name.startswith(('sharedfx_assets_fx_sparkle_','materials_shared_assets_lit_mat_')):return False
 old={v.path_id:v for v in oldenv.objects};new={v.path_id:v for v in newenv.objects}
 if set(old)!=set(new) or any(v.type.name not in ('AssetBundle','Material') for v in old.values()):return False
 for ident,obj in new.items():
  previous=old[ident]
  if obj.type.name!=previous.type.name:return False
  if obj.type.name=='AssetBundle':continue
  before,after=normalized_material(previous),normalized_material(obj)
  if name.startswith('materials_shared_assets_lit_mat_'):
   # Newer URP default material adds only an XR motion-vector toggle absent
   # from this client. Retain its exact original 0.429.2 material instead.
   if before['m_Name']!='Lit' or after['m_Name']!='Lit':return False
   floats=after['m_SavedProperties']['m_Floats']
   if ['_XRMotionVectorsPass',1.0] not in floats:return False
   floats.remove(['_XRMotionVectorsPass',1.0])
  if before!=after:return False
 return True

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',default='.private/rocket-runtime-overlay');ap.add_argument('--component',default='builds/m17-rocket-assets-probe/component.json');ap.add_argument('--base',default='builds/m17-client-runtime/registry.json');ap.add_argument('--catalog',required=True,help='Project-local independently supplied source Addressables catalog');a=ap.parse_args();out=(ROOT/a.output).resolve()
 if not any(out.is_relative_to(ROOT/name) and out!=ROOT/name for name in ('.private','.staging')):raise ValueError('Ignored private locator output required')
 out.mkdir(exist_ok=False)
 base=json.loads((ROOT/a.base).read_text());component=json.loads((ROOT/a.component).read_text())
 spec=importlib.util.spec_from_file_location('runtime',ROOT/'scripts/import-client-runtime.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 original=m.RuntimeCatalog(json.loads((ROOT/base['files']['catalog_926.json']['file']).read_text()));npc=Catalog(json.loads((ROOT/component['local_catalog']).read_text()))
 source=Catalog(json.loads((ROOT/a.catalog).read_text()))
 def bundles(c):return {c.options(i)['value']['m_Hash']:c.location(i)['internal_id'] for i in range(len(c.entries)) if c.location(i)['type'].endswith('.IAssetBundleResource')}
 old_locations=bundles(original);source_locations=bundles(source);reuse={};proof=[]
 # ResourceManager must see the same canonical locations for shared bundles.
 # Distinct URLs for one CAB produce Unity's "same files already loaded" error.
 for bundle in component['bundles']:
  version=bundle['version'];name=source_locations[version].replace('\\','/').rsplit('/',1)[-1] if version in source_locations else 'librenlp_grunt_'+version+'.bundle'
  existing=served_bundle_entry(original,base['files'],version)
  location=original.location(existing)['internal_id'] if existing is not None else 'https://asset-cdn-rel.nianticstatic.com/ClientRemoteAssets/catalog_926/bundles/Android/'+name
  npc.document['m_InternalIds'][npc.entries[bundle['index']][0]]=location
  content=component['content'][bundle['sha256']]
  if name in base['files'] and base['files'][name]['sha256']!=bundle['sha256']:
   import UnityPy
   identity=lambda env:{(o.assets_file.name,o.path_id,o.type.name):hashlib.sha256(o.get_raw_data()).hexdigest() for o in env.objects}
   if identity(UnityPy.load(str(ROOT/base['files'][name]['file'])))!=identity(UnityPy.load(str(ROOT/content['file']))):raise ValueError('Conflicting existing runtime objects: '+name)
   proof.append(dict(resource=name,reason='Every serialized object byte-identical; retain existing bundle/container bytes'))
  base['files'].setdefault(name,dict(**content,sha256=bundle['sha256'],cache_version=version))
  # A previous merge can retain an unused entry for this exact version while
  # routing its prefab to an older canonical shader. Resolve CAB identity even
  # when the version exists; otherwise a later NPC merge reintroduces it.
  ring=name.startswith('sharedfx_assets_mesh_ring_outward_short_')
  ball=name.startswith('pokeball_shared_assets_all_')
  geometry=ring or ball
  if existing is None or geometry or name.startswith(('sharedshaders_','materials_shared_assets_defaultimport_')):
   # Different build versions may retain the exact serialized CAB/object IDs.
   # Reuse the already accepted native shader, never load the same CAB twice.
   import UnityPy
   prefix=name.rsplit('_',1)[0]+'_'
   for oldname,oldrow in base['files'].items():
    if oldname==name or not oldname.startswith(prefix) or not oldname.endswith('.bundle'):continue
    oldenv=UnityPy.load(str(ROOT/oldrow['file']));newenv=UnityPy.load(str(ROOT/content['file']))
    identity=lambda env:{(v.assets_file.name.lower(),v.path_id,v.type.name) for v in env.objects}
    if identity(oldenv)!=identity(newenv):continue
    allowed=('Shader','Material','AssetBundle') if not ring else ('MeshRenderer','GameObject','Transform','Mesh','AssetBundle','MeshFilter','Material')
    if not ball and any(v.type.name not in allowed for v in oldenv.objects):continue
    if geometry:
     # The newer ring adds only Unity mesh-LOD defaults. Its geometry,
     # materials, transforms and common renderer fields must match exactly.
     # Keep the already accepted old resource; do not rewrite either bundle.
     oldobjects={v.path_id:v for v in oldenv.objects};compatible=True
     defaults={'MeshRenderer':{'m_ForceMeshLod':-1,'m_MeshLodSelectionBias':0.0,'m_MaskInteraction':0},'Mesh':{'m_MeshLodInfo':{'m_LodSelectionCurve':{'m_LodSlope':0.0,'m_LodBias':0.0},'m_NumLevels':1,'m_SubMeshes':[{'m_Levels':[{'m_IndexStart':0,'m_IndexCount':0}]}]}}}
     defaults['SkinnedMeshRenderer']=defaults['MeshRenderer']
     for value in newenv.objects:
      previous=oldobjects[value.path_id]
      if value.get_raw_data()==previous.get_raw_data():continue
      if value.type.name not in defaults:compatible=False;break
      before=previous.read_typetree();after=value.read_typetree()
      for field,expected in defaults[value.type.name].items():
       if field not in before:
        if after.pop(field,None)!=expected:compatible=False;break
      if before!=after:compatible=False;break
     if not compatible:continue
    # The default material has identical serialized data in both catalogs,
    # but its embedded shader has a new build header. Keep the old accepted
    # resource for both NPCs and result screens; never replace material values.
    unchanged=lambda env:{(v.assets_file.name,v.path_id):v.get_raw_data() for v in env.objects if v.type.name=='Material'}
    compatible_shared=compatible_ball_shared(oldenv,newenv,name)
    if unchanged(oldenv)!=unchanged(newenv) and not compatible_shared:continue
    candidate=served_bundle_entry(original,base['files'],oldrow['cache_version'])
    if candidate is not None:
     reason='Exact CAB/object identity and common mesh data; only verified default Unity LOD fields differ' if geometry else 'Exact CAB, object IDs/types and Material bytes; retain existing native shader build'
     if compatible_shared:reason='Exact resolved material values; only external PPtr slots/native built-in alias and reviewed default XR toggle differ; retain accepted original bytes'
     existing=candidate;proof.append(dict(resource=name,reused=oldname,reason=reason,objects=sorted(identity(oldenv))));break
  if existing is not None:reuse[bundle['index']]=existing
 npc=Catalog(npc.document)
 merged,chosen,offsets=Catalog.combine([npc,original],lambda source,index:True)
 if reuse:
  # Point the NPC's dependency buckets at the original entries themselves,
  # preserving their exact options, provider and ResourceManager cache identity.
  raw=base64.b64decode(merged.document['m_BucketDataString']);count=struct.unpack_from('<i',raw)[0];pos=4;patched=bytearray(struct.pack('<i',count))
  for key in merged.keys:
   offset,length=struct.unpack_from('<ii',raw,pos);pos+=8;indices=list(struct.unpack_from('<'+'i'*length,raw,pos));pos+=length*4
   if key.startswith('libre-dependencies:0:'):indices=[offsets[1]+reuse[i] if i in reuse else i for i in indices]
   patched+=struct.pack('<ii',offset,length)+struct.pack('<'+'i'*length,*indices)
  assert pos==len(raw);merged.document['m_BucketDataString']=base64.b64encode(patched).decode();merged=Catalog(merged.document)
 selected=component.get('import_metadata',{}).get('selected',['m_combat_grunt'])
 assert all(chosen[key]==0 for key in selected)
 assert all(chosen[k]==1 for k in original.buckets if k not in npc.buckets)
 merged.document['m_LocatorId']=original.document.get('m_LocatorId','ClientRemoteAssets')
 raw=json.dumps(merged.document,separators=(',',':')).encode();catalog=out/'catalog_926.json';catalog.write_bytes(raw)
 digest=hashlib.md5(raw).hexdigest().encode();hashfile=out/'catalog_926.hash';hashfile.write_bytes(digest)
 for path in (catalog,hashfile):base['files'][path.name]=dict(file=path.relative_to(ROOT).as_posix(),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),bytes=path.stat().st_size)
 base['m17_runtime_locator_probe']=dict(selected=selected,source=a.component,all_other_addresses_preserved=True,native_builtin_resolution='NOT_TESTED',dependency_entries_reused=reuse,shader_compatibility_proof=proof)
 (out/'registry.json').write_text(json.dumps(base,indent=2)+'\n');print('Exact Rocket resource keys registered in isolated runtime locator; other runtime selections preserved.')
if __name__=='__main__':main()
