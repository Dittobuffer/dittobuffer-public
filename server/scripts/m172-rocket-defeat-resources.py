"""Reproduce a narrow defeat-coach overlay from authorized LOCAL inputs only.

No acquisition, source bundle modification or APK installation is performed.
"""
import argparse,hashlib,importlib.util,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from dittobuffer.core.asset_catalog import Catalog
from dittobuffer.core.client_resources import ClientResources
from dittobuffer.gameplay.rocket_resources import COACH_KEYS,preservation,validate

def project(value,builds=False):
    path=(ROOT/value).resolve()
    if not (any(path.is_relative_to(ROOT/name) and path!=ROOT/name for name in ('.private','.staging')) if builds else path.is_relative_to(ROOT)):
        raise ValueError('Project-local input and fresh builds output required')
    return path

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--catalog',required=True);ap.add_argument('--base',required=True);ap.add_argument('--component',required=True);ap.add_argument('--output',required=True)
    args=ap.parse_args();out=project(args.output,True)
    if out.exists():raise ValueError('Refusing to overwrite an existing output')
    base=project(args.base);component=project(args.component);data=json.loads(component.read_text())
    if set(data.get('import_metadata',{}).get('selected',[]))!=set(COACH_KEYS):
        raise ValueError('Only the three reviewed native defeat coach prefabs are allowed')
    original=ClientResources(ROOT,dict(enabled=True,registry=args.base))
    before=Catalog(json.loads(original.read('catalog_926.json')[0]))
    # Preserve canonical shared-CAB/script/shader handling. Loading a second URL
    # for the same Unity CAB would introduce another native loading failure.
    spec=importlib.util.spec_from_file_location('rocket_runtime_merge',ROOT/'scripts/m17-runtime-catalog.py')
    merge=importlib.util.module_from_spec(spec);spec.loader.exec_module(merge);previous=sys.argv
    try:
        sys.argv=['m17-runtime-catalog.py','--base',args.base,'--component',args.component,'--output',args.output,'--catalog',args.catalog];merge.main()
    finally:sys.argv=previous
    registry=(out/'registry.json').relative_to(ROOT).as_posix()
    resources=ClientResources(ROOT,dict(enabled=True,registry=registry))
    after=Catalog(json.loads(resources.read('catalog_926.json')[0]));result=preservation(before,after)
    result.update(validate(ROOT,registry,resources))
    result.update(base_sha256=hashlib.sha256(base.read_bytes()).hexdigest(),component_sha256=hashlib.sha256(component.read_bytes()).hexdigest(),
                  source_bundles_modified=False,android='NOT_TESTED: structural checks only; native evidence is separate')
    (out/'defeat-resource-preservation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(registry=registry,valid=result['valid'],preserved=result['all_other_dependency_graphs_preserved'])))
if __name__=='__main__':main()
