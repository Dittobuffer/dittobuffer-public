// Autonomous, packaged integration. Script interaction opens no controller port.
const androidLog=new NativeFunction(Module.getGlobalExportByName('__android_log_write'),'int',['int','pointer','pointer']);
const logTag=Memory.allocUtf8String('DittobufferBootstrap');
console.log=(...values)=>androidLog(4,logTag,Memory.allocUtf8String(values.join(' ')));
console.error=(...values)=>androidLog(6,logTag,Memory.allocUtf8String(values.join(' ')));
const runtime=JSON.parse(File.readAllText('/data/user/0/org.librenlp.pokemongo.dev/files/dittobuffer-bootstrap.json'));
if(runtime.family!==true||runtime.local_origin!=='http://127.0.0.1:18080')throw Error('Invalid standalone bootstrap');
globalThis.LIBRE_FAMILY_MODE=true;
globalThis.LIBRE_STANDALONE=true;
globalThis.LIBRE_PVP_SERVER_COMBAT=runtime.pvp_server_combat===true;
globalThis.LIBRE_ROCKET_SERVER_COMBAT=runtime.rocket_server_combat===true;
globalThis.LIBRE_TRACE_NETWORK=runtime.network_diagnostics===true;
globalThis.LIBRE_CLIENT_RESOURCES=runtime.client_resources===true;
globalThis.LIBRE_WORLD_ENABLED=runtime.world===true;
globalThis.LIBRE_WORLD_STYLE=runtime.world_style;
globalThis.LIBRE_WORLD_WEATHER_CREDIT=true;
if(runtime.pokemon_assets)globalThis.LIBRE_POKEMON_CATALOG_URL=runtime.local_origin+'/assets/pokemon/catalog.json';
if(runtime.avatar_assets){
 globalThis.LIBRE_AVATAR_CATALOG_URL=runtime.local_origin+'/assets/avatar/catalog.json';
 globalThis.LIBRE_AVATAR_BODY_PRESETS=runtime.avatar_body_presets;
 globalThis.LIBRE_AVATAR_FACE_PRESETS=runtime.avatar_face_presets;
}
// Development fixture profiles are tied to the exact isolated server profile.
if(runtime.fixture_origin){
 globalThis.LIBRE_GEOGRAPHIC_FIXTURE=runtime.fixture_origin;
 setInterval(()=>{
  try{
   const site=JSON.parse(File.readAllText('/data/user/0/org.librenlp.pokemongo.dev/files/dittobuffer-fixture-location.json'));
   if(!runtime.fixture_locations.some(v=>v.lat===site.lat&&v.lng===site.lng))return;
   Object.assign(globalThis.LIBRE_GEOGRAPHIC_FIXTURE,{lat:site.lat,lng:site.lng});
  }catch(e){}
 },1000);
}
let installed=false;
function installLocal(){
 if(installed)return;installed=true;
 try{
  /* DITTOBUFFER_INTEGRATION */
  const unlink=new NativeFunction(Module.getGlobalExportByName('unlink'),'int',['pointer']);
  unlink(Memory.allocUtf8String('/data/user/0/org.librenlp.pokemongo.dev/files/dittobuffer-family.json'));
  console.log('Dittobuffer standalone integration ready');
  File.writeAllText('/data/user/0/org.librenlp.pokemongo.dev/files/dittobuffer-bootstrap-status','ready');
 }catch(e){
  console.error('Dittobuffer bootstrap failed: '+e.stack);
  File.writeAllText('/data/user/0/org.librenlp.pokemongo.dev/files/dittobuffer-bootstrap-status','failed');
  // Stop this clone rather than continue with partially installed adapters.
  new NativeFunction(Module.getGlobalExportByName('_exit'),'void',['int'])(78);
 }
}
{
 const m=Process.getModuleByName('libil2cpp.so');
 Interceptor.attach(m.getExportByName('il2cpp_init'),{onLeave(){setImmediate(installLocal);}});
 setTimeout(()=>{
  if(installed)return;
  try{if(!new NativeFunction(m.getExportByName('il2cpp_domain_get'),'pointer',[])().isNull())installLocal();}
  catch(e){console.error('Dittobuffer managed initialization pending');}
 },1500);
}
File.writeAllText('/data/user/0/org.librenlp.pokemongo.dev/files/dittobuffer-bootstrap-status','configured');
console.log('Autonomous integration configured; waiting for Unity');
