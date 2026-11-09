// Exercise URI routing and full-width GC handle lifetime without a live client.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const handle=0x71b5870020n,freed=[];let hook;
const managed=text=>({isNull:()=>false,add:()=>({readS32:()=>text.length,readUtf16String:()=>text})});
const context={console:{log(){}},Process:{getModuleByName:()=>({base:{add:v=>v},getExportByName:v=>v})},
 Memory:{alloc:()=>({writeU64(){},readU64:()=>({toNumber:()=>0})}),allocUtf8String:v=>v},
 Interceptor:{attach:(address,callbacks)=>{hook=callbacks;}},
 NativeFunction:function(name,result,args){
  if(name==='il2cpp_string_new')return text=>managed(text);
  if(name==='il2cpp_gchandle_new')return ()=>result==='pointer'?handle:Number(handle&0xffffffffn);
  if(name==='il2cpp_gchandle_free')return value=>{assert.deepEqual(Array.from(args),['pointer']);assert.equal(value,handle,'GC handle was truncated');freed.push(value);};
  return ()=>null;
 }};
const helpers=fs.readFileSync('client/local-auth.js','utf8').split('const sourceClass=')[0];
vm.runInNewContext(helpers+'\n'+fs.readFileSync('client/local-raids.js','utf8'),context);
for(let index=0;index<20;index++){
 const invocation={handle:0xdeadn},args=[null,managed('https://pgorelease.nianticlabs.com/raid/')];
 hook.onEnter.call(invocation,args);assert.equal(args[1].add().readUtf16String(),'http://127.0.0.1:8080/raid/');hook.onLeave.call(invocation);
 const other={handle:0xbeefn},remote=managed('https://unrelated.invalid/');const untouched=[null,remote];
 hook.onEnter.call(other,untouched);hook.onLeave.call(other);assert.equal(untouched[1],remote);
}
assert.equal(freed.length,20,'Each routed connection must release exactly one root');
context.LIBRE_BASE_URL='https://family.example.org';
const routed={handle:0xdeadn},remoteArgs=[null,managed('https://pgorelease.nianticlabs.com/raid/')];
hook.onEnter.call(routed,remoteArgs);assert.equal(remoteArgs[1].add().readUtf16String(),'https://family.example.org/raid/');hook.onLeave.call(routed);
assert.equal(freed.length,21);
console.log('Raid routing/64-bit GC handle regression passed (21 connections, including configured HTTPS origin).');

// The repaired layer belongs only to an earned ordinary raid resolve screen.
const presentation=require('../client/local-raid-presentation.js');
const native={name:'RaidTransitionRingBackground',shader:'NianticCustom/UI/RingScanlines',supported:true};
for(let cycle=0;cycle<20;cycle++){
 assert.equal(presentation.eligible('RaidResolveUIController',2,native),true);
 for(const screen of ['IncidentCombatResolveUIController','BreadBattleResolveUIController','MainMenuGuiController','GymBattleVictoryGuiController'])assert.equal(presentation.eligible(screen,2,native),false);
 for(const state of [0,1,3,4,5])assert.equal(presentation.eligible('RaidResolveUIController',state,native),false);
 assert.equal(presentation.eligible('RaidResolveUIController',2,{...native,supported:false}),false);
 assert.equal(presentation.eligible('RaidResolveUIController',2,{...native,shader:'UI/Default'}),false);
}
// Native GLES mask: inner <= distance(uv-.5) <= outer. Full-screen coverage
// must include the center and every corner, independently of portrait ratio.
const q=presentation.quadProperties;
for(let x=0;x<=1;x+=.1)for(let y=0;y<=1;y+=.1){const distance=Math.hypot(x-.5,y-.5);assert(distance<q._Radius);assert(distance>q._Radius-q._Thickness);}
assert.equal(q._GradientScale,0,'A radial fade would hide stripes at the corners');
assert.equal(Object.isFrozen(q),true);
console.log('Native raid-only background guards and full-quad coverage passed (20 lifecycle scenarios).');

assert.equal(presentation.tint[3],1,'The native background must be opaque behind Summary');
assert(presentation.tint.slice(0,3).every(v=>v>0&&v<.5),'Resolve tint must retain a dark charcoal background');
