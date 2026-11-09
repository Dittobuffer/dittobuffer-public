const assert=require('node:assert/strict');
const {create}=require('../client/local-ui.js');
const {blocksRouteScreen}=require('../client/local-ui.js');
const changes=[];
const hud=create(visible=>changes.push(visible));
assert.equal(hud.gui(true),true,'overworld');
for(let cycle=0;cycle<20;cycle++){
  assert.equal(hud.menu(true),false,'menu hides portrait and touch');
  assert.equal(hud.request(true),false,'late HUD refresh cannot reveal menu HUD');
  assert.equal(hud.gui(false),false,'storage/bag/research/full-screen entry');
  assert.equal(hud.menu(false),false,'closing radial menu must not reveal through full-screen UI');
  assert.equal(hud.gui(true),true,'return to overworld');
}
hud.request(false);
hud.menu(true);hud.menu(false);
assert.equal(hud.visible(),false,'preserve independent tutorial/notification hide');
assert.equal(hud.request(true),true);
const count=changes.length;
hud.gui(true);hud.menu(false);hud.request(true);
assert.equal(changes.length,count,'unchanged state causes no extra Unity writes');
hud.menu(true);
const hiddenCount=changes.length;
assert.equal(hud.gui(true,true),false,'late native GUI visibility write cannot expose menu HUD');
assert.equal(changes.length,hiddenCount+1,'overwrite repairs render and touch state even if policy unchanged');
hud.gui(false,true);hud.menu(false);
assert.equal(hud.visible(),false,'nested full-screen close still requires overworld visibility');
assert.equal(hud.gui(true,true),true);
let fail=true;
const retry=create(()=>{if(fail)throw new Error('transient Unity failure');});
assert.throws(()=>retry.gui(true),/transient/);
fail=false;
assert.equal(retry.gui(true),true,'failed apply can be retried');
console.log('Trainer HUD navigation regression passed (20 repeated cycles).');
const friendStage=require('../client/local-social.js');
assert.strictEqual(friendStage.usePose(true),true);
assert.strictEqual(friendStage.usePose(true,true,0),false,'asynchronous public friend stage');
assert.strictEqual(friendStage.usePose(true,true,1),true,'own player profile keeps its pose');
assert.strictEqual(friendStage.usePose(true,false,0),true,'unrelated stage keeps its pose');
for(let cycle=0;cycle<20;cycle++){
  for(let context=0;context<=9;context++){
    assert.strictEqual(friendStage.usePose(true,true,context),context!==0);
    assert.strictEqual(friendStage.usePose(true,false,context),true);
    assert.strictEqual(friendStage.usePose(false,true,context),false);
  }
}
console.log('Friend stage regression passed: 20 cycles across all ten native stage contexts.');
for(let cycle=0;cycle<20;cycle++){
  hud.block('buddy',true);hud.gui(true,true);hud.request(true);
  assert.equal(hud.visible(),false,'buddy blocks late tutorial/HUD refreshes');
  hud.block('buddy-perks',true);hud.block('buddy',false);
  assert.equal(hud.visible(),false,'nested buddy screen retains block');
  hud.block('buddy-perks',false);
  assert.equal(hud.visible(),true,'all buddy screens closed restores overworld');
}
hud.block('medal',true);hud.block('level-up',true);hud.block('medal',false);
assert.equal(hud.request(true),false,'queued level-up retains hidden render and touch');
hud.block('level-up',false);
assert.equal(hud.visible(),true,'award queue ending restores overworld');
assert.equal(blocksRouteScreen('RouteCompletionGuiController'),true);
assert.equal(blocksRouteScreen('NearbyRouteDetailsGuiController'),true);
assert.equal(blocksRouteScreen('RouteNotificationGui'),false,'active Route HUD stays usable');
for(let cycle=0;cycle<20;cycle++){
  hud.block('route-details',blocksRouteScreen('NearbyRouteDetailsGuiController'));
  hud.block('route-rating',blocksRouteScreen('RouteRatingGuiController'));
  hud.block('route-details',false);hud.gui(true,true);hud.request(true);
  assert.equal(hud.visible(),false,'nested Route UI blocks portrait and touch after a late refresh');
  hud.block('route-rating',false);
  assert.equal(hud.visible(),true,'Route UI dismiss restores overworld HUD');
}
console.log('Route HUD regression passed (20 nested navigation cycles).');
const {trackerMode}=require('../client/local-routes.js');
assert.equal(trackerMode(1,true,true,true),2,'explicit foreground creator selects real native manual tracker');
assert.equal(trackerMode(1,false,true,true),1,'ordinary builds keep native policy');
assert.equal(trackerMode(1,true,false,true),1,'background cannot enable recording');
assert.equal(trackerMode(1,true,true,false),1,'missing GPS cannot enable recording');
assert.equal(trackerMode(2,true,true,true),2,'ordinary Route following is unchanged');

