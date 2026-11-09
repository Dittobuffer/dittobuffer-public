// Opt-in native Max compatibility diagnostics. Does not simulate game outcomes.
(()=>{
 const freeMaxHandle=api('gchandle_free','void',['pointer']);
 Interceptor.attach(mod.base.add(0x7635978),{
  onEnter(args){
   if(args[1].isNull())return;
   const uri=args[1].add(20).readUtf16String(args[1].add(16).readS32());
   if(!['https://pgorelease.nianticlabs.com/max/','http://127.0.0.1:8080/max/'].includes(uri))return;
   const local=stringNew(Memory.allocUtf8String((globalThis.LIBRE_BASE_URL||'http://127.0.0.1:8080')+'/max/'));
   this.maxUriHandle=rootHandle(local,0);args[1]=local;
   console.log('Local authoritative Max transport selected');
  },onLeave(){if(this.maxUriHandle)freeMaxHandle(this.maxUriHandle);}
 });
 const seen=new Set();
 Interceptor.attach(mod.base.add(0x7547fe4),{onEnter(args){
  try{
   const n=args[0].add(16).readS32();if(n<0||n>8192)return;
   let value=args[0].add(20).readUtf16String(n);
   if(seen.has(value))return;seen.add(value);
   value=value.replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]+/g,'[email omitted]')
    .replace(/(?:Bearer|authorization|token)[=: ]+[^\s,]+/gi,'[credential omitted]');
   console.log('Max compatibility client error: '+value);
  }catch(e){}
 }});
 console.log('Opt-in native Max diagnostics ready; no battle or reward overrides');
 for(const [name,address] of [['enabled',0x87a87fc],['stations-enabled',0x87a8c2c],['mp-enabled',0x87a8d20]]){
  let previous='';Interceptor.attach(mod.base.add(address),{onLeave(value){
   const state=value.toInt32()!==0;if(previous===String(state))return;previous=String(state);
   console.log('Native Max '+name+'='+state);
  }});
 }
 const formatter=new NativeFunction(mod.getExportByName('il2cpp_format_exception'),'void',['pointer','pointer','int']);
 const exceptionSeen=new Set();
 for(const count of [1,2]){
  try{Interceptor.attach(method(klass('UnityEngine','Debug'),'LogException',count).readPointer(),{onEnter(args){
   try{const buffer=Memory.alloc(8192);formatter(args[0],buffer,8192);const message=buffer.readUtf8String();if(!exceptionSeen.has(message)){exceptionSeen.add(message);console.log('Native Max exception: '+message);}}catch(e){}
  }});}catch(e){}
 }
 for(const [name,address] of [['station-enter',0x875d3e8],['station-ui-initialize',0x876f110],['station-ui-update',0x876f1e0],['station-collect',0x87988ec],['station-exit',0x875e3d8]]){
  Interceptor.attach(mod.base.add(address),{onEnter(){console.log('Native Max lifecycle '+name);}});
 }
})();
