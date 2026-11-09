// The hatch response carries reward deltas, while the detail UI's wallet is
// sourced from GetPlayer. Refresh through the real client RPC path after the
// hatch result, instead of writing any local currency or reward values.
(()=>{
  // EggHatchService.ddf.bmon is the actual response delegate. The public
  // bmpp iterator factory is inlined at its call site and cannot be hooked
  // reliably. ddf owns service at +0x10; service owns PlayerService at +0x10.
  Interceptor.attach(mod.base.add(0x8454168),{
    onEnter(args){
      const service=args[0].add(16).readPointer();
      this.player=service.isNull()?ptr(0):service.add(16).readPointer();
    },
    onLeave(){
      if(this.player.isNull())return;
      try{
        // RefreshPlayerFromServer has a 180-second throttle. The explicit
        // request method uses the same authoritative update handler directly.
        call(method(getClass(this.player),'RequestPlayerFromServer',0),this.player);
        console.log('Hatch wallet refresh requested from server');
      }catch(error){console.log('Hatch wallet refresh: '+error.message);}
    }
  });
  console.log('Authoritative hatch wallet refresh active');
})();

// 0.429.2's Today-view producer sends QuestStampCardProto directly to
// QuestPunchCard.ResetData, whose implementation accepts QuestPunchCard.Data.
// Adapt the argument shape; the widget still renders the authoritative card
// and decides fullness and pending encounters through its own normal code.
(()=>{
  const className=api('class_get_name','pointer',['pointer']);
  const nested=api('class_get_nested_types','pointer',['pointer','pointer']);
  const barrier=api('gc_wbarrier_set_field','void',['pointer','pointer','pointer']);
  let wrapperClass=null;
  Interceptor.attach(mod.base.add(0x7F40750),{
    onEnter(args){
      if(args[0].isNull()||args[1].isNull())return;
      const widget=getClass(args[0]);
      if(className(widget).readUtf8String()!=='QuestPunchCard' ||
         className(getClass(args[1])).readUtf8String()!=='QuestStampCardProto')return;
      if(wrapperClass===null){
        const it=Memory.alloc(Process.pointerSize);it.writePointer(ptr(0));
        for(let k=nested(widget,it);!k.isNull();k=nested(widget,it)){
          if(className(k).readUtf8String()==='Data'){wrapperClass=k;break;}
        }
      }
      if(wrapperClass===null)return;
      const data=objectNew(wrapperClass);
      call(method(wrapperClass,'.ctor',0),data);
      barrier(data,data.add(16),args[1]);
      args[1]=data;
    }
  });
  console.log('Research stamp-card data adapter active');
})();
