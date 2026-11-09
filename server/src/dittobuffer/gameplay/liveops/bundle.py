"""Versioned, bounded declarative interchange for the existing LiveOps engine.

No imported paths or code are evaluated. Validation and preview never write.
"""
from copy import deepcopy
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfoNotFoundError
from jsonschema import Draft202012Validator
from .store import digest
from . import validation
from .rules import interval, validate_scope
from .catalog import ITEMS, TASKS

FORMAT = 'dittobuffer.liveops.bundle'
MAX_BYTES = 2_000_000
MAX_DEPTH = 32
MAX_NODES = 100_000
SCHEMA = json.loads(Path(__file__).with_name('bundle-v1.schema.json').read_text(encoding='utf8'))
VALIDATOR = Draft202012Validator(SCHEMA)


def path(parts):
    return '$' + ''.join(f'[{v}]' if isinstance(v, int) else '.'+str(v) for v in parts)


def bounded(value):
    stack = [(value, 0)]; nodes = 0
    while stack:
        item, depth = stack.pop(); nodes += 1
        if depth > MAX_DEPTH or nodes > MAX_NODES:
            raise ValueError('JSON exceeds the 32-level / 100,000-node parsing limit')
        if isinstance(item, dict): stack.extend((v, depth+1) for v in item.values())
        elif isinstance(item, list): stack.extend((v, depth+1) for v in item)
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError('JSON bundle exceeds 2 MB')


def loads(raw):
    if isinstance(raw, str): raw = raw.encode('utf8')
    if len(raw) > MAX_BYTES: raise ValueError('JSON bundle exceeds 2 MB')
    def pairs(rows):
        result = {}
        for k, v in rows:
            if k in result: raise ValueError('Duplicate JSON object key: '+k[:100])
            result[k] = v
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda v: (_ for _ in ()).throw(ValueError('Non-finite JSON number')))
    except (RecursionError, UnicodeError, json.JSONDecodeError) as e:
        raise ValueError('Invalid JSON or excessive nesting') from e
    bounded(value)
    return value


def canonical_bundle(value):
    value = deepcopy(value)
    for key in ('content', 'regions'):
        if key in value: value[key] = sorted(value[key], key=lambda d: d['id'])
    deps = value.get('dependencies', {})
    for k in ('content','capabilities','items'):
        if k in deps: deps[k] = sorted(set(deps[k]), key=str)
    return value


def _name(v): return re.sub(r'[^a-z0-9]', '', v.casefold())


def normalize(value, catalog, errors):
    """Names are aliases of evidenced registry identities, never guessed IDs."""
    names = {}
    for sid, rules in catalog.species.items():
        names.setdefault(_name(rules['name']), set()).add(sid)
    for (sid, _), rules in catalog.forms.items():
        names.setdefault(_name(rules['name']), set()).add(sid)
    def resolve(v, choices, location):
        if not isinstance(v, str): return v
        found = choices.get(_name(v), set())
        if len(found) != 1:
            errors.append({'path':path(location),'message':'Unknown or ambiguous registry name: '+v[:120]})
            return v
        return next(iter(found))
    item_names = {_name(v):{k} for k,v in ITEMS.items()}
    def walk(o, p=()):
        if isinstance(o, dict):
            if 'species' in o:
                if isinstance(o['species'], list): o['species'] = [resolve(v,names,p+('species',i)) for i,v in enumerate(o['species'])]
                else:
                    o['species'] = resolve(o['species'], names, p+('species',))
                    sid = o['species']
                    if isinstance(o.get('form'), str):
                        forms = {}
                        for (s,f),r in catalog.forms.items():
                            if s == sid: forms.setdefault(_name(r['name']),set()).add(f)
                        o['form'] = resolve(o['form'], forms, p+('form',))
            if 'item' in o: o['item'] = resolve(o['item'], item_names, p+('item',))
            for k,v in o.items(): walk(v,p+(k,))
        elif isinstance(o,list):
            for i,v in enumerate(o): walk(v,p+(i,))
    # Metadata/settings/operator notes are opaque annotations, not model entries.
    for k in ('content','dependencies'): walk(value.get(k), (k,))
    deps=value.get('dependencies',{})
    if 'items' in deps:deps['items']=[resolve(v,item_names,('dependencies','items',i)) for i,v in enumerate(deps['items'])]


