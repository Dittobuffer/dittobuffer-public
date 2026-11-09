// Copyright (C) Dittobuffer Contributors
// Copyright (C) chucny
// Copyright (C) gekkedavi
//
// This file is part of Dittobuffer. It is distributed under the GNU General
// Public License v3.0 (see LICENSE). The copyright notices in this file must
// be preserved in all copies, forks, and derivative works. Removing or
// altering these notices violates the license and copyright law.

const assert=require('node:assert/strict');
const {create}=require('../client/local-world.js');
for(let i=0;i<20;i++){
  let calls=0;const state=create(()=>calls++);
  state.location(52.1065958,5.0755989);state.pause(true);
  state.location(52.072775,5.1234387);state.update();assert.equal(calls,0);
  state.pause(false);assert.equal(state.update(),true);state.update();assert.equal(calls,1);
  state.pause(true);state.location(52.0729,5.1235);state.pause(false);state.update();assert.equal(calls,1);
  state.pause(true);state.location(NaN,5);state.location(91,5);state.location(52,181);state.pause(false);state.update();assert.equal(calls,1);
  state.pause(true);state.location(52.3584,4.8686);state.pause(false);state.pause(true);state.update();assert.equal(calls,1);
  state.pause(false);state.update();assert.equal(calls,2);
}
console.log('World resume regression passed: 20 cycles, one targeted refresh after large paused movement, none for adjacent/invalid fixes.');
