// Opt-in read-only Shop diagnostics, never loaded by the normal launcher.
(()=>{
  const unbox=api('object_unbox','pointer',['pointer']);
  const typeObject=api('type_get_object','pointer',['pointer']);
  const classType=api('class_get_type','pointer',['pointer']);
  const find=new NativeFunction(mod.base.add(0xA454928),'pointer',['pointer','bool','pointer']);
  const read=(o,key)=>unbox(call(method(getClass(o),'get_'+key,0),o));
  const virtualMethod=api('object_get_virtual_method','pointer',['pointer','pointer']);
  const size=o=>o.isNull()?null:read(o,'Count').readS32();
  const safe=job=>{try{return job();}catch(error){return error.message;}};
  const itemStates=service=>{
    const list=service.add(0xE0).readPointer(),out=[];
    for(let i=0;i<size(list);i++){
      const index=Memory.alloc(4);index.writeS32(i);
      const item=call(method(getClass(list),'get_Item',1),list,[index]),value={};
      for(const name of ['CanBePurchased','Hidden','HasExpired','IsVendorItem'])value[name]=safe(()=>!!read(item,name).readU8());
      for(const name of ['Category','DisplayCategory','MinLevel','MaxLevel','Count','CoinCost'])value[name]=safe(()=>read(item,name).readS32());
      value.show=safe(()=>!!unbox(call(method(getClass(service),'ShouldShowInShop',1),service,[item])).readU8());
      out.push(value);
    }
    return out;
  };
  const objects=name=>{
    const a=find(typeObject(classType(klass('Niantic.Holoholo.Storefront',name))),0,ptr(0)),out=[];
    for(let i=0;i<a.add(24).readU64().toNumber();i++)out.push(a.add(32+i*8).readPointer());
    return out;
  };
  let pending=[];
  function snapshot(){
    const guardInfo=mod.base.add(0xB2D3D88).readPointer();
    const guard=api('method_get_name','pointer',['pointer'])(guardInfo).readCString();
    const guardType=api('class_get_name','pointer',['pointer'])(fromType(returnType(guardInfo))).readCString();
    return {guard,guardType,services:objects('IapService').map(o=>({coins:o.add(0x158).readS32(),
      guardNull:safe(()=>{const gm=o.add(0x40).readPointer();return call(virtualMethod(gm,guardInfo),gm).isNull();}),
      items:size(o.add(0xE0).readPointer()),visibleItems:size(o.add(0xE8).readPointer()),
      rawItems:size(o.add(0xF8).readPointer()),rawBlocked:size(o.add(0x100).readPointer()),
      category:!o.add(0x110).readPointer().isNull(),refreshPending:!o.add(0x120).readPointer().isNull(),itemStates:itemStates(o)})),
      controllers:objects('StorefrontGuiController').map(o=>({
        open:!!read(o,'IsOpen').readU8(),visible:!!read(o,'IsVisible').readU8(),
        contentActive:!!read(o.add(0x160).readPointer(),'activeSelf').readU8(),
        categories:size(o.add(0x280).readPointer()),setups:size(o.add(0x2C0).readPointer()),
        selectedSetup:!o.add(0x2B8).readPointer().isNull()}))};
  }
  Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
    for(const job of pending.splice(0))try{job.resolve(snapshot());}catch(error){job.reject(error);}
  }});
  rpc.exports={snapshot(){return new Promise((resolve,reject)=>pending.push({resolve,reject}));}};
})();
