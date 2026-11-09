// Opt-in read-only vNext raid initialization probe; never launcher-loaded.
(()=>{
 const className=api('class_get_name','pointer',['pointer']);
 const typeObject=api('type_get_object','pointer',['pointer']);
 const classType=api('class_get_type','pointer',['pointer']);
 const find=new NativeFunction(mod.base.add(0xA454928),'pointer',['pointer','bool','pointer']);
 const str=v=>v.isNull()?null:v.add(20).readUtf16String(v.add(16).readS32());
 const name=v=>v.isNull()?null:className(getClass(v)).readUtf8String();
 let pending=[];
 function snapshot(){
  const values=find(typeObject(classType(klass('Niantic.Holoholo.VNext','RaidBattleVNextState'))),0,ptr(0));
  const count=values.add(24).readU64().toNumber();if(!count)return {raid:false};
  const s=values.add(32).readPointer(),comm=s.add(0x138).readPointer(),pbs=comm.add(0x80).readPointer(),director=s.add(0x118).readPointer(),state=director.add(0x108).readPointer(),sync=state.add(0x88).readPointer();
  return {raid:true,battleId:str(s.add(0x1C8).readPointer()),ui:name(s.add(0x188).readPointer()),
   stage:name(s.add(0x1E8).readPointer()),start:s.add(0x1B8).readS64().toString(),end:s.add(0x1C0).readS64().toString(),
   communication:name(comm),pbs:comm.isNull()?null:name(comm.add(0x80).readPointer()),
   turn:comm.isNull()?null:comm.add(0x50).readS64().toString(),
   nebula:pbs.isNull()?null:name(pbs.add(0x10).readPointer()),communicationUri:str(comm.add(0xC0).readPointer()),
   communicationBattle:str(comm.add(0xC8).readPointer()),pbsBattle:pbs.isNull()?null:str(pbs.add(0x18).readPointer()),
   serverStart:comm.add(0x58).readS64().toString(),serverEnd:comm.add(0x60).readS64().toString(),started:!!director.add(0x110).readU8(),
   inputBlocked:new NativeFunction(mod.base.add(0x7638AC0),'bool',['pointer','pointer'])(director,ptr(0)),received:!!state.add(0x68).readU8(),
   status:state.add(0x64).readS32(),uiMode:state.add(0x90).readS32(),lastInputTurn:director.add(0xC0).readS64().toString(),stateTurn:sync.add(0x30).readS64().toString(),
   director:name(s.add(0x118).readPointer()),gameMaster:name(s.add(0xD8).readPointer())};
 }
 Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){for(const j of pending.splice(0))try{j.resolve(snapshot());}catch(e){j.reject(e);}}});
 rpc.exports={snapshot(){return new Promise((resolve,reject)=>pending.push({resolve,reject}));}};
})();
