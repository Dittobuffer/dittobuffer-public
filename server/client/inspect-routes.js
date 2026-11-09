// Opt-in 0.429.2 diagnostic hooks. Observes native services without changing state.
(()=>{
  let map=ptr(0),location=ptr(0),last=0,cells=0;
  let completion=ptr(0),completionAt=0,completionSeen='';
  const label=api('class_get_name','pointer',['pointer']);
  let probed='',presentationProbe='',reconstructed='';
  function traceInvoke(m,self,args=[]){
    const argv=Memory.alloc(Math.max(8,args.length*8));args.forEach((v,i)=>argv.add(i*8).writePointer(v));
    const error=Memory.alloc(8);error.writePointer(ptr(0));const result=invoke(m,self,args.length?argv:ptr(0),error);
    if(!error.readPointer().isNull()){
      const formatted=Memory.alloc(4096),stack=Memory.alloc(8192);
      api('format_exception','void',['pointer','pointer','int'])(error.readPointer(),formatted,4096);
      api('format_stack_trace','void',['pointer','pointer','int'])(error.readPointer(),stack,8192);
      throw Error(formatted.readUtf8String()+' stack='+stack.readUtf8String());
    }
    return result;
  }
  function parseMessage(name,bytes){
    if(!Array.isArray(bytes)||bytes.length>131072||bytes.some(v=>!Number.isInteger(v)||v<0||v>255))throw Error('Invalid bounded diagnostic wire payload');
    const raw=arrayNew(klass('System','Byte'),bytes.length);raw.add(32).writeByteArray(bytes);
    const streamClass=klass('Niantic.Protobuf','CodedInputStream'),stream=objectNew(streamClass);call(overload(streamClass,'.ctor','System.Byte[]'),stream,[raw]);
    const type=klass('Holoholo.Rpc',name),value=objectNew(type);call(method(type,'.ctor',0),value);call(overload(type,'MergeFrom','Niantic.Protobuf.CodedInputStream'),value,[stream]);return value;
  }
  function reconstruct(command){
    if(command.recreate!==true||!command.id||reconstructed===command.id)return;
    reconstructed=command.id;
    try{
      const factoryClass=klass('Niantic.Holoholo.Routes','IRouteCompletionGuiControllerFactory');
      const type=api('type_get_object','pointer',['pointer'])(api('class_get_type','pointer',['pointer'])(factoryClass));
      let factory=ptr(0);
      for(const context of [...objects('Zenject','SceneContext'),...objects('Zenject','ProjectContext')]){
        try{
          const container=call(method(getClass(context),'get_Container',0),context);
          factory=call(overload(getClass(container),'Resolve','System.Type'),container,[type]);if(!factory.isNull())break;
        }catch(_){}
      }
      if(factory.isNull())throw Error('No active native Route completion factory');
      const controllerClass=klass('Niantic.Holoholo.Routes','RouteCompletionGuiController');
      const dataClass=fromType(api('method_get_param','pointer',['pointer','uint'])(method(controllerClass,'Initialize',1),0));
      const data=objectNew(dataClass);call(method(dataClass,'.ctor',0),data);
      for(const [field,value] of [['RouteData',parseMessage('SharedRouteProto',command.route)],['ProgressProto',parseMessage('ProgressRouteOutProto',command.receipt)]]){
        const info=api('class_get_field_from_name','pointer',['pointer','pointer'])(dataClass,Memory.allocUtf8String(field));
        api('field_set_value_object','void',['pointer','pointer','pointer'])(data,info,value);
      }
      traceInvoke(method(getClass(factory),'Create',1),factory,[data]);
      console.log('Routes isolated cached-receipt presentation created; no gameplay action/reward claimed');
    }catch(error){console.log('Routes isolated presentation diagnosis '+error.message);}
    completion=ptr(0);
  }
  function objects(ns,name){
    const type=api('type_get_object','pointer',['pointer'])(api('class_get_type','pointer',['pointer'])(klass(ns,name)));
    const values=call(method(klass('UnityEngine','Resources'),'FindObjectsOfTypeAll',1),ptr(0),[type]);
    return Array.from({length:values.add(0x18).readU64().toNumber()},(_,i)=>values.add(0x20+i*8).readPointer());
  }
  function objectState(value){
    const go=call(method(getClass(value),'get_gameObject',0),value);
    const active=call(method(getClass(go),'get_activeInHierarchy',0),go).add(16).readU8();
    const name=call(method(klass('UnityEngine','Object'),'get_name',0),go);
    return name.add(20).readUtf16String(name.add(16).readS32())+' active='+active;
  }
  // Observe the existing GUI visibility boundary, not throwing coroutine entry
  // points. runtime_invoke catches managed exceptions inside IL2CPP itself.
  Interceptor.attach(mod.base.add(0x8093fb8),{onEnter(args){
    if(!args[0].isNull()&&label(getClass(args[0])).readUtf8String()==='RouteCompletionGuiController'){
      if(!completion.equals(args[0])){completion=args[0];completionAt=Date.now();}
    }
  }});
  function inspectCompletion(){
    let command={};try{command=JSON.parse(File.readAllText('/data/user/0/org.librenlp.pokemongo.dev/files/m202-reward-stage-diagnosis.json'));}catch(_){}
    reconstruct(command);
    if(command.id&&command.id!==presentationProbe){presentationProbe=command.id;completionSeen='';}
    if(completion.isNull()){
      try{
        const type=api('type_get_object','pointer',['pointer'])(api('class_get_type','pointer',['pointer'])(klass('Niantic.Holoholo.Routes','RouteCompletionGuiController')));
        const objects=call(method(klass('UnityEngine','Resources'),'FindObjectsOfTypeAll',1),ptr(0),[type]);
        for(let i=0;i<objects.add(0x18).readU64().toNumber();i++){
          const candidate=objects.add(0x20+i*8).readPointer();
          if(!candidate.add(0x180).readPointer().isNull()){completion=candidate;completionAt=Date.now()-5001;break;}
        }
      }catch(e){if(completionSeen!=='lookup-failed'){console.log('Routes completion lookup: '+e.message);completionSeen='lookup-failed';}}
    }
    if(completion.isNull()||Date.now()-completionAt<5000||completionSeen===completion.toString())return;
    completionSeen=completion.toString();
    try{
      const loot=completion.add(0x180).readPointer(),converter=completion.add(0x168).readPointer();
      console.log('Routes completion diagnostic loot='+loot+' converter='+converter+' animator='+completion.add(0x130).readPointer()+' factory='+completion.add(0x160).readPointer());
      const layer=Memory.alloc(4);layer.writeS32(0);
      const state=call(method(klass('UnityEngine','Animator'),'GetCurrentAnimatorStateInfo',1),completion.add(0x130).readPointer(),[layer]);
      console.log('Routes completion animator state='+state.add(16).readS32()+' normalizedTime='+state.add(28).readFloat()+' length='+state.add(32).readFloat()+' speed='+state.add(36).readFloat());
      const group=call(method(getClass(completion),'get_RootCanvasGroup',0),completion);
      console.log('Routes completion canvas '+objectState(completion)+' alpha='+call(method(getClass(group),'get_alpha',0),group).add(16).readFloat()+' visible='+call(method(getClass(completion),'get_IsVisible',0),completion).add(16).readU8()+' open='+call(method(getClass(completion),'get_IsOpen',0),completion).add(16).readU8()+' state='+completion.add(0x70).readS32()+' rawVisible='+completion.add(0x90).readU8()+' schedule='+completion.add(0x198).readPointer());
      for(const gui of objects('Niantic.Holoholo.NUI','GuiController')){
        if(!call(method(getClass(gui),'get_gameObject',0),gui).isNull()&&objectState(gui).endsWith('active=1'))console.log('Routes active GUI '+label(getClass(gui)).readUtf8String()+' '+objectState(gui)+' state='+gui.add(0x70).readS32()+' rawVisible='+gui.add(0x90).readU8());
      }
      for(const [name,offset] of [['MajorRewardSequenceView',0x30],['MajorRewardSequenceViewOld',0x78]]){
        for(const view of objects('Niantic.Holoholo.UI',name)){
          const animator=view.add(offset).readPointer();
          const info=animator.isNull()?ptr(0):call(method(klass('UnityEngine','Animator'),'GetCurrentAnimatorStateInfo',1),animator,[layer]);
          console.log('Routes reward view '+name+' '+objectState(view)+' animator='+animator+' time='+(info.isNull()?'null':info.add(28).readFloat())+' promise='+call(method(getClass(view),'get_CompletionPromise',0),view));
        }
      }
      if(!loot.isNull()&&!converter.isNull()){
        const rewards=call(method(getClass(converter),'LootToMajorRewards',1),converter,[loot]);
        console.log('Routes completion converter class='+label(getClass(rewards)).readUtf8String()+' count='+rewards.add(0x18).readS32());
      }
      if(command.initialize===true){
        const argv=Memory.alloc(8);argv.writePointer(completion.add(0x178).readPointer());
        const exception=Memory.alloc(8);exception.writePointer(ptr(0));
        invoke(method(getClass(completion),'Initialize',1),completion,argv,exception);
        const error=exception.readPointer();
        if(error.isNull())console.log('Routes isolated Initialize completed');
        else{
          const formatted=Memory.alloc(4096),stack=Memory.alloc(8192);
          api('format_exception','void',['pointer','pointer','int'])(error,formatted,4096);
          api('format_stack_trace','void',['pointer','pointer','int'])(error,stack,8192);
          console.log('Routes isolated Initialize exception '+formatted.readUtf8String()+' stack='+stack.readUtf8String());
        }
      }
      // Isolated opt-in reproduction of the same presentation stage. Invoke
      // through IL2CPP's managed exception boundary; never intercept MoveNext.
      // This is diagnostic evidence, not successful normal UI acceptance.
      if(command.open_controller===true){call(method(getClass(completion),'Open',0),completion);console.log('Routes isolated GUI Open invoked');}
      const stage=command.enabled===true;
      if(stage){
        const iterator=call(method(getClass(completion),'bjjd',0),completion);
        const move=method(getClass(iterator),'MoveNext',0);
        console.log('Routes isolated iterator '+label(getClass(iterator)).readUtf8String()+' method='+move.readPointer().sub(mod.base)+' state='+iterator.add(16).readS32());
        const moved=call(move,iterator);
        console.log('Routes completion isolated stage MoveNext='+moved.add(16).readU8()+' state='+iterator.add(16).readS32());
      }
    }catch(e){console.log('Routes completion managed diagnosis: '+e.message);}
  }
  Interceptor.attach(mod.base.add(0x7547fe4),{onEnter(args){try{const size=args[0].add(16).readS32();if(size>0&&size<8192)console.log('Routes client error '+args[0].add(20).readUtf16String(size).replace(/(?:Bearer|authorization|token)[=: ]+[^\s,]+/gi,'[credential omitted]'));}catch(_){}}});
  function overload(k,name,type){
    const next=api('class_get_methods','pointer',['pointer','pointer']),param=api('method_get_param','pointer',['pointer','uint']),paramCount=api('method_get_param_count','uint',['pointer']),typeName=api('type_get_name','pointer',['pointer']),methodName=api('method_get_name','pointer',['pointer']);
    const it=Memory.alloc(8);it.writePointer(ptr(0));
    for(let m=next(k,it);!m.isNull();m=next(k,it))if(methodName(m).readUtf8String()===name&&paramCount(m)===1&&typeName(param(m,0)).readUtf8String()===type)return m;
    throw Error('Diagnostic overload unavailable: '+name+' '+type);
  }
  function parseProbe(){
    try{
      const suite=JSON.parse(File.readAllText('/data/user/0/org.librenlp.pokemongo.dev/files/m20-route-probes.json'));
      if(probed===suite.id)return;probed=suite.id;
      if(!Array.isArray(suite.probes)||suite.probes.length>32)throw Error('Invalid public probe suite');
      for(const probe of suite.probes){
      if(!Array.isArray(probe.bytes)||probe.bytes.length>131072)throw Error('Oversized public probe');
      const data=arrayNew(klass('System','Byte'),probe.bytes.length);data.add(32).writeByteArray(probe.bytes);
      const inputClass=klass('Niantic.Protobuf','CodedInputStream'),input=objectNew(inputClass);
      call(overload(inputClass,'.ctor','System.Byte[]'),input,[data]);
      const outputClass=klass('Holoholo.Rpc','GetRoutesOutProto'),output=objectNew(outputClass);call(method(outputClass,'.ctor',0),output);
      const args=Memory.alloc(8);args.writePointer(input);const exception=Memory.alloc(8);exception.writePointer(ptr(0));
      invoke(overload(outputClass,'MergeFrom','Niantic.Protobuf.CodedInputStream'),output,args,exception);
      const error=exception.readPointer();
      if(!error.isNull()){
        const msg=call(method(getClass(error),'get_Message',0),error);
        console.log('Routes native parser '+probe.name+' exception='+label(getClass(error)).readUtf8String()+' message='+msg.add(20).readUtf16String(msg.add(16).readS32()).slice(0,200));continue;
      }
      console.log('Routes native parser '+probe.name+' status='+output.add(32).readS32()+' routeCount='+output.add(24).readPointer().add(24).readS32()+' tabCount='+output.add(40).readPointer().add(24).readS32());
      }
    }catch(e){console.log('Routes native parser probe unavailable: '+e.message);}
  }
  for(const [rva,name] of [[0x7c3b64c,'MapContent.initialize'],[0x7c3c468,'MapContent.cells'],[0x7cdefa8,'Location.start'],[0x829c6d4,'Route.pause'],[0x829c9ac,'Route.resume']]){
    Interceptor.attach(mod.base.add(rva),{onEnter(args){
      if(name.startsWith('MapContent'))map=args[0];
      if(name==='Location.start')location=args[0];
      if(name!=='MapContent.cells'||cells++<3)console.log('Routes diagnostic '+name);
    }});
  }
  for(const [rva,name] of [[0x82c65bc,'Nearby.request'],[0x82b3944,'Nearby.byCells'],[0x82bfc18,'Creation.enabled'],[0x82cf81c,'Route.supported'],[0x82cefa4,'Route.valid']]){
    Interceptor.attach(mod.base.add(rva),{onEnter(args){
      console.log('Routes diagnostic '+name);
      if(name==='Nearby.request'){
        const throttle=args[0].add(0x58).readPointer();
        console.log('Routes diagnostic throttle '+hexdump(throttle.add(0x20),{length:80,header:false,ansi:false}));
        const provider=throttle.add(0x68).readPointer();
        console.log('Routes diagnostic throttle provider='+label(getClass(provider)).readUtf8String()+' valid='+call(method(getClass(provider),'get_HasValidLocation',0),provider).add(16).readU8());
      }
    },onLeave(result){if(name==='Creation.enabled'||name.startsWith('Route.'))console.log('Routes diagnostic '+name+'='+result.toInt32());}});
  }
  Interceptor.attach(mod.base.add(0x82c6794),{onEnter(){console.log('Routes diagnostic Nearby.scheduled');}});
  let lastTrackingError=-1;
  let anchorLog=0;
  for(const [rva,name] of [[0x8201e44,'Anchor.initialize'],[0x82047b0,'Anchor.setPlace'],[0x8204700,'Anchor.closed'],[0x82725f8,'Creator.selectAnchors']]){
    Interceptor.attach(mod.base.add(rva),{onEnter(args){if(anchorLog++<40)console.log('Routes diagnostic '+name);},onLeave(value){if(name==='Anchor.closed'&&anchorLog<40)console.log('Routes diagnostic Anchor.closed='+value.toInt32());}});
  }
  Interceptor.attach(mod.base.add(0x82affc4),{onLeave(value){
    const error=value.toInt32();
    if(error!==lastTrackingError){console.log('Routes diagnostic trackingError='+error);lastTrackingError=error;}
  }});
  Interceptor.attach(mod.base.add(0x82b907c),{onEnter(args){
    const value=args[1],count=(offset)=>value.add(offset).readPointer().add(24).readS32();
    console.log('Routes diagnostic decoded response status='+value.add(32).readS32()+' mapCells='+count(16)+' routes='+count(24)+' tabs='+count(40));
  }});
  Interceptor.attach(mod.base.add(0x82b3914),{onEnter(args){console.log('Routes diagnostic NearbyInfo routes='+args[1].add(24).readS32()+' tabs='+args[2].add(24).readS32());}});
  Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
    if(Date.now()-last<10000)return;last=Date.now();
    inspectCompletion();
    parseProbe();
    try{
      if(!location.isNull())console.log('Routes diagnostic location valid='+call(method(getClass(location),'get_HasValidLocation',0),location).add(16).readU8()+' measured='+call(method(getClass(location),'get_IsMeasuredLocation',0),location).add(16).readU8()+' raw='+hexdump(location.add(0x28),{length:48,header:false,ansi:false}));
      if(!map.isNull()){
        const provider=map.add(0x38).readPointer();
        console.log('Routes diagnostic map initial='+map.add(0x104).readU8()+' paused='+map.add(0x1a8).readU8()+' provider='+label(getClass(provider)).readUtf8String());
      }
    }catch(e){console.log('Routes diagnostic unavailable: '+e.message);}
  }});
})();
