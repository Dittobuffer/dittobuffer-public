// Refresh the native visible-cell subscription once after movement in a paused
// map screen. Ordinary adjacent-cell movement keeps the client's normal path.
const LibreNlpWorldResume=(()=>{
  function distance(a,b){
    const r=Math.PI/180,lat=(b.lat-a.lat)*r,lng=(b.lng-a.lng)*r;
    const v=Math.sin(lat/2)**2+Math.cos(a.lat*r)*Math.cos(b.lat*r)*Math.sin(lng/2)**2;
    return 6371000*2*Math.atan2(Math.sqrt(v),Math.sqrt(Math.max(0,1-v)));
  }
  function create(refresh,threshold=250){
    let latest=null,anchor=null,paused=false,dirty=false,pending=false;
    return {
      location(lat,lng){
        if(!Number.isFinite(lat)||!Number.isFinite(lng)||Math.abs(lat)>90||Math.abs(lng)>180)return;
        latest={lat,lng};
        if(paused&&anchor&&distance(anchor,latest)>=threshold)dirty=true;
      },
      pause(value){
        value=!!value;
        if(value&&!paused){anchor=latest;dirty=false;}
        if(!value&&paused&&dirty)pending=true;
        paused=value;
      },
      update(){if(!paused&&pending){pending=false;refresh();return true;}return false;},
      pending(){return pending;}
    };
  }
  return {create,distance};
})();
if(typeof module==='object'&&module.exports)module.exports=LibreNlpWorldResume;
if(typeof Process!=='undefined'&&globalThis.LIBRE_WORLD_ENABLED)(()=>{
  const style=globalThis.LIBRE_WORLD_STYLE;
  const styled=new Set();
  if(style)Interceptor.attach(mod.base.add(0x7d05fc4),{onEnter(args){
    const builder=args[0],key=builder.toString();
    if(styled.has(key))return;
    styled.add(key);
    builder.add(0x40).writeFloat(builder.add(0x40).readFloat()*style.outline_scale);
    const widths=builder.add(0x38).readPointer();
    for(let i=0;i<widths.add(24).readU64().toNumber();i++){
      const width=widths.add(32+i*8).readPointer(),scale=style.width_scales[width.add(0x14).readS32()];
      if(scale!==undefined)width.add(0x10).writeFloat(width.add(0x10).readFloat()*scale);
    }
    console.log('Local world: native road width/outline style '+style.name);
  }});
  let content=ptr(0);
  let sampledAt=0,observedPause=null;
  const unboxWorld=api('object_unbox','pointer',['pointer']);
  const refresh=new NativeFunction(mod.base.add(0x7c3b328),'void',['pointer','pointer']);
  const state=LibreNlpWorldResume.create(()=>{
    if(content.isNull())return;
    refresh(content,ptr(0));
    console.log('Local world: refreshed native visible cells after paused location transition');
  });
  Interceptor.attach(mod.base.add(0x95321d0),{onEnter(args){
    const fix=args[1];
    if(fix.isNull()||fix.add(40).readU32()!==3)return;
    state.location(fix.readDouble(),fix.add(8).readDouble());
  }});
  Interceptor.attach(mod.base.add(0x7c3b300),{onEnter(args){content=args[0];state.pause(args[1].toInt32()!==0);}});
  // Several IL2CPP callers inline the tiny pause setter. Observe the actual
  // field as well, including frames where no cell-update callback is running.
  Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
    if(content.isNull())return;
    const paused=content.add(0x1a8).readU8()!==0;
    if(Date.now()-sampledAt>=1000||paused!==observedPause){
      sampledAt=Date.now();
      const provider=content.add(0x38).readPointer();
      if(!provider.isNull()){
        const location=unboxWorld(call(method(getClass(provider),'get_Location',0),provider));
        state.location(location.readDouble(),location.add(8).readDouble());
      }
    }
    if(paused!==observedPause){observedPause=paused;console.log('Local world: native entity pause='+paused);}
    state.pause(paused);
    state.update();
  }});
  Interceptor.attach(mod.base.add(0x7c3c468),{onEnter(args){content=args[0];state.pause(content.add(0x1a8).readU8()!==0);},onLeave(){state.update();}});
})();
