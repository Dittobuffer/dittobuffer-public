// Opt-in 0.429.2 cached Route Gift compatibility. The native converter drops
// GiftBoxProto.PinnedPostcardId when constructing temporary GiftBoxDetailsProto.
// Preserve only that server-authoritative value for the same Gift and encounter;
// the native pin widget, persistence, pin/unpin RPCs and rewards remain in charge.
const LibreNlpPostcards=(()=>{
  const validGift=value=>typeof value==='string'&&/^[1-9][0-9]{0,19}$/.test(value)&&(value.length<20||value<='18446744073709551615');
  const validEncounter=value=>typeof value==='string'&&/^mateo-[a-f0-9]{32}$/.test(value);
  const validPin=value=>typeof value==='string'&&/^[a-f0-9]{32}$/i.test(value);
  function createSavedPinBridge(clock=Date.now){
    const pending=new Map(),ttl=120000,limit=8;
    function prune(){const now=clock();for(const [key,value] of pending)if(value.expires<=now)pending.delete(key);}
    function keyOf(value){return value&&validEncounter(value.encounterId)&&validGift(value.giftId)?value.encounterId+'\0'+value.giftId:null;}
    return {
      capture(source){
        prune();const key=keyOf(source);if(key===null)return false;
        // A newer authoritative unpinned/malformed projection must invalidate
        // an older saved value, including during repeated menu navigation.
        pending.delete(key);
        if(source.pinId==='')return true;
        if(!validPin(source.pinId))return false;
        while(pending.size>=limit)pending.delete(pending.keys().next().value);
        pending.set(key,{pinId:source.pinId,expires:clock()+ttl});return true;
      },
      take(target){
        prune();const key=keyOf(target);
        if(key===null||target.isSending!==false)return null;
        const source=pending.get(key);if(!source)return null;
        pending.delete(key);
        // Never overwrite a saved value already owned by the native flow.
        return target.pinId===''?source.pinId:null;
      }
    };
  }
  // The main Collector counter counts completed regions, so a first genuine
  // pin can correctly leave that counter and its rank at zero. Permit access
  // to the native regional progress screen without inventing a medal award.
  function showVivillonProgress(value){
    if(!value||value.enabled!==true||value.profileGate!==true||value.badgeType!==1003)return false;
    const settings=value.settings,data=value.data,regions=new Set([1,2,3,5,6,7,8,9,10,11,12,13,15,16,17,18,19,20]);
    if(!settings||settings.enabled!==true||!Number.isInteger(settings.version)||settings.version<1||!data||data.version!==settings.version)return false;
    if(!Array.isArray(settings.regions)||settings.regions.length<1||settings.regions.length>18||settings.regions.some(region=>!regions.has(region))||new Set(settings.regions).size!==settings.regions.length)return false;
    if(!Array.isArray(data.regions)||data.regions.length<1||data.regions.length>18)return false;
    const seen=new Set();let progressed=false;
    for(const row of data.regions){
      if(!row||!settings.regions.includes(row.region)||seen.has(row.region)||!Number.isInteger(row.rank)||row.rank<0||!Number.isInteger(row.progress)||row.progress<0||(row.state!==0&&row.state!==1))return false;
      seen.add(row.region);progressed=progressed||row.progress>0||row.rank>0||row.state===1;
    }
    return progressed;
  }
  function emptyBookHeader(value){
    if(!value||value.enabled!==true||value.actualCount!==0||!Number.isInteger(value.capacity)||value.capacity<1||value.capacity>2147483647)return null;
    return {count:'0',capacity:String(value.capacity)};
  }
  return {createSavedPinBridge,showVivillonProgress,emptyBookHeader};
})();
if(typeof module==='object'&&module.exports)module.exports=LibreNlpPostcards;

