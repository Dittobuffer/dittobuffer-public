"""Interchange validation, transactions, authorization and meaningful round trips."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from dittobuffer.paths import ROOT
from dittobuffer.gameplay.liveops.bundle import inspect,loads,export,from_generated,make_bundle,SCHEMA
from dittobuffer.gameplay.liveops.catalog import Catalog
from dittobuffer.gameplay.liveops.generator import generate,base,research
from dittobuffer.gameplay.liveops.store import Store,Conflict,digest
from dittobuffer.gameplay.liveops.rules import resolve,instant

@pytest.fixture(scope='module')
def catalog():return Catalog(json.loads((ROOT/'config/family.json').read_text(encoding='utf8')))
@pytest.fixture
def bundle(catalog):return from_generated(generate(catalog,{'start':'2026-11-01T10:00:00Z','seed':'json-roundtrip','weeks':4}))

def test_schema_is_valid_and_generated_season_is_compatible(catalog,bundle):
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(SCHEMA)
    review=inspect(bundle,catalog)
    assert review['valid'],review['errors']
    assert len(review['summary'])>10
    assert review['counts']['season']==1 and review['counts']['research']>1

def test_read_only_review_atomic_import_and_durable_retry(catalog,bundle,tmp_path):
    store=Store(tmp_path/'content.sqlite3');before=store.history()
    r=inspect(bundle,catalog,store);assert store.history()==before and not store.list()
    a=store.import_bundle(r['bundle'],'test');assert not store.published()
    assert all(v['status']=='draft' for v in store.list())
    b=Store(store.path).import_bundle(r['bundle'],'retry')
    assert b['replayed'] and a['receipt_id']==b['receipt_id']
    assert len(store.list())==len(bundle['content'])
    assert store.imported_ids(a['receipt_id'])==a['ids']

def test_failure_rolls_back_regions_and_earlier_documents(catalog,bundle,tmp_path):
    store=Store(tmp_path/'content.sqlite3');doc=bundle['content'][-1];store.save(doc,'test')
    bundle['regions']=[{'id':'city','type':'polygon','points':[[4,52],[5,52],[5,53]]}]
    with pytest.raises(Conflict):store.import_bundle(bundle,'test')
    assert len(store.list())==1 and not store.regions()
    with store.connect() as db:assert db.execute('SELECT COUNT(*) FROM import_receipts').fetchone()[0]==0

def test_compare_update_copy_published_protection_and_stale_review(catalog,bundle,tmp_path):
    store=Store(tmp_path/'content.sqlite3');r=inspect(bundle,catalog,store);store.import_bundle(r['bundle'],'a')
    changed=deepcopy(bundle);changed['content'][0]['name']+=' edited';r=inspect(changed,catalog,store)
    versions=r['expected_revisions'];key=changed['content'][0]['id']
    current=store.get(key);store.save(current['document']|{'description':'racing editor'},'other',current['revision'])
    with pytest.raises(Conflict):store.import_bundle(r['bundle'],'a','update',versions)
    r=inspect(changed,catalog,store);store.import_bundle(r['bundle'],'a','update',r['expected_revisions'])
    before=store.get(key);store.action(key,'publish','a',before['digest'])
    changed['metadata']['name']+=' v2'
    with pytest.raises(Conflict):store.import_bundle(changed,'a','update',inspect(changed,catalog,store)['expected_revisions'])
    copied=store.import_bundle(changed,'a','copy');assert key not in copied['ids']
    assert store.import_bundle(changed,'a','copy')['replayed']
    copied_docs=[store.get(k)['document'] for k in copied['ids']]
    assert inspect(make_bundle(copied_docs),catalog,store)['valid']
    assert store.get(key)['published_revision']==before['revision']

def test_export_import_preserves_effective_gameplay_all_four_weeks(catalog,bundle,tmp_path):
    a=Store(tmp_path/'a.sqlite3');b=Store(tmp_path/'b.sqlite3');a.import_bundle(bundle,'test')
    season=next(d['id'] for d in bundle['content'] if d['kind']=='season')
    exported=export(a,[season]);review=inspect(exported,catalog);assert review['valid'],review['errors']
    b.import_bundle(exported,'test');assert export(b,[season])==exported
    for store in (a,b):
        for row in store.list():store.action(row['id'],'publish','test',row['digest'])
    for week in range(4):
        at=instant('2026-11-01T10:00:00Z')+week*7*86400000+60000
        def gameplay(store):
            r=resolve(store.published(),at,52.08,5.12,'forest');return {k:v for k,v in r.items() if k not in ('generation','generations','sources')}
        assert gameplay(a)==gameplay(b)

def test_schema_rejects_all_unknown_fields_and_reports_independent_errors(catalog,bundle):
    d=next(v for v in bundle['content'] if v['kind']=='season');d['execute_python']='print(1)';d['weeks'][0]['rules']['spawns'][0]['weight']=-1
    r=inspect(bundle,catalog);assert not r['valid'];assert len(r['errors'])>=2
    assert any('weeks[0].rules.spawns[0].weight' in e['path'] for e in r['errors'])
    assert not inspect(bundle|{'schema_version':999},catalog)['valid']

def test_semantic_errors_are_path_specific_and_aggregate(catalog,bundle):
    d=next(v for v in bundle['content'] if v['kind']=='season');d['timezone']='Not/AZone';d['weeks'][0]['rules']['spawns'][0]['species']=1025;d['weeks'][1]['rules']['spawns'][0]['form']=9999
    r=inspect(bundle,catalog);assert not r['valid'];assert any('weeks[0]' in e['path'] for e in r['errors']);assert any('weeks[1]' in e['path'] for e in r['errors']);assert any('schedule' in e['path'] for e in r['errors'])

def test_names_resolve_only_against_real_registry(catalog):
    d=base('event','Named event','2026-11-01T10:00Z');d['id']='names';d['rules']={'spawns':[{'species':'BULBASAUR','weight':1}]}
    r=inspect(make_bundle([d]),catalog);assert r['valid'],r['errors'];assert r['bundle']['content'][0]['rules']['spawns'][0]['species']==1
    d['rules']['spawns'][0]['species']='invented monster';assert not inspect(make_bundle([d]),catalog)['valid']

def test_research_only_missing_dependencies_and_unavailable_features(catalog):
    d=research('Original steps','2026-11-01T10:00Z');d['id']='steps';v=make_bundle([d]);assert inspect(v,catalog)['valid']
    d['research_type']='timed';assert inspect(make_bundle([d]),catalog)['valid']
    d['research_type']='level_up';assert not inspect(make_bundle([d]),catalog)['valid']
    d['research_type']='special';d['prerequisite']='missing';assert not inspect(make_bundle([d]),catalog)['valid']
    d['prerequisite']=d['id'];assert not inspect(make_bundle([d]),catalog)['valid']
    d.pop('prerequisite');d['stages'][0]['rewards']=[{'type':'item','item':4,'amount':1}]
    assert not inspect(make_bundle([d]),catalog)['valid'] # Master Ball stays excluded.

@pytest.mark.parametrize('raw',['{"a":1,"a":2}','{"a":NaN}','['*1100+'0'+']'*1100,'{"a":'+('"x"'*1000000)+'}'],ids=['duplicate-keys','nonfinite','deep','oversized'])
def test_parser_limits_duplicate_keys_nonfinite_and_size(raw):
    with pytest.raises(ValueError):loads(raw)

def test_all_source_examples_pass_real_importer(catalog):
    files=list((ROOT/'config/liveops-examples').glob('*.json'));assert len(files)==7
    for file in files:
        review=inspect(loads(file.read_bytes()),catalog);assert review['valid'],(file.name,review['errors'])


def test_mega_import_matches_engine_default_and_explicit_disable(catalog):
    from copy import copy
    from dittobuffer.gameplay.liveops.validation import rules
    cat=copy(catalog);cat.config=deepcopy(catalog.config);cat.config.pop('mega',None)
    cat.config['raids']={'enabled':True}
    value={'raids':[{'species':3,'tier':6,'mega_evolution':1,'hp':180,'cp':30000,
                    'reward_policy':'Explicit isolated test tuning',
                    'mega_energy_rewards':[{'max_seconds':3600,'amount':100}]}]}
    rules(value,cat)  # Existing Mega engine defaults to enabled when omitted.
    cat.config['mega']={'enabled':False}
    with pytest.raises(ValueError,match='Unsupported Mega'):rules(value,cat)
    cat.config['mega']={'enabled':True};value['raids'][0]['mega_evolution']=99
    with pytest.raises(ValueError,match='Unsupported Mega'):rules(value,cat)
