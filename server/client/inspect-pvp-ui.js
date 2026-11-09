// Opt-in read-only hierarchy evidence. Names/state only; no player data.
(()=>{
  const unbox=api('object_unbox','pointer',['pointer']);
  const typeObject=api('type_get_object','pointer',['pointer']);
  const classType=api('class_get_type','pointer',['pointer']);
  const find=new NativeFunction(mod.base.add(0xA454928),'pointer',['pointer','bool','pointer']);
  const read=(o,k)=>unbox(call(method(getClass(o),'get_'+k,0),o));
  const text=o=>o.isNull()?'':o.add(20).readUtf16String(o.add(16).readS32());
  function objects(ns,name){
    const a=find(typeObject(classType(klass(ns,name))),0,ptr(0)),v=[];
    for(let i=0;i<Math.min(300,a.add(24).readU64().toNumber());i++)v.push(a.add(32+i*8).readPointer());return v;
  }
  let due=0,remaining=0;
  Interceptor.attach(mod.base.add(0x84F6528),{onEnter(args){
    const data=args[1];
    const check=offset=>{
      const player=data.add(offset).readPointer();
      return {present:!player.isNull(),avatar:!player.isNull()&&!player.add(0x18).readPointer().isNull()};
    };
    console.log('PvP stage inputs '+JSON.stringify({opponent:check(0x28),local:check(0x20)}));
  }});
  for(const rva of [0x85758D0,0x8568E8C,0x856C950,0x85713F0,0x8564EB4,0x850AFCC,0x850F370])
    Interceptor.attach(mod.base.add(rva),{onEnter(){remaining=3;due=Date.now()+1000;}});
  Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
    if(!remaining||Date.now()<due)return;remaining--;due=Date.now()+10000;
    try{
      const group=o=>o.isNull()?null:{alpha:read(o,'alpha').readFloat(),interactable:!!read(o,'interactable').readU8(),raycasts:!!read(o,'blocksRaycasts').readU8()};
      const hud=objects('Niantic.Holoholo.Map.UI','PlayerHudGuiController').map(o=>({root:group(o.add(0x40).readPointer()),trainer:group(o.add(0x170).readPointer())}));
      const images=[];
      for(const o of objects('UnityEngine.UI','RawImage')){
        if(!read(o,'isActiveAndEnabled').readU8())continue;
        let t=call(method(getClass(o),'get_transform',0),o),parts=[];
        for(let i=0;i<7&&!t.isNull();i++){
          parts.push(text(call(method(klass('UnityEngine','Object'),'get_name',0),t)));
          t=call(method(getClass(t),'get_parent',0),t);
        }
        images.push(parts.join('/'));
      }
      console.log('PvP UI hierarchy '+JSON.stringify({hud,images}));
    }catch(e){console.log('PvP UI hierarchy unavailable '+e.message);}
  }});
})();
