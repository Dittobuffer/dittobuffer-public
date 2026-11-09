// Optional diagnostics only, for the separately installed test app.
const mod=Process.getModuleByName('libil2cpp.so');
for(const offset of [0x7547fe4]){
  Interceptor.attach(mod.base.add(offset),{onEnter(args){
    try{
      const n=args[0].add(16).readS32();
      if(n<0||n>8192)return;
      let value=args[0].add(20).readUtf16String(n);
      value=value.replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]+/g,'[email omitted]')
        .replace(/(?:Bearer|authorization|token)[=: ]+[^\s,]+/gi,'[credential omitted]');
      console.log('CLIENT ERROR '+value);
    }catch(e){}
  }});
}
console.log('Error diagnostics active');
const seen=new Set();
for(const address of [0x7679858,0x7679578,0x76794dc]){
 Interceptor.attach(mod.base.add(address),{
  onEnter(args){this.species=args[1].toInt32();},
  onLeave(result){
    const key='pokemon'+address+':'+this.species;
    if(seen.has(key))return;seen.add(key);
    console.log('Pokemon settings '+this.species+' '+(result.isNull()?'missing':'found'));
  }
 });
}
Interceptor.attach(mod.base.add(0x767c5d4),{
  onEnter(args){this.item=args[1].toInt32();},
  onLeave(result){
    const key='item'+this.item;
    if(seen.has(key))return;seen.add(key);
    console.log('Item settings '+this.item+' '+(result.isNull()?'missing':
      'id='+result.add(16).readS32()+' type='+result.add(20).readS32()+' category='+result.add(24).readS32()));
  }
});
