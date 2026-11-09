// Read-only isolated storage scrolling probe. Never loaded by the launcher.
(()=>{
  if(!globalThis.LIBRE_GAMEPLAY_STORAGE_GRID_ALLOWED)throw Error('Explicit isolated grid inspection required');
  const unbox=api('object_unbox','pointer',['pointer']),typeObject=api('type_get_object','pointer',['pointer']),classType=api('class_get_type','pointer',['pointer']);
  const find=new NativeFunction(mod.base.add(0xA454928),'pointer',['pointer','bool','pointer']);
  const get=(o,n)=>call(method(getClass(o),'get_'+n,0),o);
  const scalar=(o,n,t='readS32')=>unbox(get(o,n))[t]();
  const text=o=>o.isNull()?null:o.add(20).readUtf16String(o.add(16).readS32());
  const name=o=>o.isNull()?null:text(get(o,'name'));
  const objects=(ns,n)=>{
    const a=find(typeObject(classType(klass(ns,n))),0,ptr(0)),r=[];
    for(let i=0;i<a.add(24).readU64().toNumber();i++)r.push(a.add(32+i*8).readPointer());return r;
  };
  function snapshot(){
    const inventory=objects('Niantic.Holoholo.UI.Pokemon','PokemonInventoryGuiController').find(o=>scalar(o,'IsOpen','readU8')&&scalar(o,'IsVisible','readU8'));
    if(!inventory)throw Error('Actual visible Pokemon inventory required');
    const bag=inventory.add(0x1D0).readPointer(),cells=[];
    for(const cell of objects('Niantic.Holoholo.UI','PokemonInventoryCellView')){
      if(!scalar(get(cell,'gameObject'),'activeInHierarchy','readU8'))continue;
      const id=cell.add(0x2B0).readU64().toString();if(id==='0')continue;
      const key=Memory.alloc(8);key.writeU64(uint64(id));const pokemon=call(method(getClass(bag),'GetPokemon',1),bag,[key]);
      if(pokemon.isNull())throw Error('Grid references a missing owned Pokemon');
      const widget=cell.add(0xA0).readPointer(),image=widget.add(0x70).readPointer(),sprite=get(image,'sprite');
      const d=get(pokemon,'PokemonDisplay'),position=unbox(get(get(cell,'transform'),'position'));
      cells.push({id,species:scalar(pokemon,'PokemonId'),form:d.add(0x1C).readS32(),gender:d.add(0x14).readS32(),shiny:!!d.add(0x18).readU8(),costume:d.add(0x10).readS32(),cp:scalar(pokemon,'Cp'),
        label:text(get(cell.add(0x90).readPointer(),'text')),cpLabel:text(get(cell.add(0x98).readPointer(),'text')),
        sprite:name(sprite),spritePopulated:!!unbox(call(method(getClass(widget),'SpriteIsPopulated',0),widget)).readU8(),
        imageEnabled:!!scalar(image,'enabled','readU8'),texture:sprite.isNull()?null:name(get(sprite,'texture')),
        position:[position.readFloat(),position.add(4).readFloat(),position.add(8).readFloat()]});
    }
    return {at:Date.now(),cells};
  }
  let pending=[];
  Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
    for(const job of pending.splice(0))try{job.resolve(snapshot());}catch(e){job.reject(new Error(String(e)+'\n'+e.stack));}
  }});
  rpc.exports={snapshot(){return new Promise((resolve,reject)=>pending.push({resolve,reject}));}};
})();
