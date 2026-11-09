// 0.429.2 friend-stage compatibility: keep its native animated idle, rather
// than entering the optional equipped-pose path before the stage is parented.
// The avatar, clothing, buddy, camera and native stage retain their own owners.
const LibreNlpFriendStage=(()=>{
  function usePose(requested,publicProfile=false,context=-1){
    // The deferred friend build runs outside SetFriendInfo's call stack.
    // Its explicit public profile and Unset stage context identify that path.
    return !!requested&&!(publicProfile&&context===0);
  }
  return {usePose};
})();
if(typeof module==='object'&&module.exports)module.exports=LibreNlpFriendStage;
if(typeof Process!=='undefined')(()=>{
  Interceptor.attach(mod.base.add(0x88A7384),{onEnter(args){
    const parameters=args[1];
    const requested=parameters.add(0x1A).readU8()!==0;
    const publicProfile=!parameters.readPointer().isNull();
    const context=parameters.add(0x14).readS32();
    const selected=LibreNlpFriendStage.usePose(requested,publicProfile,context);
    if(requested!==selected){
      parameters.add(0x1A).writeU8(selected?1:0);
      console.log('Friend avatar stage: native idle compatibility');
    }
  }});
  console.log('Social stage compatibility loaded');
})();
