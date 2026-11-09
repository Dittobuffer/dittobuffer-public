"""Retarget real same-species idle channels in matching local joint frames.

This explicit compatibility path preserves target bind proportions when a
nonuniformly scaled rig makes world-matrix retargeting produce shear. It does
not recover an original form-specific animation or animate target-only bones.
"""
import argparse,copy,hashlib,importlib.util,json,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('retarget',ROOT/'scripts/retarget-same-species-animation.py');base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)

def multiply(a,b):
    av,aw=np.asarray(a[:3]),a[3];bv,bw=np.asarray(b[:3]),b[3]
    result=np.r_[aw*bv+bw*av+np.cross(av,bv),aw*bw-av@bv]
    return result/np.linalg.norm(result)

def retarget(source,target):
    idle=[c for c in source['animations'] if c['name'].endswith('waitA01')]
    if len(idle)!=1:raise ValueError('Exactly one actual same-species source idle required')
    clip=idle[0];before=base.index_nodes(source['rootnode']);after=base.index_nodes(target['rootnode']);channels=[];omitted=[]
    for channel in clip['channels']:
        name=channel['name']
        if name not in after or before[name]['parent']!=after[name]['parent']:
            omitted.append(name);continue
        source_matrix=before[name]['matrix'];target_matrix=after[name]['matrix']
        if min(np.linalg.norm(source_matrix[:3,:3],axis=0))<1e-8 or min(np.linalg.norm(target_matrix[:3,:3],axis=0))<1e-8:
            omitted.append(name);continue
        sp,sq,ss=base.convert.decompose(source_matrix);tp,tq,ts=base.convert.decompose(target_matrix)
        sp=np.array([sp[k] for k in 'xyz']);tp=np.array([tp[k] for k in 'xyz']);sq=np.array([sq[k] for k in 'xyzw']);tq=np.array([tq[k] for k in 'xyzw']);ss=np.array([ss[k] for k in 'xyz']);ts=np.array([ts[k] for k in 'xyz'])
        correction=multiply(tq,np.r_[-sq[:3],sq[3]]);rotation=base.local_matrix([0,0,0],[correction[3],*correction[:3]],[1,1,1])[:3,:3]
        built=dict(name=name,positionkeys=[],rotationkeys=[],scalingkeys=[]);duration=clip['duration']/clip['tickspersecond']
        for frame in range(math.ceil(duration*30)+1):
            at=min(duration,frame/30);tick=at*clip['tickspersecond'];p=np.array(base.convert.sample(channel['positionkeys'],tick));q=base.convert.sample(channel['rotationkeys'],tick,True);scale=np.array(base.convert.sample(channel['scalingkeys'],tick));q=multiply(correction,q)
            built['positionkeys'].append([at*30,(tp+rotation@(p-sp)).tolist()]);built['rotationkeys'].append([at*30,[q[3],*q[:3]]]);built['scalingkeys'].append([at*30,(ts*scale/ss).tolist()])
        channels.append(built)
    if len(channels)<3 or not any(c['name'].lower()=='waist' for c in channels):raise ValueError('Insufficient matching local source idle joints')
    result=copy.deepcopy(target);result['animations']=[dict(name=clip['name'],duration=clip['duration']/clip['tickspersecond']*30,tickspersecond=30,channels=channels)];result['libre_local_idle_limitations']=dict(animated_matching_joints=[c['name'] for c in channels],omitted_source_joints=omitted,scope='Original same-species source idle, local quaternion and translation deltas; target scales/proportions preserved. Target-only or singular joints retain bind pose; no original form motion claimed.')
    return result

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--source',required=True);ap.add_argument('--target',required=True);ap.add_argument('--output',required=True);ap.add_argument('--species',type=int,required=True);a=ap.parse_args();paths=[(ROOT/p).resolve() for p in [a.source,a.target,a.output]]
    if any(not p.is_relative_to(ROOT/'builds') for p in paths) or paths[2].exists():raise ValueError('Pinned project scenes and fresh builds output required')
    source,target=[json.loads(p.read_text()) for p in paths[:2]]
    for scene in [source,target]:
        if not any(n.startswith(f'pm{a.species:04}_') for n in base.index_nodes(scene['rootnode'])):raise ValueError('Identified same-species rigs required')
    result=retarget(source,target);result['libre_animation_provenance']=dict(source_scene=a.source,source_sha256=hashlib.sha256(paths[0].read_bytes()).hexdigest(),target_scene=a.target,target_sha256=hashlib.sha256(paths[1].read_bytes()).hexdigest(),method='Explicit matching-local-frame TRS compatibility idle; no matrix shear approximation')
    paths[2].write_text(json.dumps(result,separators=(',',':'))+'\n');print('Matching local joints:',len(result['animations'][0]['channels']))
if __name__=='__main__':main()
