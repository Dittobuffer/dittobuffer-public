// Copyright (C) Dittobuffer Contributors
// Copyright (C) chucny
// Copyright (C) gekkedavi
//
// This file is part of Dittobuffer. It is distributed under the GNU General
// Public License v3.0 (see LICENSE). The copyright notices in this file must
// be preserved in all copies, forks, and derivative works. Removing or
// altering these notices violates the license and copyright law.

// Read-only exact model-address selection for this pinned 0.429.2 APK.
(()=>{
  const records=[];
  function managedText(p){return p.isNull()?null:p.add(20).readUtf16String(p.add(16).readS32());}
  Interceptor.attach(mod.base.add(0x50D2650),{
    onEnter(args){
      this.row={at_ms:Date.now(),species:args[1].toInt32()};
      // PokemonDisplayProto fields from this APK: costume +0x10, gender +0x14,
      // shiny +0x18, form +0x1c (not protobuf tag numbers).
      if(!args[2].isNull())Object.assign(this.row,{costume:args[2].add(16).readS32(),gender:args[2].add(20).readS32(),form:args[2].add(28).readS32(),shiny:args[2].add(24).readU8()!==0});
    },
    onLeave(result){try{this.row.address=managedText(result);records.push(this.row);}catch(error){records.push({error:String(error)});}}
  });
  rpc.exports={snapshot(){return records.slice();}};
})();
