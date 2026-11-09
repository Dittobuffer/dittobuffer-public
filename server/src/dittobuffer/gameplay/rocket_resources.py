"""Fail-closed checks for the native Rocket defeat coach's Addressables graph.

0.429.2 IncidentCombatResolveState loads combat_<team leader> on defeat,
before its result GUI is initialized. A key alone is insufficient: every
reachable bundle must be in the exact runtime registry embedded in the APK.
No combat state, client result or Unity asset is changed by these checks.
"""
import hashlib,json
from dittobuffer.core.asset_catalog import Catalog
from dittobuffer.core.client_resources import ClientResources

COACH_KEYS=('combat_blanche','combat_candela','combat_spark')
COACH_PREFABS={key:'Assets/AddressableAssets/NPC/AvatarNPC/'+name+'/'+key+'.prefab'
               for key,name in zip(COACH_KEYS,('Blanche','Candela','Spark'))}

def bundle_name(location):
    # Exported locators use both slash styles and a BundleAddress macro.
    return location['internal_id'].replace('\\','/').rsplit('/',1)[-1]

def inspect(catalog,resources):
    """Validate prefab identities/types and their complete locally served closure."""
    rows=[]
    for key in COACH_KEYS:
        indices=catalog.buckets.get(key,[])
        if len(indices)!=1:
            rows.append(dict(key=key,valid=False,error='Expected one native coach prefab',missing_bundles=[]))
            continue
        loc=catalog.location(indices[0])
        if loc['type']!='UnityEngine.GameObject':
            rows.append(dict(key=key,valid=False,error='Coach address must resolve to GameObject',missing_bundles=[]))
            continue
        if loc['internal_id'].replace('\\','/')!=COACH_PREFABS[key]:
            rows.append(dict(key=key,valid=False,error='Wrong native coach prefab identity',missing_bundles=[]))
            continue
        bundles=[];missing=[]
        for index in catalog.closure(key):
            value=catalog.location(index)
            if not value['type'].endswith('.IAssetBundleResource'):continue
            name=bundle_name(value);bundles.append(name)
            if resources.read(name) is None:missing.append(name)
        rows.append(dict(key=key,prefab=loc['internal_id'],valid=bool(bundles) and not missing,
                         bundles=sorted(set(bundles)),missing_bundles=sorted(set(missing))))
    return dict(valid=all(v['valid'] for v in rows),coaches=rows)

def validate(root,registry,resources=None):
    """Check selected registry, hashing files through the existing server API."""
    resources=resources or ClientResources(root,dict(enabled=True,registry=registry))
    document=resources.read('catalog_926.json')
    if document is None:raise ValueError('Rocket defeat requires the selected embedded runtime catalog')
    result=inspect(Catalog(json.loads(document[0])),resources)
    if not result['valid']:
        broken=', '.join(v['key'] for v in result['coaches'] if not v['valid'])
        raise ValueError('Incomplete native Rocket defeat resources: '+broken+
                         '. Build a reviewed overlay with scripts/m172-rocket-defeat-resources.py; '
                         'select its registry for BOTH server and standalone APK.')
    return result

def graph_signatures(catalog):
    """Index-independent semantic hashes including bundle options/dependencies."""
    memo={};active=set()
    def node(index):
        if index in active:raise ValueError('Cyclic runtime dependency graph')
        if index not in memo:
            active.add(index);value=catalog.location(index)
            dependencies=sorted(node(i) for i in value.pop('dependencies'))
            value.pop('index');value['dependencies']=dependencies
            if catalog.entries[index][4]>=0:value['options']=catalog.options(index)
            memo[index]=hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            active.remove(index)
        return memo[index]
    return {key:sorted(node(index) for index in indices) for key,indices in catalog.buckets.items()}

def preservation(before,after):
    """Only the three native coach addresses may change existing public graphs."""
    a,b=graph_signatures(before),graph_signatures(after)
    # Catalog.combine rebuilds these private dependency-bucket names from
    # source/index pairs. They are not resource addresses; every public root's
    # recursively hashed closure above still verifies all of their contents.
    public=[key for key in a if not key.startswith('libre-dependencies:')]
    changed=[key for key in public if key not in COACH_KEYS and a[key]!=b.get(key)]
    if changed:raise ValueError('Unrelated runtime dependency graph changed: '+', '.join(changed[:8]))
    return dict(all_other_dependency_graphs_preserved=len(public)-sum(k in public for k in COACH_KEYS),
                keys=list(COACH_KEYS),changed_other_keys=[])
