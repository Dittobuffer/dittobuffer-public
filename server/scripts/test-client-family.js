// Exercise the exact credential boundary used by the clone; no real credential.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('client/local-auth.js','utf8');
const code=source.slice(source.indexOf('function familyCredentialFromText('),source.indexOf('const sourceClass='));
const context={};vm.runInNewContext(code+'\nglobalThis.read=familyCredentialFromText;globalThis.route=privateResourceUrl;globalThis.runtimeRoute=runtimeResourceUrl;',context);
assert.equal(context.read(null,false),null);assert.throws(()=>context.read(null,true),/Family login required/);
for(const text of ['null','false','0','[]','"text"','{}','{"token":"invalid","username":"alice"}','{bad json']){
  assert.throws(()=>context.read(text,false),/credential/);assert.throws(()=>context.read(text,true),/credential/);
}
const fixture=JSON.stringify({username:'fixture-only',token:'lf1_'+'X'.repeat(43)});
assert.equal(context.read(fixture,true).username,'fixture-only');
assert.throws(()=>context.read(JSON.stringify({username:'../other',token:'lf1_'+'X'.repeat(43)}),true),/credential/);
console.log('Family credential boundary regression passed: missing, malformed, wrong types, invalid principal and valid fixture.');
for(const origin of ['http://127.0.0.1:8080','http://127.0.0.1:18080','https://family.example.org']){
 for(const original of ['http://127.0.0.1:8080','https://127.0.0.1','https://127.0.0.1:18080'])assert.equal(context.route(original+'/world/placeholders/example.png',origin),origin+'/world/placeholders/example.png');
}
for(const url of ['https://example.org/world/photo.png','http://127.0.0.1:18081/world/photo.png','https://127.0.0.1.example.org/world/photo.png'])assert.equal(context.route(url,'http://127.0.0.1:18080'),null);
for(const extension of ['/world/placeholders/'+ 'a'.repeat(64)+'.png','/world/photos/'+'b'.repeat(64)+'.jpg','/assets/dittobuffer-stop.png']){
 for(const suffix of ['=s96','?size=96',''])assert.equal(context.route('https://127.0.0.1:18080'+extension+suffix,'http://127.0.0.1:18080'),'http://127.0.0.1:18080'+extension);
}
assert.equal(context.route('https://127.0.0.1/map/tiles/15/1/2?tileZoom=17','http://127.0.0.1:18080'),'http://127.0.0.1:18080/map/tiles/15/1/2?tileZoom=17');
console.log('Private resource routing regression passed, including normalized standalone HTTPS photo URLs.');

const cdn='https://asset-cdn-rel.nianticstatic.com',base='http://127.0.0.1:18080';
for(const name of ['catalog_926.json','catalog_926.hash','original.bundle']){
  for(const prefix of ['/ClientRemoteAssets/catalog_926/','/ClientRemoteAssets/catalog_926/bundles/Android/']){
    assert.equal(context.runtimeRoute(cdn+prefix+name,base,true),base+'/assets/client'+prefix+name);
    assert.equal(context.runtimeRoute(cdn+prefix+name,base,false),null);
  }
}
for(const path of ['/ClientRemoteAssets/catalog_other/new.bundle','/ClientRemoteAssets/catalog_ext_12.json','/ClientRemoteAssets/catalog_ext_12.hash']){
  assert.equal(context.runtimeRoute(cdn+path,base,true),base+'/assets/client'+path);
  assert.equal(context.runtimeRoute(cdn+path,base,false),null);
}
for(const path of ['/ClientRemoteAssets/catalog_926/../private','/unrecognized']){
  assert.equal(context.runtimeRoute(cdn+path,base,true),null);
  assert.equal(context.runtimeRoute(cdn+path,base,false),null);
}
for(const url of ['https://example.org/original.bundle',cdn+'.example.org/ClientRemoteAssets/catalog_926/original.bundle'])
  assert.equal(context.runtimeRoute(url,base,true),null);
console.log('Runtime routing passed: exact CDN catalog paths preserved, registry-disabled and unrelated requests untouched.');
