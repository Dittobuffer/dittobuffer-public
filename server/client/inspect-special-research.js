// Opt-in, read-only 0.429.2 ARM64 diagnostics. No strategy/capture overrides.
(()=>{
 const seen=new Set();
 const className=o=>api('class_get_name','pointer',['pointer'])(getClass(o)).readCString();
 function note(o,what){
  try{const key=String(o)+':'+what;if(seen.has(key))return;seen.add(key);
   console.log('M23 native '+what+' controller='+className(o));
  }catch(e){console.log('M23 observation unavailable: '+String(e));}
 }
 Interceptor.attach(mod.base.add(0x8709A24),{onEnter(args){
  console.log('M23 native strategy context='+args[1].toInt32()+' species='+args[2].toInt32());
 }});
 // Authored subclasses return UsesInventory=false. Record actual constructed
 // controller identity; merely reaching this getter is not playback proof.
 for(const rva of [0x871A3C4,0x871CD64,0x87212B8,0x872505C,0x8729704,
                  0x872DE8C,0x8732934,0x8736634,0x873AC2C,0x873EDB0,
                  0x8742EC0,0x874B1B8]){
  Interceptor.attach(mod.base.add(rva),{onEnter(args){note(args[0],'scripted inventory');}});
 }
 Interceptor.attach(mod.base.add(0x87323BC),{onEnter(args){
  this.controller=args[0];this.before=this.controller.add(0x70).readS32();
 },onLeave(){
  try{console.log('M23 native local scripted escape controller='+className(this.controller)+
   ' step='+this.before+'->'+this.controller.add(0x70).readS32());}catch(e){}
 }});
 console.log('M23 native research diagnostics installed; observation only');
})();
