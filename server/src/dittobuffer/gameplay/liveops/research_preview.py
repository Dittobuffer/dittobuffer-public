"""Read-only Research review; warnings are feasibility hints, never progress."""
from copy import deepcopy
from .rules import interval
from .catalog import TASKS

def preview(document,catalog,documents=(),defaults=()):
    d=deepcopy(document);start,end=interval(d);warnings=[];tasks=[];rewards=[]
    def stages(values,path):
        for i,stage in enumerate(values):
            rewards.extend(stage.get('rewards',[]))
            for j,t in enumerate(stage.get('objectives',[])):
                tasks.append(dict(path=f'{path}[{i}].objectives[{j}]',**t));rewards.extend(t.get('rewards',[]))
    stages(d.get('stages',[]),'stages')
    for p in d.get('paths',[]):stages(p.get('stages',[]),'paths.'+p['id']+'.stages');rewards.extend(p.get('rewards',[]))
    for i,e in enumerate(d.get('entries',[])):tasks.append(dict(path='entries.'+str(i),event=e.get('methods',['catch'])[0],target=1,conditions={'species':[e['species']]},methods=e.get('methods',['catch'])))
    rewards.extend(d.get('rewards',[]));rewards.extend(d.get('encounter_pool',[]))
    sources=[]
    for other in documents:
        if other.get('kind') not in ('season','event'):continue
        a,b=interval(other)
        if a>=end or b<=start:continue
        for rules in [other.get('rules',{}),*[w.get('rules',{}) for w in other.get('weeks',[])],*[r.get('rules',{}) for r in other.get('rotations',[])]]:
            if not d.get('event_only') or d.get('id') in rules.get('research',[]):sources.extend(rules.get('spawns',[]))
    possible={e.get('species',e.get('id')) for e in sources}
    if not sources and not d.get('event_only'):
        possible={e.get('id',e.get('species')) for pool in defaults for e in pool.get('species',[])}
    for t in tasks:
        if t['event'] not in TASKS:warnings.append(dict(path=t['path'],message='Unsupported task counter: '+t['event']))
        wanted=set(t.get('conditions',{}).get('species',[]))
        if t['event']=='catch' and wanted and not wanted&possible:warnings.append(dict(path=t['path'],message=f"Requires {t['target']} qualifying catches, but species {sorted(wanted)} are absent from the reviewed event/default spawn pools."))
        if t['event']=='trade':warnings.append(dict(path=t['path'],message='Requires an independent cooperating Trainer; native two-device M25 verification is NOT_TESTED.'))
    if d.get('prerequisite')==d.get('id'):warnings.append(dict(path='prerequisite',message='Research cannot depend on itself.'))
    if d.get('event_only') and not sources:warnings.append(dict(path='event_only',message='No overlapping referenced event spawn pool was found. Review publication, scope and eligibility.'))
    encounters=[]
    for r in rewards:
        if r.get('type')=='encounter':
            result=deepcopy(r)
            try:catalog.mon(r);result['resources_ready']=True
            except ValueError as e:result['resources_ready']=False;warnings.append(dict(path='rewards',message=str(e)))
            encounters.append(result)
    return dict(read_only=True,title=d.get('name'),research_type=d.get('research_type','special'),start=d['start'],end=d['end'],timezone=d.get('timezone','UTC'),eligibility={k:d[k] for k in ('scope','minimum_level','prerequisite','event_only','entitlement') if k in d},tasks=tasks,rewards=rewards,encounters=encounters,paths=d.get('paths',[]),warnings=warnings,note='Static feasibility review only. Does not simulate catches, grant rewards, create accounts or change progression. Weather, time, geography and cooperating players still affect availability.')
