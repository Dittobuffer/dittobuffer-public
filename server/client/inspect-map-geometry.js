// Opt-in development diagnostics. No renderer/material/camera mutation on capture.
// Commands and large geometry are private files, not truncated Android log lines.
(()=>{
 const dir='/data/user/0/org.librenlp.pokemongo.dev/files/';
 const unbox=api('object_unbox','pointer',['pointer']);
 const str=o=>o.isNull()?'':o.add(20).readUtf16String(o.add(16).readS32());
 const prop=(o,n)=>call(method(getClass(o),'get_'+n,0),o);
 const floats=(p,n)=>Array.from({length:n},(_,i)=>p.add(i*4).readFloat());
 const value=(o,n,size)=>floats(unbox(prop(o,n)),size);
 const scalar=n=>{const p=Memory.alloc(4);p.writeS32(n);return p;};
 const type=k=>api('type_get_object','pointer',['pointer'])(api('class_get_type','pointer',['pointer'])(k));
 const objects=k=>{
  const a=call(method(klass('UnityEngine','Resources'),'FindObjectsOfTypeAll',1),ptr(0),[type(k)]);
  return Array.from({length:a.add(24).readU64().toNumber()},(_,i)=>a.add(32+i*8).readPointer());
 };
 const array=(a,stride,integers=false)=>{
  if(a.isNull())return [];
  const n=a.add(24).readU64().toNumber();if(n>300000)throw Error('Diagnostic array budget exceeded');
  return Array.from({length:n},(_,i)=>integers?a.add(32+i*4).readS32():floats(a.add(32+i*stride*4),stride));
 };
 const path=t=>{const names=[];for(let i=0;!t.isNull()&&i<12;i++,t=prop(t,'parent'))names.unshift(str(prop(t,'name')));return names.join('/');};
 const cname=o=>api('class_get_name','pointer',['pointer'])(getClass(o)).readCString();
 const overload=(k,n,param)=>{
  const iter=Memory.alloc(8);iter.writePointer(ptr(0));let m;
  const next=api('class_get_methods','pointer',['pointer','pointer']);
  while(!(m=next(k,iter)).isNull()){
   if(api('method_get_name','pointer',['pointer'])(m).readCString()!==n||api('method_get_param_count','uint',['pointer'])(m)!==1)continue;
   const t=api('method_get_param','pointer',['pointer','uint'])(m,0);
   if(api('type_get_name','pointer',['pointer'])(t).readCString()===param)return m;
  }
  throw Error('Diagnostic overload missing '+n+' '+param);
 };
 function material(m){
  if(m.isNull())return null;
  const shader=prop(m,'shader'),out={id:m.toString(),name:str(prop(m,'name')),shader:str(prop(shader,'name')),queue:unbox(prop(m,'renderQueue')).readS32(),properties:{}};
  for(const n of ['_ZWrite','_ZTest','_Cull','_Offset','_OffsetFactor','_OffsetUnits','_StencilRef','_StencilComp']){
   const s=stringNew(Memory.allocUtf8String(n));
   try{if(unbox(call(overload(getClass(m),'HasProperty','System.String'),m,[s])).readU8())out.properties[n]=unbox(call(overload(getClass(m),'GetFloat','System.String'),m,[s])).readFloat();}catch(e){out.propertyError=e.message;break;}
  }
  return out;
 }
 let frame=0,last='',deltas=[],hidden=[];
 function snapshot(command){
  const started=Date.now(),out={at:started,label:command.id,fixture:{...globalThis.LIBRE_GEOGRAPHIC_FIXTURE},cameras:[],tiles:[],meshes:[],builders:[],frames:deltas.splice(0),errors:[]};
  for(const c of objects(klass('UnityEngine','Camera'))){
   const t=prop(c,'transform');if(!unbox(prop(prop(c,'gameObject'),'activeInHierarchy')).readU8()||!unbox(prop(c,'enabled')).readU8())continue;
   out.cameras.push({name:str(prop(c,'name')),path:path(t),position:value(t,'position',3),rotation:value(t,'rotation',4),near:value(c,'nearClipPlane',1)[0],far:value(c,'farClipPlane',1)[0],fov:value(c,'fieldOfView',1)[0],projection:value(c,'projectionMatrix',16),view:value(c,'worldToCameraMatrix',16)});
  }
  try{for(const tile of objects(klass('Niantic.Holoholo.Map','GameMapTile'))){
   if(!unbox(prop(prop(tile,'gameObject'),'activeInHierarchy')).readU8())continue;
   const id=tile.add(0x100).readU64(),arg=Memory.alloc(8);arg.writeU64(id);
   const coord=call(method(klass('Niantic.Lightship.Maps.Core.Coordinates','TileCoord'),'FromIndex',1),ptr(0),[arg]);
   out.tiles.push({id:id.toString(),coord:[0x10,0x14,0x18].map(n=>coord.add(n).readS32()),origin:[0x150,0x158,0x160].map(n=>tile.add(n).readDouble()),size:tile.add(0x168).readDouble(),position:value(prop(tile,'transform'),'position',3),scale:value(prop(tile,'transform'),'lossyScale',3)});
  }}catch(e){out.errors.push('Tiles: '+e.message);}
  let vertices=0;
  for(const r of objects(klass('UnityEngine','MeshRenderer'))){
   const go=prop(r,'gameObject'),t=prop(r,'transform'),name=str(prop(r,'name'));
   const objectPath=path(t);
   if(!objectPath.startsWith('WorldMapRenderer(Clone)/MapTile(Clone)/')||!unbox(prop(go,'activeInHierarchy')).readU8())continue;
   const row={name,path:objectPath,enabled:!!unbox(prop(r,'enabled')).readU8(),bounds:value(r,'bounds',6),position:value(t,'position',3),scale:value(t,'lossyScale',3),matrix:value(t,'localToWorldMatrix',16),materials:[]};
   try{
    const materials=prop(r,'sharedMaterials');for(let i=0;i<materials.add(24).readU64().toNumber();i++)row.materials.push(material(materials.add(32+i*8).readPointer()));
    const filter=call(overload(klass('UnityEngine','GameObject'),'GetComponent','System.Type'),go,[type(klass('UnityEngine','MeshFilter'))]);
    if(!filter.isNull()){
     const mesh=prop(filter,'sharedMesh');if(mesh.isNull()){row.empty=true;out.meshes.push(row);continue;}row.meshName=str(prop(mesh,'name'));row.vertexCount=unbox(prop(mesh,'vertexCount')).readS32();row.readable=!!unbox(prop(mesh,'isReadable')).readU8();
     if(row.readable&&name!=='BuildingDepth'&&vertices+row.vertexCount<400000){
      row.vertices=array(prop(mesh,'vertices'),3);row.triangles=array(prop(mesh,'triangles'),1,true);row.uv=array(prop(mesh,'uv'),2);vertices+=row.vertexCount;
     }
    }
   }catch(e){row.error=e.message;}
   out.meshes.push(row);if(out.meshes.length>=600){out.errors.push('Renderer budget reached');break;}
  }
  for(const [ns,name] of [['Niantic.Holoholo.Map.Builders','LandTileBuilderBase'],['Niantic.Holoholo.Map.Builders.Buildings','BuildingBuilder']]){
   try{for(const b of objects(klass(ns,name)))out.builders.push({name:cname(b),path:path(prop(b,'transform')),height:name==='BuildingBuilder'?b.add(0x40).readFloat():null});}catch(e){out.errors.push(e.message);}
  }
  out.capture_ms=Date.now()-started;File.writeAllText(dir+'dittobuffer-map-'+command.id+'.json',JSON.stringify(out));
  console.log('Map geometry capture '+command.id+': '+out.meshes.length+' meshes, '+vertices+' vertices, '+out.capture_ms+'ms');
 }
 Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
  frame++;try{deltas.push(unbox(call(method(klass('UnityEngine','Time'),'get_unscaledDeltaTime',0),ptr(0))).readFloat());if(deltas.length>600)deltas.shift();}catch(e){}
  if(frame%60)return;
  try{
   const command=JSON.parse(File.readAllText(dir+'dittobuffer-map-command.json'));
   if(!/^[a-z0-9_-]{1,60}$/.test(command.id)||command.id===last)return;last=command.id;
   if(command.action==='snapshot')snapshot(command);
   // Temporary, explicitly requested layer isolation; cold restart restores all.
   else if(command.action==='isolate'){
    for(const [r,enabled] of hidden.splice(0))try{call(method(getClass(r),'set_enabled',1),r,[scalar(enabled)]);}catch(e){}
    if(command.keep)for(const r of objects(klass('UnityEngine','MeshRenderer'))){
     const name=str(prop(r,'name'));if(!path(prop(r,'transform')).startsWith('WorldMapRenderer(Clone)/MapTile(Clone)/'))continue;
     const enabled=unbox(prop(r,'enabled')).readU8();hidden.push([r,enabled]);
     call(method(getClass(r),'set_enabled',1),r,[scalar(command.keep.some(n=>name.toLowerCase().includes(n))?enabled:0)]);
    }
    snapshot(command);
   }
  }catch(e){if(!/no such file|not found/i.test(String(e)))console.log('Map capture unavailable: '+e.message);}
 }});
 console.log('Opt-in private map geometry diagnostics active');
})();
