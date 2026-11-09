// Explicit private-client foreground recording policy. It never represents
// Adventure Sync authorization, Android grants or background fitness support.
const LibreNlpRoutesForeground=(()=>{
  function trackerMode(requested,enabled,focused,valid){
    return requested===1&&enabled===true&&focused===true&&valid===true?2:requested;
  }
  return {trackerMode};
})();
if(typeof module==='object'&&module.exports)module.exports=LibreNlpRoutesForeground;

if(typeof Process!=='undefined'&&runtime.routes_foreground_creation===true)(()=>{
  let creator=ptr(0),tracker=ptr(0),busy=false,lastPolicy='';
  const unbox=api('object_unbox','pointer',['pointer']);
  const boolean=value=>!value.isNull()&&unbox(value).readU8()!==0;
  function foreground(){return boolean(call(method(klass('UnityEngine','Application'),'get_isFocused',0),ptr(0)));}
  function ready(value){
    if(value.isNull()||!foreground())return false;
    const provider=value.add(0x58).readPointer();
    return !provider.isNull()&&boolean(call(method(getClass(provider),'get_HasValidLocation',0),provider));
  }
  // These calls also cover resuming an existing draft after a cold launch,
  // when StartCreation does not enter the authorization/planner callback.
  for(const rva of [0x82a7e68,0x82a894c])Interceptor.attach(mod.base.add(rva),{onEnter(args){creator=args[0];console.log('Routes foreground recording request state='+creator.add(0x208).readS32()+' focused='+foreground()+' ready='+ready(creator));}});
  if(runtime.routes_diagnostics===true)Interceptor.attach(mod.base.add(0x79c3c58),{onEnter(args){console.log('Routes diagnostic permission prompt context='+args[2].toInt32()+' caller='+this.returnAddress.sub(mod.base));}});
  // RouteCreationService.bits asks the Adventure Sync adapter to authorize.
  // In this explicitly configured mode, continue the normal native planner
  // using its existing foreground tracker. No adapter/consent flag is changed.
  const promptAddress=mod.base.add(0x82a9058);
  const originalPrompt=new NativeFunction(promptAddress,'void',['pointer','pointer','pointer','pointer']);
  Interceptor.replace(promptAddress,new NativeCallback((self,continuation,cancel,info)=>{
    try{
      creator=self;
      console.log('Routes foreground planner eligibility='+ready(self));
      if(ready(self)&&!continuation.isNull()){
        console.log('Routes foreground planner selected; Adventure Sync state unchanged');
        call(method(getClass(continuation),'Invoke',0),continuation);return;
      }
    }catch(e){console.log('Routes foreground planner unavailable: '+e.message);}
    originalPrompt(self,continuation,cancel,info);
  },'void',['pointer','pointer','pointer','pointer']));
  // The tracker has Creation=1 (AS required outside Editor) and Travel=2
  // (native ManualSession allowed). Select only the latter tracking policy;
  // RouteCreationService retains its own planner/recording/submission states.
  const setType=new NativeFunction(mod.base.add(0x82af560),'void',['pointer','int','pointer']);
  // Select the policy at the actual scheduler and validation entry points.
  // Do not hook the adjacent eight-byte property setters: their trampolines
  // can overlap and a scheduler can begin before set_RouteId returns.
  for(const rva of [0x82af428,0x82affc4])Interceptor.attach(mod.base.add(rva),{onEnter(args){
    const valid=ready(creator),type=args[0].add(0xd8).readS32(),policy=type+':'+valid;
    if(runtime.routes_diagnostics===true&&policy!==lastPolicy){lastPolicy=policy;console.log('Routes diagnostic tracker policy type='+type+' ready='+valid);}
    if(valid&&type!==2){
      tracker=args[0];setType(tracker,2,ptr(0));
      console.log('Routes native ManualSession policy active; type='+tracker.add(0xd8).readS32());
    }
  }});
  Interceptor.attach(mod.base.add(0x82afbd0),{onEnter(){
    // OnPause also runs during tracker/session reconfiguration. Only an
    // actual loss of application focus should pause a recording.
    if(busy||foreground()||creator.isNull()||tracker.isNull()||creator.add(0x208).readS32()!==3)return;
    busy=true;
    try{
      call(method(getClass(creator),'PauseRecording',0),creator);
      console.log('Routes foreground recording paused on application background');
    }catch(e){console.log('Routes background pause pending: '+e.message);}
    finally{busy=false;}
  }});
})();
