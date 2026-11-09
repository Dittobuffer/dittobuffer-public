// Explicit, process-only M14 geographic fixtures. Never loaded for normal play.
(()=>{
  const origin=globalThis.LIBRE_GEOGRAPHIC_FIXTURE;
  if(!origin)return;
  const plugin=Process.getModuleByName('libNianticLabsPlugin.so');
  const table=Memory.alloc(288);
  new NativeFunction(plugin.getExportByName('GetN2Api'),'void',['pointer'])(table);
  const setter=new NativeFunction(table.add(0x110).readPointer(),'uint',['pointer','double','double','double','double','uint','uint']);
  const getter=new NativeFunction(table.add(0xc8).readPointer(),'uint',['pointer','pointer']);
  let context=ptr(0);
  // Real Java callbacks still arrive while the plugin debug getter is active.
  // Keep the two managed location paths consistent in this isolated process.
  // Replace only the callback's argument with our own 48-byte ABI payload.
  const locationPayload=Memory.alloc(48);
  Interceptor.attach(mod.base.add(0x95321d0),{onEnter(args){
    if(context.isNull())return;
    getter(context,locationPayload);
    locationPayload.add(32).writeDouble(Date.now()/1000);
    args[1]=locationPayload;
  }});
  let applied=false;
  let lastOrigin='';
  let lastFix=0;
  Interceptor.attach(table.add(0x78).readPointer(),{onEnter(args){
    context=args[0];
    const key=origin.lat+','+origin.lng;
    if(applied&&(!globalThis.LIBRE_STANDALONE||(key===lastOrigin&&Date.now()-lastFix<10000)))return;
    const status=setter(args[0],origin.lat,origin.lng,0,1,3,1);
    if(!applied)console.log('M14 simulated geographic origin applied; status='+status+'; no outdoor walking claim');
    applied=true;
    lastOrigin=key;
    lastFix=Date.now();
  }});
  const nameMethod=method(klass('UnityEngine','Object'),'get_name',0);
  Interceptor.attach(mod.base.add(0x7b9ed90),{onEnter(args){
    try{
      const name=call(nameMethod,args[1]);
      const text=name.isNull()?'null':name.add(20).readUtf16String(name.add(16).readS32());
      console.log('M14 native prefab instantiation: '+text);
    }catch(e){console.log('M14 prefab name inspection unavailable: '+e.message);}
  }});
})();
