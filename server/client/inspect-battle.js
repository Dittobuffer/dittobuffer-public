// Opt-in, read-only battle-frame evidence. Never loaded by the normal launcher.
(()=>{
  let frame=ptr(0),visual=ptr(0),visualCalls=0,changes=0,pending=[],cpReads=[];
  const unbox=api('object_unbox','pointer',['pointer']);
  const fieldInfo=api('class_get_field_from_name','pointer',['pointer','pointer']);
  const offset=api('field_get_offset','int',['pointer']);
  const field=(o,key)=>o.add(offset(fieldInfo(getClass(o),Memory.allocUtf8String(key))));
  for(const [rva,name] of [[0x88FA198,'ActualDisplayCp'],[0x88F6A6C,'AdjustedCp']]){
    Interceptor.attach(mod.base.add(rva),{onEnter(args){
      const caller=this.returnAddress.sub(mod.base).toUInt32();
      if(caller>=0x88026F0&&caller<0x8803054)this.record={method:name,caller,cp:field(args[0],'cp_').readS32(),displayCp:field(args[0],'displayCp_').readS32(),id:field(args[0],'id_').readU64().toString()};
    },onLeave(value){if(this.record){this.record.result=value.toInt32();cpReads.push(this.record);}}});
  }
  const health=new NativeFunction(mod.base.add(0x87EA400),'int',['pointer','pointer']);
  const energy=new NativeFunction(mod.base.add(0x87EA418),'int',['pointer','pointer']);
  Interceptor.attach(mod.base.add(0x87F2420),{onEnter(args){frame=args[2].readPointer();}});
  Interceptor.attach(mod.base.add(0x87EB860),{onEnter(args){frame=args[0].add(0xA8).readPointer();}});
  Interceptor.attach(mod.base.add(0x880FF48),{onEnter(args){visual=args[0];visualCalls++;}});
  Interceptor.attach(mod.base.add(0x88095A0),{onEnter(){changes++;}});
  function pokemon(o){
    if(o.isNull())return null;
    const data=o.add(0x18).readPointer();
    return {id:field(data,'id_').readU64().toString(),species:field(data,'pokemonId_').readS32(),
      cp:field(data,'cp_').readS32(),displayCp:field(data,'displayCp_').readS32(),cpMultiplier:field(data,'cpMultiplier_').readFloat(),
      additionalCpMultiplier:field(data,'additionalCpMultiplier_').readFloat(),
      maxStamina:field(data,'maxStamina_').readS32(),serializedStamina:field(data,'stamina_').readS32(),
      health:health(o,ptr(0)),energy:energy(o,ptr(0)),fainted:!!o.add(0x28).readU8()};
  }
  function snapshot(){
    if(frame.isNull())return {observed:false};
    const player=frame.add(0x10).readPointer(),list=player.add(0x10).readPointer();
    const count=unbox(call(method(getClass(list),'get_Count',0),list)).readS32(),party=[];
    for(let i=0;i<count;i++){
      const index=Memory.alloc(4);index.writeS32(i);
      party.push(pokemon(call(method(getClass(list),'get_Item',1),list,[index])));
    }
    const model=visual.isNull()?null:{observeCalls:visualCalls,changeCalls:changes,
      id:field(visual.add(0x1F8).readPointer(),'id_').readU64().toString(),
      stage:visual.add(0x158).readS32(),health:visual.add(0x1EC).readS32(),
      alive:!!visual.add(0x1E2).readU8(),changePending:!visual.add(0x1C0).readPointer().isNull()};
    return {observed:true,model,cpReads,status:frame.add(0x38).readS32(),serverMs:frame.add(0x30).readS64().toString(),
      activeIndex:player.add(0x18).readS32(),party,enemy:pokemon(frame.add(0x28).readPointer())};
  }
  Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
    for(const job of pending.splice(0))try{job.resolve(snapshot());}catch(error){job.reject(error);}
  }});
  rpc.exports={snapshot(){return new Promise((resolve,reject)=>pending.push({resolve,reject}));}};
})();
