// Opt-in native diagnostic. Reads on Unity's main thread; refresh is explicit.
(()=>{
  const unbox=api('object_unbox','pointer',['pointer']);
  const typeObject=api('type_get_object','pointer',['pointer']);
  const classType=api('class_get_type','pointer',['pointer']);
  const find=new NativeFunction(mod.base.add(0xA454928),'pointer',['pointer','bool','pointer']);
  const string=p=>p.isNull()?'':p.add(20).readUtf16String(p.add(16).readS32());
  const property=(o,n)=>call(method(getClass(o),'get_'+n,0),o);
  const integer=(o,n)=>unbox(property(o,n)).readS32();
  const floating=(o,n)=>unbox(property(o,n)).readFloat();
  const count=o=>o.isNull()?0:integer(o,'Count');
  function objects(name,ns='Niantic.Holoholo.Map'){
    const array=find(typeObject(classType(klass(ns,name))),1,ptr(0));
    return Array.from({length:array.add(24).readU64().toNumber()},(_,i)=>array.add(32+i*8).readPointer());
  }
  function transform(t){
    const v=unbox(property(t,'position')),go=property(t,'gameObject'),parent=property(t,'parent');
    return {position:[0,4,8].map(n=>v.add(n).readFloat()),active:!!unbox(property(go,'activeInHierarchy')).readU8(),
      parent:parent.isNull()?'':string(property(parent,'name'))};
  }
  let events=[],jobs=[];
  function log(name,data){events.push({at:Date.now(),name,...data});if(events.length>500)events.shift();}
  for(const [address,name] of [[0x7c3b2a0,'suspendCells'],[0x7c3b300,'pauseEntities'],[0x7c3b310,'mapViewId'],[0x7cd1988,'switchMapView'],[0x7c3b328,'forceRefresh']]){
    Interceptor.attach(mod.base.add(address),{onEnter(args){log(name,{value:args[1].toInt32()});}});
  }
  Interceptor.attach(mod.base.add(0x7c3c468),{onEnter(args){
    log('updateCells',{fetch:count(args[1]),deleted:count(args[2]),added:count(args[3]),removed:count(args[4]),updated:count(args[5])});
  }});
  Interceptor.attach(mod.base.add(0x7c3f4c4),{onEnter(args){log('serverCells',{count:count(property(args[1],'MapCell'))});}});
  function snapshot(refresh,utcTicks){
    const content=objects('MapContentHandler')[0],entities=objects('MapEntityService')[0];
    if(!content||!entities)throw Error('Map services not yet loaded');
    if(refresh){call(method(getClass(content),'ForceRefreshVisibleCells',0),content);log('requestedRefresh',{});}
    const cells=objects('MapEntityCell').map(o=>({id:o.add(0x50).readU64().toString(),pois:count(o.add(0x28).readPointer()),wild:count(o.add(0x40).readPointer()),...transform(property(o,'Transform'))}));
    const weather=objects('WeatherService')[0],time=objects('TimeOfDayService')[0],paletteService=objects('MapPaletteService')[0],view=objects('MapSceneViewService')[0];
    if(time&&utcTicks){
      const nullable=Memory.alloc(16);nullable.writeByteArray(new Uint8Array(16));
      if(utcTicks!=='clear'){nullable.writeU8(1);nullable.add(8).writeU64(uint64(utcTicks));}
      call(method(getClass(time),'SetTimeOverride',1),time,[nullable]);
      log('isolatedPresentationTimeOverride',{utcTicks});
    }
    let visual={};
    if(weather){
      for(const name of ['CurrentCloudLevel','CurrentRainLevel','CurrentSnowLevel','CurrentWindLevel'])visual[name]=integer(weather,name);
      for(const name of ['CurrentCloudPercentage','CurrentRainPercentage','CurrentSnowPercentage','CurrentWindPercentage','CurrentFogPercentage'])visual[name]=floating(weather,name);
      visual.particles=[0x138,0x148,0x158,0x168].map(offset=>{
        const system=weather.add(offset).readPointer();
        try{return system.isNull()?null:{count:integer(system,'particleCount'),...transform(property(system,'transform'))};}
        catch(error){return {destroyed:true,error:error.message};}
      });
    }
    if(time){visual.dayPeriod=integer(time,'DayPeriod');visual.dateTimeRaw=unbox(property(time,'CurrentTime')).readU64().toString();}
    visual.roads=objects('RoadBuilder','Niantic.Holoholo.Map.Builders.Roads').map(o=>{
      const array=o.add(0x38).readPointer();
      return {outline:o.add(0x40).readFloat(),widths:Array.from({length:array.add(24).readU64().toNumber()},(_,i)=>{
        const entry=array.add(32+i*8).readPointer();return {kind:entry.add(0x14).readS32(),width:entry.add(0x10).readFloat()};
      })};
    });
    const materials=objects('WorldMapRendererMaterialsService')[0];
    if(materials)visual.materials=Array.from({length:10},(_,kind)=>{
      try{
      const arg=Memory.alloc(4);arg.writeS32(kind);
      const def=call(method(getClass(materials),'GetMaterialDefForType',1),materials,[arg]);
      const material=unbox(def).add(0x10).readPointer();
      if(material.isNull())return {kind,loaded:false};
      const shader=property(material,'shader'),properties=[];
      const propertyCount=unbox(call(method(getClass(shader),'GetPropertyCount',0),shader)).readS32();
      for(let i=0;i<propertyCount;i++){
        arg.writeS32(i);properties.push(string(call(method(getClass(shader),'GetPropertyName',1),shader,[arg])));
      }
      return {kind,name:string(property(material,'name')),shader:string(property(shader,'name')),properties};
      }catch(error){return {kind,error:error.message};}
    });
    if(paletteService){const palette=paletteService.add(0x118).readPointer();if(!palette.isNull())visual.palette={sky:[0,4,8,12].map(n=>palette.add(0x10+n).readFloat()),fog:[0,4,8,12].map(n=>palette.add(0x3c+n).readFloat()),fogDistance:palette.add(0x4c).readFloat(),sunStrength:palette.add(0xb4).readFloat()};}
    return {at:Date.now(),content:{suspended:!!content.add(0x194).readU8(),paused:!!content.add(0x1a8).readU8(),viewId:content.add(0x1ac).readS32(),queued:count(content.add(0xe0).readPointer()),cacheCells:count(content.add(0x140).readPointer()),sceneCells:count(content.add(0x148).readPointer())},
      mode:view?view.add(0xc0).readS32():null,roots:[0x20,0x28,0x30].map(n=>transform(entities.add(n).readPointer())),
      totals:{cells:cells.length,active:cells.filter(c=>c.active).length,pois:cells.reduce((a,c)=>a+c.pois,0),wild:cells.reduce((a,c)=>a+c.wild,0)},cells,visual,events:events.splice(0)};
  }
  Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
    for(const job of jobs.splice(0))try{job.resolve(snapshot(job.refresh,job.utcTicks));}catch(error){job.reject(error);}
  }});
  rpc.exports={snapshot(refresh=false,utcTicks=null){return new Promise((resolve,reject)=>jobs.push({resolve,reject,refresh,utcTicks}));}};
})();
