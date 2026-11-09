// 0.429.2 vNext -> legacy resolve background handoff compatibility.
// Reuses the ORIGINAL resident raid material on the existing native Image.
// No new GameObject, Canvas, texture, reward row, animation or battle mutation.
const LibreRaidResolvePresentation=(()=>{
 function eligible(screen,status,resource){return screen==='RaidResolveUIController'&&status===2&&resource?.name==='RaidTransitionRingBackground'&&resource.shader==='NianticCustom/UI/RingScanlines'&&resource.supported===true;}
 const quadProperties=Object.freeze({_Radius:1,_Thickness:2,_GradientScale:0});
 return {eligible,quadProperties,tint:Object.freeze([.25,.25,.25,1])};
})();
if(typeof module==='object'&&module.exports)module.exports=LibreRaidResolvePresentation;
if(typeof Process!=='undefined')(()=>{
 const unbox=api('object_unbox','pointer',['pointer']),typeObject=api('type_get_object','pointer',['pointer']),classType=api('class_get_type','pointer',['pointer']);
 const className=api('class_get_name','pointer',['pointer']),getField=api('class_get_field_from_name','pointer',['pointer','pointer']),fieldOffset=api('field_get_offset','int',['pointer']);
 const str=o=>o.isNull()?'':o.add(20).readUtf16String(o.add(16).readS32());
 const get=(o,n)=>call(method(getClass(o),'get_'+n,0),o);
 const name=o=>o.isNull()?'':str(get(o,'name'));
 const type=k=>typeObject(classType(k));
 function originalMaterial(){
  const all=call(method(klass('UnityEngine','Resources'),'FindObjectsOfTypeAll',1),ptr(0),[type(klass('UnityEngine','Material'))]);
  for(let i=0;i<all.add(24).readU64().toNumber();i++){
   const m=all.add(32+8*i).readPointer();if(name(m)!=='RaidTransitionRingBackground')continue;
   const shader=get(m,'shader');if(shader.isNull())continue;
   const resource={name:name(m),shader:name(shader),supported:!!unbox(get(shader,'isSupported')).readU8()};
   if(LibreRaidResolvePresentation.eligible('RaidResolveUIController',2,resource))return m;
  }return ptr(0);
 }
 const clones=new Map();
 function localMaterial(template){
  // The source is the intro's animated ring: radius=0 in the asset. Its
  // original shader supplies scanlines/colors, while the recipient is a full
  // normalized UI quad. The decoded native GLES program uses distance(uv-.5)
  // for its ring mask. Radius 1 / thickness 2 covers all corners and center;
  // gradient 0 retains the source charcoal color across the whole quad.
  const k=klass('UnityEngine','Material'),methods=api('class_get_methods','pointer',['pointer','pointer']),param=api('method_get_param','pointer',['pointer','uint']),typeName=api('type_get_name','pointer',['pointer']),methodName=api('method_get_name','pointer',['pointer']),nparam=api('method_get_param_count','uint',['pointer']);
  const it=Memory.alloc(8);it.writePointer(ptr(0));let ctor=ptr(0);
  for(let m=methods(k,it);!m.isNull();m=methods(k,it))if(methodName(m).readCString()==='.ctor'&&nparam(m)===1&&typeName(param(m,0)).readCString()==='UnityEngine.Material'){ctor=m;break;}
  if(ctor.isNull())throw Error('Native material copy constructor unavailable');
  const copy=objectNew(k);call(ctor,copy,[template]);
  call(method(getClass(copy),'set_name',1),copy,[stringNew(Memory.allocUtf8String('DittoBufferRaidResolveNativeBackground'))]);
  for(const [key,number] of Object.entries(LibreRaidResolvePresentation.quadProperties)){const value=Memory.alloc(4);value.writeFloat(number);call(method(k,'SetFloat',2),copy,[stringNew(Memory.allocUtf8String(key)),value]);}
  return copy;
 }
 function repair(controller,status){
  if(controller.isNull()||className(getClass(controller)).readCString()!=='RaidResolveUIController'||status!==2)return false;
  const field=getField(getClass(controller),Memory.allocUtf8String('backgroundImage'));if(field.isNull()||fieldOffset(field)!==0x198)return false;
  const image=controller.add(fieldOffset(field)).readPointer();if(image.isNull())return false;
  const material=originalMaterial();if(material.isNull()){console.log('Raid resolve background: original RingScanlines material unavailable; native state retained');return false;}
  // Initialize copied a cleared TransitionScreenService in vNext. Repair its
  // local recipient, preserving the global transition service and all other UIs.
  const key=controller.toString();
  let owned=clones.get(key);if(!owned){owned=localMaterial(material);clones.set(key,owned);}
  call(method(getClass(image),'set_material',1),image,[owned]);
  // Charcoal recipient tint; original shader colors/scanline/glow remain intact.
  const white=Memory.alloc(16);for(let i=0;i<4;i++)white.add(i*4).writeFloat(LibreRaidResolvePresentation.tint[i]);
  call(method(getClass(image),'set_color',1),image,[white]);
  console.log('Raid resolve background restored from original RaidTransitionRingBackground / RingScanlines');return true;
 }
 // Exposed only for opt-in diagnostic invocation on an already-earned screen.
 globalThis.LIBRE_REPAIR_RAID_RESOLVE_BACKGROUND=repair;
 const controller=klass('Niantic.Holoholo.Raid','RaidResolveUIController');
 Interceptor.attach(method(controller,'HandleDismissComplete',0).readPointer(),{onEnter(args){const key=args[0].toString(),owned=clones.get(key);if(owned){clones.delete(key);try{call(method(klass('UnityEngine','Object'),'Destroy',1),ptr(0),[owned]);}catch(e){}}}});
 Interceptor.attach(method(controller,'Initialize',-1).readPointer(),{
  onEnter(args){this.controller=args[0];this.status=-1;try{if(!args[1].isNull())this.status=unbox(get(args[1],'Status')).readS32();}catch(e){}},
  onLeave(){try{repair(this.controller,this.status);}catch(e){console.log('Raid resolve background compatibility unavailable: '+e.message);}}
 });
})();