def inspect(value, catalog, store=None):
    """Aggregate structural and semantic errors; provide a read-only import plan."""
    errors=[]; warnings=[]; summaries=[]; conflicts=[]
    def error(p, message): errors.append({'path':path(p), 'message':str(message)})
    def check(p, fn, *args):
        try: fn(*args)
        except (ValueError, KeyError, TypeError, ZoneInfoNotFoundError, OverflowError) as e: error(p,str(e))
    try: bounded(value)
    except (ValueError,RecursionError) as e:
        return {'valid':False,'errors':[{'path':'$','message':str(e)}],'read_only':True}
    value=deepcopy(value)
    for e in VALIDATOR.iter_errors(value): error(tuple(e.absolute_path),e.message)
    if errors: return {'valid':False,'errors':errors[:200],'read_only':True}
    normalize(value,catalog,errors)
    existing={r['id']:r for r in store.list()} if store else {}
    regions={r['id']:r for r in store.regions()} if store else {}
    seen=set(); docs={}
    for i,doc in enumerate(value['content']):
        if doc['id'] in seen: error(('content',i,'id'),'Duplicate exclusive content ID')
        seen.add(doc['id']); docs[doc['id']]=doc
    for i,r in enumerate(value.get('regions',[])):
        p=('regions',i)
        if r.get('type')!='polygon' or not r.get('id'): error(p,'Regions need stable IDs and polygon geometry')
        if r.get('id') in seen: error(p+('id',),'Duplicate exclusive identity')
        seen.add(r.get('id'));check(p,validate_scope,r)
        if r.get('id') in regions and regions[r['id']]!=r:
            conflicts.append({'id':r['id'],'kind':'region','different':True,'update_allowed':False})
        regions[r.get('id')]=r
    def reference(key,p,kind):
        target=docs.get(key) or existing.get(key,{}).get('document')
        if not target or target['kind']!=kind: error(p,'Missing '+kind+' dependency: '+key)
    def rules(rule,p):
        check(p,validation.rules,rule,catalog)
        for group in ('spawns','raids','eggs','max'):
            if sum(v.get('weight',1) for v in rule.get(group,[]))>1000000:error(p+(group,),'Total pool weight exceeds 1,000,000')
        for group in ('spawns','raids','eggs','max'):
            for j,row in enumerate(rule.get(group,[])):
                check(p+(group,j),validation.rules,{group:[row]},catalog)
        for j,key in enumerate(rule.get('research',[])):reference(key,p+('research',j),'research')
    for i,doc in enumerate(value['content']):
        p=('content',i)
        check(p+('schedule',),interval,doc);check(p+('scope',),validate_scope,doc['scope'])
        if doc['scope'].get('type')=='region' and doc['scope'].get('id') not in regions:error(p+('scope','id'),'Missing geographic region')
        if doc.get('season_id'): reference(doc['season_id'],p+('season_id',),'season')
        if doc.get('prerequisite'):reference(doc['prerequisite'],p+('prerequisite',),'research')
        if doc.get('required_capabilities'):error(p+('required_capabilities',),'Unimplemented native requirements: '+', '.join(doc['required_capabilities']))
        if doc['kind']=='research':
            typ=doc['research_type']
            if typ not in ('field','special','timed','collection','branching','timed_branching','breakthrough'):error(p+('research_type',),'Unsupported native Research adapter')
            for j,stage in enumerate(doc.get('stages',[])):
                for k,task in enumerate(stage['objectives']):
                    check(p+('stages',j,'objectives',k),validation.objective,task,catalog,typ)
                    for n,r in enumerate(task['rewards']):check(p+('stages',j,'objectives',k,'rewards',n),validation.reward,r,catalog,typ)
                for k,r in enumerate(stage['rewards']):check(p+('stages',j,'rewards',k),validation.reward,r,catalog,typ)
        else:
            rules(doc.get('rules',{}),p+('rules',))
            for j,w in enumerate(doc.get('weeks',[])):rules(w['rules'],p+('weeks',j,'rules'))
            for j,r in enumerate(doc.get('rotations',[])):rules(r['rules'],p+('rotations',j,'rules'))
            rotations=doc.get('rotations',[])
            if len({r['id'] for r in rotations})!=len(rotations):error(p+('rotations',),'Duplicate rotation IDs')
        check(p,validation.validate,doc,catalog)
        if doc['id'] in existing:
            old=existing[doc['id']]
            conflicts.append({'id':doc['id'],'kind':old['kind'],'revision':old['revision'],'status':old['status'],
                              'different':digest(doc)!=old['digest'],'update_allowed':old['published_revision'] is None and old['status']=='draft'})
        groups=[('Base',doc.get('rules',{}))]+[(w['name'],w['rules']) for w in doc.get('weeks',[])]+[(r['id'],r['rules']) for r in doc.get('rotations',[])]
        summaries.append({'id':doc['id'],'name':doc['name'],'kind':doc['kind'],'start':doc['start'],'end':doc['end'],
                          'timezone':doc['timezone'],'scope':doc['scope'],'schedule':[{ 'name':n, **rule,
                            'spawn_probabilities':[dict(v,probability=v.get('weight',1)/sum(x.get('weight',1) for x in rule['spawns'])) for v in rule.get('spawns',[])]} for n,rule in groups],
                          'stages':doc.get('stages',[]),'research_type':doc.get('research_type'),'entries':doc.get('entries',[]),'paths':doc.get('paths',[]),'rewards':doc.get('rewards',[]),'eligibility':{k:doc[k] for k in ('minimum_level','prerequisite','event_only') if k in doc}})
    deps=value.get('dependencies',{})
    for i,key in enumerate(deps.get('content',[])):
        if key not in docs and key not in existing:error(('dependencies','content',i),'Missing declared content dependency')
    supported={'field_research','special_research','timed_research','collection_challenges','branching_research','research_breakthrough','wild_spawns','bonuses','regions'}
    if catalog.config.get('raids',{}).get('enabled'):supported.add('raids')
    if catalog.config.get('mega',{}).get('enabled'):supported.add('mega_raids')
    if catalog.config.get('max_system',{}).get('enabled'):supported.add('released_max')
    for i,v in enumerate(deps.get('capabilities',[])):
        if v not in supported:error(('dependencies','capabilities',i),'Unavailable capability: '+v)
    for i,row in enumerate(deps.get('models',[])):check(('dependencies','models',i),catalog.mon,row)
    for i,v in enumerate(deps.get('items',[])):
        if v not in ITEMS:error(('dependencies','items',i),'Unsupported reward item')
    # Flag overlaps without treating intentional event overrides as publication errors.
    candidates=list(docs.values())+[r['document'] for k,r in existing.items() if k not in docs]
    for i,a in enumerate(candidates):
        if a['kind']=='research':continue
        for b in candidates[i+1:]:
            if b['kind']=='research' or (a['id'] not in docs and b['id'] not in docs):continue
            try:
                x,y=interval(a);z,w=interval(b)
                if max(x,z)<min(y,w) and a.get('priority',10)==b.get('priority',10):warnings.append({'path':'$.content','message':'Overlapping equal-priority schedules: '+a['id']+' / '+b['id']+'. Stable ID order resolves overrides; inspect geography.'})
            except (ValueError,KeyError,ZoneInfoNotFoundError):pass
    kinds={d['kind'] for d in docs.values()}; declared=value['bundle_type']
    if declared!='collection' and (declared not in kinds or (declared!='season' and kinds!={declared} and kinds!={declared,'research'})):error(('bundle_type',),'Declared type does not match included content')
    # Prerequisite and parent dependency cycles are never playable.
    def visit(key, trail):
        if key in trail:raise ValueError('Cyclic content dependency: '+key)
        d=docs.get(key,{})
        for child in (d.get('prerequisite'),d.get('season_id')):
            if child:visit(child,trail|{key})
    for key in docs:check(('content',),visit,key,set())
    value=canonical_bundle(value);fingerprint=digest(value)
    return {'valid':not errors,'errors':list({(e['path'],e['message']):e for e in errors}.values())[:200],
            'warnings':warnings[:100],'read_only':True,'bundle_type':declared,'name':value['metadata']['name'],
            'fingerprint':fingerprint,'bundle':value,'summary':summaries,'conflicts':conflicts,
            'counts':{k:sum(d['kind']==k for d in docs.values()) for k in ('season','event','research')},
            'expected_revisions':{v['id']:v['revision'] for v in conflicts if 'revision' in v}}


