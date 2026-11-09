// Opt-in, read-only 0.429.2 raid presentation inspector; never auto-loaded.
(()=>{
 const unbox=api('object_unbox','pointer',['pointer']);
 const typeObject=api('type_get_object','pointer',['pointer']),classType=api('class_get_type','pointer',['pointer']);
 const className=api('class_get_name','pointer',['pointer']),classNs=api('class_get_namespace','pointer',['pointer']);
 const find=new NativeFunction(mod.base.add(0xA454928),'pointer',['pointer','bool','pointer']);
 const str=o=>o.isNull()?null:o.add(20).readUtf16String(o.add(16).readS32());
 const get=(o,n)=>call(method(getClass(o),'get_'+n,0),o),name=o=>str(get(o,'name'));
 const scalar=(o,n,kind='readS32')=>unbox(get(o,n))[kind]();
 const vector=(o,n,count=3)=>{const p=unbox(get(o,n));return Array.from({length:count},(_,i)=>p.add(i*4).readFloat());};
 const array=a=>{const rows=[];for(let i=0;i<a.add(24).readU64().toNumber();i++)rows.push(a.add(32+8*i).readPointer());return rows;};
 const safe=f=>{try{return f();}catch(e){return {error:String(e)};}};
 const cls=o=>classNs(getClass(o)).readCString()+'.'+className(getClass(o)).readCString();
 function classes(){
  const rows=[],nclass=api('image_get_class_count','ulong',['pointer']),classAt=api('image_get_class','pointer',['pointer','ulong']);
  const fields=api('class_get_fields','pointer',['pointer','pointer']),fn=api('field_get_name','pointer',['pointer']),fo=api('field_get_offset','int',['pointer']);
  const methods=api('class_get_methods','pointer',['pointer','pointer']),mn=api('method_get_name','pointer',['pointer']);
  for(let a=0;a<count.readU64().toNumber();a++){
   const image=getImage(assemblies.add(a*8).readPointer());
   for(let i=0;i<nclass(image);i++){
    const c=classAt(image,i),n=className(c).readCString();if(!/RaidResolve/.test(n))continue;
    const row={name:n,namespace:classNs(c).readCString(),fields:[],methods:[]},iterator=Memory.alloc(8);iterator.writePointer(ptr(0));
    for(let f=fields(c,iterator);!f.isNull();f=fields(c,iterator))row.fields.push({name:fn(f).readCString(),offset:fo(f)});
    iterator.writePointer(ptr(0));for(let m=methods(c,iterator);!m.isNull();m=methods(c,iterator))row.methods.push({name:mn(m).readCString(),rva:m.readPointer().sub(mod.base).toString()});rows.push(row);
   }
  }return rows;
 }
 function texture(t){return t.isNull()?null:{name:name(t),type:cls(t),width:scalar(t,'width'),height:scalar(t,'height')};}
 function material(m){return m.isNull()?null:{name:name(m),shader:safe(()=>name(get(m,'shader'))),texture:safe(()=>texture(get(m,'mainTexture'))),queue:safe(()=>scalar(m,'renderQueue'))};}
 function component(c){
  const type=cls(c),row={pointer:String(c),type};
  if(/Image$/.test(type)){
   row.enabled=safe(()=>scalar(c,'enabled','readU8'));row.color=safe(()=>vector(c,'color',4));row.material=safe(()=>material(get(c,'material')));row.renderMaterial=safe(()=>material(get(c,'materialForRendering')));row.mainTexture=safe(()=>texture(get(c,'mainTexture')));
   if(/\.Image$/.test(type)){row.imageType=safe(()=>scalar(c,'type'));row.sprite=safe(()=>{const s=get(c,'sprite');return s.isNull()?null:{name:name(s),texture:texture(get(s,'texture'))};});}
   if(/RawImage$/.test(type))row.uv=safe(()=>vector(c,'uvRect',4));
  }
  if(/CanvasGroup$/.test(type))row.alpha=safe(()=>scalar(c,'alpha','readFloat'));
  if(/\.Canvas$/.test(type)){row.sort=safe(()=>scalar(c,'sortingOrder'));row.renderMode=safe(()=>scalar(c,'renderMode'));}
  return row;
 }
 function tree(go,depth=0){
  const t=get(go,'transform'),row={name:name(go),active:!!scalar(go,'activeSelf','readU8'),hierarchyActive:!!scalar(go,'activeInHierarchy','readU8'),layer:scalar(go,'layer'),scale:vector(t,'localScale'),position:vector(t,'localPosition'),rect:safe(()=>vector(t,'rect',4))};
  const type=typeObject(classType(klass('UnityEngine','Component')));row.components=array(call(method(getClass(go),'GetComponents',1),go,[type])).map(c=>safe(()=>component(c)));
  const children=scalar(t,'childCount');if(depth<12)row.children=Array.from({length:children},(_,i)=>{const index=Memory.alloc(4);index.writeS32(i);return tree(get(call(method(getClass(t),'GetChild',1),t,[index]),'gameObject'),depth+1);});return row;
 }
 function providers(){
  const controllers=array(find(typeObject(classType(klass('Niantic.Holoholo.Raid','RaidResolveUIController'))),1,ptr(0)));
  const fields=api('class_get_fields','pointer',['pointer','pointer']),fn=api('field_get_name','pointer',['pointer']),fo=api('field_get_offset','int',['pointer']),ft=api('field_get_type','pointer',['pointer']),tn=api('type_get_name','pointer',['pointer']);
  const methods=api('class_get_methods','pointer',['pointer','pointer']),mn=api('method_get_name','pointer',['pointer']),pc=api('method_get_param_count','uint',['pointer']);
  function describe(c,instance){
   const row={type:classNs(c).readCString()+'.'+className(c).readCString(),fields:[],methods:[]},it=Memory.alloc(8);it.writePointer(ptr(0));
   for(let f=fields(c,it);!f.isNull();f=fields(c,it)){
    const type=tn(ft(f)).readCString(),offset=fo(f),r={name:fn(f).readCString(),type,offset};
    if(instance&&/UnityEngine\.(Sprite|Material|Texture2D)/.test(type)&&offset>0){const o=instance.add(offset).readPointer();r.object=String(o);r.resource=safe(()=>o.isNull()?null:name(o));}row.fields.push(r);
   }
   it.writePointer(ptr(0));for(let m=methods(c,it);!m.isNull();m=methods(c,it))row.methods.push({name:mn(m).readCString(),parameters:pc(m),rva:m.readPointer().sub(mod.base).toString()});return row;
  }
  const vnext=array(find(typeObject(classType(klass('Niantic.Holoholo.VNext','RaidBattleVNextState'))),1,ptr(0))).map(o=>describe(getClass(o),o));return controllers.map(c=>{const service=c.add(0x110).readPointer();const raid=c.add(0xf0).readPointer(),resolve=c.add(0xf8).readPointer();return {vnext,controller:describe(getClass(c)),raid:raid.isNull()?null:describe(getClass(raid),raid),resolve:resolve.isNull()?null:describe(getClass(resolve),resolve),service:service.isNull()?null:describe(getClass(service),service),information:resolve.isNull()?null:safe(()=>{const info=resolve.add(0xb0).readPointer();return {type:cls(info),status:scalar(info,'Status')};}),transition:service.isNull()?null:safe(()=>({sprite:name(get(service,'MainImageSprite')),material:material(get(service,'MainImageMaterial')),color:vector(service,'MainImageColor',4)}))};});
 }
 function snapshot(){const materials=array(call(method(klass('UnityEngine','Resources'),'FindObjectsOfTypeAll',1),ptr(0),[typeObject(classType(klass('UnityEngine','Material')))])).filter(m=>name(m)==='RaidTransitionRingBackground').map(m=>({pointer:String(m),...material(m),supported:safe(()=>scalar(get(m,'shader'),'isSupported','readU8'))}));return {materials,roots:array(find(typeObject(classType(klass('UnityEngine','GameObject'))),1,ptr(0))).filter(o=>/^RaidResolveUI(?:\(Clone\))?$/.test(name(o))).map(o=>tree(o)),providers:providers()};}

 let pending=[];Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){for(const job of pending.splice(0))try{job.resolve(snapshot());}catch(e){job.reject(String(e));}}});
 rpc.exports={classes,snapshot(){return new Promise((resolve,reject)=>pending.push({resolve,reject}));}};
})();
