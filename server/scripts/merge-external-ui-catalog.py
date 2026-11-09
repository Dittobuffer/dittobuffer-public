#!/usr/bin/env python3
"""Merge the external badge/NPC addresses into the catalog the client loads.

The installed client does not fetch ``catalog_ext_12`` reliably. It does load
``catalog_926``, so the badge and team-leader addresses need to live there.
This is a private-server compatibility overlay: every original catalog entry is
retained, the eight genuinely shared keys keep their original main-catalog
entries, and only additional external entries are appended.

The recovered atlases also get fresh Addressables cache identities. That is
important because an earlier all-badges fallback cached the player atlas under
the legacy/event names; changing only the catalog hash would leave those wrong
bundles usable.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dittobuffer.core.asset_catalog import Catalog  # noqa: E402
from dittobuffer.core.client_resources import ClientResources  # noqa: E402

REGISTRY = ROOT / "assets/client-runtime/registry/registry.json"
BUILD_REGISTRY = ROOT / "builds/registry.json"
CATALOG = ROOT / "builds/m151-client-runtime-v3/catalog_926.json"
CATALOG_HASH = ROOT / "builds/m151-client-runtime-v3/catalog_926.hash"
BACKUP = CATALOG.with_name("catalog_926.before-external-ui.json")
BACKUP_HASH = CATALOG_HASH.with_name("catalog_926.before-external-ui.hash")
EXTERNAL = ROOT / "assets/client-runtime/cdn/catalog_ext_12.json"

# Prefix -> exact recovered file in the runtime registry.
RECOVERED = {
    "badges_generallegacy_": "badges_generallegacy_assets_all_1644f3cf4b8b0b1426d88365b0c86f27.bundle",
    "badges_eventdynamic_": "badges_eventdynamic_assets_all_fc1210107c70f65681170f4b76dbc413.bundle",
    "badges_playerdynamic_": "badges_playerdynamic_assets_all_87037e48bb637139462ce7e6f3fdb0a4.bundle",
    "npctrainerassets_assets_all_": "npctrainerassets_assets_all_e64405df3996ca7863c3e815c2f6cbca.bundle",
}

REQUIRED_KEYS = (
    "tx_badge_frame_shine",
    "default_badge_0",
    "default_badge_1",
    "Badge_1",
    "candela_billboard",
    "professor_billboard",
    "willowB_phone",
)


class RuntimeCatalog(Catalog):
    """Read the client catalog, including its opaque dependency-bucket keys."""

    @classmethod
    def string(cls, data, offset):
        if data[offset] in (0, 1):
            return super().string(data, offset)
        # These keys are dependency buckets, not public addresses. Preserve a
        # stable distinct identity; Catalog.combine recreates every dependency
        # bucket from the referenced entries anyway.
        return "opaque-key-at-" + str(offset), offset + 1


def digest(path: Path) -> tuple[int, str, str]:
    raw = path.read_bytes()
    return len(raw), hashlib.sha256(raw).hexdigest(), hashlib.md5(raw).hexdigest()


def bundle_name(internal_id: str) -> str:
    return internal_id.replace("\\", "/").rsplit("/", 1)[-1]


def remap_player_paths(catalog, bundle_path: Path):
    """Point player-badge entries at the paths actually inside the atlas.

    The recovered player atlas kept its original project paths
    (``Assets/UI/.../Badges``), while the external catalog's internal IDs use
    package paths (``Packages/.../Badges/PlayerDynamic``). Addressables loads
    by the catalog path, so every badge load failed even though the bundle had
    the sprite. Legacy/event and NPC bundles already use package paths; only
    this atlas needs remapping.
    """
    import UnityPy

    environment = UnityPy.load(str(bundle_path))
    paths = {}
    for name, item in environment.container.items():
        try:
            resource_type = item.type.name
        except Exception:
            continue
        normalized = name.replace("\\", "/")
        if resource_type in ("Sprite", "Texture2D") and "/Badges/" in normalized:
            paths.setdefault(Path(normalized).stem.lower(), name)
    if not paths:
        raise SystemExit("player atlas has no badge container paths")

    entries = [list(entry) for entry in catalog.entries]
    internal_ids = list(catalog.document["m_InternalIds"])
    remapped = 0
    for entry in entries:
        old = internal_ids[entry[0]].replace("\\", "/")
        if "/Badges/PlayerDynamic/" not in old:
            continue
        stem = Path(old).stem.lower()
        replacement = paths.get(stem)
        if not replacement:
            raise SystemExit("player atlas lacks badge asset path: " + old)
        if replacement != old:
            if replacement not in internal_ids:
                internal_ids.append(replacement)
            entry[0] = internal_ids.index(replacement)
            remapped += 1
    if not remapped:
        raise SystemExit("no player badge paths were remapped")
    document = dict(catalog.document)
    document["m_InternalIds"] = internal_ids
    document["m_EntryDataString"] = base64.b64encode(
        struct.pack("<i", len(entries)) +
        b"".join(struct.pack("<7i", *entry) for entry in entries)
    ).decode("ascii")
    return RuntimeCatalog(document), remapped


def main() -> int:
    registry = json.loads(REGISTRY.read_text(encoding="utf8"))
    files = registry["files"]
    current = RuntimeCatalog(json.loads(CATALOG.read_text(encoding="utf8")))
    already_merged = any(key in current.buckets for key in REQUIRED_KEYS)
    if already_merged:
        if not BACKUP.is_file():
            raise SystemExit("catalog is already merged and the pre-merge backup is missing")
        source = BACKUP
        print("rebuilding from the pre-overlay catalog backup")
    else:
        source = CATALOG
    main_catalog = RuntimeCatalog(json.loads(source.read_text(encoding="utf8")))
    external = Catalog(json.loads(EXTERNAL.read_text(encoding="utf8")))

    resources = ClientResources(ROOT, dict(enabled=True, allow_partial=True,
        registry=REGISTRY.relative_to(ROOT).as_posix()))
    main_prefix_match = re.match(
        r"^(.*?bundles[\\/]Android[\\/])",
        next(value for value in main_catalog.document["m_InternalIds"]
             if "bundles" in value.replace("\\", "/")),
    )
    if not main_prefix_match:
        raise SystemExit("cannot determine main catalog bundle URL prefix")
    main_prefix = main_prefix_match.group(1)

    replacements = {}
    available_bundles = set()
    for prefix, registry_name in RECOVERED.items():
        matches = [i for i in range(len(external.entries))
                   if external.location(i)["type"].endswith(".IAssetBundleResource")
                   and bundle_name(external.location(i)["internal_id"]).startswith(prefix)]
        if len(matches) != 1:
            raise SystemExit(f"expected one external bundle for {prefix}, found {len(matches)}")
        index = matches[0]
        name = bundle_name(external.location(index)["internal_id"])
        row = files.get(registry_name)
        if not row:
            raise SystemExit("missing recovered registry row: " + registry_name)
        path = (ROOT / row["file"]).resolve()
        if not path.is_file():
            raise SystemExit("missing recovered bundle: " + str(path))
        size, sha, md5 = digest(path)
        if sha != row["sha256"] or size != row["bytes"]:
            raise SystemExit("recovered bundle changed: " + registry_name)
        option = external.options(index)
        value = dict(option["value"])
        value.update(
            m_BundleName="libre_" + re.sub(r"[^A-Za-z0-9_]", "_", prefix) + md5[:8],
            m_BundleSize=size,
            m_Hash=md5,
            m_Crc=0,
            m_UseCrcForCachedBundles=False,
            m_ClearOtherCachedVersionsWhenLoaded=True,
        )
        replacements[index] = dict(option, value=value)
        available_bundles.add(name)
        files[registry_name] = dict(row, cache_version=md5)
        if prefix == "badges_playerdynamic_":
            player_bundle_path = path
        print(f"using {name} -> {size} bytes md5={md5}")

    external = external.rewrite_bundle_options(replacements)
    if any(key not in external.buckets for key in REQUIRED_KEYS):
        raise SystemExit("external catalog is missing a required UI key")

    def available(source, index):
        if source == 0:
            return True
        location = external.location(index)
        if location["type"].endswith(".IAssetBundleResource"):
            return bundle_name(location["internal_id"]) in available_bundles
        return all(available(source, dependency)
                   for dependency in location["dependencies"])

    merged, chosen, _ = Catalog.combine([main_catalog, external], available)
    merged, remapped = remap_player_paths(merged, player_bundle_path)
    print(f"remapped {remapped} player-badge internal paths")
    internal_ids = []
    rewritten = 0
    for internal_id in merged.document["m_InternalIds"]:
        if "ExternalAddressablesAssetManager" in internal_id or "catalog_ext_12" in internal_id:
            name = bundle_name(internal_id)
            if not re.fullmatch(r"[A-Za-z0-9_.-]+\.bundle", name):
                raise SystemExit("invalid external bundle name: " + name)
            internal_ids.append(main_prefix + name)
            rewritten += 1
        else:
            internal_ids.append(internal_id)
    merged.document["m_InternalIds"] = internal_ids
    merged.document["m_LocatorId"] = main_catalog.document.get(
        "m_LocatorId", "AddressablesMainContentCatalog")
    merged.document["m_InternalIdPrefixes"] = []
    encoded = json.dumps(merged.document, separators=(",", ":")).encode("utf8")
    build_hash = hashlib.md5(encoded).hexdigest()
    merged.document["m_BuildResultHash"] = build_hash
    encoded = json.dumps(merged.document, separators=(",", ":")).encode("utf8")
    catalog_hash = hashlib.md5(encoded).hexdigest()
    result = RuntimeCatalog(json.loads(encoded))
    for key in REQUIRED_KEYS:
        if key not in result.buckets:
            raise SystemExit("merged catalog lost required key: " + key)
        bundles = [bundle_name(result.location(i)["internal_id"])
                   for i in result.closure(key)
                   if result.location(i)["type"].endswith(".IAssetBundleResource")]
        if not any(name in available_bundles for name in bundles):
            raise SystemExit("merged key has no recovered bundle: " + key)
    if len(result.entries) < len(main_catalog.entries):
        raise SystemExit("merged catalog lost original entries")
    if any("ExternalAddressablesAssetManager" in value or "catalog_ext_12" in value
           for value in result.document["m_InternalIds"]):
        raise SystemExit("external bundle URL rewrite incomplete")
    print(f"merged {len(external.buckets)} external keys; "
          f"{rewritten} bundle URLs rewritten; {len(result.entries)} entries")

    if not BACKUP.exists():
        shutil.copy2(CATALOG, BACKUP)
        shutil.copy2(CATALOG_HASH, BACKUP_HASH)
    CATALOG.write_bytes(encoded)
    CATALOG_HASH.write_bytes(catalog_hash.encode("ascii"))
    for name, path in (("catalog_926.json", CATALOG), ("catalog_926.hash", CATALOG_HASH)):
        size, sha, _ = digest(path)
        files[name] = dict(file=path.relative_to(ROOT).as_posix(),
                           bytes=size, sha256=sha)
    registry["files"] = dict(sorted(files.items()))
    registry["counts"] = dict(registry.get("counts") or {}, entries=len(files))
    REGISTRY.write_text(json.dumps(registry, indent=1) + "\n", encoding="utf8")
    BUILD_REGISTRY.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf8")
    ClientResources(ROOT, dict(enabled=True, allow_partial=True,
        registry=REGISTRY.relative_to(ROOT).as_posix()))
    print(f"wrote {CATALOG.relative_to(ROOT)} bytes={len(encoded)} "
          f"hash={catalog_hash} build={build_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
