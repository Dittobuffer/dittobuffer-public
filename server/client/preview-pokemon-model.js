// Explicit isolated-fixture preview. Never installed by start-client.ps1.
// Native HoloCharacter owns loading, instantiation, materials and animation.
(()=>{
 if(globalThis.LIBRE_MODEL_PREVIEW_ALLOWED!==true)throw Error('Explicit isolated preview launcher required');
 const permittedSpecies=new Set(globalThis.LIBRE_MODEL_PREVIEW_SPECIES||Array.from({length:151},(_,i)=>i+1));
 const unbox=api('object_unbox','pointer',['pointer']);
 const typeObject=api('type_get_object','pointer',['pointer']);
 const classType=api('class_get_type','pointer',['pointer']);
 const find=new NativeFunction(mod.base.add(0xA454928),'pointer',['pointer','bool','pointer']);
 const text=o=>o.isNull()?null:o.add(20).readUtf16String(o.add(16).readS32());
 const value=(o,n)=>call(method(getClass(o),'get_'+n,0),o);
 const name=o=>{try{return o.isNull()?null:text(value(o,'name'));}catch(e){return null;}};
 function locateClass(n){
  for(const ns of ['','Niantic.Holoholo','Niantic.Holoholo.Assets.Character','Niantic.PokemonAssets'])try{return klass(ns,n);}catch(e){}
  throw Error('Preview class namespace unresolved: '+n);
 }
 const holoClass=locateClass('HoloCharacter');
 const objects=k=>{const a=find(typeObject(classType(k)),0,ptr(0)),out=[];for(let i=0;i<a.add(24).readU64().toNumber();i++)out.push(a.add(32+i*8).readPointer());return out;};
 function path(o){let t=value(o,'transform'),parts=[];for(let i=0;i<18&&!t.isNull();i++){parts.push(name(t));t=value(t,'parent');}return parts.reverse().join('/');}
 function describe(o){
  const asset=o.add(0x230).readPointer(),model=o.add(0x248).readPointer();
  const out={object:String(o),path:path(o),ready:false,loading:true,
   species:o.add(0x23C).readS32(),asset:String(asset),assetName:name(asset),model:String(model)};
  try{out.ready=!!unbox(value(o,'Ready')).readU8();out.loading=!!unbox(value(o,'IsLoading')).readU8();}catch(e){out.transition=String(e);}
  if(!asset.isNull())try{out.prefabLocalScale=vec(unbox(value(value(asset,'transform'),'localScale')));}catch(e){}
  if(!model.isNull()){
   out.modelName=name(model);out.viewId=model.add(0x20).readS32();out.formId=model.add(0x24).readS32();
   out.gender=model.add(0x48).readS32();out.shiny=!!model.add(0x58).readU8();out.costume=text(model.add(0x68).readPointer());
   const controller=model.add(0xA0).readPointer();
   if(!controller.isNull())try{out.normalizedTime=unbox(value(controller,'CurrentAnimationsNormalizedTime')).readFloat();out.animationContext=unbox(value(controller,'CurrentContext')).readS32();}catch(e){out.animationError=String(e);}
  }
  return out;
 }
 const scalar=(o,n,t='readFloat')=>unbox(value(o,n))[t]();
 const vec=o=>[o.readFloat(),o.add(4).readFloat(),o.add(8).readFloat()];
 const buffer=(numbers)=>{const b=Memory.alloc(numbers.length*4);numbers.forEach((v,i)=>b.add(i*4).writeFloat(v));return b;};
 const integer=n=>{const p=Memory.alloc(4);p.writeS32(n);return p;};
 function set(o,n,v){call(method(getClass(o),'set_'+n,1),o,[v]);}
 const array=a=>{const out=[];if(!a.isNull())for(let i=0;i<a.add(24).readU64().toNumber();i++)out.push(a.add(32+i*8).readPointer());return out;};
 const renderers=asset=>{const t=value(asset,'transform');return objects(klass('UnityEngine','Renderer')).filter(r=>!!unbox(call(method(getClass(value(r,'transform')),'IsChildOf',1),value(r,'transform'),[t])).readU8());};
 let pending=[],selected=null,stage=ptr(0),camera=ptr(0),canvases=[],errors=[],slots=[];
 Interceptor.attach(mod.base.add(0x7547FE4),{onEnter(args){try{const s=text(args[0]);if(s)errors.push({at:Date.now(),message:s.replace(/(?:token|authorization)[=: ]+[^\s,]+/gi,'[credential omitted]').slice(0,3000)});if(errors.length>100)errors.shift();}catch(e){}}});
 function detail(){
  if(!selected)throw Error('No preview stage');
  const out=describe(selected),asset=selected.add(0x230).readPointer();out.renderers=[];
  if(asset.isNull()||!out.ready)return out;
  for(const r of renderers(asset)){
   const bounds=unbox(value(r,'bounds')),entry={name:name(r),type:api('class_get_name','pointer',['pointer'])(getClass(r)).readCString(),
    enabled:!!scalar(r,'enabled','readU8'),visible:!!scalar(r,'isVisible','readU8'),center:vec(bounds),extents:vec(bounds.add(12)),materials:[]};
   try{
    const mesh=value(r,'sharedMesh');entry.mesh=name(mesh);entry.vertices=scalar(mesh,'vertexCount','readS32');
    const bones=array(value(r,'bones'));entry.bones=bones.length;entry.nullBones=bones.filter(b=>b.isNull()||name(b)===null).length;
    entry.boneSamples=bones.filter(b=>!b.isNull()).slice(0,8).map(b=>{const q=unbox(value(b,'localRotation'));return {name:name(b),position:vec(unbox(value(b,'localPosition'))),rotation:[...vec(q),q.add(12).readFloat()]};});
   }catch(e){entry.meshDiagnostic=String(e);}
   for(const material of array(value(r,'sharedMaterials'))){
    if(material.isNull()){entry.materials.push({missing:true});continue;}
    const m={name:name(material),shader:name(value(material,'shader')),textures:[]};
    try{
     const props=array(call(method(getClass(material),'GetTexturePropertyNames',0),material));
     for(const key of props){const texture=call(method(getClass(material),'GetTexture',1),material,[key]);
      if(!texture.isNull())m.textures.push({property:text(key),name:name(texture),width:scalar(texture,'width','readS32'),height:scalar(texture,'height','readS32')});}
    }catch(e){m.textureDiagnostic=String(e);}
    entry.materials.push(m);
   }
   out.renderers.push(entry);
  }
  const animator=value(selected,'Animator');out.animator={object:String(animator)};
  if(!animator.isNull())try{
   out.animator.initialized=!!scalar(animator,'isInitialized','readU8');out.animator.enabled=!!scalar(animator,'enabled','readU8');
   const controller=value(animator,'runtimeAnimatorController');out.animator.controller=name(controller);
   out.animator.clips=array(value(controller,'animationClips')).map(clip=>({name:name(clip),length:scalar(clip,'length')}));
  }catch(e){out.animator.error=String(e);}
  return out;
 }
 function clearBaked(tracker){
  if(!tracker?.bakedMeshes)return;
  for(const mesh of tracker.bakedMeshes.values())call(method(klass('UnityEngine','Object'),'Destroy',2),ptr(0),[mesh,buffer([0])]);
  tracker.bakedMeshes.clear();
 }
 function framingBounds(renderer,tracker){
  const original=unbox(value(renderer,'bounds'));
  if(!tracker?.bakedBounds||api('class_get_name','pointer',['pointer'])(getClass(renderer)).readCString()!=='SkinnedMeshRenderer')return {center:vec(original),extents:vec(original.add(12))};
  // Diagnostic camera only: bake to a new temporary mesh, never edit the source.
  tracker.bakedMeshes ||= new Map();const key=String(renderer);let mesh=tracker.bakedMeshes.get(key);
  if(!mesh){mesh=objectNew(klass('UnityEngine','Mesh'));call(method(getClass(mesh),'.ctor',0),mesh);tracker.bakedMeshes.set(key,mesh);}
  call(method(getClass(renderer),'BakeMesh',1),renderer,[mesh]);
  call(method(getClass(mesh),'RecalculateBounds',0),mesh);
  const bounds=unbox(value(mesh,'bounds')),c=vec(bounds),e=vec(bounds.add(12)),t=value(renderer,'transform'),low=[Infinity,Infinity,Infinity],high=[-Infinity,-Infinity,-Infinity];
  for(let corner=0;corner<8;corner++){
   const local=c.map((v,i)=>v+e[i]*((corner>>i)&1?1:-1));
   const world=vec(unbox(call(method(getClass(t),'TransformPoint',1),t,[buffer(local)])));
   world.forEach((v,i)=>{low[i]=Math.min(low[i],v);high[i]=Math.max(high[i],v);});
  }
  return {center:low.map((v,i)=>(v+high[i])/2),extents:low.map((v,i)=>(high[i]-v)/2)};
 }
 function frame(degrees=0,cached=null,tracker=null){
  const asset=selected.add(0x230).readPointer(),rs=cached||renderers(asset).filter(r=>scalar(r,'enabled','readU8'));
  let low=[Infinity,Infinity,Infinity],high=[-Infinity,-Infinity,-Infinity];
  for(const r of rs){if(!cached){const go=value(r,'gameObject');set(go,'layer',integer(31));}
   if(!['SkinnedMeshRenderer','MeshRenderer'].includes(api('class_get_name','pointer',['pointer'])(getClass(r)).readCString()))continue;
   const b=framingBounds(r,tracker),c=b.center,e=b.extents;for(let i=0;i<3;i++){low[i]=Math.min(low[i],c[i]-e[i]);high[i]=Math.max(high[i],c[i]+e[i]);}}
  if(!Number.isFinite(low[0]))throw Error('No skinned renderer bounds for preview framing');
  const center=low.map((v,i)=>(v+high[i])/2),extents=low.map((v,i)=>(high[i]-v)/2);
  const aspect=scalar(camera,'aspect'),angle=degrees*Math.PI/180,elevation=(tracker?.elevation||0)*Math.PI/180;
  const horizontal=Math.abs(Math.cos(angle))*extents[0]+Math.abs(Math.sin(angle))*extents[2];
  const depth=Math.abs(Math.sin(angle))*extents[0]+Math.abs(Math.cos(angle))*extents[2];
  let size=Math.max(Math.cos(elevation)*extents[1]+Math.abs(Math.sin(elevation))*depth,horizontal/aspect)*(tracker?.margin||1.3);
  if(tracker){tracker.maxSize=Math.max(tracker.maxSize||0,size);size=tracker.maxSize;}
  set(camera,'orthographicSize',buffer([Math.max(.1,size)]));
  set(value(camera,'transform'),'position',buffer([center[0]+Math.sin(angle)*Math.cos(elevation)*25,center[1]+Math.sin(elevation)*25,center[2]+Math.cos(angle)*Math.cos(elevation)*25]));
  const sy=Math.sin((angle+Math.PI)/2),cy=Math.cos((angle+Math.PI)/2),sx=Math.sin(elevation/2),cx=Math.cos(elevation/2);
  set(value(camera,'transform'),'rotation',buffer([cy*sx,sy*cx,-sy*sx,cy*cx]));
  return {center,extents,orthographicSize:Math.max(.1,size),aspect,elevation:tracker?.elevation||0,margin:tracker?.margin||1.3,boundsMode:tracker?.bakedBounds?'temporary-baked-mesh':'renderer'};
 }
 function configure(address,gender){
  const model=selected.add(0x248).readPointer();if(model.isNull())throw Error('Preview model not ready');
  set(model,'Gender',integer(gender));set(model,'IsShiny',integer(address.endsWith('.s')?1:0));
  const costume=address.match(/\.c([A-Z0-9_]+)/);set(model,'Costume',stringNew(Memory.allocUtf8String(costume?costume[1]:'')));
  return describe(selected);
 }
 function create(seed){
  if(selected||!stage.isNull())throw Error('Preview stage already exists');
  const found=objects(holoClass).find(o=>String(o)===seed);if(!found)throw Error('Unknown seed HoloCharacter');
  if(!/EncounterPokemon\(Clone\)\/Character$/.test(path(found)))throw Error('Use only the isolated encounter display as factory seed');
  stage=objectNew(klass('UnityEngine','GameObject'));call(method(getClass(stage),'.ctor',1),stage,[stringNew(Memory.allocUtf8String('LibreKantoModelPreview'))]);
  const parent=value(stage,'transform'),factory=found.add(0xC0).readPointer();selected=call(method(getClass(factory),'Create',1),factory,[parent]);
  const cameraObject=objectNew(klass('UnityEngine','GameObject'));call(method(getClass(cameraObject),'.ctor',1),cameraObject,[stringNew(Memory.allocUtf8String('LibreKantoPreviewCamera'))]);
  call(method(getClass(value(cameraObject,'transform')),'SetParent',1),value(cameraObject,'transform'),[parent]);
  camera=call(method(getClass(cameraObject),'AddComponent',1),cameraObject,[typeObject(classType(klass('UnityEngine','Camera')))]);
  set(camera,'cullingMask',integer(-2147483648));set(camera,'clearFlags',integer(2));set(camera,'backgroundColor',buffer([.12,.18,.23,1]));
  set(camera,'depth',buffer([100]));set(camera,'orthographic',integer(1));set(camera,'nearClipPlane',buffer([.01]));set(camera,'farClipPlane',buffer([1000]));
  for(const c of objects(klass('UnityEngine','Canvas'))){if(scalar(c,'enabled','readU8')){canvases.push(c);set(c,'enabled',integer(0));}}
  return {previewCharacter:String(selected),seed,stage:String(stage),camera:String(camera)};
 }
 function cleanup(){
  for(const c of canvases)try{set(c,'enabled',integer(1));}catch(e){}
  canvases=[];
  if(!stage.isNull()){const delay=buffer([0]);call(method(klass('UnityEngine','Object'),'Destroy',2),ptr(0),[stage,delay]);}
  stage=ptr(0);selected=null;return true;
 }
 const snapshots=()=>({at:Date.now(),characters:objects(holoClass).map(describe)});
 function loadNow(address){
   if(!selected)throw Error('Select an isolated preview HoloCharacter first');
   const identity=/^pm([1-9][0-9]*)(?:\.[A-Za-z0-9_]+)*$/.exec(address);
   if(!identity||!permittedSpecies.has(Number(identity[1]))||/GIGANTAMAX|DYNAMAX|ETERNAMAX|GMAX|BREAD|\.fLOAF(?:\.|$)|\.dMax(?:\.|$)/i.test(address))throw Error('Only explicitly permitted fixture model addresses');
   const current=slots.find(s=>s.selected.equals(selected));if(current){clearBaked(current);current.cachedRenderers=null;current.maxSize=0;}
   const cryAddress=(globalThis.LIBRE_MODEL_PREVIEW_CRY_ADDRESSES||{})[address]||address.match(/^pm\d+/)[0]+'.cry';
   const s=stringNew(Memory.allocUtf8String(address)),cry=stringNew(Memory.allocUtf8String(cryAddress));
   call(method(getClass(selected),'LoadPokemon',2),selected,[s,cry]);
   return {at:Date.now(),requested:address,character:String(selected)};
 }
 function slot(index){const s=slots[index];if(!s)throw Error('Unknown preview slot');selected=s.selected;stage=s.stage;camera=s.camera;return s;}
 const previewAnimations={0:'Idle',5:'Jump',6:'Walk',8:'Eat',10:'Happy'};
 function animationController(){
  if(globalThis.LIBRE_MODEL_PREVIEW_ANIMATION_ALLOWED!==true||!slots.length||slots.length>4||!slots.some(s=>s.selected.equals(selected)))throw Error('Explicit bounded preview animation fixture required');
  const model=selected.add(0x248).readPointer();if(model.isNull())throw Error('Preview model not loaded');
  const controller=model.add(0xA0).readPointer();if(controller.isNull())throw Error('No native model animation controller');return controller;
 }
 function animationCatalog(){
  const c=animationController(),k=getClass(c);
  return Object.entries(previewAnimations).map(([id,label])=>{
   const n=Number(id),count=unbox(call(method(k,'GetClipCount',1),c,[integer(n)])).readS32();
   const state=!!unbox(call(method(k,'AnimatorHasState',2),c,[integer(n),integer(0)])).readU8();
   const baseClip=call(method(k,'GetClipForAnimation',2),c,[integer(n),integer(0)]);
   let clip=baseClip;
   const runtime=value(value(selected,'Animator'),'runtimeAnimatorController');
   const effectiveClips=array(value(runtime,'animationClips'));
   if(!baseClip.isNull()&&!effectiveClips.some(p=>p.equals(baseClip))&&api('class_get_name','pointer',['pointer'])(getClass(runtime)).readCString()==='AnimatorOverrideController'){
    clip=call(method(getClass(runtime),'Internal_GetClipByName',2),runtime,[stringNew(Memory.allocUtf8String(name(baseClip))),integer(1)]);
   }
   return {animation:n,label,count,hasState:state,baseClipName:name(baseClip),clipName:name(clip),clipLength:clip.isNull()?null:scalar(clip,'length'),boundToRuntimeController:!clip.isNull()&&effectiveClips.some(p=>p.equals(clip))};
  });
 }
 function playAnimation(n){
  if(!Number.isInteger(n)||!(n in previewAnimations))throw Error('Only named non-combat preview clips permitted');
  const c=animationController(),entry=animationCatalog().find(r=>r.animation===n);
  if(!entry.hasState||entry.count<1||!entry.clipName||!entry.boundToRuntimeController)return {...entry,requested:false,reason:'Native controller has no bound source clip/state'};
  call(method(getClass(c),'PlayAnimationAtNormalizedTime',3),c,[integer(n),buffer([0]),integer(0)]);
  return {...entry,requested:true};
 }
 function gridCreate(seed,count){
   if(globalThis.LIBRE_MODEL_PREVIEW_GRID_ALLOWED!==true||slots.length||!Number.isInteger(count)||count<1||count>16)throw Error('Explicit bounded native grid fixture required');
   const usedLayers=new Set(objects(klass('UnityEngine','Renderer')).map(r=>scalar(value(r,'gameObject'),'layer','readS32')));
   const freeLayers=Array.from({length:31},(_,i)=>31-i).filter(n=>!usedLayers.has(n));
   if(freeLayers.length<count)throw Error('Not enough unused rendering layers for this bounded preview');
   const columns=Math.min(count,count<=8?2:4),rows=Math.ceil(count/columns),out=[];
   for(let i=0;i<count;i++){
     const created=create(seed),column=i%columns,row=Math.floor(i/columns),viewport=[column/columns,1-(row+1)/rows,1/columns,1/rows];
     const layer=freeLayers[i];set(camera,'cullingMask',integer(1<<layer));set(camera,'rect',buffer(viewport));
     const rect=unbox(value(camera,'pixelRect'));out.push({...created,index:i,layer,viewport,pixelRect:[...vec(rect),rect.add(12).readFloat()]});
     slots.push({selected,stage,camera,layer});selected=null;stage=ptr(0);camera=ptr(0);
   }
   slot(0);return {slots:out,columns,rows,screen:[unbox(call(method(klass('UnityEngine','Screen'),'get_width',0),ptr(0))).readS32(),unbox(call(method(klass('UnityEngine','Screen'),'get_height',0),ptr(0))).readS32()]};
 }
 let trackingFrame=-1;const getFrameCount=method(klass('UnityEngine','Time'),'get_frameCount',0);
 Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
  for(const job of pending.splice(0))try{job.resolve(job.run());}catch(e){job.reject(Error(String(e)));}
  if(slots.some(s=>s.cachedRenderers)){
   const currentFrame=unbox(call(getFrameCount,ptr(0))).readS32();
   if(currentFrame!==trackingFrame){trackingFrame=currentFrame;slots.forEach((s,i)=>{if(s.cachedRenderers)try{slot(i);frame(s.degrees||0,s.cachedRenderers,s);}catch(e){errors.push({message:'Preview camera tracking: '+String(e)});}});}
  }
 }});
 const onFrame=run=>new Promise((resolve,reject)=>pending.push({resolve,reject,run}));
 rpc.exports={snapshot(){return onFrame(snapshots);},create(seed){return onFrame(()=>create(seed));},detail(){return onFrame(detail);},frame(degrees=0){return onFrame(()=>frame(degrees));},configure(address,gender){return onFrame(()=>configure(address,gender));},cleanup(){return onFrame(cleanup);},errors(){return errors.splice(0);},
  load(address){return onFrame(()=>loadNow(address));},
  gridcreate(seed,count){return onFrame(()=>gridCreate(seed,count));},
  gridload(addresses){return onFrame(()=>{if(addresses.length!==slots.length)throw Error('Exact slot/address count required');return addresses.map((a,i)=>{slot(i);return loadNow(a);});});},
  gridstatus(){return onFrame(()=>slots.map((s,i)=>{slot(i);return describe(selected);}));},
  gridconfigure(values){return onFrame(()=>{if(values.length!==slots.length)throw Error('Exact slot configuration required');return values.map((v,i)=>{slot(i);return configure(v.address,v.gender);});});},
  griddetail(){return onFrame(()=>slots.map((s,i)=>{slot(i);return detail();}));},
  gridframe(degrees=0,elevation=0,margin=1.3,bakedBounds=false){if(!Number.isFinite(elevation)||Math.abs(elevation)>75||!Number.isFinite(margin)||margin<1.3||margin>4||bakedBounds&&slots.length>4)throw Error('Bounded diagnostic camera required');return onFrame(()=>slots.map((s,i)=>{slot(i);s.degrees=degrees;s.elevation=elevation;s.margin=margin;s.bakedBounds=!!bakedBounds;if(!s.cachedRenderers){const asset=selected.add(0x230).readPointer();s.cachedRenderers=renderers(asset).filter(r=>scalar(r,'enabled','readU8'));for(const r of s.cachedRenderers)set(value(r,'gameObject'),'layer',integer(s.layer));}return frame(degrees,s.cachedRenderers,s);}));},
  gridcleanup(){return onFrame(()=>{slots.forEach((s,i)=>{slot(i);clearBaked(s);cleanup();});slots=[];return true;});}
  ,gridanimationcontext(context){if(![1,2].includes(context))throw Error('Only Field/Refresh preview context');return onFrame(()=>slots.map((s,i)=>{slot(i);const c=animationController();call(method(getClass(c),'SetAnimationContext',1),c,[integer(context)]);return context;}));}
  ,gridanimationcatalog(){return onFrame(()=>slots.map((s,i)=>{slot(i);return animationCatalog();}));}
  ,gridplayanimation(animation){return onFrame(()=>slots.map((s,i)=>{slot(i);return playAnimation(animation);}));}
  ,gridanimationstatus(animation){if(!(animation in previewAnimations))throw Error('Unsupported preview clip');return onFrame(()=>slots.map((s,i)=>{slot(i);const c=animationController();return {playing:!!unbox(call(method(getClass(c),'IsPlayingAnimation',1),c,[integer(animation)])).readU8(),normalizedTime:scalar(c,'CurrentAnimationsNormalizedTime')};}));}
 };
})();
