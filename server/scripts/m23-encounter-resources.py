"""Import only native research scene closures from operator-supplied resources.

No downloads. The optional rooted-device reader is read-only and requires an
explicit serial. Output is a fresh ignored private directory. Source cache/APKs,
accepted registries and model registrations are never changed. A generated
registry is experimental until native Android acceptance.
"""
import os, shutil
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import subprocess
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from dittobuffer.core.asset_catalog import Catalog
from dittobuffer.core.client_resources import ClientResources
from dittobuffer.gameplay.special_encounters import registry
from scripts.pokemon_asset_import import catalog_document


def semantic_keys(catalog):
    """Index-independent fingerprints, including ordered dependency graphs."""
    cache = {}
    def node(index):
        if index not in cache:
            location = catalog.location(index)
            value = {k: location[k] for k in ('key', 'internal_id', 'provider', 'type')}
            value.update(options=catalog.options(index), dependencies=[node(i) for i in location['dependencies']])
            cache[index] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return cache[index]
    return {key: [node(i) for i in indices] for key, indices in catalog.buckets.items()}


def namespace_shaders(raw, namespace):
    """Isolate shader identities without changing compiled programs or material links."""
    import UnityPy
    env = UnityPy.load(raw)
    before = {(o.assets_file.name, o.path_id): o.get_raw_data() for o in env.objects}
    changes = []
    for obj in env.objects:
        if obj.type.name != 'Shader':
            continue
        tree = obj.read_typetree()
        guid = tree.get('m_AssetGUID')
        if not isinstance(guid, dict) or set(guid) != {f'data[{i}]' for i in range(4)}:
            raise ValueError('Unsupported shader identity format')
        original = struct.pack('<4I', *(guid[f'data[{i}]'] for i in range(4)))
        derived = hashlib.sha256(b'libre-m23-shader:' + namespace.encode() + original).digest()[:16]
        payload = obj.get_raw_data()
        if payload.count(original) != 1:
            raise ValueError('Shader identity is not a unique fixed-size field')
        obj.set_raw_data(payload.replace(original, derived))
        changes.append(dict(object_id=obj.path_id, source_guid=original.hex(), derived_guid=derived.hex()))
    if not changes:
        return raw, changes
    bundle = next(iter(env.files.values()))
    result = bundle.save(packer='lz4')
    check = UnityPy.load(result)
    changed = {c['object_id']: c for c in changes}
    for obj in check.objects:
        source = before[obj.assets_file.name, obj.path_id]
        change = changed.get(obj.path_id) if obj.type.name == 'Shader' else None
        expected = source.replace(bytes.fromhex(change['source_guid']), bytes.fromhex(change['derived_guid'])) if change else source
        if obj.get_raw_data() != expected:
            raise ValueError('Shader isolation changed unrelated serialized data')
    return result, changes


