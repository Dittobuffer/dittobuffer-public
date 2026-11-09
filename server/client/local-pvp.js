// Opt-in 0.429.2 native Trainer combat transport. Presentation and touch input
// remain native. This does not modify the independent Rocket integration.
if (typeof Process !== 'undefined' && globalThis.LIBRE_PVP_SERVER_COMBAT === true) {
  const avatarClass=klass('Holoholo.Rpc','PlayerAvatarProto');
  const writeBarrier=api('gc_wbarrier_set_field','void',['pointer','pointer','pointer']);
  Interceptor.attach(mod.base.add(0x84F6528),{onEnter(args){
    // Native rematches construct new ChallengePlayers without legacy avatar
    // records. CombatStage.Initialize nevertheless calls PlayerAvatar.IsRemote
    // unconditionally (0x84F68B8). Keep the existing NeutralAvatar appearance;
    // provide only the absent compatibility record used by first-entry battles.
    const data=args[1];
    if(data.isNull())return;
    for(const [offset,combatOffset] of [[0x20,0x18],[0x28,0x10]]){
      const player=data.add(offset).readPointer();
      if(player.isNull()||!player.add(0x18).readPointer().isNull())continue;
      const combat=data.add(combatOffset).readPointer();
      const profile=combat.isNull()?ptr(0):combat.add(0x10).readPointer();
      if(profile.isNull()||profile.add(0x68).readPointer().isNull())continue;
      if(player.add(0x30).readPointer().isNull()){
        player.add(0x30).writePointer(profile);writeBarrier(player,player.add(0x30),profile);
      }
      let legacy=profile.add(0x20).readPointer();
      if(legacy.isNull()){legacy=objectNew(avatarClass);call(method(avatarClass,'.ctor',0),legacy);}
      player.add(0x18).writePointer(legacy);
      writeBarrier(player,player.add(0x18),legacy);
      console.log('PvP rematch: supplied missing legacy avatar record; neutral appearance retained');
    }
  }});
  Interceptor.attach(mod.base.add(0x84832F0), {onEnter(args) {
    if (args[1].toInt32() === 0) {
      args[1] = ptr(1);
      console.log('PvP: native authoritative server communication selected');
    }
  }});
  // These verified native state lifetimes encompass the arena's separate UI
  // layer, which does not pass through GuiController.CanvasGroupVisible.
  // Reuse the existing HUD owner so render, interaction and raycasts agree.
  for(const [enter,exit] of [[0x85758D0,0x8579B14], // VsSeekerCombatState
    [0x8568E8C,0x856B58C],[0x856C950,0x856FC20], // friend roles
    [0x85713F0,0x8574818], // PvpCombatRematchState
    [0x850F370,0x851068C], // VsSeekerCombatResolveState
    [0x8564EB4,0x8567C54]]){ // NpcCombatState; Rocket remains separate
    const key='trainer-combat-'+enter.toString(16);
    Interceptor.attach(mod.base.add(enter),{onEnter(){globalThis.LIBRE_HUD_BLOCK(key,true);console.log('PvP HUD enter '+key);}});
    Interceptor.attach(mod.base.add(exit),{onLeave(){globalThis.LIBRE_HUD_BLOCK(key,false);console.log('PvP HUD exit '+key);}});
  }
  // Results are a separate state; keep the same visibility owner through the
  // handover rather than allowing the overworld HUD to reappear between them.
  Interceptor.attach(mod.base.add(0x850AFCC),{onEnter(){globalThis.LIBRE_HUD_BLOCK('trainer-results',true);}});
  Interceptor.attach(mod.base.add(0x850D46C),{onLeave(){globalThis.LIBRE_HUD_BLOCK('trainer-results',false);}});
  // Bounded diagnostics contain exception types and code offsets only, never
  // account identifiers, credentials, request payloads or exception messages.
  let failures=0;
  for(const [ns,name] of [['System','NullReferenceException'],['System','ArgumentException'],
    ['System.Collections.Generic','KeyNotFoundException'],['System','InvalidOperationException']]){
    try{
      const ctor=method(klass(ns,name),'.ctor',0).readPointer();
      if(!ctor.isNull())Interceptor.attach(ctor,{onEnter(){
        if(failures++>=30)return;
        const frames=Thread.backtrace(this.context,Backtracer.ACCURATE)
          .filter(p=>p.compare(mod.base)>=0&&p.compare(mod.base.add(mod.size))<0)
          .map(p=>p.sub(mod.base).toString());
        console.log('PvP diagnostic '+name+' offsets='+JSON.stringify(frames));
      }});
    }catch(e){console.log('PvP diagnostic unavailable: '+name);}
  }
}
