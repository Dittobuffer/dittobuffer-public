// Read-only 0.429.2 encounter physics trace. No collision/trajectory overrides.
(()=>{
  const unbox=api('object_unbox','pointer',['pointer']);
  const typeObject=api('type_get_object','pointer',['pointer']);
  const classType=api('class_get_type','pointer',['pointer']);
  const find=new NativeFunction(mod.base.add(0xA454928),'pointer',['pointer','bool','pointer']);
  const ns='Niantic.Holoholo.Encounter';
  const value=(o,n)=>call(method(getClass(o),'get_'+n,0),o);
  const scalar=(o,n,t='readFloat')=>unbox(value(o,n))[t]();
  const str=o=>o.isNull()?null:o.add(20).readUtf16String(o.add(16).readS32());
  const name=o=>o.isNull()?null:str(value(o,'name'));
  const vec=o=>[o.readFloat(),o.add(4).readFloat(),o.add(8).readFloat()];
  const vector=(o,n)=>vec(unbox(value(o,n)));
  const objects=(namespace,type)=>{
    const a=find(typeObject(classType(klass(namespace,type))),0,ptr(0)),out=[];
    for(let i=0;i<a.add(24).readU64().toNumber();i++)out.push(a.add(32+i*8).readPointer());
    return out;
  };
  function transform(o){
    const t=value(o,'transform');
    return {name:name(o),position:vector(t,'position'),scale:vector(t,'lossyScale'),
      localScale:vector(t,'localScale'),eulerAngles:vector(t,'eulerAngles'),forward:vector(t,'forward')};
  }
  function collider(o){
    const go=value(o,'gameObject'),b=unbox(value(o,'bounds')),t=value(o,'transform'),path=[];
    let parent=t;
    for(let i=0;i<9&&!parent.isNull();i++){path.push(name(parent));parent=value(parent,'parent');}
    const body=value(o,'attachedRigidbody');
    return {object:String(o),type:api('class_get_name','pointer',['pointer'])(getClass(o)).readCString(),path:path.reverse().join('/'),
      enabled:!!scalar(o,'enabled','readU8'),includeLayers:scalar(o,'includeLayers','readS32'),layer:scalar(go,'layer','readS32'),
      center:vec(b),extents:vec(b.add(12)),...transform(o),body:String(body)};
  }
  function ball(o){
    const body=o.add(0xC0).readPointer(),col=o.add(0xC8).readPointer(),props=o.add(0xE0).readPointer();
    const target=o.add(0x250).readPointer();
    const art=offset=>{const v=o.add(offset).readPointer();return v.isNull()?null:transform(v);};
    return {object:String(o),ballType:o.add(0x374).readS32(),pokemonHit:String(o.add(0x378).readPointer()),...transform(o),
      maxBall:{holding:!!o.add(0x240).readU8(),scale:vec(o.add(0x148)),rotation:vec(o.add(0x154)),
        shrinkDelay:o.add(0x160).readFloat(),shrinkLength:o.add(0x164).readFloat(),
        catchDelay:o.add(0x168).readFloat(),animationSpeed:o.add(0x16C).readFloat(),
        normalArt:art(0x220),maxArt:art(0x298)},
      target:target.isNull()?null:{object:String(target),invulnerable:!!scalar(target,'Invulnerable','readU8'),position:transform(target).position},
      velocity:body.isNull()?null:vector(body,'linearVelocity'),isKinematic:body.isNull()?null:!!scalar(body,'isKinematic','readU8'),
      collider:col.isNull()?null:collider(col),throwProperties:props.isNull()?null:{
        curve:vec(props.add(0x10)),overshoot:props.add(0x28).readFloat(),undershoot:props.add(0x2C).readFloat(),
        minAngle:props.add(0x30).readFloat(),maxAngle:props.add(0x34).readFloat(),maxSpeed:props.add(0x3C).readFloat(),
        velocityMultiplier:vec(props.add(0x44)).slice(0,2)}};
  }
  function snapshot(){
    const pokemon=objects(ns,'EncounterPokemon').map(o=>({object:String(o),...transform(o),
      initialized:!!o.add(0x1E0).readU8(),inPokeball:!!o.add(0x1FB).readU8(),invulnerable:!!scalar(o,'Invulnerable','readU8'),
      canAct:!!o.add(0x1FC).readU8(),base:vec(o.add(0x218)),target:vec(o.add(0x224)),cameraTarget:vec(o.add(0x230)),
      preferredCameraDistance:scalar(o,'PreferredCameraDistance'),heightOffGround:scalar(o,'HeightOffGround')}));
    const cameras=objects('UnityEngine','Camera').filter(o=>scalar(o,'enabled','readU8')).map(o=>({object:String(o),...transform(o),
      fov:scalar(o,'fieldOfView'),near:scalar(o,'nearClipPlane'),far:scalar(o,'farClipPlane'),mask:scalar(o,'cullingMask','readS32')}));
    const balls=objects(ns,'Pokeball').map(ball);
    const colliders=objects('UnityEngine','Collider').map(collider).filter(o=>/encounter|pokeball|reticle|pm0143|monsterball/i.test(o.path));
    const reticles=objects(ns,'Reticle').map(o=>({object:String(o),...transform(o),worldRadius:scalar(o,'WorldRadius'),
      normalizedSize:scalar(o,'NormalizedSize'),outer:collider(o.add(0x28).readPointer())}));
    return {at:Date.now(),pokemon,cameras,balls,colliders,reticles};
  }
  function readiness(){
    return {at:Date.now(),pokemon:objects(ns,'EncounterPokemon').map(o=>({object:String(o),
      initialized:!!o.add(0x1E0).readU8(),inPokeball:!!o.add(0x1FB).readU8(),
      invulnerable:!!scalar(o,'Invulnerable','readU8'),canAct:!!o.add(0x1FC).readU8(),
      heightOffGround:scalar(o,'HeightOffGround')}))};
  }
  let last=0,frames=false,interval=100,pending=[];
  function emit(event,data){send({at:Date.now(),event,...data});}
  Interceptor.attach(mod.base.add(0x869A964),{onEnter(){emit('buddy-cameo-sequence',{});}});
  for(const [rva,event] of [[0x86E8ACC,'launch'],[0x86E7108,'initialize'],[0x86E7B64,'collision'],[0x86E8264,'trigger'],[0x86E84C8,'throw-result'],[0x86EAD48,'submit-throw']]){
    Interceptor.attach(mod.base.add(rva),{onEnter(args){
      try{
        const out={ball:ball(args[0])};
        if(event==='collision')out.other=collider(value(args[1],'collider'));
        if(event==='trigger')out.other=collider(args[1]);
        // btie(bool) takes forceMiss, not hit. The submitted value type's
        // third ARM64 argument contains HitBullseye/Spinning/Missed bytes.
        if(event==='throw-result')out.forceMiss=!!args[1].toInt32();
        if(event==='submit-throw'){
          const flags=args[2].toInt32();
          out.submitted={hitBullseye:!!(flags&1),spinning:!!(flags&256),missed:!!(flags&65536)};
        }
        emit(event,out);
      }catch(error){emit(event,{error:String(error)});}
    },onLeave(){
      if(event==='throw-result'){
        const flags=this.context.x1.toInt32();
        emit('graded-throw',{hitBullseye:!!(flags&1),spinning:!!(flags&256),missed:!!(flags&65536)});
      }
    }});
  }
  Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
    for(const job of pending.splice(0))try{job.resolve(job.light?readiness():snapshot());}catch(e){job.reject(new Error(String(e)+'\n'+e.stack));}
    const at=Date.now();
    if(frames&&at-last>=interval){last=at;try{emit('frame',{balls:objects(ns,'Pokeball').map(ball)});}catch(error){emit('error',{error:String(error)});}}
  }});
  rpc.exports={snapshot(){return new Promise((resolve,reject)=>pending.push({resolve,reject}));},
    readiness(){return new Promise((resolve,reject)=>pending.push({resolve,reject,light:true}));},
    trace(ms){interval=ms;frames=true;},stop(){frames=false;}};
})();