if(typeof Process!=='undefined'&&runtime.routes_saved_postcard_compatibility===true)(()=>{
  const bridge=LibreNlpPostcards.createSavedPinBridge();
  const className=api('class_get_name','pointer',['pointer']);
  const stringClass=klass('System','String');
  const field=api('class_get_field_from_name','pointer',['pointer','pointer']);
  const setObject=api('field_set_value_object','void',['pointer','pointer','pointer']);
  const freeHandle=api('gchandle_free','void',['pointer']);
  const targetHandle=api('gchandle_get_target','pointer',['pointer']);
  function isClass(value,name){return !value.isNull()&&className(getClass(value)).readUtf8String()===name;}
  function text(value){
    if(value.isNull())return '';
    if(!getClass(value).equals(stringClass))return null;
    const length=value.add(16).readS32();return length>=0&&length<=128?value.add(20).readUtf16String(length):null;
  }
  // Exact native signature: (self, GiftExchangeEntryProto, string, Action).
  // Only JS text survives this call; no managed pointers cross an async boundary.
  Interceptor.attach(mod.base.add(0x8426358),{onEnter(args){
    try{
      if(!isClass(args[1],'GiftExchangeEntryProto'))return;
      const gift=args[1].add(0x10).readPointer();if(!isClass(gift,'GiftBoxProto'))return;
      bridge.capture({encounterId:text(args[2]),giftId:gift.add(0x10).readU64().toString(),pinId:text(gift.add(0x98).readPointer())});
    }catch(error){console.log('Cached Route Postcard source unavailable: '+error.message);}
  }});
  // Exact native signature: GiftPinSection.Initialize(GiftPinSection.Data).
  // Update the matching temporary details before native Initialize reads it.
  // Do not invoke a UI lifecycle method, set a toggle, or submit a gameplay RPC.
  Interceptor.attach(mod.base.add(0x843ea00),{onEnter(args){
    let handle=ptr(0);
    try{
      const data=args[1];if(data.isNull())return;
      const details=data.add(0x10).readPointer();if(!isClass(details,'GiftBoxDetailsProto'))return;
      const saved=bridge.take({encounterId:text(data.add(0x18).readPointer()),giftId:details.add(0x10).readU64().toString(),pinId:text(details.add(0x90).readPointer()),isSending:data.add(0x20).readU8()!==0});
      if(saved===null)return;
      const pinField=field(getClass(details),Memory.allocUtf8String('PinnedPostcardId'));if(pinField.isNull())throw Error('Native saved-Postcard field unavailable');
      const value=stringNew(Memory.allocUtf8String(saved));if(value.isNull())throw Error('Native saved-Postcard string allocation failed');
      handle=rootHandle(value,0);if(handle.isNull())throw Error('Native saved-Postcard GC root unavailable');
      const rooted=targetHandle(handle);if(rooted.isNull())throw Error('Native saved-Postcard GC target unavailable');
      setObject(details,pinField,rooted);
      console.log('Cached Route Postcard saved ID preserved for matching native Gift details');
    }catch(error){console.log('Cached Route Postcard projection unavailable: '+error.message);}
    finally{if(!handle.isNull())freeHandle(handle);}
  }});
  // PlayerProfilePage.cbze rejects zero-count non-basic medals before creating
  // their native list item. Limit this compatibility exception to its two
  // verified IsBasicBadge call sites. All later native feature/template checks,
  // the native detail controller and every stored badge value remain intact.
  const profileReturns=[mod.base.add(0x7885fd8),mod.base.add(0x7886000)];
  const typeObject=api('type_get_object','pointer',['pointer']);
  const classType=api('class_get_type','pointer',['pointer']);
  const settingsClass=klass('Holoholo.Rpc','ButterflyCollectorSettings');
  const profileClasses={page:klass('Niantic.Holoholo.UI','PlayerProfilePage'),player:klass('Niantic.Holoholo','PlayerService'),master:klass('','GameMasterData'),inventory:klass('Niantic.Holoholo.Collections','InventoryCache'),settings:settingsClass,item:klass('Holoholo.Rpc','HoloInventoryItemProto'),badge:klass('Holoholo.Rpc','BadgeData'),collector:klass('Holoholo.Rpc','ButterflyCollectorBadgeData'),region:klass('Holoholo.Rpc','ButterflyCollectorRegionMedal')};
  function hasType(value,type){return !value.isNull()&&getClass(value).equals(type);}
  function repeated(value,read,max=18){
    if(value.isNull())return [];
    const size=value.add(0x18).readS32(),array=value.add(0x10).readPointer();
    if(size<0||size>max||array.isNull()||array.add(0x18).readU64().compare(size)<0)throw Error('Invalid native Vivillon collection');
    return Array.from({length:size},(_,index)=>read(array,index));
  }
  function currentVivillon(page){
    let handle=ptr(0),typeHandle=ptr(0),staticMasterReady=null,stage='native profile caller class';
    const unavailable=reason=>({guard:{reason,staticMasterReady}});
    try{
      // Confirm the exact managed caller by metadata identity. This path does
      // not read class-name strings or rely on IsBasicBadge's unused instance.
      if(!hasType(page,profileClasses.page))throw Error('PlayerProfilePage metadata identity mismatch');
      stage='injected player and Game Master';
      const player=page.add(0x2c0).readPointer(),master=page.add(0x240).readPointer();
      if(!hasType(player,profileClasses.player)||!hasType(master,profileClasses.master))throw Error('Injected service metadata identity mismatch');
      // The static prelogin file can fail independently of authenticated GM
      // templates. MaybeGet<T> does not gate on this bit; require the actual
      // enabled typed settings below instead of assuming a global ready state.
      staticMasterReady=master.add(0x120).readU8()!==0;
      // This is the same Type-keyed cache used by GameMasterData.MaybeGet<T>.
      // It reads the server's current native template; no local feature default.
      stage='Game Master settings cache';
      const dictionary=master.add(0xe8).readPointer();if(dictionary.isNull())return unavailable('master-settings-cache-missing');
      stage='Game Master settings Type root';
      const settingsType=typeObject(classType(settingsClass));
      typeHandle=rootHandle(settingsType,0);if(typeHandle.isNull())throw Error('Native Vivillon settings Type GC root unavailable');
      const found=Memory.alloc(8);found.writePointer(ptr(0));
      stage='Game Master TryGetValue';
      const exists=call(method(getClass(dictionary),'TryGetValue',2),dictionary,[targetHandle(typeHandle),found]);
      stage='Game Master TryGetValue result';
      if(exists.isNull()||exists.add(16).readU8()===0)return unavailable('master-settings-type-key-missing');
      const settings=found.readPointer();if(!hasType(settings,profileClasses.settings))throw Error('Settings metadata identity mismatch');
      stage='injected InventoryCache';
      const inventory=player.add(0xe0).readPointer();if(!hasType(inventory,profileClasses.inventory))throw Error('InventoryCache metadata identity mismatch');
      stage='inventory key allocation';
      const keyClass=klass('Holoholo.Rpc','HoloInventoryKeyProto'),key=objectNew(keyClass);
      handle=rootHandle(key,0);if(handle.isNull())throw Error('Native Vivillon key GC root unavailable');
      stage='inventory key constructor';
      call(method(keyClass,'.ctor',0),key);
      stage='inventory key Badge setter';
      const badge=Memory.alloc(4);badge.writeS32(1003);call(method(keyClass,'set_Badge',1),key,[badge]);
      stage='InventoryCache GetItem';
      const item=call(method(getClass(inventory),'GetItem',1),inventory,[key]);if(item.isNull())return unavailable('inventory-badge-key-missing');
      // GetItem can also contain a client prediction. Read serverState only,
      // matching VivillonSubBadgeService.bjsy; predictions never unlock access.
      stage='inventory authoritative server state';
      const state=item.add(0x10).readPointer();if(state.isNull())return unavailable('inventory-badge-server-state-missing');
      if(state.add(0x18).readU8()!==0)return unavailable('inventory-badge-server-state-predicted');
      const gameItem=state.add(0x10).readPointer();if(!hasType(gameItem,profileClasses.item))throw Error('Inventory item metadata identity mismatch');
      stage='inventory BadgeData getter';
      const data=call(method(getClass(gameItem),'get_BadgeData',0),gameItem);
      if(!hasType(data,profileClasses.badge)||data.add(0x10).readS32()!==1003)return unavailable('inventory-badge-data-missing-or-wrong-type');
      stage='inventory ButterflyCollectorData getter';
      const collector=call(method(getClass(data),'get_ButterflyCollectorData',0),data);if(!hasType(collector,profileClasses.collector))return unavailable('inventory-collector-data-missing');
      stage='native regional settings and progress';
      return {
        staticMasterReady,
        settings:{enabled:settings.add(0x10).readU8()!==0,version:settings.add(0x14).readS32(),regions:repeated(settings.add(0x18).readPointer(),(array,index)=>array.add(0x20+index*4).readS32())},
        data:{version:collector.add(0x10).readS32(),regions:repeated(collector.add(0x18).readPointer(),(array,index)=>{
          const row=array.add(0x20+index*8).readPointer();if(!hasType(row,profileClasses.region))throw Error('Invalid native Vivillon region');
          return {region:row.add(0x10).readS32(),rank:row.add(0x14).readS32(),state:row.add(0x18).readS32(),progress:row.add(0x1c).readS32()};
        })}
      };
    }catch(error){throw Error(stage+': '+error.message);}
    finally{if(!handle.isNull())freeHandle(handle);if(!typeHandle.isNull())freeHandle(typeHandle);}
  }
  const profileDiagnostics=new Set();
  const profileSnapshots=new Set();
  function profileError(error){if(profileDiagnostics.size<8&&!profileDiagnostics.has(error.message)){profileDiagnostics.add(error.message);console.log('Native Vivillon profile progress unavailable: '+error.message);}}
  function profileSnapshot(current,accepted){
    const snapshot=current&&current.guard?current.guard:{reason:accepted?'authoritative-progress-accepted':'settings-or-progress-validation-failed'};
    if(current&&current.settings&&current.data){
      snapshot.staticMasterReady=current.staticMasterReady;
      snapshot.settingsEnabled=current.settings.enabled;snapshot.settingsVersion=current.settings.version;snapshot.inventoryVersion=current.data.version;
      snapshot.settingsRegionCount=current.settings.regions.length;snapshot.inventoryRegionCount=current.data.regions.length;
      snapshot.progressedRegions=current.data.regions.filter(row=>row.rank>0||row.progress>0||row.state===1).map(row=>({region:row.region,rank:row.rank,progress:row.progress,state:row.state}));
    }
    const encoded=JSON.stringify(snapshot);
    if(profileSnapshots.size<8&&!profileSnapshots.has(encoded)){profileSnapshots.add(encoded);console.log('Native Vivillon profile guard snapshot '+encoded);}
  }
  Interceptor.attach(mod.base.add(0x8332414),{
    onEnter(args){
      this.showProgress=false;
      if(args[1].toInt32()!==1003||!profileReturns.some(value=>value.equals(this.returnAddress)))return;
      try{
        // At both verified call sites cbze keeps PlayerProfilePage in x20.
        // Read and validate that caller synchronously, retaining only a JS
        // boolean across the native call, never a managed pointer.
        const current=currentVivillon(ptr(this.context.x20.toString()));
        this.showProgress=LibreNlpPostcards.showVivillonProgress({enabled:true,profileGate:true,badgeType:1003,...current});
        profileSnapshot(current,this.showProgress);
      }catch(error){profileError(error);}
    },
    onLeave(result){
      if(this.showProgress&&result.toInt32()===0){result.replace(1);console.log('Native Vivillon profile entry enabled for authoritative regional progress; badge rank/count unchanged');}
    }
  });
  // DisplayPostcards' empty branch returns before its native header refresh.
  // After that exact branch, display the actual empty count and the same
  // server-backed PlayerService capacity used by its nonempty branch.
  const bookClass=klass('Niantic.Holoholo.Collections','PostcardBookPage');
  const bookListClass=fromType(api('method_get_param','pointer',['pointer','uint'])(method(bookClass,'DisplayPostcards',1),0));
  const textClass=klass('UnityEngine.UI','Text');
  const assignable=api('class_is_assignable_from','int',['pointer','pointer']);
  function setBookText(target,value){
    if(target.isNull()||assignable(textClass,getClass(target))===0)throw Error('Native Book header Text type mismatch');
    let handle=ptr(0);
    try{
      const textValue=stringNew(Memory.allocUtf8String(value));
      handle=rootHandle(textValue,0);if(handle.isNull())throw Error('Native Book header string GC root unavailable');
      call(method(getClass(target),'set_text',1),target,[targetHandle(handle)]);
    }finally{if(!handle.isNull())freeHandle(handle);}
  }
  Interceptor.attach(mod.base.add(0x85ad100),{
    onEnter(args){
      this.bookPageHandle=ptr(0);this.bookListHandle=ptr(0);
      try{
        if(!hasType(args[0],bookClass)||!hasType(args[1],bookListClass)||args[1].add(0x18).readS32()!==0)return;
        this.bookPageHandle=rootHandle(args[0],0);this.bookListHandle=rootHandle(args[1],0);
        if(this.bookPageHandle.isNull()||this.bookListHandle.isNull())throw Error('Native empty Book GC root unavailable');
      }catch(error){profileError(Error('Empty Book header entry: '+error.message));}
    },
    onLeave(){
      try{
        if(this.bookPageHandle.isNull()||this.bookListHandle.isNull())return;
        const page=targetHandle(this.bookPageHandle),list=targetHandle(this.bookListHandle);
        if(!hasType(page,bookClass)||!hasType(list,bookListClass)||list.add(0x18).readS32()!==0)return;
        const player=page.add(0x1a0).readPointer();if(!hasType(player,profileClasses.player))throw Error('Native Book PlayerService metadata mismatch');
        const capacity=call(method(getClass(player),'get_MaxPostcards',0),player);
        const header=LibreNlpPostcards.emptyBookHeader({enabled:true,actualCount:0,capacity:capacity.isNull()?null:capacity.add(16).readS32()});
        if(header===null)return;
        setBookText(page.add(0x40).readPointer(),header.count);setBookText(page.add(0x48).readPointer(),header.capacity);
        console.log('Native empty Postcard Book header refreshed from actual count and server capacity');
      }catch(error){profileError(Error('Empty Book header projection: '+error.message));}
      finally{
        if(!this.bookPageHandle.isNull())freeHandle(this.bookPageHandle);
        if(!this.bookListHandle.isNull())freeHandle(this.bookListHandle);
      }
    }
  });
  console.log('Cached Route Postcard compatibility loaded');
})();
