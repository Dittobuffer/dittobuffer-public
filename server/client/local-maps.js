// Session-only compatibility for our own public OSM map provider. All other
// providers retain the APK's normal URL/decoding path. No upstream keys/assets.
(()=>{
  const readString=p=>p.isNull()?'':p.add(20).readUtf16String(p.add(16).readS32());
  const urlAddress=mod.base.add(0x9433574);
  const originalUrl=new NativeFunction(urlAddress,'pointer',['pointer','pointer','int','int','pointer']);
  const urlCallback=new NativeCallback((self,coord,epoch,bundleZoom,info)=>{
    ensureThread();
    // The server profile can be HTTP or HTTPS and may use 8445 instead of the
    // development admin port. Match the endpoint path instead of one origin.
    const provider=readString(self.add(24).readPointer());
    const match=provider.match(/^(https?:\/\/[^/]+)\/map\/tiles\/?$/);
    if(!match)return originalUrl(self,coord,epoch,bundleZoom,info);
    const z=coord.add(16).readS32(),x=coord.add(20).readS32(),y=coord.add(24).readS32();
    console.log('Local map request: bundle zoom '+z+', tile zoom '+bundleZoom);
    return stringNew(Memory.allocUtf8String(`${match[1]}/map/tiles/${z}/${x}/${y}?epoch=${epoch}&tileZoom=${bundleZoom}`));
  },'pointer',['pointer','pointer','int','int','pointer']);
  callbacks.push(urlCallback);Interceptor.replace(urlAddress,urlCallback);
  const decodeAddress=mod.base.add(0x942e1fc);
  const originalDecode=new NativeFunction(decodeAddress,'void',Array(5).fill('pointer'));
  const streamClass=klass('System.IO','MemoryStream');
  // Select the verified byte[] overload, not MemoryStream(int capacity).
  const streamCtor=new NativeFunction(mod.base.add(0x923fff0),'void',Array(3).fill('pointer'));
  const decodeCallback=new NativeCallback((coord,data,key,consumer,info)=>{
    ensureThread();
    const length=data.isNull()?0:data.add(24).readU64().toNumber();
    if(length<8||length>8*1024*1024||data.add(32).readUtf8String(8)!=='LNLPMAP1')return originalDecode(coord,data,key,consumer,info);
    const raw=arrayNew(klass('System','Byte'),length-8);
    raw.add(32).writeByteArray(data.add(40).readByteArray(length-8));
    const stream=objectNew(streamClass);
    streamCtor(stream,raw,ptr(0));
    call(method(getClass(consumer),'Invoke',1),consumer,[stream]);
    console.log('Local OSM bundle decoded '+(length-8)+' bytes');
  },'void',Array(5).fill('pointer'));
  callbacks.push(decodeCallback);Interceptor.replace(decodeAddress,decodeCallback);
  console.log('Local OSM provider compatibility active');
})();
