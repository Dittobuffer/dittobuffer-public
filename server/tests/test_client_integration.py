"""Source integration checks; these do not claim APK or Android acceptance."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')


def node_check(source, harness=None):
    if harness is None:
        # Windows Node can wait indefinitely on a large piped --check input.
        # Check the exact assembled bytes as a file, with a bounded child.
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'assembled-agent.js'
            script.write_text(source, encoding='utf-8')
            result = subprocess.run([NODE, '--check', str(script)], text=True, capture_output=True, cwd=ROOT, timeout=60)
    else:
        result = subprocess.run([NODE, '-e', harness], input=source, text=True, capture_output=True, cwd=ROOT, timeout=60)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)


@unittest.skipUnless(NODE, 'Node.js is required for client source integration checks')
class ClientIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('integration_standalone_builder', ROOT/'scripts/build-standalone.py')
        cls.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.builder)

    def test_agent_assembly_keeps_existing_adapters_and_opt_in_route_hooks(self):
        cases = (
            ({}, False),
            ({'routes_foreground_creation': False, 'routes_saved_postcard_compatibility': False}, False),
            ({'routes_foreground_creation': 'true', 'routes_saved_postcard_compatibility': 1}, False),
            ({'routes_foreground_creation': True, 'routes_saved_postcard_compatibility': True}, True),
        )
        for profile, enabled in cases:
            with self.subTest(profile=profile):
                agent = self.builder.build_agent(profile)
                self.assertEqual('const LibreNlpRoutesForeground=' in agent, enabled)
                self.assertEqual('const LibreNlpPostcards=' in agent, enabled)
                self.assertIn('Opt-in native Max diagnostics ready', agent)
                self.assertIn('PvP rematch: supplied missing legacy avatar record', agent)
                self.assertIn('DittobufferBootstrap', agent)
                self.assertNotIn('/* DITTOBUFFER_INTEGRATION */', agent)
                node_check(agent)

    def test_all_supported_diagnostics_can_be_assembled_together(self):
        agent = self.builder.build_agent({
            'routes_foreground_creation': True,
            'routes_saved_postcard_compatibility': True,
            'routes_diagnostics': True,
            'pvp_ui_diagnostics': True,
            'map_diagnostics': True,
        })
        node_check(agent)

    def test_missing_or_duplicate_bootstrap_marker_fails_closed(self):
        bootstrap = ROOT/'client/standalone-bootstrap.js'
        original_read = Path.read_text
        for replacement in ('// no integration marker', '/* DITTOBUFFER_INTEGRATION */\n/* DITTOBUFFER_INTEGRATION */'):
            def read_text(path, *args, **kwargs):
                return replacement if path == bootstrap else original_read(path, *args, **kwargs)
            with self.subTest(replacement=replacement), patch.object(Path, 'read_text', read_text):
                with self.assertRaisesRegex(ValueError, 'one Dittobuffer integration marker'):
                    self.builder.build_agent({})

    def test_runtime_enables_pvp_only_for_explicit_boolean_opt_in(self):
        bootstrap = (ROOT/'client/standalone-bootstrap.js').read_text(encoding='utf8')
        prefix = bootstrap.split('let installed=false;', 1)[0]
        node_check(prefix, r"""
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(0,'utf8');
for(const value of [undefined,false,'true',1,true]){
  const runtime={family:true,local_origin:'http://127.0.0.1:18080',pvp_server_combat:value};
  const context={Module:{getGlobalExportByName:x=>x},Memory:{allocUtf8String:x=>x},
    NativeFunction:function(){return ()=>{};},File:{readAllText:()=>JSON.stringify(runtime)},console:{}};
  vm.runInNewContext(source,context);
  assert.equal(context.LIBRE_PVP_SERVER_COMBAT,value===true);
}
""")

    def test_battle_hud_bridge_blocks_render_and_touch_before_and_after_initialization(self):
        node_check((ROOT/'client/local-ui.js').read_text(encoding='utf8'), r"""
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const hooks=new Map(),writes=[];let menuOpen=false;
const pointer=value=>({value,equals:other=>value===other.value,
  isNull:()=>value===0,add:()=>({readU8:()=>menuOpen?1:0})});
const context={Process:{},console:{log(){}},ptr:pointer,
  mod:{base:{add:value=>value}},Interceptor:{attach:(address,hook)=>hooks.set(address,hook)},
  NativeFunction:function(address){return (...args)=>writes.push([address,args[1]]);},
  api:name=>name==='object_unbox'?(()=>({readU8:()=>1})):(value=>({readUtf8String:()=>value.name})),
  getClass:value=>value,method:(_type,name)=>name,call:()=>({})};
vm.runInNewContext(fs.readFileSync(0,'utf8'),context);
const last=address=>writes.filter(row=>row[0]===address).at(-1)[1];
function visible(value){
  assert.equal(last(0xA779900),value?1:0,'alpha follows visibility');
  assert.equal(last(0xA779AD4),value?1:0,'interaction follows visibility');
  assert.equal(last(0xA779C94),value?1:0,'raycasts follow visibility');
}
// A battle may enter before PlayerHud.Initialize finishes.
context.LIBRE_HUD_BLOCK('trainer-results',true);
const init={};hooks.get(0x7D176D4).onEnter.call(init,[pointer(1)]);
hooks.get(0x7D176D4).onLeave.call(init);visible(false);
context.LIBRE_HUD_BLOCK('trainer-results',false);visible(true);
for(let cycle=0;cycle<20;cycle++){
  context.LIBRE_HUD_BLOCK('trainer-combat',true);visible(false);
  context.LIBRE_HUD_BLOCK('route-details',true);
  context.LIBRE_HUD_BLOCK('trainer-combat',false);visible(false);
  menuOpen=true;const menu={};hooks.get(0x7D11304).onEnter.call(menu,[pointer(2)]);
  context.LIBRE_HUD_BLOCK('route-details',false);visible(false);
  menuOpen=false;hooks.get(0x7D126F4).onEnter.call(menu,[pointer(2)]);
  hooks.get(0x7D126F4).onLeave.call(menu);visible(true);
}
""")


if __name__ == '__main__':
    unittest.main()
