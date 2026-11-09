// Read-only, once per cold start on Unity's render thread. No camera setters.
(()=>{
 let frames=0,done=false,surfacesDone=false;
 const unpack=api('object_unbox','pointer',['pointer']);
 const text=o=>o.isNull()?'':o.add(20).readUtf16String(o.add(16).readS32());
 Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
  ++frames;
  if(done){
   if(!surfacesDone&&frames>=900){
    surfacesDone=true;
    try{
     const k=klass('UnityEngine','MeshRenderer'),type=api('type_get_object','pointer',['pointer'])(api('class_get_type','pointer',['pointer'])(k));
     const renderers=call(method(klass('UnityEngine','Resources'),'FindObjectsOfTypeAll',1),ptr(0),[type]),rows=[];
     const floats=(p,n)=>Array.from({length:n},(_,i)=>p.add(i*4).readFloat());
     for(let i=0;i<renderers.add(24).readU64().toNumber()&&rows.length<80;i++){
      const r=renderers.add(32+i*8).readPointer(),name=text(call(method(klass('UnityEngine','Object'),'get_name',0),r));
      if(!/land|building|water|road/i.test(name))continue;
      const go=call(method(getClass(r),'get_gameObject',0),r);
      if(!unpack(call(method(getClass(go),'get_activeInHierarchy',0),go)).readU8())continue;
      const b=unpack(call(method(getClass(r),'get_bounds',0),r));
      const m=call(method(getClass(r),'get_sharedMaterial',0),r),value={name,bounds:floats(b,6)};
      if(!m.isNull()){
       value.material=text(call(method(klass('UnityEngine','Object'),'get_name',0),m));
       value.queue=unpack(call(method(getClass(m),'get_renderQueue',0),m)).readS32();
       const shader=call(method(getClass(m),'get_shader',0),m);
       value.shader=text(call(method(klass('UnityEngine','Object'),'get_name',0),shader));
      }
      rows.push(value);
     }
     console.log('Standalone map surface diagnostics '+JSON.stringify(rows));
    }catch(e){console.log('Standalone map surface diagnostics unavailable: '+e.message);}
   }
   return;
  }
  if(frames<180)return;done=true;
  try{
   const info=klass('UnityEngine','SystemInfo');
   const device={};
   for(const name of ['graphicsDeviceName','graphicsDeviceVendor','graphicsDeviceVersion'])device[name]=text(call(method(info,'get_'+name,0),ptr(0)));
   device.graphicsDeviceType=unpack(call(method(info,'get_graphicsDeviceType',0),ptr(0))).readS32();
   const cameras=call(method(klass('UnityEngine','Camera'),'get_allCameras',0),ptr(0)),views=[];
   for(let i=0;i<Math.min(32,cameras.add(24).readU64().toNumber());i++){
    const camera=cameras.add(32+i*8).readPointer(),k=getClass(camera);
    const value={name:text(call(method(klass('UnityEngine','Object'),'get_name',0),camera))};
    for(const name of ['nearClipPlane','farClipPlane','fieldOfView'])value[name]=unpack(call(method(k,'get_'+name,0),camera)).readFloat();
    views.push(value);
   }
   console.log('Standalone render diagnostics '+JSON.stringify({device,cameras:views}));
  }catch(e){console.log('Standalone render diagnostics unavailable: '+e.message);}
 }});
})();