const {createSavedPinBridge,showVivillonProgress,emptyBookHeader}=require('../client/local-postcards.js');
const encounterId='mateo-'+'a'.repeat(32),giftId='9007199254740993',pinId='b'.repeat(32);
const source={encounterId,giftId,pinId},target={encounterId,giftId,pinId:'',isSending:false};
let clock=1000,pins=createSavedPinBridge(()=>clock);
assert.equal(pins.capture(source),true);
assert.equal(pins.take({...target,giftId:'9007199254740992'}),null,'different uint64 Gift must not receive saved state');
assert.equal(pins.take({...target,encounterId:'mateo-'+'c'.repeat(32)}),null,'different encounter must not receive saved state');
assert.equal(pins.take({...target,isSending:true}),null,'outgoing Gifts retain their native behavior');
assert.equal(pins.take(target),pinId,'exact matching temporary details recover the authoritative saved ID');
assert.equal(pins.take(target),null,'projection correlation is consumed once');
for(const bad of [null,{}, {...source,giftId:9007199254740993},{...source,giftId:'018'},{...source,giftId:'18446744073709551616'},{...source,pinId:'made-up-card'},{...source,encounterId:'ordinary-friend'}])assert.equal(pins.capture(bad),false,'malformed/unsafe source cannot produce a saved ID');
assert.equal(pins.capture(source),true);assert.equal(pins.capture({...source,pinId:''}),true);
assert.equal(pins.take(target),null,'newer authoritative unpinned state invalidates a prior saved correlation');
assert.equal(pins.capture(source),true);
assert.equal(pins.take({...target,pinId:'d'.repeat(32)}),null,'existing native saved state is never overwritten');
assert.equal(pins.capture(source),true);clock+=120001;
assert.equal(pins.take(target),null,'expired async correlation cannot affect a later UI');
for(let cycle=0;cycle<20;cycle++){assert.equal(pins.capture(source),true);assert.equal(pins.take(target),pinId);assert.equal(pins.take(target),null);}
console.log('Cached Route Gift saved-pin projection regression passed (identity, expiry and 20 reopen cycles).');

const regionIds=[1,2,3,5,6,7,8,9,10,11,12,13,15,16,17,18,19,20];
const unearned=regionIds.map(region=>({region,rank:0,state:0,progress:0}));
const visibility={enabled:true,profileGate:true,badgeType:1003,settings:{enabled:true,version:1,regions:regionIds},data:{version:1,regions:unearned}};
assert.equal(showVivillonProgress(visibility),false,'projecting 18 zero-progress regions must not unlock an entry');
const firstPin={...visibility,data:{version:1,regions:unearned.map(row=>({...row,progress:row.region===16?1:0}))}};
assert.equal(showVivillonProgress(firstPin),true,'a real first River pin exposes native 1/3 progress');
assert.equal(showVivillonProgress({...firstPin,staticMasterReady:false}),true,'a failed unrelated static file cannot hide real enabled GM settings and authoritative pin progress');
assert.equal(showVivillonProgress({...firstPin,staticMasterReady:false,settings:null}),false,'static-file failure cannot substitute for missing authoritative GM settings');
for(const field of ['enabled','profileGate'])assert.equal(showVivillonProgress({...firstPin,[field]:false}),false,'compatibility is opt-in and restricted to the profile gate');
assert.equal(showVivillonProgress({...firstPin,badgeType:1}),false,'ordinary medals retain native filtering');
assert.equal(showVivillonProgress({...firstPin,settings:{...firstPin.settings,enabled:false}}),false,'server-disabled feature retains native filtering');
assert.equal(showVivillonProgress({...firstPin,settings:null}),false,'missing native Game Master data fails closed');
assert.equal(showVivillonProgress({...firstPin,data:{...firstPin.data,version:2}}),false,'stale or unrelated inventory versions fail closed');
for(const row of [{region:16,rank:1,state:0,progress:0},{region:16,rank:0,state:1,progress:0}])assert.equal(showVivillonProgress({...visibility,data:{version:1,regions:[row]}}),true,'earned rank or pending encounter retains access after progress resets');
for(const regions of [[],[unearned[0],unearned[0]],[{region:4,rank:1,state:0,progress:0}],[{region:16,rank:0,state:2,progress:1}],[{region:16,rank:0,state:0,progress:-1}],[{region:16,rank:0,state:0,progress:NaN}]])assert.equal(showVivillonProgress({...visibility,data:{version:1,regions}}),false,'malformed, duplicate or unsupported regional data fails closed');
const originalProgress=JSON.stringify(firstPin);
for(let cycle=0;cycle<20;cycle++)assert.equal(showVivillonProgress(firstPin),true);
assert.equal(JSON.stringify(firstPin),originalProgress,'repeated native profile opens preserve all authoritative inventory data');
console.log('Vivillon profile visibility regression passed (first pin, native feature gate, truthful counters and 20 reopen cycles).');
assert.deepEqual(emptyBookHeader({enabled:true,actualCount:0,capacity:350}),{count:'0',capacity:'350'},'empty Book uses the authoritative server-backed capacity');
assert.deepEqual(emptyBookHeader({enabled:true,actualCount:0,capacity:721}),{count:'0',capacity:'721'},'capacity is not hardcoded to a production or fixture default');
for(const value of [null,{}, {enabled:false,actualCount:0,capacity:350},{enabled:true,actualCount:1,capacity:350},{enabled:true,actualCount:-1,capacity:350},{enabled:true,actualCount:0,capacity:0},{enabled:true,actualCount:0,capacity:'350'},{enabled:true,actualCount:0,capacity:350.5},{enabled:true,actualCount:0,capacity:2147483648}])assert.equal(emptyBookHeader(value),null,'nonempty, disabled or invalid Book data keeps native behavior');
console.log('Empty Postcard Book header regression passed (real count, configurable capacity and fail-closed guards).');

