const LibreNlpRocketResearch={confirmed(status,id,updated,child){
  return status===1&&!!id&&child.id===id&&child.status===2&&child.updated>=updated;
}};
if(typeof module!=='undefined')module.exports=LibreNlpRocketResearch;
// Opt-in 0.429.2 Rocket transport: use the existing native RPC communication
// service so actions and state come from our authoritative server. Native
// battle screens/models/animations/input remain in charge of presentation.
if(typeof Process!=='undefined'&&globalThis.LIBRE_ROCKET_SERVER_COMBAT===true)(()=>{
  // CombatServerServingService.ServerType: LOCAL=0, SERVER=1.
  Interceptor.attach(mod.base.add(0x84832F0),{onEnter(args){
    if(args[1].toInt32()===0){args[1]=ptr(1);console.log('Rocket combat: native server communication selected');}
  }});
  // QuestListTaskView rebuilds sibling rows synchronously on SuccessfulClaimed,
  // before this RPC's inventory payload reaches QuestService. Preserve the
  // authoritative completion in its existing shared child object first.
  const multiPart=new NativeFunction(mod.base.add(0x8C0B23C),'pointer',['pointer','pointer']);
  function text(p){return p.isNull()?'':p.add(20).readUtf16String(p.add(16).readS32());}
  Interceptor.attach(mod.base.add(0x7F2E0E0),{onEnter(args){
    try{
      const response=args[1];if(response.isNull()||response.add(0x10).readS32()!==1)return;
      const view=args[0].add(0x10).readPointer(),client=view.add(0x100).readPointer();
      if(client.isNull())return;
      const current=client.add(0x10).readPointer(),result=response.add(0x18).readPointer();
      if(current.isNull()||result.isNull())return;
      const parent=result.add(0x10).readPointer();if(parent.isNull())return;
      const part=multiPart(parent,ptr(0));if(part.isNull())return;
      const children=part.add(0x10).readPointer();if(children.isNull())return;
      const count=children.add(0x18).readS32(),array=children.add(0x10).readPointer();
      if(count<1||count>4||array.isNull())return;
      const id=text(current.add(0x28).readPointer());
      for(let i=0;i<count;i++){
        const source=array.add(0x20+i*Process.pointerSize).readPointer();
        if(source.isNull()||!LibreNlpRocketResearch.confirmed(1,id,Number(current.add(0x70).readS64().toString()),{id:text(source.add(0x28).readPointer()),status:source.add(0x58).readS32(),updated:Number(source.add(0x70).readS64().toString())}))continue;
        current.add(0x48).writeS32(source.add(0x48).readS32());
        current.add(0x58).writeS32(2);
        for(const offset of [0x70,0x78])current.add(offset).writeS64(source.add(offset).readS64());
        console.log('Rocket Research: confirmed child completion applied before row rebuild');
        break;
      }
    }catch(e){console.log('Rocket Research completion refresh unavailable: '+e.message);}
  }});
})();
