// Isolated storage acceptance only. Uses real client-owned Pokemon objects and
// normal inventory ShowPokemonInfo pathway; never instantiates a preview model.
(()=>{
  if(!globalThis.LIBRE_GAMEPLAY_STORAGE_TEST_ALLOWED)throw Error('Explicit isolated storage test required');
  const allowed=new Set(globalThis.LIBRE_GAMEPLAY_STORAGE_OWNED_IDS||[]);
  const unbox=api('object_unbox','pointer',['pointer']),typeObject=api('type_get_object','pointer',['pointer']),classType=api('class_get_type','pointer',['pointer']);
  const className=api('class_get_name','pointer',['pointer']);
  const find=new NativeFunction(mod.base.add(0xA454928),'pointer',['pointer','bool','pointer']);
  const barrier=api('gc_wbarrier_set_field','void',['pointer','pointer','pointer']),free=api('gchandle_free','void',['pointer']);
  const get=(o,n)=>call(method(getClass(o),'get_'+n,0),o);
  const scalar=(o,n,t='readS32')=>unbox(get(o,n))[t]();
  const text=o=>o.isNull()?null:o.add(20).readUtf16String(o.add(16).readS32());
  const name=o=>o.isNull()?null:text(get(o,'name'));
  const vec=o=>[o.readFloat(),o.add(4).readFloat(),o.add(8).readFloat()];
  const vector=(o,n)=>vec(unbox(get(o,n)));
  const objects=(ns,n)=>{
    const a=find(typeObject(classType(klass(ns,n))),0,ptr(0)),r=[];
    for(let i=0;i<a.add(24).readU64().toNumber();i++)r.push(a.add(32+i*8).readPointer());return r;
  };
  const path=o=>{
    let t=get(o,'transform'),r=[];for(let i=0;i<20&&!t.isNull();i++){r.push(name(t));t=get(t,'parent');}return r.reverse().join('/');
  };
  function controller(n){
    return objects('Niantic.Holoholo.UI.Pokemon',n).find(o=>scalar(o,'IsOpen','readU8')&&scalar(o,'IsVisible','readU8'));
  }
  function identity(o){
    const d=get(o,'PokemonDisplay');
    return {id:unbox(get(o,'Id')).readU64().toString(),species:scalar(o,'PokemonId'),cp:scalar(o,'Cp'),hp:scalar(o,'Stamina'),max_hp:scalar(o,'MaxStamina'),
      ivs:['IndividualAttack','IndividualDefense','IndividualStamina'].map(n=>scalar(o,n)),
      form:d.add(0x1C).readS32(),gender:d.add(0x14).readS32(),shiny:!!d.add(0x18).readU8(),costume:d.add(0x10).readS32()};
  }
  function model(o,renderers){
    const root=get(o,'transform'),controller=o.add(0xA0).readPointer(),animator=o.add(0xE8).readPointer();
    return {object:String(o),name:name(o),path:path(o),viewId:o.add(0x20).readS32(),formId:o.add(0x24).readS32(),gender:o.add(0x48).readS32(),shiny:!!o.add(0x58).readU8(),
      localScale:vector(root,'localScale'),lossyScale:vector(root,'lossyScale'),position:vector(root,'position'),
      normalizedTime:controller.isNull()?null:scalar(controller,'CurrentAnimationsNormalizedTime','readFloat'),
      animationContext:controller.isNull()?null:scalar(controller,'CurrentContext'),animatorEnabled:!animator.isNull()&&!!scalar(animator,'enabled','readU8'),
      animatorInitialized:!animator.isNull()&&!!scalar(animator,'isInitialized','readU8'),renderers:renderers.filter(r=>{
        const t=get(r,'transform');return !!unbox(call(method(getClass(t),'IsChildOf',1),t,[root])).readU8();
      }).map(r=>{
        const a=get(r,'sharedMaterials'),materials=[];
        for(let i=0;i<a.add(24).readU64().toNumber();i++){
          const m=a.add(32+i*8).readPointer();materials.push(m.isNull()?null:{name:name(m),shader:name(get(m,'shader'))});
        }
        const bounds=unbox(get(r,'bounds'));
        return {name:name(r),enabled:!!scalar(r,'enabled','readU8'),visible:!!scalar(r,'isVisible','readU8'),center:vec(bounds),extents:vec(bounds.add(12)),materials};
      })};
  }
  function snapshot(){
    const info=controller('PokemonInfoGuiController'),inventory=controller('PokemonInventoryGuiController');
    const renderers=objects('UnityEngine','Renderer'),models=objects('Niantic.PokemonAssets','PokemonModel').map(o=>model(o,renderers));
    const current=info?get(info,'CurrentPokemon'):ptr(0);
    const renderer=info?info.add(0x320).readPointer():ptr(0),detailRoot=renderer.isNull()?ptr(0):get(renderer,'CharacterRoot');
    for(const m of models)m.inCurrentDetail=!detailRoot.isNull()&&!!unbox(call(method(getClass(get(ptr(m.object),'transform')),'IsChildOf',1),get(ptr(m.object),'transform'),[detailRoot])).readU8();
    let camera=null;
    if(!renderer.isNull()){
      const detail=get(renderer,'DetailCamera'),source=get(detail,'RenderSource');
      camera={displayScale:scalar(renderer,'PokemonDisplayScale','readFloat'),fov:scalar(detail,'CurrentFov','readFloat'),
        position:vector(get(source,'transform'),'position'),forward:vector(get(source,'transform'),'forward'),pixelWidth:scalar(source,'pixelWidth'),pixelHeight:scalar(source,'pixelHeight')};
    }
    const blockers=objects('Niantic.Holoholo.NUI','GuiController').filter(o=>scalar(o,'IsOpen','readU8')&&scalar(o,'IsVisible','readU8'))
      .map(o=>className(getClass(o)).readUtf8String()).filter(n=>n==='ConversationLayoutGui'||n==='SimpleTransparentGradientModalGui');
    return {at:Date.now(),detailOpen:!!info,inventoryOpen:!!inventory,current:current.isNull()?null:identity(current),models,camera,blockers};
  }
  let listClass=null;
  function close(){
    const info=controller('PokemonInfoGuiController');
    if(!info)return {closed:false};
    const data=info.add(0x398).readPointer();
    if(!data.isNull()&&!data.add(0x10).readPointer().isNull())listClass=getClass(data.add(0x10).readPointer());
    call(method(getClass(info),'DismissPokemonInfoPanel',0),info);return {closed:true};
  }
  function prepareClose(){
    const info=controller('PokemonInfoGuiController');
    if(info){const data=info.add(0x398).readPointer();if(!data.isNull()&&!data.add(0x10).readPointer().isNull())listClass=getClass(data.add(0x10).readPointer());}
    return {detailOpen:!!info};
  }
  function display(id){
    if(!allowed.has(id))throw Error('Owned ID is outside the explicit fixture whitelist');
    if(controller('PokemonInfoGuiController'))throw Error('Dismiss the previous detail through normal navigation first');
    const inventories=objects('Niantic.Holoholo.UI.Pokemon','PokemonInventoryGuiController');
    if(inventories.length!==1||!controller('PokemonInventoryGuiController')||!listClass)throw Error('Open a real detail once, then return to the actual inventory');
    const bag=inventories[0].add(0x1D0).readPointer(),key=Memory.alloc(8);key.writeU64(uint64(id));
    const pokemon=call(method(getClass(bag),'GetPokemon',1),bag,[key]);
    if(pokemon.isNull()||identity(pokemon).id!==id)throw Error('ID not present in native owned collection');
    const list=objectNew(listClass),handles=[rootHandle(list,0)];
    try{
      call(method(getClass(list),'.ctor',0),list);call(method(getClass(list),'Add',1),list,[pokemon]);
      const state=Memory.alloc(4);state.writeS32(-1);const no=Memory.alloc(1);no.writeU8(0);
      call(method(getClass(inventories[0]),'ShowPokemonInfo',5),inventories[0],[list,pokemon,state,no,no]);
      return {requested:id,source:'Native PokemonBagImpl.GetPokemon -> real PokemonInventoryGuiController.ShowPokemonInfo',pokemon:identity(pokemon)};
    }finally{handles.forEach(h=>free(h));}
  }
  let pending=[];
  Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
    for(const job of pending.splice(0))try{job.resolve(job.operation());}catch(e){job.reject(new Error(String(e)+'\n'+e.stack));}
  }});
  rpc.exports={snapshot(){return new Promise((resolve,reject)=>pending.push({resolve,reject,operation:snapshot}));},
    prepareclose(){return new Promise((resolve,reject)=>pending.push({resolve,reject,operation:prepareClose}));},
    closedetail(){return new Promise((resolve,reject)=>pending.push({resolve,reject,operation:close}));},
    displayowned(id){return new Promise((resolve,reject)=>pending.push({resolve,reject,operation:()=>display(String(id))}));}};
})();
