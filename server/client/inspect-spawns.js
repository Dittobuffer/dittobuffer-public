// Opt-in read-only lifecycle trace. Never changes visibility or map data.
(()=>{
  const unbox=api('object_unbox','pointer',['pointer']);
  const typeObject=api('type_get_object','pointer',['pointer']);
  const classType=api('class_get_type','pointer',['pointer']);
  const find=new NativeFunction(mod.base.add(0xA454928),'pointer',['pointer','bool','pointer']);
  const wildClass=klass('Niantic.Holoholo.Map','WildMapPokemon');
  let events=[],pending=[],characters=new Map();
  for(const [rva,event] of [[0x7B9AE80,'asset'],[0x7B9D320,'load']]){
    Interceptor.attach(mod.base.add(rva),{onEnter(args){
      const id=characters.get(args[0].toString());
      if(id)events.push({id,event,at:Date.now(),value:args[1].toString()});
    }});
  }
  function identity(o){
    const proto=o.add(0x258).readPointer();
    return {object:o.toString(),id:proto.isNull()?null:proto.add(0x10).readU64().toString()};
  }
  for(const [rva,event] of [[0x7C7F71C,'initialize'],[0x7C80D44,'destroy'],[0x7C80E08,'dispose'],[0x7C7F1F0,'visible']]){
    Interceptor.attach(mod.base.add(rva),{onEnter(args){
      try{events.push({...identity(args[0]),event,at:Date.now(),value:event==='visible'?args[1].toInt32():null});}catch(error){events.push({event,error:String(error)});}
    }});
  }
  function snapshot(withColliders=false){
    const found=find(typeObject(classType(wildClass)),0,ptr(0)),pokemon=[];
    const colliders=withColliders?find(typeObject(classType(klass('UnityEngine','Collider'))),0,ptr(0)):null;
    const height=unbox(call(method(klass('UnityEngine','Screen'),'get_height',0),ptr(0))).readS32();
    for(let i=0;i<found.add(24).readU64().toNumber();i++){
      const o=found.add(32+i*8).readPointer();
      const get=name=>unbox(call(method(getClass(o),'get_'+name,0),o));
      const id=identity(o),holo=o.add(0x1E0).readPointer();
      characters.set(holo.toString(),id.id);
      const value={...id,species:get('PokemonId').readS32(),visible:!!get('VisibleOnMap').readU8(),destroyed:!!get('Destroyed').readU8(),asset:holo.add(0x230).readPointer().toString()};
      try{
        const rig=o.add(0x88).readPointer(),camera=call(method(getClass(rig),'get_MainCamera',0),rig);
        const transform=call(method(getClass(o),'get_transform',0),o);
        const pos=unbox(call(method(getClass(transform),'get_position',0),transform));
        const point=unbox(call(method(getClass(camera),'WorldToScreenPoint',1),camera,[pos]));
        value.screen={x:point.readFloat(),y:height-point.add(4).readFloat(),depth:point.add(8).readFloat()};
        value.collider_screens=[];
        for(let j=0;colliders&&j<colliders.add(24).readU64().toNumber();j++){
          const col=colliders.add(32+j*8).readPointer();
          const ct=call(method(getClass(col),'get_transform',0),col);
          if(!unbox(call(method(getClass(ct),'IsChildOf',1),ct,[transform])).readU8())continue;
          if(!unbox(call(method(getClass(col),'get_enabled',0),col)).readU8())continue;
          const bounds=unbox(call(method(getClass(col),'get_bounds',0),col));
          const projected=unbox(call(method(getClass(camera),'WorldToScreenPoint',1),camera,[bounds]));
          const label=call(method(getClass(col),'get_name',0),col);
          value.collider_screens.push({object:String(col),name:label.add(20).readUtf16String(label.add(16).readS32()),
            x:projected.readFloat(),y:height-projected.add(4).readFloat(),depth:projected.add(8).readFloat()});
        }
      }catch(error){value.projectionError=String(error);}
      pokemon.push(value);
    }
    return {at:Date.now(),screen:{height,width:unbox(call(method(klass('UnityEngine','Screen'),'get_width',0),ptr(0))).readS32()},pokemon,events:events.splice(0)};
  }
  Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
    for(const job of pending.splice(0))try{job.resolve(snapshot(job.withColliders));}catch(error){job.reject(String(error));}
  }});
  rpc.exports={snapshot(withColliders=false){return new Promise((resolve,reject)=>pending.push({resolve,reject,withColliders}));}};
})();
