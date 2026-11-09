"""Experimental offline Assimp scene -> native Pokemon prefab conversion.

Component/serialization layouts come from a native accepted 2021 bundle. All
geometry, bones, bind poses, pixels and motion come from the identified source
scene. This is NOT a Unity Editor substitute or Android compatibility claim.
Fresh isolated builds outputs only; never changes gameplay or source bundles.
"""
import argparse,collections,copy,hashlib,json,math,re,struct,sys,zlib
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from dittobuffer.core.asset_catalog import Catalog
from pokemon_asset_import import write_manifest

def ptr(value=0):return dict(m_FileID=0,m_PathID=value)
def vec(values):return dict(zip('xyzw',map(float,values)))
def decompose(matrix):
    m=np.array(matrix,dtype=float).reshape(4,4);scale=np.linalg.norm(m[:3,:3],axis=0);r=m[:3,:3]/scale
    # Stable matrix -> xyzw quaternion; reject sheared source transforms.
    if not np.allclose(r.T@r,np.eye(3),atol=2e-4):raise ValueError('Sheared node needs explicit conversion')
    k=np.array([[r[0,0]-r[1,1]-r[2,2],r[1,0]+r[0,1],r[2,0]+r[0,2],r[2,1]-r[1,2]],
      [r[1,0]+r[0,1],r[1,1]-r[0,0]-r[2,2],r[2,1]+r[1,2],r[0,2]-r[2,0]],
      [r[2,0]+r[0,2],r[2,1]+r[1,2],r[2,2]-r[0,0]-r[1,1],r[1,0]-r[0,1]],
      [r[2,1]-r[1,2],r[0,2]-r[2,0],r[1,0]-r[0,1],r.trace()]])/3
    vals,qs=np.linalg.eigh(k);q=qs[:,np.argmax(vals)]
    if q[3]<0:q=-q
    return vec(m[:3,3]),vec(q),vec(scale)

def sample(keys,t,quaternion=False):
    if not keys:raise ValueError('Missing source animation channel')
    if t<=keys[0][0]:a=b=np.array(keys[0][1],float);u=0
    elif t>=keys[-1][0]:a=b=np.array(keys[-1][1],float);u=0
    else:
        right=next(i for i,v in enumerate(keys) if v[0]>=t);ta,a=keys[right-1];tb,b=keys[right];a=np.array(a,float);b=np.array(b,float);u=(t-ta)/(tb-ta)
    if quaternion:
        a=a/np.linalg.norm(a);b=b/np.linalg.norm(b);dot=float(a@b)
        if dot<0:b=-b;dot=-dot
        if dot<.9995:
            angle=math.acos(min(1,dot));q=(math.sin((1-u)*angle)*a+math.sin(u*angle)*b)/math.sin(angle)
        else:q=a+(b-a)*u;q/=np.linalg.norm(q)
        return [float(v) for v in q[[1,2,3,0]]]
    return (a+(b-a)*u).tolist()

