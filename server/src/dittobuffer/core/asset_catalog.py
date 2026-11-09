"""Offline Unity compact-catalog decoding and verified local bundle serving.

The imported catalog remains authoritative for keys, dependencies, resource
types and Unity cache hashes. SHA256 verifies copied bytes independently; a
catalog hash is a version identifier, not a claim about the file's SHA256.
"""
import base64
import hashlib
import json
import logging
import re
import struct
from pathlib import Path


class Catalog:
    def __init__(self, document):
        self.document = document
        raw = {name: base64.b64decode(document['m_' + name + 'DataString'], validate=True)
               for name in ('Key', 'Bucket', 'Entry', 'Extra')}
        self.extra = raw['Extra']
        buckets, keys = raw['Bucket'], raw['Key']
        count = self.integer(buckets, 0)
        if not 0 <= count <= 500000:
            raise ValueError('Invalid catalog bucket count')
        self.keys, self.buckets = [], {}
        at = 4
        for _ in range(count):
            offset, number = self.integers(buckets, at, 2)
            at += 8
            if not 0 <= number <= 500000:
                raise ValueError('Invalid catalog bucket length')
            indices = self.integers(buckets, at, number)
            at += 4 * number
            key, _ = self.string(keys, offset)
            self.keys.append(key)
            self.buckets[key] = indices
        if at != len(buckets):
            raise ValueError('Trailing catalog bucket data')
        entries = raw['Entry']
        count = self.integer(entries, 0)
        if not 0 <= count <= 500000 or len(entries) != 4 + 28 * count:
            raise ValueError('Invalid catalog entry data')
        self.entries = [self.integers(entries, 4 + 28 * i, 7) for i in range(count)]
        for indices in self.buckets.values():
            if any(i < 0 or i >= count for i in indices):
                raise ValueError('Invalid catalog bucket entry')
        for internal, provider, dep, _, extra, primary, resource_type in self.entries:
            if not (0 <= internal < len(document['m_InternalIds']) and
                    0 <= provider < len(document['m_ProviderIds']) and
                    -1 <= dep < len(self.keys) and -1 <= extra < len(self.extra) and
                    0 <= primary < len(self.keys) and
                    0 <= resource_type < len(document['m_resourceTypes'])):
                raise ValueError('Invalid catalog entry indices')

    @staticmethod
    def integers(data, offset, count):
        if offset < 0 or count < 0 or offset + 4 * count > len(data):
            raise ValueError('Truncated catalog data')
        return struct.unpack_from('<' + 'i' * count, data, offset)

    @classmethod
    def integer(cls, data, offset):
        return cls.integers(data, offset, 1)[0]

    @classmethod
    def string(cls, data, offset):
        if offset < 0 or offset + 5 > len(data) or data[offset] not in (0, 1):
            raise ValueError('Unsupported catalog key encoding')
        size = cls.integer(data, offset + 1)
        end = offset + 5 + size
        if size < 0 or end > len(data):
            raise ValueError('Truncated catalog string')
        return data[offset + 5:end].decode('ascii' if data[offset] == 0 else 'utf-16-le'), end

    def options(self, index):
        offset = self.entries[index][4]
        if offset < 0:
            return None
        data = self.extra
        if data[offset] != 7:
            raise ValueError('Unsupported catalog extra-data type')
        at = offset + 1
        names = []
        for _ in range(2):
            size = data[at]
            names.append(data[at + 1:at + 1 + size].decode('ascii'))
            at += 1 + size
        size = self.integer(data, at)
        at += 4
        if size < 0 or at + size > len(data):
            raise ValueError('Truncated catalog options')
        return {'assembly': names[0], 'class': names[1],
                'value': json.loads(data[at:at + size].decode('utf-16-le'))}

    def location(self, index):
        internal, provider, dep, _, _, primary, resource_type = self.entries[index]
        return {'index': index, 'key': self.keys[primary],
                'internal_id': self.document['m_InternalIds'][internal],
                'provider': self.document['m_ProviderIds'][provider],
                'type': self.document['m_resourceTypes'][resource_type]['m_ClassName'],
                'dependencies': list(self.buckets[self.keys[dep]]) if dep >= 0 else []}

    def rewrite_bundle_options(self, replacements):
        """Rebuild option offsets without touching any keys/dependency indexes."""
        document=dict(self.document);entries=[list(e) for e in self.entries];extra=bytearray()
        offsets=sorted({e[4] for e in self.entries if e[4]>=0})
        ends={offset:(offsets[i+1] if i+1<len(offsets) else len(self.extra)) for i,offset in enumerate(offsets)}
        preserved={}
        for index,entry in enumerate(entries):
            if index in replacements:
                options=replacements[index];names=[options['assembly'].encode('ascii'),options['class'].encode('ascii')]
                if any(len(n)>255 for n in names):raise ValueError('Catalog option type name too long')
                raw=json.dumps(options['value'],separators=(',',':')).encode('utf-16-le')
                entry[4]=len(extra)
                extra+=b'\7'+bytes([len(names[0])])+names[0]+bytes([len(names[1])])+names[1]+struct.pack('<i',len(raw))+raw
            elif entry[4]>=0:
                old=entry[4]
                if old not in preserved:
                    preserved[old]=len(extra);extra+=self.extra[old:ends[old]]
                entry[4]=preserved[old]
        document['m_ExtraDataString']=base64.b64encode(extra).decode()
        document['m_EntryDataString']=base64.b64encode(struct.pack('<i',len(entries))+b''.join(struct.pack('<7i',*e) for e in entries)).decode()
        return Catalog(document)

    def closure(self, key):
        visited, active = set(), set()
        def walk(index):
            if index in active:
                raise ValueError('Cyclic catalog dependencies')
            if index in visited:
                return
            active.add(index)
            for child in self.location(index)['dependencies']:
                walk(child)
            active.remove(index)
            visited.add(index)
        for index in self.buckets.get(key, ()):
            walk(index)
        return sorted(visited)

    @classmethod
    def combine(cls, catalogs, available):
        """Prefer the first complete source per key, retaining exact dependencies.

        A synthetic dependency key isolates each original location's graph.
        Matching address names never permit mixing different bundle versions.
        `available(source_index, entry_index)` tests each required bundle.
        """
        document=dict(catalogs[0].document)
        if any(c.document.get('m_InternalIdPrefixes') for c in catalogs):
            raise ValueError('Prefixed internal IDs require explicit expansion before combining')
        providers=[];types=[];internal_ids=[];entries=[];extra=b'';offsets=[]
        keys={};chosen={};total=0
        for catalog in catalogs:
            offsets.append(total);total+=len(catalog.entries)
        # Original public keys, then private dependency keys, share one table.
        for source,catalog in enumerate(catalogs):
            for key in catalog.keys:
                if key in chosen:continue
                choices=[i for i,c in enumerate(catalogs) if key in c.buckets]
                complete=[i for i in choices if all(available(i,j) for j in catalogs[i].closure(key))]
                selected=complete[0] if complete else choices[0]
                chosen[key]=selected
                keys[key]=[offsets[selected]+j for j in catalogs[selected].buckets[key]]
        for source,catalog in enumerate(catalogs):
            for index in range(len(catalog.entries)):
                children=catalog.location(index)['dependencies']
                if children:keys[f'dittobuffer-dependencies:{source}:{index}']=[offsets[source]+j for j in children]
        key_list=list(keys);key_indices={k:i for i,k in enumerate(key_list)}
        for source,catalog in enumerate(catalogs):
            internal_offset=len(internal_ids);internal_ids.extend(catalog.document['m_InternalIds'])
            provider_map=[];type_map=[]
            for provider in catalog.document['m_ProviderIds']:
                if provider not in providers:providers.append(provider)
                provider_map.append(providers.index(provider))
            for resource_type in catalog.document['m_resourceTypes']:
                if resource_type not in types:types.append(resource_type)
                type_map.append(types.index(resource_type))
            for index,(internal,provider,dep,dep_hash,options,primary,resource_type) in enumerate(catalog.entries):
                children=catalog.location(index)['dependencies']
                entries.append((internal_offset+internal,provider_map[provider],
                    key_indices[f'dittobuffer-dependencies:{source}:{index}'] if children else -1,
                    dep_hash,options+len(extra) if options>=0 else -1,
                    key_indices[catalog.keys[primary]],type_map[resource_type]))
            extra+=catalog.extra
        key_data=bytearray(struct.pack('<i',len(key_list)));bucket_data=bytearray(struct.pack('<i',len(key_list)))
        for key in key_list:
            raw=key.encode('ascii');indices=keys[key]
            bucket_data+=struct.pack('<ii',len(key_data),len(indices))+struct.pack('<'+'i'*len(indices),*indices)
            key_data+=b'\0'+struct.pack('<i',len(raw))+raw
        entry_data=struct.pack('<i',len(entries))+b''.join(struct.pack('<7i',*entry) for entry in entries)
        document.update(m_LocatorId='LibrePokemonLocalCatalog',m_InternalIds=internal_ids,
                        m_ProviderIds=providers,m_resourceTypes=types)
        for name,data in [('Key',key_data),('Bucket',bucket_data),('Entry',entry_data),('Extra',extra)]:
            document['m_'+name+'DataString']=base64.b64encode(data).decode()
        # Decode our output too, so malformed references cannot be published.
        return cls(document),chosen,offsets


