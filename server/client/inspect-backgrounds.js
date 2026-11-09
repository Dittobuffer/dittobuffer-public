// Read-only, opt-in native summary diagnostics. No renderer/gate overrides.
(()=>{
  const unbox=api('object_unbox','pointer',['pointer']);
  const typeObject=api('type_get_object','pointer',['pointer']);
  const classType=api('class_get_type','pointer',['pointer']);
  const find=new NativeFunction(mod.base.add(0xA454928),'pointer',['pointer','bool','pointer']);
  const value=(o,n)=>unbox(call(method(getClass(o),n,0),o));
  const safe=f=>{try{return f();}catch(e){return e.message;}};
  const out=()=>{
    const a=find(typeObject(classType(klass('Niantic.Holoholo.UI','PokemonInfoBackgroundController'))),0,ptr(0)),rows=[];
    for(let i=0;i<a.add(24).readU64().toNumber();i++){
      const c=a.add(32+8*i).readPointer(),d=c.add(0x88).readPointer(),row={controller:c.toString(),locationRenderer:c.add(0x78).readPointer().toString()};
      if(!d.isNull()){
        row.species=d.add(0x14).readS32();row.card=safe(()=>value(d,'get_LocationCard').readS32());
        row.canShow=safe(()=>!!value(d,'CanShowLocationCard').readU8());
      }
      row.activeKind=safe(()=>value(c,'cbau').readS32());rows.push(row);
      const r=c.add(0x78).readPointer();
      if(!r.isNull()){
        row.widget=r.add(0x80).readPointer().toString();
        const w=r.add(0x80).readPointer();
        if(!w.isNull()){
          const raw=w.add(0x80).readPointer();
          row.texture=safe(()=>call(method(getClass(raw),'get_texture',0),raw).toString());
          row.rawEnabled=safe(()=>!!value(raw,'get_enabled').readU8());
          const promise=w.add(0x150).readPointer();
          row.contentError=safe(()=>{const e=promise.add(0x38).readPointer();return e.isNull()?null:e.add(20).readUtf16String(e.add(16).readS32());});
          const service=w.add(0xB0).readPointer(),mi=mod.base.add(0xB2D3510).readPointer();
          row.existsMethod=api('method_get_name','pointer',['pointer'])(mi).readCString();
          row.assetExists=safe(()=>{
            const concrete=api('object_get_virtual_method','pointer',['pointer','pointer'])(service,mi);
            return !!unbox(call(concrete,service,[stringNew(Memory.allocUtf8String('http://127.0.0.1:8080/assets/dittobuffer-stop.png'))])).readU8();
          });
          row.providers=safe(()=>{
            const rm=call(method(klass('UnityEngine.AddressableAssets','Addressables'),'get_ResourceManager',0),ptr(0));
            const list=call(method(getClass(rm),'get_ResourceProviders',0),rm),n=value(list,'get_Count').readS32(),items=[];
            for(let i=0;i<n;i++){
              const j=Memory.alloc(4);j.writeS32(i);const p=call(method(getClass(list),'get_Item',1),list,[j]);
              const s=call(method(getClass(p),'get_ProviderId',0),p);items.push(s.add(20).readUtf16String(s.add(16).readS32()));
            }
            return items;
          });
        }
        const s=r.add(0x30).readPointer();row.gates={};
        for(const n of ['FeatureEnabled','IsClientPokemonDetailsPageRendererEnabled','IsPokemonDetailsPageInFlowRendererEnabled','PlayerEnabledLocationCardBackground'])
          row.gates[n]=safe(()=>!!value(s,'get_'+n).readU8());
        const gm=r.add(0x40).readPointer(),mi=mod.base.add(0xB2D3EF0).readPointer();
        row.lookupMethod=api('method_get_name','pointer',['pointer'])(mi).readCString();
        const id=Memory.alloc(4);id.writeS32(row.card);
        const box=api('value_box','pointer',['pointer','pointer'])(klass('Holoholo.Rpc','LocationCard'),id);
        row.template=safe(()=>{
          const concrete=api('object_get_virtual_method','pointer',['pointer','pointer'])(gm,mi),t=call(concrete,gm,[box]);
          if(t.isNull())return null;
          const url=t.add(0x18).readPointer();
          return {pointer:t.toString(),id:t.add(0x10).readS32(),url:url.isNull()?null:url.add(20).readUtf16String(url.add(16).readS32())};
        });
      }
    }
    return rows;
  };
  let pending=[];
  Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){for(const job of pending.splice(0))try{job.resolve(out());}catch(e){job.reject(e);}}});
  rpc.exports={snapshot(){return new Promise((resolve,reject)=>pending.push({resolve,reject}));}};
})();