def script_contract(dump, name, namespace, tree):
    """Include auto-property backing fields and serialized inherited fields."""
    cache = {}
    def declared(class_name, preferred_namespace, inherited=False):
        key = class_name, preferred_namespace, inherited
        if key in cache:
            return cache[key]
        matches = []
        for match in re.finditer(r'^public (?:(?:abstract|sealed) )?class ' + re.escape(class_name) + r'(?=[ <:])[^\n]*', dump, re.M):
            ns = dump[dump.rfind('// Namespace:', 0, match.start()):match.start()].splitlines()[0].removeprefix('// Namespace:').strip()
            end = dump.find('\n// Namespace:', match.end())
            matches.append((ns, match.group(), dump[match.start():end if end >= 0 else None]))
        matching = [v for v in matches if v[0] == preferred_namespace]
        if inherited and not matching and len(matches) == 1:
            matching = matches
        if len(matching) != 1:
            return set()
        ns, header, block = matching[0]
        fields = block.split('// Properties', 1)[0].split('// Methods', 1)[0]
        result = set(re.findall(r'([<>A-Za-z_][<>A-Za-z0-9_]*)\s*;\s*// 0x', fields))
        cache[key] = result
        header = header.split('//', 1)[0]
        if ':' in header:
            parent = header.split(':', 1)[1].strip().split(',')[0].split('<')[0].strip()
            result |= declared(parent, ns, inherited=True)
        return result
    fields = declared(name, namespace)
    if not fields:
        raise ValueError('Referenced script absent from client: ' + namespace + '.' + name)
    serialized = {key for key in tree if not key.startswith('m_')}
    missing = serialized - fields
    if missing:
        raise ValueError('Serialized fields absent from client script: ' + ','.join(sorted(missing)))
    return dict(class_name=name, namespace=namespace, serialized_fields=sorted(serialized),
                present_in_04292_dump=True, missing_client_fields=[], native_abi_compatibility='NOT_TESTED')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--bundle-dir', help='Operator directory with exact catalog bundle filenames')
    parser.add_argument('--serial', help='Explicit authorized rooted-device read-only cache import')
    parser.add_argument('--adb', default=os.environ.get('ADB') or shutil.which('adb') or 'adb')
    parser.add_argument('--dump', required=True, help='Independently supplied 0.429.2 IL2CPP metadata')
    parser.add_argument('--base', default='config/resources/client-runtime.json')
    parser.add_argument('--encounter', action='append', choices=list(registry()))
    args = parser.parse_args()
    if bool(args.bundle_dir) == bool(args.serial):
        raise ValueError('Choose exactly one operator input: bundle directory or device serial')
    output = (ROOT / args.output).resolve()
    if not any(output.is_relative_to(ROOT / path) for path in ('.private', 'assets/pokemon/local')) or output.exists():
        raise ValueError('Fresh ignored private resource output required; accepted resources are preserved')
    source = Path(args.catalog).resolve()
    catalog = Catalog(json.loads(source.read_bytes()))
    original = ClientResources(ROOT, dict(enabled=True, registry=args.base))
    baseline = Catalog(json.loads(original.read('catalog_926.json')[0]))
    base = json.loads((ROOT / args.base).read_text())
    spec = importlib.util.spec_from_file_location('m20_resources', ROOT / 'scripts/m20-compatible-resources.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    definitions = [v for k, v in registry().items() if not args.encounter or k in args.encounter]
    keys = {k for v in definitions for k in v['required_resources']}
    if any(k not in catalog.buckets for k in keys):
        raise ValueError('Source catalog lacks a required native prefab reference')
    selected = {i for k in keys for i in catalog.buckets[k]}
    # Preserve exact GUID and named aliases, never unrelated broad labels.
    aliases = {k: indices for k, indices in catalog.buckets.items() if set(indices) <= selected}
    # Mew/Celebi/normal Jirachi reuse the accepted generic park. Importing a
    # newer copy under its global GUID would also change ordinary encounters.
    # Retain the existing complete graph; the scene audit still inventories it.
    preserved = []
    if 'DefaultParkScene' in baseline.buckets:
        shared = set(catalog.buckets.get('DefaultParkScene', ()))
        preserved = [k for k, indices in aliases.items() if set(indices) == shared]
        aliases = {k: indices for k, indices in aliases.items() if k not in preserved}
    needed = sorted({i for k in keys for i in catalog.closure(k)
                     if catalog.location(i)['type'].endswith('.IAssetBundleResource')})
    output.mkdir(parents=True)
    import UnityPy
    env = UnityPy.Environment()
    namespace = hashlib.sha256(source.read_bytes() + b':m23-native-scenes-v1').hexdigest()
    nodes, remap, records = [], {}, []
    apk = None
    adb = [args.adb, '-s', args.serial] if args.serial else None
    for index in needed:
        loc = catalog.location(index)
        options = copy.deepcopy(catalog.options(index)['value'])
        name = loc['internal_id'].replace('\\', '/').rsplit('/', 1)[-1]
        if not re.fullmatch(r'[A-Za-z0-9_.-]+\.bundle', name):
            raise ValueError('Unsafe source bundle filename')
        if args.bundle_dir:
            raw = (Path(args.bundle_dir) / name).read_bytes()
            origin = name
        else:
            if loc['internal_id'].startswith('{UnityEngine.AddressableAssets.Addressables.RuntimePath}'):
                if apk is None:
                    paths = subprocess.check_output([*adb, 'shell', 'pm', 'path', 'com.nianticlabs.pokemongo']).decode().splitlines()
                    apk = next(v.removeprefix('package:').strip() for v in paths if v.endswith('/base.apk'))
                    if not apk.startswith('/data/app/'):
                        raise ValueError('Unexpected official APK location')
                origin = apk + '!assets/aa/Android/' + name
                command = 'unzip -p ' + shlex.quote(apk) + ' ' + shlex.quote('assets/aa/Android/' + name)
            else:
                if not re.fullmatch('[a-f0-9]{32}', options['m_Hash']) or not re.fullmatch('[A-Za-z0-9_]+', options['m_BundleName']):
                    raise ValueError('Invalid exact cache identity')
                origin = '/sdcard/Android/data/com.nianticlabs.pokemongo/files/UnityCache/Shared/' + options['m_BundleName'] + '/' + options['m_Hash'] + '/__data'
                command = 'cat ' + shlex.quote(origin)
            raw = subprocess.check_output([*adb, 'exec-out', 'su', '-c', command], timeout=60)
        if not raw.startswith(b'UnityFS\0'):
            raise ValueError('Exact operator resource unavailable: ' + name)
        source_sha = hashlib.sha256(raw).hexdigest()
        (output / (source_sha + '.source.bundle')).write_bytes(raw)
        raw, changes = helper.namespace_bundle(raw, namespace)
        raw, shader_changes = namespace_shaders(raw, namespace)
        digest = hashlib.sha256(raw).hexdigest()
        target = output / (digest + '.bundle')
        target.write_bytes(raw)
        local = UnityPy.load(raw)
        env.load_file(str(target))
        options.update(m_Hash=hashlib.md5(raw).hexdigest(), m_BundleSize=len(raw),
                       m_BundleName='libre-m23-' + digest[:32], m_ClearOtherCachedVersionsWhenLoaded=False)
        remap[index] = len(nodes)
        nodes.append(dict(sha256=digest, file=target.relative_to(ROOT).as_posix(),
                          url='http://127.0.0.1:8080/assets/client/' + digest + '.bundle', options=options))
        records.append(dict(source_catalog_index=index, name=name, source=origin,
                            source_sha256=source_sha, sha256=digest, bytes=len(raw),
                            serialized_files=sorted({o.assets_file.name for o in local.objects}),
                            unity_versions=sorted({o.assets_file.unity_version for o in local.objects}),
                            namespace_changes=changes, shader_identity_changes=shader_changes))
        print('Prepared', name, len(raw), flush=True)
    dump = Path(args.dump).read_text(encoding='utf8')
    contracts, errors = {}, []
    for obj in env.objects:
        if obj.type.name != 'MonoBehaviour':
            continue
        try:
            tree = obj.read_typetree()
            script = obj.read().m_Script.read()
            key = script.m_Namespace + '.' + script.m_ClassName
            signature = key + ':' + ','.join(sorted(tree))
            if signature not in contracts:
                contracts[signature] = script_contract(dump, script.m_ClassName, script.m_Namespace, tree)
        except Exception as error:
            errors.append(dict(object_id=obj.path_id, serialized_file=obj.assets_file.name, error=str(error)))
    references = helper.reference_audit(env)
    audit = dict(source_catalog_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                 definitions=[d['encounter_id'] for d in definitions], dependencies=records,
                 script_contracts=contracts, compatibility_errors=errors,
                 references=references, android='NOT_TESTED', redistribution='Proprietary payloads excluded from Git')
    (output / 'audit.json').write_text(json.dumps(audit, indent=2))
    if errors or references['missing']:
        raise ValueError('Resource compatibility gate failed; exact audit saved, no registry emitted')
    resources = []
    for key, indices in aliases.items():
        for i in indices:
            loc = catalog.location(i)
            resources.append(dict(address=key, path=loc['internal_id'], type=loc['type'],
                                  owner=remap[loc['dependencies'][0]],
                                  bundles=[remap[j] for j in catalog.closure(key) if j in remap]))
    component = Catalog(catalog_document(catalog.document, nodes, resources, catalog.options(needed[0])))
    # Unique dependency keys also preserve earlier Mateo/Max overlays.
    merged = helper.overlay(component, baseline, namespace='libre-m23-overlay-dependencies:',component_namespace='libre-m23-component:')
    before, after = semantic_keys(baseline), semantic_keys(merged)
    unrelated = [key for key in before if key not in aliases and before[key] != after.get(key)]
    if unrelated:
        raise ValueError('Unrelated accepted resource graphs changed: ' + ','.join(unrelated[:10]))
    audit['preservation'] = dict(accepted_keys=len(before), unrelated_changes=unrelated,
                                 preserved_default_park_aliases=preserved,
                                 selected_override_keys=sorted(aliases))
    (output / 'audit.json').write_text(json.dumps(audit, indent=2))
    encoded = json.dumps(merged.document, separators=(',', ':')).encode()
    for name, raw in [('catalog_926.json', encoded), ('catalog_926.hash', hashlib.md5(encoded).hexdigest().encode())]:
        path = output / name
        path.write_bytes(raw)
        base['files'][name] = dict(file=path.relative_to(ROOT).as_posix(), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    for node in nodes:
        base['files'][node['sha256'] + '.bundle'] = dict(file=node['file'], bytes=(ROOT / node['file']).stat().st_size, sha256=node['sha256'])
    (output / 'registry.json').write_text(json.dumps(base, indent=2))
    print('Experimental registry prepared; Android verification NOT_TESTED')


if __name__ == '__main__':
    main()
