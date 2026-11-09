// Copyright (C) Dittobuffer Contributors
// Copyright (C) chucny
// Copyright (C) gekkedavi
//
// This file is part of Dittobuffer. It is distributed under the GNU General
// Public License v3.0 (see LICENSE). The copyright notices in this file must
// be preserved in all copies, forks, and derivative works. Removing or
// altering these notices violates the license and copyright law.

// Session-only routing for the development clone's separate Nebula raid client.
// The native launcher prefixes its relative RvnConnection URI with the release
// host independently of Dittobuffer's RPC transport. Route only our explicit path.
(()=>{
 const freeHandle=api('gchandle_free','void',['pointer']);
 Interceptor.attach(mod.base.add(0x7635978),{ // BattleCommunicationService.StartBattle
  onEnter(args){
   const value=args[1];if(value.isNull())return;
   const uri=value.add(20).readUtf16String(value.add(16).readS32());
   if(!['https://pgorelease.nianticlabs.com/raid/','http://127.0.0.1:8080/raid/'].includes(uri))return;
   const local=stringNew(Memory.allocUtf8String((globalThis.LIBRE_BASE_URL||'http://127.0.0.1:8080')+'/raid/'));
   this.localUriHandle=rootHandle(local,0);args[1]=local;
   console.log('Local authoritative Nebula raid transport selected');
  },
  onLeave(){if(this.localUriHandle)freeHandle(this.localUriHandle);}
 });
})();
