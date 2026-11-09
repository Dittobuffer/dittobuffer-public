#!/usr/bin/env python3
"""Merge the verified missing-asset recovery into the local Pokémon registry.

These are private research inputs. This script only stages already-recovered,
byte-verified bundle files inside the project and rewrites the project's local
manifest/catalog. It performs no downloads, no database or gameplay writes, and
never modifies the recovered collection.

The active manifest keeps its exact dependency graph. For every missing bundle
that has a matching row in the recovery INDEX, the script:

* verifies the recovered bytes against the catalog size and INDEX SHA-256;
* extracts the Unity CAB identity names from the recovered bundle;
* copies the exact bytes to the project content directory;
* marks the bundle local and points its catalog entry at the local URL;
* recomputes variant dependency status and counts; and
* writes a new ``*.full.catalog.json`` plus the updated manifest atomically.

Validation is fail-closed: the manifest/catalog are only replaced after the
proposed dependency graph passes ``validate_manifest``. The recovery contains
different versions that reuse the same internal Unity CAB identity (the two
source catalogs); only the first compatible version per identity is imported
and the conflicting rows are reported and left MISSING. The curated 60-bundle
priority set is processed first.

Run with ``--dry-run`` first, then with ``--verify`` on the deployment copy::

    python scripts/apply-recovered-assets.py --dry-run --only priority
    python scripts/apply-recovered-assets.py --verify
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
from pathlib import Path

DEFAULT_ROUTE = '/assets/pokemon'
ROUTES = ('/assets/pokemon', '/assets/avatar')
SHA_RE = re.compile(r'[0-9a-f]{64}')
CSV_FIELDS = {'catalog', 'key', 'expected_bytes', 'version', 'bundle_name',
              'original_index', 'refs', 'sha256', 'file', 'source_kind'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def project_path(root, value):
    """Resolve a project path and refuse anything that escapes ``root``."""
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f'path leaves the project root: {value}')
    return path


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + '.tmp-')
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _read_c_string(data, offset):
    end = data.index(b'\0', offset)
    return data[offset:end].decode('utf8'), end + 1


def _lz4_block(data, size):
    """Decompress one UnityFS LZ4/LZ4HC block (not the LZ4 frame format)."""
    output = bytearray()
    at, end = 0, len(data)
    while at < end:
        token = data[at]
        at += 1
        literals = token >> 4
        if literals == 15:
            while True:
                value = data[at]
                at += 1
                literals += value
                if value != 255:
                    break
        output += data[at:at + literals]
        at += literals
        if at >= end:
            break
        offset = data[at] | (data[at + 1] << 8)
        at += 2
        length = (token & 15) + 4
        if (token & 15) == 15:
            while True:
                value = data[at]
                at += 1
                length += value
                if value != 255:
                    break
        start = len(output) - offset
        if offset <= 0 or start < 0:
            raise ValueError('invalid LZ4 match offset')
        for index in range(length):
            output.append(output[start + index])
    if len(output) != size:
        raise ValueError(f'LZ4 block size mismatch: {len(output)} != {size}')
    return bytes(output)


def _decompress_block(data, size, flags):
    compression = flags & 0x3f
    if compression == 0:
        return data
    if compression in (2, 3):
        return _lz4_block(data, size)
    if compression == 1:
        import lzma
        return lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(data[5:])
    raise ValueError(f'unsupported UnityFS compression type {compression}')


def cab_names(data):
    """Read the serialized-file names from a UnityFS bundle directory.

    UnityPy's ``assets_file.name`` is the directory entry name. Reading only the
    compressed blocks-info header is enough; no UnityPy dependency is needed.
    The ``.resS`` stream entry is not a serialized-file identity.
    """
    if not data.startswith(b'UnityFS\0'):
        raise ValueError('not a UnityFS bundle')
    offset = len(b'UnityFS\0')
    version = struct.unpack_from('>I', data, offset)[0]
    offset += 4
    _, offset = _read_c_string(data, offset)
    _, offset = _read_c_string(data, offset)
    _, compressed, uncompressed, flags = struct.unpack_from('>qIII', data, offset)
    offset += 20
    if version >= 7:
        offset = (offset + 15) & ~15
    blocks_info = _decompress_block(data[offset:offset + compressed], uncompressed, flags)
    at = 16
    block_count = struct.unpack_from('>i', blocks_info, at)[0]
    at += 4
    if not 0 <= block_count <= 100000:
        raise ValueError('invalid UnityFS block count')
    at += 10 * block_count
    directory_count = struct.unpack_from('>i', blocks_info, at)[0]
    at += 4
    if not 0 <= directory_count <= 100000:
        raise ValueError('invalid UnityFS directory count')
    names = []
    for _ in range(directory_count):
        _, _, _ = struct.unpack_from('>qqI', blocks_info, at)
        at += 20
        name, at = _read_c_string(blocks_info, at)
        if name.startswith('CAB-') and not name.endswith('.resS'):
            names.append(name)
    names = sorted(set(names))
    if not names:
        raise ValueError('UnityFS directory has no CAB identity')
    return names


def atomic_copy(source_bytes, destination, expected_sha):
    """Copy bytes once; an existing destination must already be identical."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    require(not destination.exists() or destination.is_file(),
            f'content destination is not a file: {destination}')
    if destination.is_file():
        require(destination.stat().st_size == len(source_bytes),
                f'existing content has a different size: {destination}')
        require(sha256_file(destination) == expected_sha,
                f'existing content has different bytes: {destination}')
        return False
    descriptor, temporary = tempfile.mkstemp(dir=str(destination.parent),
                                             prefix=destination.name + '.tmp-')
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(source_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return True


def load_index(path):
    require(path.is_file(), f'recovery index is missing: {path}')
    rows = []
    with path.open('r', encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        require(set(reader.fieldnames or ()) >= CSV_FIELDS,
                f'recovery index has an unexpected header: {path}')
        for line, raw in enumerate(reader, start=2):
            row = {name: (raw.get(name) or '').strip() for name in CSV_FIELDS}
            require(row['key'], f'{path}:{line}: empty key')
            require(SHA_RE.fullmatch(row['sha256']) is not None,
                    f'{path}:{line}: invalid SHA-256')
            try:
                row['expected_bytes'] = int(row['expected_bytes'])
            except ValueError as error:
                raise ValueError(f'{path}:{line}: invalid expected_bytes') from error
            require(row['expected_bytes'] > 0, f'{path}:{line}: non-positive size')
            require(row['file'], f'{path}:{line}: empty file path')
            rows.append(row)
    return rows


def index_lookup(rows):
    by_pair, by_key = {}, {}
    for row in rows:
        by_pair.setdefault((row['key'], row['version']), []).append(row)
        by_key.setdefault(row['key'], []).append(row)
    return by_pair, by_key


def resolve_index_row(bundle, by_pair, by_key):
    """Resolve one manifest bundle to exactly one recovered file.

    Prefer the exact (key, version) identity. A key-only fallback is allowed
    only when every candidate has the same bytes. This is what keeps the two
    source catalogs (catalog_pkmn_19 and catalog_pkmn_20) separate when they
    contain the same filename at different versions or sizes.
    """
    def exact(candidates):
        if len({row['sha256'] for row in candidates}) == 1:
            return candidates[0]
        # Same catalog version but different bytes across the two source
        # catalogs (42 known size groups). The manifest still records the
        # expected byte size, so it is a safe disambiguator when unique.
        same_size = [row for row in candidates
                     if row['expected_bytes'] == bundle.get('expected_bytes')]
        if len({row['sha256'] for row in same_size}) == 1:
            return same_size[0]
        return None

    candidates = by_pair.get((bundle['key'], bundle.get('version')))
    if candidates:
        chosen = exact(candidates)
        return (chosen, None) if chosen else (None, 'ambiguous key+version with differing contents')
    candidates = by_key.get(bundle['key'], [])
    if not candidates:
        return None, 'no recovered entry'
    if len(candidates) == 1:
        return candidates[0], None
    chosen = exact(candidates)
    return (chosen, None) if chosen else (None, 'ambiguous key with differing contents')


def load_priority(path):
    if not path.is_file():
        return None
    rows = load_index(path)
    return {(row['key'], row['version']) for row in rows}


def verify_manifest(root, manifest_path, added_shas, Catalog, validate_manifest):
    """Full structural + exact-byte verification of a written manifest."""
    manifest = json.loads(manifest_path.read_text(encoding='utf8'))
    require(manifest.get('format') == 1, 'written manifest has an unsupported format')
    catalog_path = project_path(root, manifest['local_catalog'])
    catalog_bytes = catalog_path.read_bytes()
    require(sha256_bytes(catalog_bytes) == manifest['local_catalog_sha256'],
            'written catalog hash does not match the manifest')
    catalog = Catalog(json.loads(catalog_bytes))
    validate_manifest(manifest, catalog)
    # Only this run's additions are proven here. The manifest may legitimately
    # reference older staged caches that are not part of this deployment.
    for sha in added_shas:
        entry = manifest['content'].get(sha)
        require(entry is not None, f'new content identity is missing: {sha}')
        path = project_path(root, entry['file'])
        require(sha256_file(path) == sha, f'new content file failed its SHA-256 check: {entry["file"]}')
    return manifest


def run(args):
    root = Path(args.root).resolve()
    require(root.is_dir(), f'project root is not a directory: {root}')

    manifest_path = project_path(root, args.manifest)
    recovered = project_path(root, args.recovered)
    content_dir = project_path(root, args.content_dir)
    require(manifest_path.is_file(), f'manifest is missing: {manifest_path}')
    require(recovered.is_dir(), f'recovery directory is missing: {recovered}')

    # Import the project's decoder only after --root is known, so a copy running
    # from a USB deployment uses that deployment's source tree.
    sys.path.insert(0, str(root / 'src'))
    from dittobuffer.core.asset_catalog import Catalog, validate_manifest

    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    require(manifest.get('format') == 1, 'unsupported asset registry format')
    route = args.route or manifest.get('route') or DEFAULT_ROUTE
    require(route in ROUTES, f'unsupported local asset route: {route}')
    require(isinstance(manifest.get('bundles'), list), 'manifest has no bundle list')
    require(isinstance(manifest.get('variants'), list), 'manifest has no variant list')
    require(isinstance(manifest.get('content'), dict), 'manifest has no content map')

    catalog_path = project_path(root, manifest['local_catalog'])
    catalog_bytes = catalog_path.read_bytes()
    require(sha256_bytes(catalog_bytes) == manifest['local_catalog_sha256'],
            'local catalog hash does not match the manifest')
    document = json.loads(catalog_bytes)
    catalog = Catalog(document)

    index_path = recovered / 'INDEX.csv'
    index_rows = load_index(index_path)
    by_pair, by_key = index_lookup(index_rows)

    priority_pairs = load_priority(recovered / 'PRIORITY.csv') or set()
    if args.only == 'priority' and not priority_pairs:
        print('note: PRIORITY.csv is absent; processing every recovered entry', file=sys.stderr)

    bundles = manifest['bundles']
    bundle_by_index = {}
    for row in bundles:
        index = row.get('index')
        require(type(index) is int, f'manifest bundle has an invalid index: {row.get("key")}')
        require(index not in bundle_by_index, f'duplicate manifest bundle index: {index}')
        require(0 <= index < len(catalog.entries),
                f'manifest bundle index is outside the catalog: {index}')
        bundle_by_index[index] = row

    missing_before = sum(row.get('status') != 'LOCATED' for row in bundles)
    complete_before = sum(not variant.get('missing_dependencies') for variant in manifest['variants'])

    stats = {'matched': 0, 'added': 0, 'aliased': 0, 'already': 0, 'not_priority': 0,
             'no_index': 0, 'ambiguous': 0, 'invalid': 0, 'cabinet_conflict': 0,
             'identity_conflict': 0}
    ambiguous = []
    conflicts = []
    verified_bytes = 0
    copied_bytes = 0
    added_shas = set()
    changed = False

    # ``validate_manifest`` enforces one SHA-256 per Unity CAB identity. The
    # recovery deliberately contains different versions of the same internal
    # CAB (the two source catalogs), so the compatible subset is selected by
    # importing one exact version per identity and reporting the rest.
    cabinet_owner = {}
    identity_owner = {}
    content_serialized = {}
    for existing in bundles:
        if existing.get('status') != 'LOCATED':
            continue
        sha = existing.get('sha256')
        serialized = tuple(sorted(str(c) for c in existing.get('serialized_files') or ()))
        previous = content_serialized.get(sha)
        if previous is not None and previous != serialized:
            content_serialized[sha] = None      # ambiguous: never alias onto it
        elif previous is None:
            content_serialized[sha] = serialized
        for cabinet in existing.get('serialized_files') or ():
            cabinet_owner[str(cabinet).lower()] = (sha, existing.get('key', ''), existing)
        identity_owner[(existing.get('bundle_name'), existing.get('version'))] = (
            sha, existing.get('key', ''))

    # Catalog options must be rewritten for aliases: the catalog entry must
    # describe the content that is actually served (same cache identity and
    # byte size as the existing bundle), or validate_manifest rejects it.
    pending_urls = {}
    option_replacements = {}

    pending = [row for row in bundles if row.get('status') != 'LOCATED']
    stats['already'] = len(bundles) - len(pending)
    priority_only = args.only == 'priority' and bool(priority_pairs)
    selected = [row for row in pending
                if not priority_only or (row['key'], row.get('version')) in priority_pairs]
    stats['not_priority'] = len(pending) - len(selected)
    # Prefer the curated dependency-complete set first, then the manifest order.
    selected.sort(key=lambda row: (0 if (row['key'], row.get('version')) in priority_pairs else 1,
                                   row['index']))

    for row in selected:
        index_row, reason = resolve_index_row(row, by_pair, by_key)
        if index_row is None:
            if reason == 'no recovered entry':
                stats['no_index'] += 1
            else:
                stats['ambiguous'] += 1
                ambiguous.append((row['key'], reason))
            continue
        if index_row['version'] != row.get('version'):
            # A key-only fallback must never substitute another catalog version.
            stats['ambiguous'] += 1
            ambiguous.append((row['key'], f'version {index_row["version"]} != {row.get("version")}'))
            continue
        stats['matched'] += 1

        if row.get('bundle_name'):
            require(index_row['bundle_name'] == row['bundle_name'],
                    f'{row["key"]}: recovered bundle_name does not match the manifest')
        require(index_row['expected_bytes'] == row.get('expected_bytes'),
                f'{row["key"]}: recovered size does not match the manifest')

        source = project_path(root, recovered / index_row['file'])
        require(source.is_relative_to(recovered),
                f'{row["key"]}: recovered path leaves the recovery directory')
        require(source.is_file(), f'{row["key"]}: recovered bundle is missing: {index_row["file"]}')
        data = source.read_bytes()
        require(len(data) == index_row['expected_bytes'],
                f'{row["key"]}: recovered bundle has the wrong size')
        sha = sha256_bytes(data)
        require(sha == index_row['sha256'],
                f'{row["key"]}: recovered bundle failed its SHA-256 check')
        try:
            cabs = cab_names(data)
        except ValueError as error:
            stats['invalid'] += 1
            print(f'skip (unreadable bundle): {row["key"]}: {error}', file=sys.stderr)
            continue

        identity = (row.get('bundle_name'), row.get('version'))
        identity_conflict = identity_owner.get(identity)

        # A recovered bundle whose every CAB identity is already present in a
        # local bundle is not imported again.  Unity resolves cross-bundle
        # references by CAB identity, so the local bundle (for example the
        # M113 patched shader bundle) is a drop-in replacement.  Point this
        # catalog entry at the existing content and align its cache identity
        # and size with it; this is what completes shared-dependency prefabs
        # without introducing a second copy of the same CAB.
        owners = [cabinet_owner.get(cabinet.lower()) for cabinet in cabs]
        owner_shas = {owner[0] for owner in owners if owner is not None}
        if all(owner is not None for owner in owners) and len(owner_shas) == 1:
            alias_sha = owner_shas.pop()
            alias_serialized = content_serialized.get(alias_sha)
            if alias_sha != sha and alias_serialized:
                owner = owners[0]
                owner_row = owner[2]
                alias_bytes = manifest['content'][alias_sha]['bytes']
                url = f'http://127.0.0.1:8080{route}/bundles/{alias_sha}.bundle'
                current = catalog.options(row['index'])
                require(current is not None and current['class'].endswith('AssetBundleRequestOptions'),
                        f'{row["key"]}: manifest index has no asset bundle options')
                value = dict(current['value'])
                value['m_Hash'] = owner_row['version']
                value['m_BundleName'] = owner_row['bundle_name']
                value['m_BundleSize'] = alias_bytes
                option_replacements[row['index']] = {
                    'assembly': current['assembly'], 'class': current['class'], 'value': value}
                pending_urls[row['index']] = url
                row['status'] = 'LOCATED'
                row['sha256'] = alias_sha
                row['serialized_files'] = list(alias_serialized)
                row['expected_bytes'] = alias_bytes
                row['internal_id'] = url
                row['version'] = owner_row['version']
                row['bundle_name'] = owner_row['bundle_name']
                row.pop('error', None)
                row.pop('source_import', None)
                stats['aliased'] += 1
                changed = True
                continue

        if identity_conflict is not None and identity_conflict[0] != sha:
            stats['identity_conflict'] += 1
            conflicts.append((row['key'], f'cache {identity[0]}/{identity[1]}', identity_conflict[1]))
            continue
        conflict = next(((cabinet, owner) for cabinet, owner in zip(cabs, owners)
                         if owner is not None and owner[0] != sha), None)
        if conflict is not None:
            stats['cabinet_conflict'] += 1
            conflicts.append((row['key'], conflict[0], conflict[1][1]))
            continue

        destination = content_dir / f'{sha}.bundle'
        verified_bytes += len(data)
        if args.dry_run:
            stats['added'] += 1
        else:
            if atomic_copy(data, destination, sha):
                copied_bytes += len(data)
            added_shas.add(sha)
            stats['added'] += 1
            changed = True
        for cabinet in cabs:
            cabinet_owner[cabinet.lower()] = (sha, row['key'], row)
        identity_owner[identity] = (sha, row['key'])

        url = f'http://127.0.0.1:8080{route}/bundles/{sha}.bundle'
        location = catalog.location(row['index'])
        require(location['type'].endswith('.IAssetBundleResource'),
                f'{row["key"]}: manifest index does not refer to an asset bundle')
        pending_urls[row['index']] = url

        row['status'] = 'LOCATED'
        row['sha256'] = sha
        row['serialized_files'] = cabs
        row['expected_bytes'] = len(data)
        row['internal_id'] = url
        row.pop('error', None)
        row.pop('source_import', None)
        manifest['content'][sha] = {
            'file': destination.relative_to(root).as_posix(),
            'bytes': len(data),
        }
        content_serialized[sha] = tuple(sorted(cabs))

    # Apply every options rewrite in one pass; rewriting per alias would copy
    # the whole catalog extra-data blob thousands of times.
    if option_replacements:
        catalog = catalog.rewrite_bundle_options(option_replacements)
    # Apply the internal URLs once, after every possible catalog rewrite, so
    # the rewrites never operate on a stale document.
    for index, url in pending_urls.items():
        internal_id = catalog.entries[index][0]
        catalog.document['m_InternalIds'][internal_id] = url
    document = catalog.document

    # Recompute the dependency status of every prefab from the current bundle
    # states. Nothing else in the variant records is rewritten.
    status_by_index = {row['index']: row.get('status') for row in bundles}
    key_by_index = {row['index']: row.get('key', '') for row in bundles}
    for variant in manifest['variants']:
        missing = [key_by_index.get(index, f'#{index}')
                   for index in variant.get('bundle_indices', ())
                   if status_by_index.get(index) != 'LOCATED']
        variant['missing_dependencies'] = missing
        variant['asset_status'] = 'DEPENDENCIES LOCATED' if not missing else 'MISSING DEPENDENCIES'

    complete_after = sum(not variant.get('missing_dependencies') for variant in manifest['variants'])
    manifest.setdefault('counts', {}).update({
        'catalog_bundles': len(bundles),
        'located_bundles': len(manifest['content']),
        'prefab_addresses': len(manifest['variants']),
        'dependency_complete_addresses': complete_after,
        'dependency_complete_species': len({variant.get('species') for variant in manifest['variants']
                                            if not variant.get('missing_dependencies')}),
        'android_verified': 0,
    })

    catalog_name = catalog_path.name
    if catalog_name.endswith('.full.catalog.json'):
        full_name = catalog_name
    elif catalog_name.endswith('.catalog.json'):
        full_name = catalog_name[:-len('.catalog.json')] + '.full.catalog.json'
    else:
        full_name = catalog_path.stem + '.full.catalog.json'
    full_catalog_path = catalog_path.with_name(full_name)

    # Validate the proposed graph before replacing any live file. A CAB or
    # cache-identity collision must abort while the existing manifest is still
    # intact; copied content files are harmless and can be reused.
    validate_manifest(manifest, Catalog(document))

    if args.dry_run:
        # Structural verification only: no content file was written and no
        # manifest/catalog file was replaced.
        pass
    elif not changed:
        # Idempotent rerun: every selectable bundle was already merged. Do not
        # rewrite a 50 MB manifest just to reformat it.
        if args.verify:
            verify_manifest(root, manifest_path, set(), Catalog, validate_manifest)
    else:
        merged_catalog_bytes = json.dumps(document, separators=(',', ':'),
                                          ensure_ascii=False).encode('utf8')
        atomic_write(full_catalog_path, merged_catalog_bytes)
        manifest['local_catalog'] = full_catalog_path.relative_to(root).as_posix()
        manifest['local_catalog_sha256'] = sha256_bytes(merged_catalog_bytes)
        manifest_output = (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode('utf8')
        atomic_write(manifest_path, manifest_output)
        if args.verify:
            verify_manifest(root, manifest_path, added_shas, Catalog, validate_manifest)
        else:
            # Cheap post-write sanity: both outputs parse and the catalog hash
            # recorded in the manifest still matches the file on disk.
            written = json.loads(manifest_path.read_text(encoding='utf8'))
            written_catalog = project_path(root, written['local_catalog']).read_bytes()
            require(sha256_bytes(written_catalog) == written['local_catalog_sha256'],
                    'written catalog hash does not match the manifest')

    print('recovered asset merge')
    print(f'  manifest: {manifest_path}')
    print(f'  route:    {route}')
    print(f'  recovery: {recovered}')
    print(f'  content:  {content_dir}')
    print(f'  mode:     {"dry run (nothing written)" if args.dry_run else ("merged" if changed else "already merged")}')
    print(f'  missing bundles before: {missing_before}')
    print(f'  matched: {stats["matched"]}')
    print(f'  {"would add" if args.dry_run else "added"}:   {stats["added"]}')
    print(f'  aliased to existing local bundles: {stats["aliased"]}')
    print(f'  skipped (already located): {stats["already"]}')
    print(f'  skipped (not in priority): {stats["not_priority"]}')
    print(f'  skipped (no recovered entry): {stats["no_index"]}')
    print(f'  skipped (ambiguous match): {stats["ambiguous"]}')
    print(f'  skipped (CAB identity conflict): {stats["cabinet_conflict"]}')
    print(f'  skipped (cache identity conflict): {stats["identity_conflict"]}')
    print(f'  invalid/unreadable: {stats["invalid"]}')
    if ambiguous:
        for key, reason in ambiguous[:10]:
            print(f'    ambiguous: {key}: {reason}')
        if len(ambiguous) > 10:
            print(f'    ... and {len(ambiguous) - 10} more')
    if conflicts:
        for key, cabinet, owner in conflicts[:10]:
            print(f'    conflict: {key}: {cabinet} already belongs to {owner}')
        if len(conflicts) > 10:
            print(f'    ... and {len(conflicts) - 10} more')
    print(f'  verified bytes: {verified_bytes}')
    print(f'  copied bytes:   {copied_bytes}')
    print(f'  unique content entries: {len(manifest["content"])}')
    print(f'  dependency-complete variants: {complete_before} -> {complete_after}')
    if not args.dry_run and changed:
        print(f'  catalog:  {full_catalog_path}')
    if args.verify:
        print('  verification: passed')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]),
                        help='project root (default: the parent of this script)')
    parser.add_argument('--manifest', default='builds/m113-reviewed-assets.json')
    parser.add_argument('--recovered', default='builds/missing-assets-recovered-01')
    parser.add_argument('--content-dir', default='builds/m113-recovered-content')
    parser.add_argument('--route', choices=ROUTES,
                        help='override the manifest route (defaults to its own route)')
    parser.add_argument('--only', choices=('all', 'priority'), default='all')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
