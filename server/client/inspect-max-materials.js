// Opt-in, read-only 0.429.2 Power Spot / Wildfire material diagnostics.
// Uses local-auth's reflection helpers; runs Unity reads on its main thread.
// Does not replace shaders, modify materials, hide UI, or change game state.
(()=>{
  const unbox=api('object_unbox','pointer',['pointer']);
  const typeObject=api('type_get_object','pointer',['pointer']);
  const classType=api('class_get_type','pointer',['pointer']);
  const className=api('class_get_name','pointer',['pointer']);
  const methods=api('class_get_methods','pointer',['pointer','pointer']);
  const methodName=api('method_get_name','pointer',['pointer']);
  const parameterCount=api('method_get_param_count','uint',['pointer']);
  const parameterType=api('method_get_param','pointer',['pointer','uint']);
  const typeName=api('type_get_name','pointer',['pointer']);
  const free=api('free','void',['pointer']);
  const find=new NativeFunction(mod.base.add(0xA454928),'pointer',['pointer','bool','pointer']);
  const value=(o,key)=>call(method(getClass(o),'get_'+key,0),o);
  const text=o=>o.isNull()?null:o.add(20).readUtf16String(o.add(16).readS32());
  const name=o=>o.isNull()?null:text(value(o,'name'));
  const scalar=(o,key,read='readFloat')=>unbox(value(o,key))[read]();
  const floats=(o,count)=>Array.from({length:count},(_,i)=>o.add(4*i).readFloat());
  const array=o=>{
    if(o.isNull())return [];
    const size=o.add(24).readU64().toNumber();
    if(size<0||size>10000)throw Error('Unexpected native array size');
    return Array.from({length:size},(_,i)=>o.add(32+8*i).readPointer());
  };
  const objects=(ns,type)=>array(find(typeObject(classType(klass(ns,type))),0,ptr(0)));
  function stringMethod(type,key){
    const iterator=Memory.alloc(8);iterator.writePointer(ptr(0));
    for(let m=methods(type,iterator);!m.isNull();m=methods(type,iterator)){
      if(methodName(m).readCString()!==key||parameterCount(m)!==1)continue;
      const allocated=typeName(parameterType(m,0));
      const parameter=allocated.readCString();free(allocated);
      if(parameter==='System.String')return m;
    }
    throw Error('Exact string overload missing: '+key);
  }
  function inspect(read){try{return read();}catch(error){return {error:String(error)};}}
  function texture(o){
    if(o.isNull())return null;
    return {object:String(o),name:name(o),
      type:className(getClass(o)).readCString(),
      width:inspect(()=>scalar(o,'width','readS32')),
      height:inspect(()=>scalar(o,'height','readS32')),
      dimension:inspect(()=>scalar(o,'dimension','readS32'))};
  }
  function path(o){
    let tr=value(o,'transform');const names=[];
    for(let i=0;i<30&&!tr.isNull();i++){
      names.push(name(tr));tr=value(tr,'parent');
    }
    return names.reverse().join('/');
  }
  function material(o){
    if(o.isNull())return null;
    const shader=value(o,'shader');
    const names=inspect(()=>array(call(method(getClass(o),'GetTexturePropertyNames',0),o)).map(text));
    const propertyNames=Array.isArray(names)?names:[];
    const getTexture=inspect(()=>stringMethod(getClass(o),'GetTexture'));
    const textures={};
    // The error shader can report no texture properties even when the saved
    // material still has its original _MainTex. Read that known property too.
    for(const key of new Set([...propertyNames,'_MainTex'])){
      textures[key]=inspect(()=>texture(call(getTexture,o,[stringNew(Memory.allocUtf8String(key))])));
    }
    return {object:String(o),name:name(o),shader:shader.isNull()?null:{
      object:String(shader),name:name(shader),supported:!!scalar(shader,'isSupported','readU8'),
      passCount:scalar(shader,'passCount','readS32')
    },textureProperties:names,textures,
      keywords:inspect(()=>array(value(o,'shaderKeywords')).map(text)),
      renderQueue:inspect(()=>scalar(o,'renderQueue','readS32'))};
  }
  function common(o){
    const go=value(o,'gameObject');
    return {object:String(o),type:className(getClass(o)).readCString(),path:path(o),
      active:!!scalar(go,'activeInHierarchy','readU8'),enabled:!!scalar(o,'enabled','readU8')};
  }
  const wildfire=/gmax[_ .-]?wildfire|VN_BM_019/i;
  function snapshot(relevant=/station|bread|power.?spot|pou|gmax[_ .-]?wildfire|VN_BM_019/i,includeGraphics=true){
    const graphics=[],renderers=[];
    for(const o of includeGraphics?objects('UnityEngine.UI','Graphic'):[]){
      try{
        const row=common(o);if(!relevant.test(row.path))continue;
        const tr=value(o,'rectTransform'),rect=unbox(value(tr,'rect'));
        row.material=material(value(o,'material'));
        row.color=floats(unbox(value(o,'color')),4);
        row.position=floats(unbox(value(tr,'position')),3);
        row.rect=floats(rect,4);
        row.depth=scalar(o,'depth','readS32');
        row.raycastTarget=!!scalar(o,'raycastTarget','readU8');
        graphics.push(row);
      }catch(error){graphics.push({object:String(o),error:String(error)});}
    }
    for(const o of objects('UnityEngine','Renderer')){
      try{
        const row=common(o);if(!relevant.test(row.path))continue;
        row.materials=array(value(o,'sharedMaterials')).map(material);
        const bounds=unbox(value(o,'bounds'));
        row.center=floats(bounds,3);row.extents=floats(bounds.add(12),3);
        renderers.push(row);
      }catch(error){renderers.push({object:String(o),error:String(error)});}
    }
    const shaderFind=stringMethod(klass('UnityEngine','Shader'),'Find');
    const shaderLookups={};
    for(const key of ['Legacy Shaders/Particles/Additive (Soft)','Unlit/Texture',
      'Mobile/Particles/Additive','Mobile/Particles/Alpha Blended',
      'Legacy Shaders/Particles/Alpha Blended','NianticCustom/FX/Stencil/Additive',
      'NianticCustom/FX/AdditiveEmission','NianticCustom/FX/AdditivePanning',
      'NianticCustom/FX/Dissolve','NianticCustom/Items/BRDF/LegendaryMegaRaidEgg']){
      shaderLookups[key]=inspect(()=>{
        const shader=call(shaderFind,ptr(0),[stringNew(Memory.allocUtf8String(key))]);
        return shader.isNull()?null:{object:String(shader),name:name(shader),
          supported:!!scalar(shader,'isSupported','readU8'),passCount:scalar(shader,'passCount','readS32')};
      });
    }
    return {at:Date.now(),graphics,renderers,shaderLookups};
  }
  const pending=[];
  let wildfireUntil=0,wildfireNext=0,wildfireCaptures=0,wildfireFingerprint='';
  // Observe native ParticleSystem.Play only. A named Wildfire effect opens a
  // short, bounded diagnostic window. No material, shader, renderer or game
  // state is written. Ordinary frames perform no object enumeration.
  const hooked=new Set();
  for(const count of [0,1]){
    try{
      const address=method(klass('UnityEngine','ParticleSystem'),'Play',count).readPointer();
      if(address.isNull()||hooked.has(String(address)))continue;
      hooked.add(String(address));
      Interceptor.attach(address,{onEnter(args){
        this.wildfire=false;
        if(wildfireCaptures>=12)return;
        try{this.wildfire=wildfire.test(path(args[0]));}catch(_error){}
      },onLeave(){
        if(this.wildfire){
          const now=Date.now();
          if(now>wildfireUntil)wildfireFingerprint='';
          wildfireUntil=now+2200;
        }
      }});
    }catch(error){console.log('Max wildfire probe hook '+String(error));}
  }
  Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
    for(const job of pending.splice(0))try{job.resolve(snapshot(job.filter,job.graphics));}catch(error){job.reject(error);}
    const now=Date.now();
    if(now>wildfireUntil||now<wildfireNext||wildfireCaptures>=12)return;
    wildfireNext=now+500;
    try{
      const state=snapshot(wildfire,false);
      state.renderers=state.renderers.filter(row=>row.active&&row.enabled);
      if(!state.renderers.length)return;
      const fingerprint=JSON.stringify(state.renderers.map(row=>[row.path,row.materials]));
      if(fingerprint===wildfireFingerprint)return;
      wildfireFingerprint=fingerprint;wildfireCaptures++;
      console.log('Max wildfire shader lookups '+JSON.stringify(state.shaderLookups));
      for(const row of state.renderers)console.log('Max wildfire material renderer '+JSON.stringify(row));
    }catch(error){console.log('Max wildfire material probe '+String(error));wildfireCaptures++;}
  }});
  globalThis.LIBRE_MAX_MATERIAL_SNAPSHOT=()=>new Promise((resolve,reject)=>pending.push({resolve,reject}));
  globalThis.LIBRE_MAX_WILDFIRE_MATERIAL_SNAPSHOT=()=>new Promise((resolve,reject)=>pending.push({resolve,reject,filter:wildfire,graphics:false}));
  rpc.exports={snapshot:globalThis.LIBRE_MAX_MATERIAL_SNAPSHOT};
})();