const {createMaxBallReadinessAdapter}=require('../client/local-max-ball.js');
const nativeBalls={max:true,loaded:false,holding:false,powers:0,held:[],released:[],errors:[]};
const maxBallReady=createMaxBallReadinessAdapter({
  retain:b=>{const handle='0x1234567800000000:'+b;nativeBalls.held.push(handle);return handle;},
  release:h=>nativeBalls.released.push(h),
  isMaxEncounter:()=>nativeBalls.max,artLoaded:()=>nativeBalls.loaded,
  isHolding:()=>nativeBalls.holding,
  readyImmediately:()=>{nativeBalls.powers++;nativeBalls.holding=true;},
  error:e=>nativeBalls.errors.push(e)
});
maxBallReady.track('ordinary');nativeBalls.max=false;maxBallReady.tick();
assert.equal(nativeBalls.powers,0,'ordinary/raid/Rocket catch presentation is untouched');
nativeBalls.max=true;
for(let cycle=0;cycle<20;cycle++){
  nativeBalls.loaded=false;nativeBalls.holding=false;maxBallReady.track('max-'+cycle);
  maxBallReady.tick();assert.equal(nativeBalls.powers,cycle,'wait for actual native art');
  nativeBalls.loaded=true;maxBallReady.tick();maxBallReady.tick();
  assert.equal(nativeBalls.powers,cycle+1,'native immediate-ready runs once per actual Max ball');
}
maxBallReady.track('already-ready');maxBallReady.tick();
assert.equal(nativeBalls.powers,20,'a native ready ball is not enlarged again');
nativeBalls.holding=false;maxBallReady.track('early-launch');
assert.equal(maxBallReady.ready('early-launch'),true,'early throw uses loaded native Max art');
assert.equal(nativeBalls.powers,21);
maxBallReady.track('destroyed');maxBallReady.forget('destroyed');maxBallReady.tick();
assert.deepEqual(nativeBalls.held.toSorted(),nativeBalls.released.toSorted(),'all full-width GC handles are released, including destruction');
assert.deepEqual(nativeBalls.errors,[]);
console.log('Max Ball readiness regression passed (20 cycles, async art, early throw, ordinary catch isolation and GC handle cleanup).');

const {createMaxBallCaptureAdapter}=require('../client/local-max-ball.js');
const captureState={hit:false,held:[],released:[],frames:[]};
const captureArt=createMaxBallCaptureAdapter({
 retain:b=>{captureState.held.push(b);return b;},release:b=>captureState.released.push(b),
 isMaxHit:()=>captureState.hit,captureScale:()=>({max:[4,4,4],normal:[1,1,1]}),
 present:(b,scale,normal)=>captureState.frames.push({b,scale,normal}),error:e=>{throw e;}
});
assert.equal(captureArt.hit('ordinary-or-miss'),false,'no Max art overrides without actual Max Pokemon collision');
captureArt.tick();assert.deepEqual(captureState.frames,[]);
captureState.hit=true;
for(let cycle=0;cycle<20;cycle++){
 const ball='capture-'+cycle;
 assert.equal(captureArt.hit(ball),true);assert.equal(captureArt.hit(ball),false,'repeated collision retains one visual owner');
 captureArt.tick();assert.deepEqual(captureState.frames.at(-1),{b:ball,scale:{max:[4,4,4],normal:[1,1,1]},normal:false},'hold native Max art until its real shrink callback');
 captureArt.shrink(ball);captureArt.tick();assert.deepEqual(captureState.frames.at(-1),{b:ball,scale:{max:[4,4,4],normal:[1,1,1]},normal:true},'retain both authored sizes; final close-up must not inherit giant scale');
 captureArt.forget(ball);const n=captureState.frames.length;captureArt.tick();assert.equal(captureState.frames.length,n,'destroy/reinitialize removes stale presentation');
}
assert.deepEqual(captureState.held,captureState.released,'capture handles released once');
console.log('Max Ball capture-art regression passed (real collision gate, native size, repeated capture ownership and cleanup).');

const confirmedRocketResearch=require('../client/local-rocket.js').confirmed;
assert.equal(confirmedRocketResearch(1,'owned-child',10,{id:'owned-child',status:2,updated:10}),true);
assert.equal(confirmedRocketResearch(1,'owned-child',10,{id:'other-child',status:2,updated:10}),false);
assert.equal(confirmedRocketResearch(1,'owned-child',10,{id:'owned-child',status:1,updated:10}),false);
assert.equal(confirmedRocketResearch(1,'owned-child',10,{id:'owned-child',status:2,updated:9}),false);
console.log('Recovered Rocket Research confirmation guards passed.');
