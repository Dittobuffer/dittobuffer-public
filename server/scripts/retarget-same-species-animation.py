"""Move pinned same-species source motion between explicit rest poses.

Each target local pose is targetBind * inverse(sourceBind) * sourcePose.
No cross-species motion or generated animation is accepted. Native visual
review remains necessary after this offline coordinate conversion.
"""
import argparse,copy,hashlib,importlib.util,json,math,re,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('fbx_converter',ROOT/'scripts/convert-pokemon-fbx.py');convert=importlib.util.module_from_spec(spec);spec.loader.exec_module(convert)
def local_matrix(position,rotation,scale):
    w,x,y,z=np.asarray(rotation,float)/np.linalg.norm(rotation)
    r=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],[2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],[2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
    matrix=np.eye(4);matrix[:3,:3]=r@np.diag(scale);matrix[:3,3]=position;return matrix
def index_nodes(root,required=()):
    out={}
    def visit(node,parent):
        name=node['name']
        if name in out and name in required:raise ValueError('Ambiguous animated source node '+name)
        out[name]=dict(matrix=np.asarray(node['transformation']).reshape(4,4),parent=parent)
        for child in node.get('children',[]):visit(child,name)
    visit(root,None);return out
def retarget(source,target,world_hierarchy=False,shared_idle=False,joint_aliases=None,root_idle=False):
    if joint_aliases:
        source=copy.deepcopy(source)
        if len(set(joint_aliases.values()))!=len(joint_aliases):raise ValueError('Joint aliases must be one-to-one')
        def rename(node):
            node['name']=joint_aliases.get(node['name'],node['name'])
            for child in node.get('children',[]):rename(child)
        rename(source['rootnode'])
        for clip in source['animations']:
            for channel in clip['channels']:channel['name']=joint_aliases.get(channel['name'],channel['name'])
    if root_idle and not (shared_idle and world_hierarchy):raise ValueError('Root idle requires explicit shared world idle conversion')
    if shared_idle:
        source=copy.deepcopy(source)
        source['animations']=[c for c in source['animations'] if c['name'].endswith('waitA01')]
        if len(source['animations'])!=1 or not world_hierarchy:raise ValueError('Explicit shared idle requires one real source idle and world hierarchy conversion')
    names={c['name'] for clip in source['animations'] for c in clip['channels']}
    before=index_nodes(source['rootnode'],names);after=index_nodes(target['rootnode'],names);result=copy.deepcopy(target);clips=[]
    omitted=sorted(names-set(after)) if shared_idle else []
    if shared_idle:
        names &= set(after)
        if not any(n.lower()=='waist' for n in names) or len(names)<(1 if root_idle else 3):raise ValueError('Insufficient same-species shared idle joints')
        if root_idle:
            names={n for n in names if n.lower()=='waist'}
            waist=next(iter(names))
            for mesh in target['meshes']:
                for bone in mesh.get('bones',[]):
                    if not any(w>0 for _,w in bone['weights']):continue
                    current=bone['name']
                    while current and current!=waist:current=after[current]['parent']
                    if current!=waist:raise ValueError('Root idle cannot drive a weighted bone outside its waist hierarchy')
    def world(nodes,name,local,cache):
        if name in cache:return cache[name]
        row=nodes[name];parent=row['parent'];value=local.get(name,row['matrix'])
        cache[name]=(world(nodes,parent,local,cache)@value) if parent else value
        return cache[name]
    source_bind={};target_bind={}
    for name in names:
        if name not in after:raise ValueError('Animated target node absent: '+name)
        world(before,name,{},source_bind);world(after,name,{},target_bind)
    corrections={name:target_bind[name]@np.linalg.inv(source_bind[name]) for name in names}
    for clip in source['animations']:
        duration=clip['duration']/clip['tickspersecond'];built=copy.deepcopy(clip);built.update(duration=duration*30,tickspersecond=30);channels=[]
        if world_hierarchy:
            outputs={c['name']:dict(name=c['name'],positionkeys=[],rotationkeys=[],scalingkeys=[]) for c in clip['channels'] if c['name'] in names}
            for frame in range(math.ceil(duration*30)+1):
                tick=min(duration,frame/30)*clip['tickspersecond'];at=min(duration*30,frame);local={}
                for c in clip['channels']:
                    q=convert.sample(c['rotationkeys'],tick,True);local[c['name']]=local_matrix(convert.sample(c['positionkeys'],tick),[q[3],*q[:3]],convert.sample(c['scalingkeys'],tick))
                source_pose={};target_pose={}
                for name in outputs:target_pose[name]=corrections[name]@world(before,name,local,source_pose)
                for name,output in outputs.items():
                    parent=after[name]['parent'];matrix=np.linalg.inv(world(after,parent,{},target_pose))@target_pose[name] if parent else target_pose[name];pos,rot,scale=convert.decompose(matrix.reshape(-1))
                    output['positionkeys'].append([at,[pos[k] for k in 'xyz']]);output['rotationkeys'].append([at,[rot[k] for k in 'wxyz']]);output['scalingkeys'].append([at,[scale[k] for k in 'xyz']])
            built['channels']=list(outputs.values());clips.append(built);continue
        for channel in clip['channels']:
            name=channel['name']
            if name not in after or before[name]['parent']!=after[name]['parent']:raise ValueError('Rest hierarchy is incompatible: '+name)
            correction=after[name]['matrix']@np.linalg.inv(before[name]['matrix']);output=dict(name=name,positionkeys=[],rotationkeys=[],scalingkeys=[])
            for frame in range(math.ceil(duration*30)+1):
                tick=min(duration,frame/30)*clip['tickspersecond'];p=convert.sample(channel['positionkeys'],tick);q=convert.sample(channel['rotationkeys'],tick,True);s=convert.sample(channel['scalingkeys'],tick);matrix=correction@local_matrix(p,[q[3],*q[:3]],s);pos,rot,scale=convert.decompose(matrix.reshape(-1));at=min(duration*30,frame)
                output['positionkeys'].append([at,[pos[k] for k in 'xyz']]);output['rotationkeys'].append([at,[rot[k] for k in 'wxyz']]);output['scalingkeys'].append([at,[scale[k] for k in 'xyz']])
            channels.append(output)
        built['channels']=channels;clips.append(built)
    result['animations']=clips
    if shared_idle:
        weighted={b['name'] for mesh in target['meshes'] for b in mesh.get('bones',[]) if any(w>0 for _,w in b['weights'])}
        result['libre_shared_idle_limitations']=dict(omitted_absent_source_channels=omitted,animated_shared_nodes=sorted(names),target_weighted_bones=len(weighted),directly_animated_weighted_bones=len(weighted&names),static_target_only_bones=sorted(weighted-names),note='Actual same-species ordinary idle on identically named joints. Form-specific extra joints follow their source bind pose and animated parents. This is derived compatibility motion, not recovered official form animation.')
    return result
def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--source',required=True);ap.add_argument('--target',required=True);ap.add_argument('--output',required=True);ap.add_argument('--species',type=int,required=True);ap.add_argument('--world-hierarchy',action='store_true');ap.add_argument('--shared-idle',action='store_true');ap.add_argument('--joint-aliases',help='Explicit source-to-target equivalent joint names in a project builds JSON');ap.add_argument('--root-idle',action='store_true',help='Use only real waist motion; every weighted target bone must descend from it');a=ap.parse_args();paths=[(ROOT/p).resolve() for p in (a.source,a.target,a.output)]
    if any(not p.is_relative_to(ROOT/'builds') for p in paths) or paths[2].exists():raise ValueError('Pinned project input scenes and fresh output required')
    source,target=[json.loads(p.read_text()) for p in paths[:2]];token='pm%04d_'%a.species
    for scene in (source,target):
        if not any(name.startswith(token) for name in index_nodes(scene['rootnode'])):raise ValueError('Same identified species required')
    if not source.get('animations'):raise ValueError('No actual source motion')
    aliases={}
    if a.joint_aliases:
        alias_path=(ROOT/a.joint_aliases).resolve()
        if not alias_path.is_relative_to(ROOT/'builds'):raise ValueError('Project joint mapping required')
        aliases=json.loads(alias_path.read_text())
    result=retarget(source,target,a.world_hierarchy,a.shared_idle,aliases,a.root_idle);result['libre_animation_provenance']=dict(source_scene=a.source,source_sha256=hashlib.sha256(paths[0].read_bytes()).hexdigest(),target_scene=a.target,target_sha256=hashlib.sha256(paths[1].read_bytes()).hexdigest(),method='Target world rest pose times inverse source world rest pose times actual source pose, reexpressed in target hierarchy' if a.world_hierarchy else 'Local target rest pose times inverse source rest pose times actual source animation',scope='Same species only; coordinate conversion, not generated motion; native visual acceptance required',shared_idle=a.shared_idle,joint_aliases=aliases,root_idle=a.root_idle)
    paths[2].write_text(json.dumps(result,separators=(',',':'))+'\n');print('Same-species clips converted:',len(result['animations']))
if __name__=='__main__':main()
