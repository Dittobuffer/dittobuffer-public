// Opt-in read-only trace of native Mega requests and UI callbacks.
const traceFieldInfo=api('class_get_field_from_name','pointer',['pointer','pointer']);
const traceOffset=api('field_get_offset','int',['pointer']);
function traceField(o,key,type){
  if(o.isNull())return null;
  const f=traceFieldInfo(getClass(o),Memory.allocUtf8String(key));
  if(f.isNull())return 'missing';
  const v=o.add(traceOffset(f));
  return type==='id'?v.readU64().toString():v.readS32();
}
const traceHooks=[[0x7E5EC2C,'MegaLevelUpPokemon'],[0x7E5F0AC,'GetMegaLevelUpPreview'],[0x7E5E814,'MegaEvolvePokemon'],
  [0x7A8AFA0,'OnClickLevelUp'],[0x7A8B9C4,'cgmv'],[0x7A8CD64,'cgnb'],[0x7A8CDF8,'cgnc'],
  [0x7A8CE14,'cgnd'],[0x7A8CC80,'cgna'],[0x7A8BF00,'cgmw'],[0x89C6784,'MegaLevelUp.WriteTo']];
for(const [address,name] of traceHooks)Interceptor.attach(mod.base.add(address),{onEnter(args){
  const entry={name,at:Date.now()};
  try{
    if(name==='MegaLevelUpPokemon'||name==='GetMegaLevelUpPreview'){
      const o=args[2];entry.request={id:traceField(o,'pokemonId_','id'),evo:traceField(o,'tempEvoId_'),current:traceField(o,'currentMegaLevel_'),target:traceField(o,'targetMegaLevel_')};
    }
    if(name==='cgmv'||name==='cgnb')entry.argument=args[1].toInt32();
    if(name==='OnClickLevelUp'||name==='cgmv')entry.selectedLevel=traceField(args[0],'edhq');
  }catch(e){entry.error=String(e);}
  send(entry);
}});
send({ready:true,readOnly:true});