def make_bundle(documents, regions=(), metadata=None):
    kinds={v['kind'] for v in documents}
    typ='season' if 'season' in kinds else 'event' if 'event' in kinds else 'research'
    return canonical_bundle(dict(format=FORMAT,schema_version=1,bundle_type=typ,
      metadata=metadata or {'name':documents[0]['name']},content=deepcopy(documents),regions=deepcopy(list(regions))))


def export(store, ids=None):
    rows={v['id']:v['document'] for v in store.list()}; selected=set(ids or rows);roots=set(selected)
    if not selected:raise ValueError('Choose content to export')
    def add(key):
        if key not in rows:raise ValueError('Export dependency missing: '+key)
        d=rows[key];refs=[]
        if d['kind']=='season' and key in roots: refs.extend(k for k,v in rows.items() if v.get('season_id')==key)
        for field in ('season_id','prerequisite'):
            if d.get(field):refs.append(d[field])
        for r in [d.get('rules',{}),*[w['rules'] for w in d.get('weeks',[])],*[w['rules'] for w in d.get('rotations',[])]]:refs.extend(r.get('research',[]))
        for ref in refs:
            if ref not in selected:selected.add(ref);add(ref)
    for key in list(selected):add(key)
    regions={d.get('scope',{}).get('id') for k,d in rows.items() if k in selected and d.get('scope',{}).get('type')=='region'}
    return make_bundle([rows[k] for k in sorted(selected)],[r for r in store.regions() if r['id'] in regions])


def from_generated(generated):
    """Serialize existing templates/generator without writing or a parallel engine."""
    docs=[]
    def flatten(bundle,parent=None,prefix='root'):
        d=deepcopy(bundle['document']);d.setdefault('id','bundle-'+digest({'path':prefix,'document':d})[:24])
        if parent:d['season_id']=parent
        refs=[]
        for i,r in enumerate(bundle.get('research',[])):
            r=deepcopy(r);r.setdefault('id','bundle-'+digest({'path':prefix+':research:'+str(i),'document':r})[:24]);refs.append(r['id']);docs.append(r)
        if refs:
            d.setdefault('rules',{})['research']=refs
            for week in d.get('weeks',[]):week.setdefault('rules',{})['research']=refs
        docs.append(d)
        for i,b in enumerate(bundle.get('events',[])):flatten(b,d['id'],prefix+':event:'+str(i))
    flatten(generated)
    return make_bundle(docs,metadata={'name':generated['document']['name'],'notes':'Original DittoBuffer content. Custom configurable balance, not official event data.'})
