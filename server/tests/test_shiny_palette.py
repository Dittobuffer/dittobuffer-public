import copy,hashlib,importlib.util,json,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
try:
    import numpy as np
    spec=importlib.util.spec_from_file_location('same_species_animation',ROOT/'scripts/retarget-same-species-animation.py');ANIMATION=importlib.util.module_from_spec(spec);spec.loader.exec_module(ANIMATION)
except (ImportError,OSError):ANIMATION=None
if ANIMATION is not None:
    spec=importlib.util.spec_from_file_location('source_palette',ROOT/'scripts/convert-source-palette.py');PALETTE=importlib.util.module_from_spec(spec);spec.loader.exec_module(PALETTE)
def transform(x):
    return [1,0,0,x,0,1,0,0,0,0,1,0,0,0,0,1]
def node(name,x=0,children=()):return dict(name=name,transformation=transform(x),children=list(children))
def clip(name='Head',start=1,end=3):
    return dict(name='actual_source_idle',duration=1,tickspersecond=1,channels=[dict(name=name,positionkeys=[[0,[start,0,0]],[1,[end,0,0]]],rotationkeys=[[0,[1,0,0,0]],[1,[1,0,0,0]]],scalingkeys=[[0,[1,1,1]],[1,[1,1,1]]])])
class ShinyPaletteTests(unittest.TestCase):
    @unittest.skipIf(ANIMATION is None,'Optional numpy asset tools not installed')
    def test_palette_transfer_preserves_alpha_and_uses_actual_source_rgb(self):
        from PIL import Image
        legacy=Image.fromarray(np.array([[[200,0,0,128],[0,0,255,0]]],dtype=np.uint8));normal=Image.fromarray(np.array([[[200,0,0,255],[0,0,255,0]]],dtype=np.uint8));shiny=Image.fromarray(np.array([[[0,200,0,255],[255,0,255,0]]],dtype=np.uint8));image,report=PALETTE.transfer(legacy,normal,shiny)
        self.assertEqual(image.getpixel((0,0)),(0,200,0,128));self.assertEqual(image.getpixel((1,0)),legacy.getpixel((1,0)));self.assertTrue(report['alpha_unchanged'])

    @unittest.skipIf(ANIMATION is None,'Optional numpy asset tools not installed')
    def test_zero_data_alpha_body_pixels_still_supply_the_palette(self):
        from PIL import Image
        legacy=Image.fromarray(np.array([[[200,0,0,255]]],dtype=np.uint8));normal=Image.fromarray(np.array([[[200,0,0,0],[0,0,0,255]]],dtype=np.uint8));shiny=Image.fromarray(np.array([[[0,200,0,0],[0,0,0,255]]],dtype=np.uint8));image,_=PALETTE.transfer(legacy,normal,shiny,True)
        self.assertEqual(image.getpixel((0,0)),(0,200,0,255))

    @unittest.skipIf(ANIMATION is None,'Optional numpy asset tools not installed')
    def test_ambiguous_source_color_chooses_a_real_mode_not_an_invented_blend(self):
        from PIL import Image
        legacy=Image.new('RGBA',(1,1),(200,0,0,255));normal=Image.new('RGBA',(3,1),(200,0,0,255));shiny=Image.fromarray(np.array([[[0,200,0,255],[0,200,0,255],[0,0,200,255]]],dtype=np.uint8));image,_=PALETTE.transfer(legacy,normal,shiny)
        self.assertEqual(image.getpixel((0,0)),(0,200,0,255))

    def test_effect_palette_changes_preserve_shader_state_and_alpha(self):
        path=ROOT/'builds/m108-magmar-source-fire-v2.json'
        if not path.exists():self.skipTest('Pinned source effect fixture absent')
        try:import UnityPy
        except ImportError:self.skipTest('Optional Unity asset tools absent')
        from PIL import Image
        m=json.loads(path.read_text());meta=m['import_metadata'];source=json.loads((ROOT/meta['source_manifest']).read_text());before=UnityPy.load(str(ROOT/source['content'][meta['source_sha256']]['file']));owner=next(row for row in m['bundles'] if row.get('source_sha256')==meta['source_sha256']);after=UnityPy.load(str(ROOT/m['content'][owner['sha256']]['file']));a={str(o.path_id):o for o in before.objects};b={str(o.path_id):o for o in after.objects}
        self.assertEqual(len(meta['material_color_changes']),3)
        for row in meta['material_color_changes']:
            old=a[row['path_id']].read_typetree();new=b[row['path_id']].read_typetree();self.assertEqual(old['m_Shader'],new['m_Shader']);self.assertEqual(old['m_SavedProperties']['m_Floats'],new['m_SavedProperties']['m_Floats']);self.assertEqual(old['m_SavedProperties']['m_TexEnvs'],new['m_SavedProperties']['m_TexEnvs']);self.assertEqual(row['expected_value']['a'],row['value']['a'])
            for key,value in row['value'].items():self.assertAlmostEqual(dict(new['m_SavedProperties']['m_Colors'])[row['property']][key],value,places=6)

    def test_palette_derivation_preserves_mesh_rig_material_and_motion_bytes(self):
        path=ROOT/'builds/m108-shiny-pilot/pm49.s.json'
        if not path.exists():self.skipTest('Pinned research fixture not present')
        try:import UnityPy
        except ImportError:self.skipTest('Optional Unity asset tools not installed')
        from PIL import Image
        m=json.loads(path.read_text());metadata=m['import_metadata'];source=json.loads((ROOT/metadata['source_manifest']).read_text());before=UnityPy.load(str(ROOT/source['content'][metadata['source_sha256']]['file']));derived=next(row for row in m['bundles'] if row.get('source_sha256')==metadata['source_sha256']);after=UnityPy.load(str(ROOT/m['content'][derived['sha256']]['file']));a={o.path_id:o for o in before.objects};b={o.path_id:o for o in after.objects}
        for key,obj in a.items():
            if obj.type.name in ('Mesh','Transform','Material','Animator','AnimatorController','AnimatorOverrideController','AnimationClip','MonoBehaviour'):
                self.assertEqual(obj.get_raw_data(),b[key].get_raw_data(),obj.type.name)
        for change in metadata['changes']:
            image=Image.open(ROOT/change['file']).convert('RGBA');self.assertEqual(hashlib.sha256(image.tobytes()).hexdigest(),change['shiny_pixels_sha256']);self.assertEqual(b[int(change['path_id'])].read().image.convert('RGBA').tobytes(),image.tobytes())

    @unittest.skipIf(ANIMATION is None,'Optional numpy asset tools not installed')
    def test_same_hierarchy_motion_is_reexpressed_in_the_target_rest_pose(self):
        source=dict(rootnode=node('Root',children=[node('Head',1)]),animations=[clip()]);target=dict(rootnode=node('Root',children=[node('Head',2)]),animations=[]);result=ANIMATION.retarget(source,target);keys=result['animations'][0]['channels'][0]['positionkeys'];self.assertAlmostEqual(keys[0][1][0],2);self.assertAlmostEqual(keys[-1][1][0],4);self.assertEqual(source['animations'][0]['channels'][0]['positionkeys'][0][1],[1,0,0])

    @unittest.skipIf(ANIMATION is None,'Optional numpy asset tools not installed')
    def test_reparented_same_species_motion_requires_explicit_world_mapping(self):
        source=dict(rootnode=node('Root',children=[node('Head',1)]),animations=[clip(end=2)]);target=dict(rootnode=node('Root',children=[node('Helper',4,children=[node('Head',2)])]),animations=[])
        with self.assertRaisesRegex(ValueError,'hierarchy'):ANIMATION.retarget(source,target)
        result=ANIMATION.retarget(source,target,True);keys=result['animations'][0]['channels'][0]['positionkeys'];self.assertAlmostEqual(keys[0][1][0],2);self.assertAlmostEqual(keys[-1][1][0],3)

    @unittest.skipIf(ANIMATION is None,'Optional numpy asset tools not installed')
    def test_explicit_orb_root_idle_preserves_motion_and_rejects_unrelated_bones(self):
        c=clip('Waist');c['name']='ba10_waitA01'
        source=dict(rootnode=node('Root',children=[node('Waist',1)]),animations=[c])
        target=dict(rootnode=node('Root',children=[node('waist',2,children=[node('eyelid')])]),meshes=[dict(bones=[dict(name='eyelid',weights=[[0,1]])])])
        with self.assertRaisesRegex(ValueError,'Insufficient'):ANIMATION.retarget(source,target,True,True,{'Waist':'waist'})
        result=ANIMATION.retarget(source,target,True,True,{'Waist':'waist'},True)
        channel=result['animations'][0]['channels'][0];self.assertEqual(channel['name'],'waist');self.assertAlmostEqual(channel['positionkeys'][0][1][0],2);self.assertAlmostEqual(channel['positionkeys'][-1][1][0],4)
        target['rootnode']['children'].append(node('unrelated'));target['meshes'][0]['bones'].append(dict(name='unrelated',weights=[[1,1]]))
        with self.assertRaisesRegex(ValueError,'outside'):ANIMATION.retarget(source,target,True,True,{'Waist':'waist'},True)

    @unittest.skipIf(ANIMATION is None,'Optional numpy asset tools not installed')
    def test_local_idle_keeps_nonuniform_target_scales_without_matrix_shear(self):
        spec=importlib.util.spec_from_file_location('local_idle',ROOT/'scripts/retarget-local-trs-idle.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        c=clip('Waist');c['name']='ba10_waitA01';c['channels']+= [dict(c['channels'][0],name=n) for n in ['Head','Tail']]
        source=dict(rootnode=node('Root',children=[node(n,1) for n in ['Waist','Head','Tail']]),animations=[c])
        target=dict(rootnode=node('Root',children=[node(n,2) for n in ['Waist','Head','Tail']]))
        for child in target['rootnode']['children']:child['transformation'][0]=2;child['transformation'][5]=3
        result=module.retarget(source,target)
        for channel in result['animations'][0]['channels']:
            self.assertEqual(channel['scalingkeys'][0][1],[2,3,1]);self.assertAlmostEqual(channel['positionkeys'][0][1][0],2);self.assertAlmostEqual(channel['positionkeys'][-1][1][0],4)
        q=module.multiply([0,0,2**-.5,2**-.5],[0,0,2**-.5,2**-.5]);self.assertTrue(np.allclose(abs(q),[0,0,1,0],atol=1e-7))

    @unittest.skipIf(ANIMATION is None,'Optional numpy asset tools not installed')
    def test_head_prop_bind_basis_cancels_native_sideways_axes_without_position_offsets(self):
        path=ROOT/'builds/m108-attachment-pilot-v3/pm25.cNOVEMBER_2018.json'
        if not path.exists():self.skipTest('Pinned native prop fixture absent')
        m=json.loads(path.read_text())['import_metadata'];head=np.array(m['source_head_bind']);bind=np.array(m['prop_bind'])
        self.assertTrue(np.allclose(head[:3,:3]@bind[:3,:3],np.eye(3),atol=1e-6))
        self.assertTrue(np.allclose(bind[:3,3],0))
        point=np.array([.04,.13,.08,1.]);self.assertTrue(np.allclose((head@bind@point)[:3],head[:3,3]+point[:3],atol=1e-6))

    def test_new_prop_preserves_native_body_mesh_skin_materials_and_motion(self):
        path=ROOT/'builds/m108-attachment-pilot-v3/pm25.cNOVEMBER_2018.json'
        if not path.exists():self.skipTest('Pinned native prop fixture absent')
        try:import UnityPy
        except ImportError:self.skipTest('Optional Unity asset tools absent')
        m=json.loads(path.read_text());meta=m['import_metadata'];source=json.loads((ROOT/meta['source_manifest']).read_text());old=UnityPy.load(str(ROOT/source['content'][meta['source_sha256']]['file']));owner=next(b for b in m['bundles'] if b.get('source_sha256')==meta['source_sha256']);new=UnityPy.load(str(ROOT/m['content'][owner['sha256']]['file']));objects={o.path_id:o for o in new.objects}
        for obj in old.objects:
            if obj.type.name in ('Mesh','SkinnedMeshRenderer','Material','Animator','AnimatorController','AnimatorOverrideController','AnimationClip','MonoBehaviour'):
                self.assertEqual(obj.get_raw_data(),objects[obj.path_id].get_raw_data(),obj.type.name)

    def test_shared_party_prop_uses_the_original_evolution_mount_without_moving_the_body(self):
        path=ROOT/'builds/m108-evolved-party-hats/pm5.cJAN_2020_NOEVOLVE.assjson'
        if not path.exists():self.skipTest('Pinned shared prop fixture absent')
        target=json.loads((ROOT/'builds/m108-current-costume-models/pm5.cJAN_2020_NOEVOLVE.assjson').read_text());source=json.loads((ROOT/'builds/m108-current-costume-scenes/pm4.cJAN_2020_NOEVOLVE.assjson').read_text());restored=json.loads(path.read_text())
        def mount(node):
            if node['name']=='doodad_pokemonday2020_hat':return node
            return next((found for child in node.get('children',[]) if (found:=mount(child))),None)
        old,new=mount(target['rootnode']),mount(restored['rootnode']);self.assertNotIn('meshes',old);self.assertEqual(old['transformation'],new['transformation']);self.assertEqual(target['meshes'],restored['meshes'][:-1]);self.assertEqual(target['animations'],restored['animations'])
        canonical=dict(source['meshes'][0]);canonical['materialindex']=len(target['materials']);self.assertEqual(restored['meshes'][-1],canonical);self.assertEqual(new['meshes'],[len(target['meshes'])])

    def test_secondary_prop_mesh_hash_identifies_its_actual_spine_mount(self):
        path=ROOT/'builds/m108-anniversary-bone-hashes/pm25.cANNIVERSARY_2022_NOEVOLVE.json'
        if not path.exists():self.skipTest('Pinned secondary mount fixture absent')
        try:import UnityPy
        except ImportError:self.skipTest('Optional Unity asset tools absent')
        import zlib
        m=json.loads(path.read_text());variant=m['variants'][0];bundles={b['index']:b for b in m['bundles']};owner=next(bundles[i] for i in variant['bundle_indices'] if any(a['type']=='GameObject' and a['path'] in variant['model_paths'] for a in bundles[i]['assets']));env=UnityPy.load(str(ROOT/m['content'][owner['sha256']]['file']));objects={o.path_id:o for o in env.objects}
        mounts={}
        for obj in env.objects:
            if obj.type.name!='SkinnedMeshRenderer':continue
            data=obj.read_typetree();mesh=objects.get(data['m_Mesh']['m_PathID'])
            if not mesh or not mesh.read_typetree()['m_Name'].endswith('_source_prop'):continue
            bone=objects[data['m_Bones'][0]['m_PathID']].read_typetree();name=objects[bone['m_GameObject']['m_PathID']].read_typetree()['m_Name'];md=mesh.read_typetree();mounts[name]=md['m_BoneNameHashes'];self.assertEqual(md['m_BoneNameHashes'],[zlib.crc32(name.encode())]);self.assertEqual(md['m_RootBoneNameHash'],zlib.crc32(name.encode()))
        self.assertEqual(set(mounts),{'Head','Spine2'})
if __name__=='__main__':unittest.main()
