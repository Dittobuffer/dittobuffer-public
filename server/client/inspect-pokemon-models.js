// Read-only native PokemonModel/animation/costume diagnostics, on Unity's thread.
(()=>{
  const unbox=api('object_unbox','pointer',['pointer']);
  const typeObject=api('type_get_object','pointer',['pointer']);
  const classType=api('class_get_type','pointer',['pointer']);
  const find=new NativeFunction(mod.base.add(0xA454928),'pointer',['pointer','bool','pointer']);
  const modelClass=klass('Niantic.PokemonAssets','PokemonModel');
  const text=o=>o.isNull()?null:o.add(20).readUtf16String(o.add(16).readS32());
  const name=o=>o.isNull()?null:text(call(method(getClass(o),'get_name',0),o));
  let pending=[];
  function snapshot(){
    const found=find(typeObject(classType(modelClass)),0,ptr(0)),models=[];
    for(let i=0;i<found.add(24).readU64().toNumber();i++){
      const o=found.add(32+i*8).readPointer(),controller=o.add(0xA0).readPointer();
      const value={object:o.toString(),name:name(o),viewId:o.add(0x20).readS32(),formId:o.add(0x24).readS32(),gender:o.add(0x48).readS32(),shiny:!!o.add(0x58).readU8(),costume:text(o.add(0x68).readPointer()),
        animator:o.add(0xE8).readPointer().toString(),fbx:name(o.add(0xC0).readPointer())};
      try{
        if(!controller.isNull()){
          value.animationContext=unbox(call(method(getClass(controller),'get_CurrentContext',0),controller)).readS32();
          value.normalizedTime=unbox(call(method(getClass(controller),'get_CurrentAnimationsNormalizedTime',0),controller)).readFloat();
        }
        const head=o.add(0xB8).readPointer();
        if(!head.isNull()){
          const position=unbox(call(method(getClass(head),'get_position',0),head));
          value.head=[position.readFloat(),position.add(4).readFloat(),position.add(8).readFloat()];
        }
      }catch(e){value.diagnosticError=String(e);}
      models.push(value);
    }
    const setters=find(typeObject(classType(klass('Niantic.PokemonAssets','PokemonCostumeSetter'))),0,ptr(0)),costumes=[];
    for(let i=0;i<setters.add(24).readU64().toNumber();i++){
      const setter=setters.add(32+i*8).readPointer(),list=setter.add(0x40).readPointer();
      const value={model:setter.add(0x28).readPointer().toString(),activeCostume:text(setter.add(0x38).readPointer()),attachments:[]};
      if(!list.isNull()){
        const array=list.add(0x10).readPointer(),size=list.add(0x18).readS32();
        if(size<0||size>100)throw Error('Unexpected costume list size');
        for(let j=0;j<size;j++){
          const go=array.add(32+j*8).readPointer();
          if(!go.isNull())value.attachments.push({name:name(go),active:!!unbox(call(method(getClass(go),'get_activeInHierarchy',0),go)).readU8()});
        }
      }
      costumes.push(value);
    }
    return {at:Date.now(),models,costumes};
  }
  Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
    for(const job of pending.splice(0))try{job.resolve(snapshot());}catch(e){job.reject(e);}
  }});
  rpc.exports={snapshot(){return new Promise((resolve,reject)=>pending.push({resolve,reject}));}};
})();
