// Native 0.429.2 Max Ball readiness. No throw, collision or reward overrides.
// btir(true), RVA 0x86EB41C, is the client's existing immediate-ready branch.
// It activates the loaded BreadBall and its authored FX/scales, stops the
// pending visual transition and restores the physical Pokeball root scale.
function createMaxBallReadinessAdapter(native){
 const pending=new Map();
 function forget(ball){const key=String(ball),entry=pending.get(key);if(entry){native.release(entry.handle);pending.delete(key);}}
 function track(ball){forget(ball);pending.set(String(ball),{ball,handle:native.retain(ball)});}
 function ready(ball){
  const entry=pending.get(String(ball));if(!entry)return false;
  try{
   if(!native.isMaxEncounter(ball)){forget(ball);return false;}
   if(!native.artLoaded(ball))return false;
   if(!native.isHolding(ball))native.readyImmediately(ball);
   forget(ball);return true;
  }catch(error){forget(ball);native.error(error);return false;}
 }
 return {track,forget,ready,tick(){for(const entry of [...pending.values()])ready(entry.ball);}};
}
// Keep capture art in the encounter's coordinate frame. Native 0.429.2's
// shrink-completion callback assigns breadballRotation as a world quaternion;
// a geographically rotated encounter consequently shows the back of the ball.
// This owns only the two art transforms after a genuine Pokemon collision.
// Preserve their distinct native sizes: the authored giant landing contracts
// to the normal ball for the closing/shake animation and camera close-up.
function createMaxBallCaptureAdapter(native){
 const captures=new Map();
 function forget(ball){const entry=captures.get(String(ball));if(entry){native.release(entry.handle);captures.delete(String(ball));}}
 return {forget,hit(ball){
  if(captures.has(String(ball))||!native.isMaxHit(ball))return false;
  captures.set(String(ball),{ball,handle:native.retain(ball),scale:native.captureScale(ball),normal:false});return true;
 },shrink(ball){const entry=captures.get(String(ball));if(entry)entry.normal=true;},
 tick(){for(const entry of [...captures.values()])try{native.present(entry.ball,entry.scale,entry.normal);}catch(error){forget(entry.ball);native.error(error);}}};
}
if(typeof module!=='undefined'&&module.exports)module.exports={createMaxBallReadinessAdapter,createMaxBallCaptureAdapter};
(()=>{
 if(typeof Process==='undefined')return;
 const unbox=api('object_unbox','pointer',['pointer']);
 const release=api('gchandle_free','void',['pointer']);
 const truth=Memory.alloc(1);truth.writeU8(1);
 const value=(object,name)=>call(method(getClass(object),'get_'+name,0),object);
 const vector=(object,name)=>{const p=unbox(value(object,name));return [p.readFloat(),p.add(4).readFloat(),p.add(8).readFloat()];};
 const setVector=(object,name,xyz)=>{const p=Memory.alloc(12);xyz.forEach((v,i)=>p.add(4*i).writeFloat(v));call(method(getClass(object),'set_'+name,1),object,[p]);};
 function isMax(ball){
  const lazy=ball.add(0x190).readPointer();if(lazy.isNull())return false;
  const state=call(method(getClass(lazy),'get_Value',0),lazy);if(state.isNull())return false;
  return !!unbox(call(method(getClass(state),'get_IsFullBreadEncounter',0),state)).readU8();
 }
 const adapter=createMaxBallReadinessAdapter({
  retain:ball=>rootHandle(ball,0),release,
  isMaxEncounter:isMax,
  artLoaded:ball=>!ball.add(0x298).readPointer().isNull()&&!ball.add(0x220).readPointer().isNull(),
  isHolding:ball=>!!ball.add(0x240).readU8(),
  readyImmediately(ball){call(method(getClass(ball),'btir',1),ball,[truth]);console.log('Native Max Ball ready immediately; native capture presentation retained');},
  error:error=>console.log('Native Max Ball readiness unavailable: '+String(error))
 });
 const capture=createMaxBallCaptureAdapter({
  retain:ball=>rootHandle(ball,0),release,
  // OnCollisionEnter clears HoldingBreadBall before it returns. The actual
  // PokemonHitCollision, rather than that holding flag, establishes a hit.
  isMaxHit:ball=>isMax(ball)&&!ball.add(0x298).readPointer().isNull()&&!ball.add(0x378).readPointer().isNull(),
  captureScale:ball=>({max:vector(value(ball.add(0x298).readPointer(),'transform'),'lossyScale'),
                     normal:vector(value(ball.add(0x220).readPointer(),'transform'),'lossyScale')}),
  present(ball,scale,normal){
   const offset=normal?0x220:0x298;
   const art=ball.add(offset).readPointer();if(art.isNull())return;
   const transform=value(art,'transform'),parent=value(transform,'parent');
   const parentScale=vector(parent,'lossyScale');
   if(parentScale.some(v=>!Number.isFinite(v)||Math.abs(v)<1e-6))throw Error('Invalid native art parent scale');
   setVector(transform,'localScale',(normal?scale.normal:scale.max).map((v,i)=>v/parentScale[i]));
   // The native physical/animated parent retains its camera heading and shakes.
   setVector(transform,'localEulerAngles',[ball.add(0x154).readFloat(),ball.add(0x158).readFloat(),ball.add(0x15C).readFloat()]);
  },error:error=>console.log('Native Max capture art unavailable: '+String(error))
 });
 Interceptor.attach(mod.base.add(0x86E7108),{onEnter(args){this.ball=args[0];capture.forget(this.ball);},onLeave(){adapter.track(this.ball);}});
 // Preload completion is asynchronous; run managed calls on Unity's thread.
 Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){adapter.tick();capture.tick();}});
 // A very early touch can precede the next Canvas update. Use the same native
 // ready method if its art is already available; retain original Launch logic.
 Interceptor.attach(mod.base.add(0x86E8ACC),{onEnter(args){adapter.ready(args[0]);adapter.forget(args[0]);}});
 Interceptor.attach(mod.base.add(0x86E7B64),{onEnter(args){this.ball=args[0];},onLeave(){capture.hit(this.ball);}});
 // Verified shrink-completion closure: this+0x10 is the owning Pokeball.
 // HoldingBreadBall becomes false earlier, while BreadBall is still visible.
 Interceptor.attach(mod.base.add(0x86EDE6C),{onEnter(args){this.ball=args[0].add(0x10).readPointer();},onLeave(){capture.shrink(this.ball);}});
 Interceptor.attach(mod.base.add(0x86E7B10),{onEnter(args){adapter.forget(args[0]);capture.forget(args[0]);}});
})();
