// Session-only local identity adapter for the separately named development APK.
// Addresses are discovered through this build's IL2CPP metadata, not guessed.
// Detaching restores the provider methods. Never attach this to the original app.
const mod=Process.getModuleByName('libil2cpp.so');
const api=(name,result,args)=>new NativeFunction(mod.getExportByName('il2cpp_'+name),result,args);
const domain=api('domain_get','pointer',[])();
// Every hook may run on a different Unity/plugin thread. Boehm GC only accepts
// allocation from threads registered with the IL2CPP domain, so register the
// current thread before allocating or invoking managed code. Idempotent.
const threadAttach=api('thread_attach','pointer',['pointer']);
function ensureThread(){try{threadAttach(domain);}catch(e){}}
ensureThread();
const getImage=api('assembly_get_image','pointer',['pointer']);
const fromName=api('class_from_name','pointer',['pointer','pointer','pointer']);
const getMethod=api('class_get_method_from_name','pointer',['pointer','pointer','int']);
const getClass=api('object_get_class','pointer',['pointer']);
const fromType=api('class_from_type','pointer',['pointer']);
const returnType=api('method_get_return_type','pointer',['pointer']);
const objectNewRaw=api('object_new','pointer',['pointer']);
const objectNew=k=>{ensureThread();return objectNewRaw(k);};
const invoke=api('runtime_invoke','pointer',['pointer','pointer','pointer','pointer']);
const stringNewRaw=api('string_new','pointer',['pointer']);
const stringNew=p=>{ensureThread();return stringNewRaw(p);};
// This ARM64 Unity build uses pointer-sized GCHandle values. Truncating one
// to uint32 appears to root successfully but faults when that handle is freed.
const rootHandle=api('gchandle_new','pointer',['pointer','int']);
const arrayNewRaw=api('array_new','pointer',['pointer','ulong']);
const arrayNew=(k,n)=>{ensureThread();return arrayNewRaw(k,n);};
const count=Memory.alloc(8);count.writeU64(0);
const assemblies=api('domain_get_assemblies','pointer',['pointer','pointer'])(domain,count);
function klass(ns,name){
  for(let i=0;i<count.readU64().toNumber();i++){
    const k=fromName(getImage(assemblies.add(i*8).readPointer()),Memory.allocUtf8String(ns),Memory.allocUtf8String(name));
    if(!k.isNull())return k;
  }
  throw new Error('Class missing '+ns+'.'+name);
}
function method(k,name,n){
  const value=getMethod(k,Memory.allocUtf8String(name),n);
  if(value.isNull())throw new Error('Method missing '+name);
  return value;
}
function call(m,self,args=[]){
  ensureThread();
  const argv=Memory.alloc(Math.max(8,args.length*8));
  args.forEach((value,i)=>argv.add(i*8).writePointer(value));
  const exception=Memory.alloc(8);exception.writePointer(ptr(0));
  const result=invoke(m,self,args.length?argv:ptr(0),exception);
  if(!exception.readPointer().isNull()){
    const ex=exception.readPointer();
    const name=api('class_get_name','pointer',['pointer'])(getClass(ex)).readCString();
    const error=Memory.alloc(8);error.writePointer(ptr(0));
    const getter=getMethod(getClass(ex),Memory.allocUtf8String('get_Message'),0);
    const message=getter.isNull()?ptr(0):invoke(getter,ex,ptr(0),error);
    const detail=message.isNull()||!error.readPointer().isNull()?'':message.add(20).readUtf16String(message.add(16).readS32());
    throw new Error('Managed '+name+': '+detail);
  }
  return result;
}
function familyCredentialFromText(text,required){
  let value=null;
  if(text!==null){
    try{value=JSON.parse(text);}catch(e){throw Error('Malformed private family credential');}
    if(!value||typeof value!=='object'||Array.isArray(value)||!/^lf1_[A-Za-z0-9_-]{43}$/.test(value.token)||!/^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$/.test(value.username))throw Error('Invalid family credential');
  }
  if(required&&!value)throw Error('Family login required: provision an account before launching this clone');
  return value;
}
function privateResourceUrl(url,base){
  const match=url.match(/^https?:\/\/127\.0\.0\.1(?::(?:8080|18080))?(\/.*)$/);
  if(!match)return null;
  // ImageDownloadService appends a Google-style '=s...' resize suffix even
  // for our byte-preserved images. It is not part of the resource filename.
  const image=match[1].match(/^(\/world\/(?:photos\/[a-f0-9]{64}\.jpg|placeholders\/[a-f0-9]{64}\.png|photo-unavailable\.png)|\/assets\/dittobuffer-stop\.png)(?:[=?].*)?$/);
  return base+(image?image[1]:match[1]);
}
function runtimeResourceUrl(url,base,localOnly){
  if(!localOnly||!/^https:\/\/asset-cdn-rel\.nianticstatic\.com\//.test(url))return null;
  // Match any CDN path that ends in a safe, content-addressed file name, not
  // only the catalog_926 spelling. The old fallback rewrote every unmatched
  // URL to 'unsupported.bundle', which the server can only answer with 404, so
  // assets fetched through another (equally valid) CDN path were permanently
  // blank. The server keeps the allowlist; a name it does not know is fetched
  // and cached there, and anything that still does not parse stays untouched
  // so the client keeps its normal CDN behaviour.
  const match=url.match(/^https:\/\/asset-cdn-rel\.nianticstatic\.com\/([A-Za-z0-9_.\/-]+\.(?:bundle|json|hash))(?:[?#].*)?$/);
  // Keep the full CDN path: external catalogs live outside catalog_926/, and
  // only the real path lets the server fetch them. Known files still resolve
  // through the verified registry by file name, so shipped assets never leave
  // the device.
  return match?base+'/assets/client/'+match[1]:null;
}
const sourceClass=klass('Niantic.Platform.Auth','GoogleAuthSource');
let credentialText=null;
try{credentialText=File.readAllText('/data/user/0/org.librenlp.pokemongo.dev/files/dittobuffer-family.json');}catch(e){
  if(!/no such file|not found/i.test(String(e)))throw Error('Cannot read private family credential');
}
const familyCredential=familyCredentialFromText(credentialText,globalThis.LIBRE_FAMILY_MODE);
let localBase='http://127.0.0.1:8080';
try{localBase=File.readAllText('/data/user/0/org.librenlp.pokemongo.dev/files/dittobuffer-server.url').trim().replace(/\/$/,'');}catch(e){}
if(!/^https?:\/\/[^/@?#]+(?::\d+)?$/.test(localBase))throw Error('Invalid private server origin');
globalThis.LIBRE_BASE_URL=localBase;
const provider=klass('Niantic.Platform.Auth','GoogleAuthProvider');
const loginResult=klass('Niantic.Platform.Auth','AuthProviderLoginResult');
const source=objectNew(sourceClass);
call(method(sourceClass,'.ctor',0),source);
rootHandle(source,0);
const result=objectNew(loginResult);
call(method(loginResult,'.ctor',1),result,[source]);
rootHandle(result,0);
const localName=stringNew(Memory.allocUtf8String(familyCredential?'family:'+familyCredential.username:'Dittobuffer local test'));
rootHandle(localName,0);
const bytes=Array.from(familyCredential?familyCredential.token:'dittobuffer-local-player',c=>c.charCodeAt(0));
const array=arrayNew(klass('System','Byte'),bytes.length);
array.add(32).writeByteArray(bytes);
rootHandle(array,0);
const callbacks=[];
function replace(m,returnValue,args,body){
  const cb=new NativeCallback(function(){ensureThread();return body.apply(this,arguments);},returnValue,args);
  callbacks.push(cb);Interceptor.replace(m.readPointer(),cb);
}
// Version negotiation uses Unity HTTP, outside N2Server*. Route the version
// document to our server too, retaining the client's normal version check.
const web=klass('UnityEngine.Networking','UnityWebRequest');
const TRACE_NETWORK=globalThis.LIBRE_TRACE_NETWORK===true;
// Bypass only this clone's prelogin cache. Earlier local rules otherwise remain
// cached before UnityWebRequest is constructed. Keep its old cache files intact.
Interceptor.attach(mod.base.add(0x7e31bbc),{onEnter(args){
  const file=args[1];if(file.isNull())return;
  const urlObject=file.add(16).readPointer();if(urlObject.isNull())return;
  const url=urlObject.add(20).readUtf16String(urlObject.add(16).readS32());
  const match=url.match(/\/prelogin\/(settings|gmts)\.binpb\.gz/);
  if(!match)return;
  const routed=stringNew(Memory.allocUtf8String(localBase+'/prelogin/'+match[1]+'.binpb.gz'));
  rootHandle(routed,0);
  // Reference fields need the IL2CPP write barrier; a raw write breaks the
  // incremental GC's snapshot-at-the-beginning bookkeeping.
  api('gc_wbarrier_set_field','void',['pointer','pointer','pointer'])(file,file.add(16),routed);
  args[2]=ptr(0);args[3]=ptr(1);
  console.log('Refreshing local prelogin '+match[1]);
}});
const hookedUrls=new Set();
const seenUrls=new Set();
for(const [name,index] of [['Get',0],['InternalSetUrl',1],['SetUrl',1],['set_url',1]]){
  const m=getMethod(web,Memory.allocUtf8String(name),1);
  if(m.isNull()||hookedUrls.has(m.readPointer().toString()))continue;
  hookedUrls.add(m.readPointer().toString());
  Interceptor.attach(m.readPointer(),{onEnter(args){
  const value=args[index];
  if(value.isNull())return;
  const url=value.add(20).readUtf16String(value.add(16).readS32());
  const privateUrl=privateResourceUrl(url,localBase);
  if(privateUrl&&privateUrl!==url){
    args[index]=stringNew(Memory.allocUtf8String(privateUrl));return;
  }
  // Redirect only when the server actually published a matching runtime
  // registry. Standalone builds may legitimately keep client_resources off,
  // in which case the native splash catalogs still come from the CDN. Forcing
  // the local path here turns that startup request into a 404 and stalls the
  // loading screen at 50%.
  const runtimeResource=runtimeResourceUrl(url,localBase,globalThis.LIBRE_CLIENT_RESOURCES===true);
  if(runtimeResource){
    args[index]=stringNew(Memory.allocUtf8String(runtimeResource));return;
  }
  const safe=url.split('?')[0].replace(/[0-9a-fA-F]{24,}/g,'[asset-hash]');
  if(TRACE_NETWORK&&!seenUrls.has(safe)){
    seenUrls.add(safe);
    console.log('Unity HTTP '+safe);
  }
  if(/nianticlabs\.com.*version/i.test(url)){
    args[index]=stringNew(Memory.allocUtf8String(localBase+'/version'));
    console.log('Version document routed to local server');
  }else if(/^https?:\/\/127\.0\.0\.1(?::8080)?\/assets\/dittobuffer-stop\.png/.test(url)){
    // ImageDownloadService normalizes POI URLs to HTTPS and removes the port.
    // Restore only this project's image endpoint after that transformation.
    args[index]=stringNew(Memory.allocUtf8String(localBase+'/assets/dittobuffer-stop.png'));
    console.log('Local stop image routed to local server');
  }else if(/^https?:\/\/127\.0\.0\.1(?::8080)?\/world\/(photos\/[a-f0-9]{64}\.jpg|placeholders\/[a-f0-9]{64}\.png|photo-unavailable\.png)(?:[=?].*)?$/.test(url)){
    // The same native image normalization applies to verified geographic photos.
    // Restore only the two project-owned world image routes, never arbitrary URLs.
    const path=url.match(/\/world\/(?:photos\/[a-f0-9]{64}\.jpg|placeholders\/[a-f0-9]{64}\.png|photo-unavailable\.png)/)[0];
    args[index]=stringNew(Memory.allocUtf8String(localBase+path));
    console.log('Local world photo routed to local server');
  }else if(/nianticstatic\.com\/prelogin\/(settings|gmts)\.binpb\.gz/.test(url)){
    const name=url.includes('/settings.')?'settings':'gmts';
    args[index]=stringNew(Memory.allocUtf8String(localBase+'/prelogin/'+name+'.binpb.gz'));
    console.log('Prelogin '+name+' routed to local snapshot');
  }
  }});
}
const versionClass=klass('Niantic.Holoholo.Connectivity','VersionCheckService');
Interceptor.attach(method(versionClass,'znj',1).readPointer(),{
  onEnter(args){
    const value=call(method(getClass(args[1]),'ToString',0),args[1]);
    console.log('Minimum client version received: '+value.add(20).readUtf16String(value.add(16).readS32()));
  },onLeave(value){console.log('Version comparison accepted: '+value.toInt32());}
});
for(const [name,n] of [['Login',1],['LoginWithCachedCredentials',0]]){
  const login=method(provider,name,n);
  const futureClass=fromType(returnType(login));
  const alloc=method(futureClass,'Alloc',0);
  const complete=method(futureClass,'CompleteSucceeded',1);
  replace(login,'pointer',Array(n+2).fill('pointer'),function(){
    const future=call(alloc,ptr(0));
    call(complete,future,[result]);
    console.log(familyCredential?'Private family login supplied':'Local test login supplied (no third-party credentials)');
    return future;
  });
}
function credentials(self,callback,info){
  call(method(getClass(callback),'Invoke',1),callback,[array]);
  console.log('Local credentials delivered to native transport');
}
for(const name of ['GoogleAuthSource','NianticTokenAuthSource']){
  const k=klass('Niantic.Platform.Auth',name);
  replace(method(k,'GetCredentials',1),'void',['pointer','pointer','pointer'],credentials);
  replace(method(k,'get_AccountName',0),'pointer',['pointer','pointer'],()=>localName);
}
console.log(familyCredential?'PRIVATE FAMILY AUTH ACTIVE: account credential exchange required':'LOCAL TEST AUTH ACTIVE: existing Google button now selects only the local development identity');
