// Read-only native wardrobe diagnostics, loaded only by inspect-avatar.py.
(()=>{
 const unbox=api('object_unbox','pointer',['pointer']);
 const typeObject=api('type_get_object','pointer',['pointer']);
 const classType=api('class_get_type','pointer',['pointer']);
 const find=new NativeFunction(mod.base.add(0xA454928),'pointer',['pointer','bool','pointer']);
 const invoke=(o,k)=>call(method(getClass(o),'get_'+k,0),o);
 const size=o=>o.isNull()?null:unbox(invoke(o,'Count')).readS32();
 const string=o=>o.isNull()?null:o.add(20).readUtf16String(o.add(16).readS32());
 const objects=name=>{
  const a=find(typeObject(classType(klass('Niantic.Holoholo.Avatar',name))),0,ptr(0)),out=[];
  for(let i=0;i<a.add(24).readU64().toNumber();i++)out.push(a.add(32+i*8).readPointer());return out;
 };
 const at=(list,i)=>{const index=Memory.alloc(4);index.writeS32(i);return call(method(getClass(list),'get_Item',1),list,[index]);};
 let pending=[];
 function snapshot(){
  return objects('NeutralAvatarCustomizationState').map(state=>{
   const service=invoke(state.add(0x98).readPointer(),'Value'),raw=service.add(0x58).readPointer();
   const result={raw:size(raw),categories:size(service.add(0x70).readPointer()),articles:size(service.add(0x80).readPointer()),
    loading:service.add(0xA0).readU8(),received:service.add(0xB8).readU8(),examples:[]};
   const avatar=invoke(state,'AvatarClone');
   if(!avatar.isNull()){
    const articles=avatar.add(0x10).readPointer();result.appearance={};
    for(const [name,offset] of [['hair',0x10],['face',0xB8],['body',0xC0]]){
     const item=articles.add(offset).readPointer();result.appearance[name]=item.isNull()?null:string(item.add(0x10).readPointer());
    }
    for(const [name,offset] of [['skin',0x48],['hairColor',0x50],['eyeColor',0x58]])result.appearance[name]=string(avatar.add(offset).readPointer());
    const blend=avatar.add(0x18).readPointer();
    if(!blend.isNull())result.bodyBlend={size:blend.add(0x10).readFloat(),muscle:blend.add(0x14).readFloat(),bust:blend.add(0x18).readFloat(),hips:blend.add(0x1C).readFloat(),shoulders:blend.add(0x20).readFloat()};
   }
   result.categoryCounts={};
   result.sheets=objects('AvatarItemSheetGuiController').map(o=>({items:size(o.add(0xA0).readPointer()),open:o.add(0xA8).readU8()}));
   for(const name of ['CAT_SHIRT','CAT_HAT','CAT_BACKPACK']){
    const key=stringNew(Memory.allocUtf8String(name)),dict=service.add(0x70).readPointer();
    try{
     const values=call(method(getClass(dict),'get_Item',1),dict,[key]);
     result.categoryCounts[name]={count:size(values),owned:[]};
     for(let i=0;i<Math.min(size(values),3);i++){
      const item=at(values,i);result.categoryCounts[name].owned.push(!!unbox(invoke(item,'Owned')).readU8());
     }
     const filtered=state.add(0xD8).readPointer();
     result.categoryCounts[name].filtered=size(call(method(getClass(filtered),'GetFilteredAvatarStoreListings',2),filtered,[key,key]));
    }catch(error){result.categoryCounts[name]=error.message;}
   }
   for(let i=0;i<Math.min(size(raw),4);i++){
    const row=at(raw,i),items=row.add(0x10).readPointer(),item=at(items,0);
    result.examples.push({group:string(row.add(0x60).readPointer()),id:string(item.add(0x10).readPointer()),owned:item.add(0x20).readU8(),slot:item.add(0x24).readS32(),items:size(items)});
   }
   result.subcategories=objects('AvatarSubcategoryGuiController').map(o=>{
    const a=o.add(0x58).readPointer(),settings=[];
    for(let i=0;i<a.add(24).readU64().toNumber();i++){
     const item=a.add(32+i*8).readPointer();settings.push({name:string(item.add(0x18).readPointer()),slot:item.add(0x30).readS32()});
    }
    const go=invoke(o,'gameObject');
    return {category:string(o.add(0x50).readPointer()),active:!!unbox(invoke(go,'activeInHierarchy')).readU8(),rows:size(o.add(0x160).readPointer()),settings,
     colorBar:string(o.add(0x60).readPointer()),primaryColors:string(o.add(0x68).readPointer()),secondaryColors:string(o.add(0x70).readPointer())};
   });
   result.styleScreens=objects('NeutralAvatarShopStylesGuiController').map(o=>{
    const buttons={};for(const [key,offset] of [['back',0x160],['exit',0x168],['colors',0x1A8]]){
     const b=o.add(offset).readPointer();if(b.isNull())continue;
     const go=invoke(b,'gameObject'),tr=invoke(b,'transform'),pos=unbox(invoke(tr,'position'));
     buttons[key]={active:!!unbox(invoke(go,'activeInHierarchy')).readU8(),position:[pos.readFloat(),pos.add(4).readFloat(),pos.add(8).readFloat()]};
    }
    return {open:!!unbox(invoke(o,'IsOpen')).readU8(),visible:!!unbox(invoke(o,'IsVisible')).readU8(),buttons,
     currentTab:o.add(0x2D0).readS32(),tabButtons:size(o.add(0x2F0).readPointer())};
   });
   return result;
  });
 }
 Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
  for(const job of pending.splice(0))try{job.resolve(job.action?job.action():snapshot());}catch(error){job.reject(error);}
 }});
 rpc.exports={snapshot(){return new Promise((resolve,reject)=>pending.push({resolve,reject}));},retrytab(){
  return new Promise((resolve,reject)=>pending.push({resolve,reject,action(){
   const o=objects('NeutralAvatarShopStylesGuiController').find(o=>!unbox(invoke(o,'IsOpen')).readU8());
   if(!o)return {error:'No incomplete style initialization'};
   const index=Memory.alloc(4);index.writeS32(0);
   try{call(method(getClass(o),'OnTabSelected',1),o,[index]);return {result:'returned'};}
   catch(error){return {error:error.message};}
  }}));
 }};
})();