def aabb(vertices):
    v=np.array(vertices).reshape(-1,3);lo=v.min(axis=0);hi=v.max(axis=0)
    return dict(m_Center=vec((lo+hi)/2),m_Extent=vec((hi-lo)/2))

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--recipe',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    recipe_path=(ROOT/a.recipe).resolve();output=(ROOT/a.output).resolve()
    if not recipe_path.is_relative_to(ROOT/'builds') or not output.is_relative_to(ROOT/'builds') or output.exists():raise SystemExit('Explicit builds recipe and fresh builds output required')
    recipe=json.loads(recipe_path.read_text());species=recipe['species'];address=recipe.get('address','pm'+str(species))
    if not 1<=species<=151:raise ValueError('Kanto visual conversion scope only')
    if not re.fullmatch('pm'+str(species)+r'(?:\.[A-Za-z0-9_]+)*',address) or re.search('DYNAMAX|GIGANTAMAX|GMAX|BREAD',address,re.I):raise ValueError('Exact same-species permitted visual address required')
    for source in recipe['sources']:
        p=(ROOT/source['file']).resolve()
        if not p.is_relative_to(ROOT/'builds') or hashlib.sha256(p.read_bytes()).hexdigest()!=source['sha256']:raise ValueError('Pinned source changed')
    scene=json.loads((ROOT/recipe['scene']).read_text());source_by_name={Path(v['file']).name:ROOT/v['file'] for v in recipe['sources']}
    import UnityPy
    from UnityPy.helpers.TypeTreeHelper import write_typetree
    from UnityPy.streams import EndianBinaryWriter,EndianBinaryReader
    manifest=json.loads((ROOT/recipe['template_manifest']).read_text());catalog=Catalog(json.loads((ROOT/manifest['local_catalog']).read_text()))
    variant=next(v for v in manifest['variants'] if v['address']==recipe['template_address']);rows={r['index']:r for r in manifest['bundles']}
    owner_index=next(i for i in variant['bundle_indices'] if any(v['path'] in variant['model_paths'] for v in rows[i]['assets']));owner=rows[owner_index]
    source_bytes=(ROOT/manifest['content'][owner['sha256']]['file']).read_bytes()
    if hashlib.sha256(source_bytes).hexdigest()!=owner['sha256']:raise ValueError('Serialization template changed')
    env=UnityPy.load(source_bytes);bundle=next(iter(env.files.values()));objects={o.path_id:o for o in env.objects};sf=next(iter(objects.values())).assets_file
    templates={typ:next(o for o in objects.values() if o.type.name==typ) for typ in ('GameObject','Transform','Animator','SkinnedMeshRenderer','Mesh','Texture2D','Material','AnimationClip','AnimatorOverrideController','AssetBundle')}
    model_template=next(o for o in objects.values() if o.type.name=='MonoBehaviour' and 'ViewID' in o.read_typetree())
    animation_template=next(o for o in objects.values() if o.type.name=='MonoBehaviour' and '_jumpClipCount' in o.read_typetree())
    # Keep scripts only, never retain template species meshes/textures/animations.
    kept={i:o for i,o in objects.items() if o.type.name=='MonoScript'};built={};next_id=100000
    def create(template,data):
        nonlocal next_id
        next_id+=1;o=copy.copy(template);o.path_id=next_id;o.save_typetree(data);built[next_id]=o;return next_id
    def clone(typ,data=None):
        o=templates[typ];return create(o,copy.deepcopy(o.read_typetree()) if data is None else data)
    def change(i,data):built[i].save_typetree(data)
    transform_data={};game_data={};node_refs={};paths={}
    rigid_indices=set(recipe.get('rigid_source_mesh_indices',[]));rigid_nodes={}
    def find_rigid_nodes(node):
        for index in node.get('meshes',[]):
            if index in rigid_indices:
                if index in rigid_nodes:raise ValueError('Instanced rigid source mesh')
                rigid_nodes[index]=node['name']
        for child in node.get('children',[]):find_rigid_nodes(child)
    find_rigid_nodes(scene['rootnode'])
    if rigid_indices!=set(rigid_nodes):raise ValueError('Explicit rigid source mesh absent')
    required_names={b['name'] for m in scene['meshes'] for b in m.get('bones',[])}|{c['name'] for an in scene.get('animations',[]) for c in an['channels']}|set(recipe['sockets'].values())|set(rigid_nodes.values())
    def add_node(name,matrix,parent=0,path=''):
        gd=copy.deepcopy(templates['GameObject'].read_typetree());gd.update(m_Name=name,m_Component=[],m_IsActive=True,m_Layer=0)
        go=clone('GameObject',gd);td=copy.deepcopy(templates['Transform'].read_typetree());pos,rot,scale=decompose(matrix)
        td.update(m_GameObject=ptr(go),m_LocalPosition=pos,m_LocalRotation=rot,m_LocalScale=scale,m_Children=[],m_Father=ptr(parent));tr=clone('Transform',td)
        gd['m_Component']=[dict(component=ptr(tr))];game_data[go]=gd;transform_data[tr]=td
        if parent:transform_data[parent]['m_Children'].append(ptr(tr))
        if name in node_refs:
            if name in required_names:raise ValueError('Ambiguous bound/animated node name '+name)
        else:node_refs[name]=(go,tr);paths[name]=path
        return go,tr
    identity=np.eye(4).reshape(-1).tolist();root_go,root_tr=add_node(address,identity)
    def visit(node,parent,path):
        name=node['name'];go,tr=add_node(name,node['transformation'],parent,path)
        for child in node.get('children',[]):visit(child,tr,path+'/'+child['name'] if path else child['name'])
    root=scene['rootnode']
    # Recipe explicitly selects an identity wrapper to omit; no inferred scaling.
    def find(node,name):
        if node['name']==name:return node
        for child in node.get('children',[]):
            found=find(child,name)
            if found:return found
    rig=find(root,recipe['rig_node'])
    if not rig:raise ValueError('Missing rig root')
    visit(rig,root_tr,'');rig_go,rig_tr=node_refs[recipe['rig_node']]
    meshes=copy.deepcopy(scene['meshes']);bone_names=[];bindposes={};mesh_transforms={}
    def collect_meshes(node,parent):
        world=parent@np.array(node['transformation']).reshape(4,4)
        for index in node.get('meshes',[]):
            if index in mesh_transforms:raise ValueError('Instanced mesh needs explicit conversion')
            mesh_transforms[index]=world
        for child in node.get('children',[]):collect_meshes(child,world)
    for child in rig.get('children',[]):collect_meshes(child,np.eye(4))
    if set(mesh_transforms)!=set(range(len(meshes))):raise ValueError('All meshes must belong to the selected rig')
    excluded=set(recipe.get('exclude_source_mesh_indices',[]))
    if any(type(i) is not int or not 0<=i<len(meshes) for i in excluded):raise ValueError('Invalid explicitly omitted source mesh index')
    for name in recipe.get('exclude_mesh_nodes',[]):
        candidate=find(rig,name)
        if not candidate or not candidate.get('meshes'):raise ValueError('Explicit omitted source mesh is absent: '+name)
        excluded.update(candidate['meshes'])
    for index,mesh in enumerate(meshes):
        if index in excluded:continue
        if not mesh.get('bones'):
            if index not in rigid_nodes:raise ValueError('Unskinned source mesh requires explicit rigid-node binding')
            mesh['bones']=[dict(name=rigid_nodes[index],offsetmatrix=np.eye(4).reshape(-1).tolist(),weights=[[i,1.] for i in range(len(mesh['vertices'])//3)])]
        if not mesh.get('texturecoords'):
            props=scene['materials'][mesh['materialindex']]['properties']
            if any(p['key']=='$tex.file' and p['semantic']==1 for p in props):raise ValueError('Textured geometry has no source UVs')
            mesh['texturecoords']=[[0.]*(len(mesh['vertices'])//3*2)];mesh['numuvcomponents']=[2]
        if not mesh.get('tangents'):
            # Derive a geometric tangent basis where the FBX omits it. No normal
            # map is added; source positions, normals and pixels remain exact.
            normal=np.array(mesh['normals']).reshape(-1,3);axis=np.tile([1.,0.,0.],(len(normal),1));axis[abs(normal[:,0])>.9]=[0.,1.,0.]
            tangent=np.cross(normal,axis);tangent/=np.linalg.norm(tangent,axis=1)[:,None];mesh['tangents']=tangent.reshape(-1).tolist();mesh['bitangents']=np.cross(normal,tangent).reshape(-1).tolist()
        matrix=mesh_transforms[index];inverse=np.linalg.inv(matrix);rotation=matrix[:3,:3]
        vertices=np.array(mesh['vertices']).reshape(-1,3);mesh['vertices']=((np.c_[vertices,np.ones(len(vertices))]@matrix.T)[:,:3]).reshape(-1).tolist()
        for key in ('normals','tangents','bitangents'):
            values=np.array(mesh[key]).reshape(-1,3);values=values@(inverse[:3,:3] if key=='normals' else rotation.T);values/=np.linalg.norm(values,axis=1)[:,None];mesh[key]=values.reshape(-1).tolist()
        for bone in mesh['bones']:bone['offsetmatrix']=(np.array(bone['offsetmatrix']).reshape(4,4)@inverse).reshape(-1).tolist()
        if np.linalg.det(rotation)<0:mesh['faces']=[list(reversed(f)) for f in mesh['faces']]
    # FBX exports of the GO face shader encode eye/mouth texture selection in
    # integer V tiles. Explicit recipe maps these to the original face pixels.
    # Split triangles without changing vertex positions or skinning.
    split=[]
    for source_index,mesh in enumerate(meshes):
        if source_index in excluded:continue
        props=scene['materials'][mesh['materialindex']]['properties'];material_name=next(p['value'] for p in props if p['key']=='?mat.name');main_texture=next((p['value'] for p in props if p['key']=='$tex.file' and p['semantic']==1),'')
        if material_name in recipe.get('exclude_materials',[]):continue
        mappings=recipe.get('uv_tile_textures',{}) if material_name in recipe.get('face_materials',['Eye','Face']) and 'Body' in main_texture else {}
        # Some exports encode face tiles on an already separate eye material.
        # Require an exact material mapping rather than wrapping every UV: body
        # textures can legitimately use coordinates outside the first tile.
        mappings=recipe.get('uv_tile_materials',{}).get(material_name,mappings)
        groups=collections.defaultdict(list);uv=np.array(mesh['texturecoords'][0]).reshape(-1,2)
        for face in mesh['faces']:
            mean=uv[face].mean(axis=0);tile=tuple(int(math.floor(v)) for v in mean);key=','.join(map(str,tile));key=key if key in mappings else str(tile[1]) if str(tile[1]) in mappings else None
            if key is not None:
                for axis in ([0,1] if ',' in key else [1]):
                    if any(not tile[axis]-1e-4<=uv[i,axis]<=tile[axis]+1+1e-4 for i in face):raise ValueError('Triangle crosses mapped face UV tiles')
            groups[key].append(face)
        for tile,faces in groups.items():
            part=copy.deepcopy(mesh);used=sorted({i for f in faces for i in f});remap={v:i for i,v in enumerate(used)}
            for key,width in [('vertices',3),('normals',3),('tangents',3),('bitangents',3)]:part[key]=np.array(mesh[key]).reshape(-1,width)[used].reshape(-1).tolist()
            part['colors']=[np.array(c).reshape(-1,4)[used].reshape(-1).tolist() for c in mesh.get('colors',[])]
            part['texturecoords']=[np.array(c).reshape(-1,n)[used].reshape(-1).tolist() for c,n in zip(mesh['texturecoords'],mesh['numuvcomponents'])]
            if tile is not None:
                values=np.array(part['texturecoords'][0]).reshape(-1,2)
                if ',' in tile:values-=np.array([int(v) for v in tile.split(',')])
                else:values[:,1]-=int(tile)
                part['texturecoords'][0]=values.reshape(-1).tolist()
                material=copy.deepcopy(scene['materials'][mesh['materialindex']])
                for prop in material['properties']:
                    if prop['key']=='$tex.file' and prop['semantic']==1:prop['value']=mappings[tile]
                    if prop['key']=='?mat.name':prop['value']+=' SourceFaceTile'+tile
                part['materialindex']=len(scene['materials']);scene['materials'].append(material)
            part['bones']=[dict(b,weights=[[remap[i],w] for i,w in b['weights'] if i in remap]) for b in mesh['bones']]
            part['faces']=[[remap[i] for i in f] for f in faces];split.append(part)
    meshes=split
    for mesh in meshes:
        for bone in mesh['bones']:
            name=bone['name']
            if name not in node_refs:raise ValueError('Missing source skin bone '+name)
            if name in bindposes and not np.allclose(bindposes[name],bone['offsetmatrix'],atol=1e-5):raise ValueError('Conflicting mesh bind matrices')
            if name not in bone_names:bone_names.append(name)
            bindposes[name]=bone['offsetmatrix']
    verts=[];indices=[];submeshes=[];vertex_bytes=bytearray();source_counts=[]
    for mesh in meshes:
        offset=len(verts)//3;vertices=mesh['vertices'];count=len(vertices)//3;weights=[[] for _ in range(count)]
        for bone in mesh['bones']:
            for vertex,weight in bone['weights']:
                if weight>0:weights[vertex].append((bone_names.index(bone['name']),weight))
        for i in range(count):
            influence=weights[i]
            if not influence or len(influence)>4 or abs(sum(w for _,w in influence)-1)>.005:raise ValueError('Unsupported source skin weights')
            total=sum(w for _,w in influence);influence=sorted(influence,key=lambda v:-v[1]);influence += [(0,0)]*(4-len(influence))
            pos=vertices[i*3:i*3+3];normal=mesh['normals'][i*3:i*3+3];tangent=mesh['tangents'][i*3:i*3+3];bitangent=mesh['bitangents'][i*3:i*3+3]
            sign=1 if np.dot(np.cross(normal,tangent),bitangent)>0 else -1
            uv=mesh['texturecoords'][0][i*2:i*2+2];color=(mesh.get('colors') or [[]])[0][i*4:i*4+4] or [1,1,1,1]
            # Source Assimp coordinates/UVs are retained; front/culling checked on Android.
            vertex_bytes+=struct.pack('<12f4B4f4I',*(pos+normal+tangent+[sign]+uv),*[round(max(0,min(1,c))*255) for c in color],*[w/total for _,w in influence],*[b for b,_ in influence])
        local=[]
        for face in mesh['faces']:
            if len(face)!=3:raise ValueError('Source must contain triangles')
            local.extend(v+offset for v in face)
        submeshes.append(dict(firstByte=len(indices)*2,indexCount=len(local),topology=0,baseVertex=0,firstVertex=offset,vertexCount=count,localAABB=aabb(vertices)))
        indices.extend(local);verts.extend(vertices);source_counts.append(dict(vertices=count,triangles=len(local)//3,material=mesh['materialindex']))
    if max(indices)>=65536:raise ValueError('Prototype only supports UInt16 mesh indices')
    md=copy.deepcopy(templates['Mesh'].read_typetree());bounds=aabb(verts)
    channels=[dict(stream=0,offset=0,format=0,dimension=0) for _ in range(14)]
    for ch,offset,fmt,dim in [(0,0,0,3),(1,12,0,3),(2,24,0,4),(3,48,2,4),(4,40,0,2),(12,52,0,4),(13,68,10,4)]:channels[ch].update(offset=offset,format=fmt,dimension=dim)
    md.update(m_Name=address+'_source_mesh',m_SubMeshes=submeshes,m_Shapes=dict(vertices=[],shapes=[],channels=[],fullWeights=[]),m_BindPose=[{f'e{i}{j}':float(bindposes[n][i*4+j]) for i in range(4) for j in range(4)} for n in bone_names],
      m_BoneNameHashes=[zlib.crc32(n.encode()) for n in bone_names],m_RootBoneNameHash=zlib.crc32(recipe['origin_node'].encode()),m_BonesAABB=[],m_VariableBoneCountWeights=dict(m_Data=[]),
      m_MeshCompression=0,m_IsReadable=True,m_KeepVertices=True,m_KeepIndices=True,m_IndexFormat=0,m_IndexBuffer=list(struct.pack('<'+'H'*len(indices),*indices)),
      m_VertexData=dict(m_VertexCount=len(verts)//3,m_Channels=channels,m_DataSize=bytes(vertex_bytes)),m_LocalAABB=bounds,m_StreamData=dict(offset=0,size=0,path=''),m_BakedConvexCollisionMesh=[],m_BakedTriangleCollisionMesh=[])
    mesh_id=clone('Mesh',md)
    textures={};materials=[];extra_dependencies=set();fire_templates={};shader_changes=[];depth_shader_ids={}
    def no_depth_shader(alpha_blend=False):
        if alpha_blend in depth_shader_ids:return depth_shader_ids[alpha_blend]
        original=templates['Material'].read_typetree()['m_Shader'];external=sf.externals[original['m_FileID']-1];cab=re.search(r'CAB-[0-9a-fA-F]{32}',external.path)[0]
        row=next(rows[i] for i in variant['bundle_indices'] if cab in rows[i]['serialized_files']);shader_env=UnityPy.load(str(ROOT/manifest['content'][row['sha256']]['file']));source=next(o for o in shader_env.objects if o.path_id==original['m_PathID'] and o.type.name=='Shader');d=source.read_typetree()
        if d['m_Dependencies'] or d['m_NonModifiableTextures']:raise ValueError('Depth compatibility shader has unresolved resource references')
        program=hashlib.sha256(bytes(d['compressedBlob'])).hexdigest();d['m_Name']='LibrePrivate/'+('AlphaSurface' if alpha_blend else 'EyeSclera')+'/UBER';d['m_ParsedForm']['m_Name']=d['m_Name']
        # A copied shader must not share its source GUID/cache identity while
        # carrying different fixed pass state. Compiled GPU bytes stay exact.
        identity=hashlib.sha256(recipe_path.read_bytes()+ (b':private-source-alpha-v1' if alpha_blend else b':private-sclera-zwrite-off-v1')).digest()[:16]
        d['m_AssetGUID']={f'data[{i}]':value for i,value in enumerate(struct.unpack('<4I',identity))}
        for sub in d['m_ParsedForm']['m_SubShaders']:
            for ps in sub['m_Passes']:
                state=ps['m_State'];state['zWrite']['val']=0.
                if alpha_blend:
                    for i in range(8):
                        for key,value in [('srcBlend',5.),('destBlend',10.),('srcBlendAlpha',1.),('destBlendAlpha',10.)]:state['rtBlend'+str(i)][key]=dict(val=value,name='<noninit>')
        if hashlib.sha256(bytes(d['compressedBlob'])).hexdigest()!=program:raise ValueError('Compiled shader program changed')
        typ=copy.copy(source.serialized_type);sf.types.append(typ);template=copy.copy(source);template.assets_file=sf;template.type_id=len(sf.types)-1;template.serialized_type=typ
        depth_shader_ids[alpha_blend]=create(template,d);shader_changes.append(dict(source_bundle_sha256=row['sha256'],source_object=str(source.path_id),compiled_program_sha256=program,change='Separate private shader object, same compiled GPU program; ZWrite off, optional explicit source-alpha blending; ZTest/culling preserved',alpha_blend=alpha_blend))
        return depth_shader_ids[alpha_blend]
    if recipe.get('native_fire_materials'):
        fire_variant=next(v for v in manifest['variants'] if v['address']=='pm78');fire_owner=next(i for i in fire_variant['bundle_indices'] if any(v['path'] in fire_variant['model_paths'] for v in rows[i]['assets']))
        fire_env=UnityPy.load(str(ROOT/manifest['content'][rows[fire_owner]['sha256']]['file']));extra_dependencies.update(set(fire_variant['bundle_indices'])-{fire_owner})
        for kind,name in [('stencil','FireStenHead'),('core','FireCoreHead')]:
            obj=next(o for o in fire_env.objects if o.type.name=='Material' and o.read_typetree()['m_Name']==name);d=obj.read_typetree()
            for field in [d['m_Shader']]:
                if field['m_FileID']<=0:raise ValueError('Expected external native fire shader')
                external=obj.assets_file.externals[field['m_FileID']-1];index=next((i for i,e in enumerate(sf.externals) if e.path==external.path),None)
                if index is None:index=len(sf.externals);sf.externals.append(copy.deepcopy(external))
                field['m_FileID']=index+1
            fire_templates[kind]=d
    for material in scene['materials']:
        props=material['properties'];name=next(p['value'] for p in props if p['key']=='?mat.name');file=next((p['value'] for p in props if p['key']=='$tex.file' and p['semantic']==1),None)
        file=recipe.get('texture_overrides',{}).get(file,file)
        if file and file not in source_by_name:raise ValueError('Missing pinned material pixels '+file)
        if file and file not in textures:
            from PIL import Image
            td=copy.deepcopy(templates['Texture2D'].read_typetree());td['m_Name']=Path(file).stem;td['m_StreamData']=dict(offset=0,size=0,path='');tid=clone('Texture2D',td)
            tex=built[tid].read();tex.m_Name=Path(file).stem
            tex.set_image(Image.open(source_by_name[file]).convert('RGBA'),target_format=4);tex.save();textures[file]=tid
        mat=copy.deepcopy(templates['Material'].read_typetree());mat['m_Name']=name;mat['m_ValidKeywords']=[v for v in mat['m_ValidKeywords'] if v not in ('NIANTIC_IRIS_ALPHA_SEPARATE','NIANTIC_UV_ANIMATE')]
        for key,value in mat['m_SavedProperties']['m_TexEnvs']:
            if key=='_MainTex':value['m_Texture']=ptr(textures[file]) if file else ptr()
            elif key in ('_FaceTex','_IrisTex'):value['m_Texture']=ptr()
        mat['m_SavedProperties']['m_Floats']=[(k,0 if k in ('_IsIrisAlphaSeparate','_UVAnimated','NIANTIC_FX','_vAmOcPower','_vAmTxPower') else v) for k,v in mat['m_SavedProperties']['m_Floats']]
        if file and fire_templates and ('FireSten' in file or 'FireCore' in file):
            mat=copy.deepcopy(fire_templates['core' if 'FireCore' in file else 'stencil']);mat['m_Name']=name
            for key,value in mat['m_SavedProperties']['m_TexEnvs']:
                if key!='_MainTex':raise ValueError('Unmapped native fire texture')
                value['m_Texture']=ptr(textures[file])
        native=recipe.get('native_surface_materials',{}).get(name)
        if native:
            # Explicit same-form source properties on the compatible UBER shader.
            # Missing optional FX resources must be named and disabled in the
            # recipe; never silently retain unresolved external texture pointers.
            source_path=ROOT/native['bundle']
            if not any(v['file']==native['bundle'] for v in recipe['sources']):raise ValueError('Unpinned native material source')
            source_env=UnityPy.load(str(source_path));source_obj=next(o for o in source_env.objects if o.path_id==native['object_id'] and o.type.name=='Material');surface=source_obj.read_typetree()
            if surface['m_Name']!=native.get('source_name',name):raise ValueError('Wrong native source material')
            props=copy.deepcopy(surface['m_SavedProperties'])
            for key,value in props['m_TexEnvs']:
                ref=value['m_Texture']
                if not ref['m_PathID']:continue
                if key in native.get('omit_optional_textures',[]):value['m_Texture']=ptr();continue
                if ref['m_FileID']:raise ValueError('Unresolved native surface texture '+key)
                texture=next(o for o in source_env.objects if o.path_id==ref['m_PathID'] and o.type.name=='Texture2D');td=texture.read_typetree();image=texture.read().image
                td['m_StreamData']=dict(offset=0,size=0,path='');tid=clone('Texture2D',td);tex=built[tid].read();tex.set_image(image,target_format=4);tex.save();value['m_Texture']=ptr(tid)
            for group in ('m_Floats','m_Colors','m_TexEnvs'):
                defaults=dict(mat['m_SavedProperties'][group]);defaults.update(props[group]);mat['m_SavedProperties'][group]=list(defaults.items())
            keywords=surface.get('m_ValidKeywords',surface.get('m_ShaderKeywords','').split())
            mat['m_ValidKeywords']=[v for v in keywords if v not in native.get('omit_optional_keywords',[])];mat['m_CustomRenderQueue']=surface['m_CustomRenderQueue']
        neutral=recipe.get('native_untextured_defaults',False)
        if not file and not (neutral is True or isinstance(neutral,list) and name in neutral):
            color=next((p['value'] for p in props if p['key']=='$clr.diffuse'),None)
            if color is None:raise ValueError('Untextured source material has no explicit color')
            mat['m_SavedProperties']['m_Colors']=[(k,dict(zip('rgba',color+[1])) if k=='_Color' else v) for k,v in mat['m_SavedProperties']['m_Colors']]
        settings=recipe.get('material_overrides',{}).get(name,{})
        if 'render_queue' in settings:mat['m_CustomRenderQueue']=int(settings['render_queue'])
        floats=settings.get('floats',{})
        if set(floats)-{k for k,v in mat['m_SavedProperties']['m_Floats']}:raise ValueError('Unknown material property override')
        mat['m_SavedProperties']['m_Floats']=[(k,floats.get(k,v)) for k,v in mat['m_SavedProperties']['m_Floats']]
        colors=settings.get('colors',{})
        if set(colors)-{k for k,v in mat['m_SavedProperties']['m_Colors']}:raise ValueError('Unknown material color override')
        mat['m_SavedProperties']['m_Colors']=[(k,colors.get(k,v)) for k,v in mat['m_SavedProperties']['m_Colors']]
        if name in recipe.get('no_depth_write_materials',[]):mat['m_Shader']=ptr(no_depth_shader())
        if name in recipe.get('alpha_blend_materials',[]):mat['m_Shader']=ptr(no_depth_shader(True))
        materials.append(clone('Material',mat))
    mesh_go,mesh_tr=add_node(address+'_CombinedSourceSkin',identity,rig_tr,address+'_CombinedSourceSkin');rd=copy.deepcopy(templates['SkinnedMeshRenderer'].read_typetree())
    rd.update(m_GameObject=ptr(mesh_go),m_Mesh=ptr(mesh_id),m_Bones=[ptr(node_refs[n][1]) for n in bone_names],m_RootBone=ptr(node_refs[recipe['origin_node']][1]),m_Materials=[ptr(materials[m['materialindex']]) for m in meshes],m_AABB=bounds,m_UpdateWhenOffscreen=True)
    renderer_id=clone('SkinnedMeshRenderer',rd);game_data[mesh_go]['m_Component'].append(dict(component=ptr(renderer_id)))
    animation_ids={};clip_meta=[];clip_template=templates['AnimationClip'];base_clip=clip_template.read_typetree()
    node=next(n for n in clip_template._get_typetree_node().m_Children if n.m_Name=='m_MuscleClip')
    writer=EndianBinaryWriter(endian=clip_template.reader.endian);write_typetree(base_clip['m_MuscleClip'],node,writer,sf);blob_overhead=base_clip['m_MuscleClipSize']-len(writer.bytes)
    for animation in scene['animations']:
        d=copy.deepcopy(base_clip);name=animation['name'];duration=animation['duration']/animation['tickspersecond'];channels=sorted(animation['channels'],key=lambda c:paths[c['name']]);bindings=[]
        for ch in channels:
            for attr in (1,2,3):bindings.append(dict(path=zlib.crc32(paths[ch['name']].encode()),attribute=attr,script=ptr(),typeID=4,customType=0,isPPtrCurve=0))
        frame_count=math.ceil(duration*30)+1;data=[];curve_count=len(channels)*10
        for frame in range(frame_count):
            t=min(duration,frame/30)*animation['tickspersecond']
            for ch in channels:data+=sample(ch['positionkeys'],t)+sample(ch['rotationkeys'],t,True)+sample(ch['scalingkeys'],t)
        muscle=d['m_MuscleClip'];muscle['m_Clip']['data']=dict(m_StreamedClip=dict(data=[],curveCount=0),m_DenseClip=dict(m_FrameCount=frame_count,m_CurveCount=curve_count,m_SampleRate=30.,m_BeginTime=0.,m_SampleArray=data),m_ConstantClip=dict(data=[]))
        muscle.update(m_StartTime=0.,m_StopTime=duration,m_IndexArray=[-1]*200,m_ValueArrayDelta=[dict(m_Start=data[i],m_Stop=data[-curve_count+i]) for i in range(curve_count)],m_ValueArrayReferencePose=[],m_LoopTime=name in recipe['loop_clips'])
        d.update(m_Name=name,m_Legacy=False,m_Compressed=False,m_SampleRate=30.,m_ClipBindingConstant=dict(genericBindings=bindings,pptrCurveMapping=[]),m_Events=[])
        w=EndianBinaryWriter(endian=clip_template.reader.endian);write_typetree(muscle,node,w,sf);d['m_MuscleClipSize']=len(w.bytes)+blob_overhead
        animation_ids[name]=clone('AnimationClip',d);clip_meta.append(dict(name=name,duration=duration,frames=frame_count,curves=curve_count,native_blob_overhead_inferred=blob_overhead))
    override=copy.deepcopy(templates['AnimatorOverrideController'].read_typetree());override['m_Name']=address+'_source_animations';mapping=[]
    for row in override['m_Clips']:
        source_id=row['m_OverrideClip']['m_PathID'];source_name=objects[source_id].read_typetree()['m_Name'] if source_id else ''
        target=recipe['animation_map'].get(source_name,recipe['default_clip']);row['m_OverrideClip']=ptr(animation_ids[target]);mapping.append(dict(controller_clip=source_name,source_motion=target))
    override_id=clone('AnimatorOverrideController',override);anim=copy.deepcopy(templates['Animator'].read_typetree());anim.update(m_GameObject=ptr(rig_go),m_Avatar=ptr(),m_Controller=ptr(override_id),m_CullingMode=0)
    animator_id=clone('Animator',anim);game_data[rig_go]['m_Component'].append(dict(component=ptr(animator_id)))
    model=copy.deepcopy(model_template.read_typetree());model.update(m_GameObject=ptr(root_go),ViewID=species,formId=-1,ViewFBX=ptr(rig_go),animator=ptr(animator_id),Origin=ptr(node_refs[recipe['origin_node']][1]),Waist=ptr(node_refs[recipe['waist_node']][1]),Head=ptr(node_refs[recipe['head_node']][1]),attentionBonesTransforms=[],attentionBonesTransformsIsSet=0)
    for key,name in recipe['sockets'].items():model[key]=ptr(node_refs[name][1])
    model_id=create(model_template,model);game_data[root_go]['m_Component'].append(dict(component=ptr(model_id)))
    driver=copy.deepcopy(animation_template.read_typetree());driver.update(m_GameObject=ptr(root_go),_pokemonModel=ptr(model_id),_animator=ptr(animator_id),_jumpClipCount=0,_eatClipCount=0)
    driver_id=create(animation_template,driver);game_data[root_go]['m_Component'].append(dict(component=ptr(driver_id)))
    for i,d in game_data.items():change(i,d)
    for i,d in transform_data.items():change(i,d)
    ab=copy.deepcopy(templates['AssetBundle'].read_typetree());namespace=hashlib.sha256(recipe_path.read_bytes()+source_bytes+Path(__file__).read_bytes()).hexdigest();ab['m_Name']=namespace[:32]+'.bundle';ab['m_AssetBundleName']=ab['m_Name'];ab['m_MainAsset']=dict(preloadIndex=0,preloadSize=0,asset=ptr())
    refs=[ptr(i) for i in sorted(set(built)|set(kept))];ab['m_PreloadTable']=refs;path='Assets/LibreResearch/'+address+'.prefab';ab['m_Container']=[(path,dict(preloadIndex=0,preloadSize=len(refs),asset=ptr(root_go)))];ab_id=clone('AssetBundle',ab)
    # Unity's native bundle loader locates its metadata at the source root ID.
    # A parseable serialized file with this object moved is rejected on Android.
    ab_object=built.pop(ab_id);ab_object.path_id=templates['AssetBundle'].path_id;built[ab_object.path_id]=ab_object
    sf.objects={**kept,**built};raw=bundle.save(packer='lz4');test=UnityPy.load(raw)
    # Isolate the new serialized file; existing external global CABs stay exact.
    cab_names={o.assets_file.name for o in test.objects};renames={n:'CAB-'+hashlib.sha256((namespace+n).encode()).hexdigest()[:32] for n in cab_names};files={}
    def rename(data):
        for old,new in renames.items():data=data.replace(old.encode(),new.encode())
        return data
    container=next(iter(test.files.values()))
    for name,file in container.files.items():
        if name not in cab_names:continue # All converted textures are inline; drop unrelated source streams.
        data=file.reader.bytes if hasattr(file,'reader') else file.bytes;reader=EndianBinaryReader(rename(data));reader.flags=file.flags;files[rename(name.encode()).decode()]=reader
    container.files=files;converted=container.save(packer='lz4');verify=UnityPy.load(converted);sha=hashlib.sha256(converted).hexdigest();directory=output.with_suffix('');directory.mkdir(exist_ok=False);target=directory/(sha+'.bundle');target.write_bytes(converted)
    nodes=[];indices_map={}
    for i in sorted(set(variant['bundle_indices'])|extra_dependencies):
        b=rows[i];node=dict(**{k:b[k] for k in ('sha256','assets','objects','unity_versions','serialized_files','external_files')},file=manifest['content'][b['sha256']]['file'],options=dict(catalog.options(i)['value']),namespace=b.get('namespace'))
        if i==owner_index:
            node.update(sha256=sha,file=target.relative_to(ROOT).as_posix(),assets=[dict(path=k,type=v.type.name) for k,v in verify.container.items()],objects=dict(collections.Counter(o.type.name for o in verify.objects)),serialized_files=sorted({o.assets_file.name for o in verify.objects}),external_files=[e.path for e in sf.externals],namespace=namespace,source_sha256=owner['sha256'])
            node['options'].update(m_Hash=hashlib.md5(converted).hexdigest(),m_BundleName=namespace[:32],m_BundleSize=len(converted),m_Crc=0,m_UseCrcForCachedBundles=False)
        node['url']='http://127.0.0.1:8080/assets/pokemon/bundles/'+node['sha256']+'.bundle';indices_map[i]=len(nodes);nodes.append(node)
    result=write_manifest(ROOT,output,catalog.document,nodes,[dict(address=address,path=path,type='UnityEngine.GameObject',owner=indices_map[owner_index],bundles=list(indices_map.values()))],catalog.options(owner_index),
      'Experimental identified same-species FBX conversion, public private-research acquisition; all visual payloads replaced with source model data, no other-species fallback',dict(recipe=a.recipe,source_mesh_counts=source_counts,bones=bone_names,clips=clip_meta,animation_mapping=mapping,shader_changes=shader_changes,acceptance='EXPERIMENTAL; NOT ANDROID VERIFIED'))
    print(json.dumps(result['counts']));print('Source vertices',len(verts)//3,'bones',len(bone_names),'clips',len(animation_ids),'converted bytes',len(converted))

if __name__=='__main__':main()