def manifest_identity_sha256(document):
    """Pin all manifest evidence except paths replaced by a portable overlay.

    File sizes, byte hashes, catalogs' hashes, forms, costumes and dependency
    graphs remain part of the identity. Local relocation is not new evidence.
    """
    if not isinstance(document, dict) or not isinstance(document.get('content'), dict):
        raise ValueError('Invalid imported manifest identity')
    stable = dict(document)
    stable.pop('local_catalog', None)
    stable['content'] = {}
    for sha, row in document['content'].items():
        if not isinstance(row, dict):
            raise ValueError('Invalid imported content identity')
        stable['content'][sha] = {key: value for key, value in row.items() if key != 'file'}
    raw = json.dumps(stable, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf8')
    return hashlib.sha256(raw).hexdigest()


def load_manifest(root, relative):
    """Resolve a hash-pinned path overlay without changing historical evidence.

    A portable overlay may relocate all payload files and the catalog; it cannot
    change content identities, dependency graphs, costume mappings or metadata.
    The original manifest stays intact and remains the evidence authority.
    """
    root = Path(root).resolve()

    def confined(value):
        if not isinstance(value, str) or not value or Path(value).is_absolute():
            raise ValueError('Project-relative asset metadata path required')
        path = (root / value).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Asset metadata path leaves project')
        return path

    document = json.loads(confined(relative).read_text(encoding='utf8'))
    if document.get('format') != 1:
        raise ValueError('Unsupported asset registry format')
    overlay = document.get('path_overlay')
    if overlay is None:
        return document
    expected = {'format', 'base_manifest', 'base_manifest_sha256', 'local_catalog', 'content'}
    if (not isinstance(overlay, dict) or set(overlay) not in
            (expected, expected | {'base_manifest_identity_sha256'}) or overlay.get('format') != 1):
        raise ValueError('Unsupported portable asset path overlay')
    identity = overlay.get('base_manifest_identity_sha256')
    if identity is not None and (not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{64}', identity)):
        raise ValueError('Invalid portable manifest identity pin')
    raw = confined(overlay['base_manifest']).read_bytes()
    baseline = json.loads(raw)
    if hashlib.sha256(raw).hexdigest() != overlay['base_manifest_sha256']:
        if identity is None or manifest_identity_sha256(baseline) != identity:
            raise ValueError('Portable base manifest integrity failure')
    if baseline.get('format') != 1 or 'path_overlay' in baseline:
        raise ValueError('Portable asset overlay requires an original version 1 manifest')
    if not isinstance(overlay['content'], dict) or set(overlay['content']) != set(baseline['content']):
        raise ValueError('Portable asset overlay must preserve every content identity')
    if document.get('costumes', {}) != baseline.get('costumes', {}):
        raise ValueError('Portable asset overlay changed costume mappings')

    def payload(value):
        if not isinstance(value, str) or not value.startswith('assets/pokemon/local/'):
            raise ValueError('Portable payloads must stay in the ignored local resource directory')
        path = confined(value)
        if not path.is_relative_to((root / 'assets/pokemon/local').resolve()):
            raise ValueError('Portable payload leaves the local resource directory')
        return value

    baseline['local_catalog'] = payload(overlay['local_catalog'])
    for sha, row in baseline['content'].items():
        row['file'] = payload(overlay['content'][sha])
    baseline['portable_restoration'] = document.get('portable_restoration', {})
    return baseline


class AssetStore:
    """Explicit project manifest allowlist; never turns URLs into file paths."""
    def __init__(self, root, config):
        self.root, self.manifest = Path(root).resolve(), None
        if config.get('enabled'):
            self.manifest = load_manifest(self.root, config['manifest'])
            if self.manifest.get('format') != 1:
                raise ValueError('Unsupported asset registry format')
            self.route = self.manifest.get('route', '/assets/pokemon')
            if self.route not in ('/assets/pokemon', '/assets/avatar'):
                raise ValueError('Unsupported local asset route')
            if 'bundles' in self.manifest:
                raw = self.project_path(self.manifest['local_catalog']).read_bytes()
                if hashlib.sha256(raw).hexdigest() != self.manifest['local_catalog_sha256']:
                    raise ValueError('Imported catalog integrity failure')
                validate_manifest(self.manifest, Catalog(json.loads(raw)))
                missing = 0
                for sha, entry in self.manifest['content'].items():
                    target = self.project_path(entry['file'])
                    if target.is_file() and target.stat().st_size == entry['bytes']:
                        continue
                    # Portable copies and recovered bundles are interchangeable
                    # with the original Unity cache layout. Use them when the
                    # historical cache path is unavailable on a fresh clone.
                    alternatives = [f'assets/pokemon/bundles/{sha}.bundle',
                                    f'assets/pokemon/bundles/{sha}',
                                    f'builds/m113-recovered-content/{sha}.bundle',
                                    f'builds/m113-recovered-content/{sha}']
                    if self.route == '/assets/avatar':
                        cached = entry['file'].replace('\\', '/')
                        prefix = 'builds/m9-device-UnityCache/'
                        if cached.startswith(prefix):
                            alternatives.insert(0, 'assets/avatar/bundles/' + cached[len(prefix):])
                    for candidate in alternatives:
                        alternative = self.project_path(candidate)
                        if alternative.is_file() and alternative.stat().st_size == entry['bytes']:
                            entry['file'] = candidate
                            break
                    else:
                        missing += 1
                if missing:
                    logging.getLogger(__name__).warning(
                        '%s asset bundle(s) are unavailable; the rest of the catalog is still served.',
                        missing,
                    )

    def project_path(self, path):
        value = (self.root / path).resolve()
        if not value.is_relative_to(self.root):
            raise ValueError('Asset path leaves project')
        return value

    def read(self, path):
        if not self.manifest:
            return None
        if self.route=='/assets/avatar' and path in ('/assets/avatar/catalog_avatars_88.json','/assets/avatar/catalog_avatars_88.hash'):
            path=self.route+'/catalog'+('.hash' if path.endswith('.hash') else '.json')
        if path in (self.route+'/catalog.json', self.route+'/catalog.hash'):
            data = self.project_path(self.manifest['local_catalog']).read_bytes()
            if hashlib.sha256(data).hexdigest() != self.manifest['local_catalog_sha256']:
                raise ValueError('Imported catalog integrity failure')
            if path.endswith('.hash'):
                return hashlib.md5(data).hexdigest().encode(), 'text/plain'
            return data, 'application/json'
        match = re.fullmatch(re.escape(self.route)+r'/bundles/([0-9a-f]{64})\.bundle', path)
        if not match:
            return None
        sha = match[1]
        entry = self.manifest['content'].get(sha)
        if entry is None:
            return None
        data = self.project_path(entry['file']).read_bytes()
        # Verify each read, including files modified since server startup.
        if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != sha:
            raise ValueError('Imported bundle integrity failure')
        return data, 'application/octet-stream'


def validate_manifest(manifest, catalog):
    """Reject incompatible catalog/cache identities before serving any prefab.

    Incomplete addresses remain explicit local failures. A complete address may
    only reference located bundles from its exact catalog dependency graph.
    Byte hashes are still verified on every HTTP read; this checks the graph
    and version metadata, including cross-import Unity CAB identity conflicts.
    """
    content = manifest['content']
    route = manifest.get('route', '/assets/pokemon')
    if route not in ('/assets/pokemon', '/assets/avatar'):
        raise ValueError('Unsupported local asset route')
    for sha, entry in content.items():
        if not re.fullmatch('[0-9a-f]{64}', sha) or type(entry['bytes']) is not int or entry['bytes'] <= 0:
            raise ValueError('Invalid imported content identity')
    bundles = {}; cabinets = {}; cache = {}
    for row in manifest['bundles']:
        index = row['index']
        if type(index) is not int or index in bundles or not 0 <= index < len(catalog.entries):
            raise ValueError('Invalid or duplicate imported bundle index')
        bundles[index] = row
        location = catalog.location(index)
        if not location['type'].endswith('.IAssetBundleResource'):
            raise ValueError('Bundle index refers to a prefab')
        if not location['internal_id'].startswith('http://127.0.0.1:8080'+route+'/'):
            raise ValueError('Nonlocal imported bundle URL')
        if row['status'] != 'LOCATED':
            continue
        if len(manifest.get('source_imports', [])) > 1 and not row.get('serialized_files'):
            raise ValueError('Merged imports require verified Unity file identities')
        sha = row['sha256']
        if sha not in content or location['internal_id'] != 'http://127.0.0.1:8080'+route+'/bundles/' + sha + '.bundle':
            raise ValueError('Imported bundle URL/content mismatch')
        options = catalog.options(index)['value']
        if options.get('m_Hash') != row['version'] or options.get('m_BundleName') != row['bundle_name'] or options.get('m_BundleSize') != content[sha]['bytes']:
            raise ValueError('Imported bundle cache version mismatch')
        identity = (row['bundle_name'], row['version'])
        if cache.setdefault(identity, sha) != sha:
            raise ValueError('Imported cache identity collision')
        for cabinet in row.get('serialized_files', []):
            if cabinets.setdefault(cabinet.lower(), sha) != sha:
                raise ValueError('Imported Unity file identity collision')
    actual = {i for i in range(len(catalog.entries)) if catalog.location(i)['type'].endswith('.IAssetBundleResource')}
    if set(bundles) != actual:
        raise ValueError('Imported bundle registry does not cover catalog')
    addresses = set()
    for variant in manifest['variants']:
        address = variant['address']
        if address in addresses or address not in catalog.buckets:
            raise ValueError('Invalid or duplicate imported prefab address')
        addresses.add(address)
        closure = {i for i in catalog.closure(address) if i in bundles}
        if closure != set(variant['bundle_indices']):
            raise ValueError('Imported prefab dependency graph mismatch')
        if not variant['missing_dependencies'] and any(bundles[i]['status'] != 'LOCATED' for i in closure):
            raise ValueError('Complete prefab references an unavailable bundle')
    return {'bundles': len(bundles), 'content': len(content), 'prefabs': len(addresses), 'unity_files': len(cabinets)}
