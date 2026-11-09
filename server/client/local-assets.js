// Opt-in, process-only Pokemon catalog routing. Native Addressables still owns
// cache hashes, dependency loading, prefab instantiation and animation assets.
// Importer rewrites ALL bundle locations, including failures, to local URLs.
(()=>{
  if(globalThis.LIBRE_AVATAR_CATALOG_URL){
    const avatarUrl=globalThis.LIBRE_AVATAR_CATALOG_URL;
    if(avatarUrl!==(globalThis.LIBRE_BASE_URL||'http://127.0.0.1:8080')+'/assets/avatar/catalog.json')throw Error('Unapproved avatar catalog rejected');
    const avatarCatalog=stringNew(Memory.allocUtf8String(avatarUrl.slice(0,-'/catalog.json'.length)));rootHandle(avatarCatalog,0);
    // AssetPath is a directory: native LoadCatalog appends catalog_avatars_88.json.
    // Preserve its matching native hash request and Addressables cache behavior.
    Interceptor.attach(mod.base.add(0x9478804),{onLeave(result){result.replace(avatarCatalog);console.log('Avatar catalog routed to verified local import');}});
  }
  if(!globalThis.LIBRE_POKEMON_CATALOG_URL)return;
  const url=globalThis.LIBRE_POKEMON_CATALOG_URL;
  if(url!==(globalThis.LIBRE_BASE_URL||'http://127.0.0.1:8080')+'/assets/pokemon/catalog.json')throw Error('Unapproved asset catalog rejected');
  const routed=stringNew(Memory.allocUtf8String(url));rootHandle(routed,0);
  Interceptor.attach(mod.base.add(0x96579CC),{
    onLeave(result){result.replace(routed);console.log('Pokemon catalog routed to verified local import');}
  });
  // Exact supplied build's generic address generator. Log actual selection;
  // never substitute one species, form, gender or costume for another.
  const seen=new Set();
  Interceptor.attach(mod.base.add(0x50D2650),{onLeave(result){
    if(result.isNull())return;
    const key=result.add(20).readUtf16String(result.add(16).readS32());
    if(!seen.has(key)){seen.add(key);console.log('Local Pokemon model address '+key);}
  }});
})();
// The native Appearance fallback advertises every baked-in body preset even
// when its addressable resource is absent. Limit that fallback to the same
// dependency-checked IDs accepted by the server; do not replace any model.
if(typeof LIBRE_AVATAR_BODY_PRESETS!=='undefined')(()=>{
 const allowed=new Set([...LIBRE_AVATAR_BODY_PRESETS,...(globalThis.LIBRE_AVATAR_FACE_PRESETS||[])]),unbox=api('object_unbox','pointer',['pointer']);
 const text=o=>o.isNull()?'':o.add(20).readUtf16String(o.add(16).readS32());
 const count=o=>unbox(call(method(getClass(o),'get_Count',0),o)).readS32();
 const at=(o,i)=>{const n=Memory.alloc(4);n.writeS32(i);return call(method(getClass(o),'get_Item',1),o,[n]);};
 Interceptor.attach(mod.base.add(0x887893C),{onLeave(list){
  if(list.isNull())return;
  try{
   for(let i=count(list)-1;i>=0;i--){
    const listing=at(list,i),items=listing.add(0x10).readPointer();
    if(items.isNull()||count(items)!==1)continue;
    const id=text(at(items,0).add(0x10).readPointer());
    if((id.startsWith('BodyPreset')||id.startsWith('FacePreset'))&&!allowed.has(id)){
     const n=Memory.alloc(4);n.writeS32(i);call(method(getClass(list),'RemoveAt',1),list,[n]);
     console.log('Unavailable appearance preset omitted: '+id);
    }
   }
  }catch(error){console.log('Body preset availability filter: '+error.message);}
 }});
})();
