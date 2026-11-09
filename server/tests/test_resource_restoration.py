"""Non-destructive restoration boundaries using small original test bytes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/restore-local-resources.py"
SPEC = importlib.util.spec_from_file_location("resource_restore", SCRIPT)
restore = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(restore)
ASSET_SCRIPT = SCRIPT.parents[1] / "src/dittobuffer/core/asset_catalog.py"
ASSET_SPEC = importlib.util.spec_from_file_location("portable_assets", ASSET_SCRIPT)
assets = importlib.util.module_from_spec(ASSET_SPEC)
ASSET_SPEC.loader.exec_module(assets)


class ResourceRestorationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root, self.source = self.base / "checkout", self.base / "authorized"
        self.root.mkdir()
        self.source.mkdir()
        self.original = b"original project-owned fixture bytes\n"
        self.row = dict(path="assets/pokemon/local/pokemon/content/test.bundle", source_path="inputs/test.bundle",
                        bytes=len(self.original), sha256=hashlib.sha256(self.original).hexdigest(), group="pokemon")

    def write_source(self, content=None):
        path = self.source / self.row["source_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.original if content is None else content)
        return path

    def manifest(self, rows):
        path = self.base / "resources.json"
        path.write_text(json.dumps(dict(format=1, files=rows)), encoding="utf8")
        return path

    def test_default_check_creates_no_directories(self):
        planned, verified, failures = restore.plan_restore(self.root, [self.row], None)
        self.assertEqual((planned, verified), ([], []))
        self.assertEqual(len(failures), 1)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_source_preview_is_read_only(self):
        self.write_source()
        planned, verified, failures = restore.plan_restore(self.root, [self.row], self.source)
        self.assertEqual((len(planned), verified, failures), (1, [], []))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_exclusive_restore_and_second_check(self):
        self.write_source()
        planned, _, failures = restore.plan_restore(self.root, [self.row], self.source)
        self.assertFalse(failures)
        restore.restore_missing(self.root, *planned[0])
        destination = self.root / self.row["path"]
        self.assertEqual(destination.read_bytes(), self.original)
        self.assertEqual(list(destination.parent.iterdir()), [destination])
        planned, verified, failures = restore.plan_restore(self.root, [self.row], None)
        self.assertEqual((planned, verified, failures), ([], [self.row["path"]], []))

    def test_existing_file_is_preserved_even_if_it_differs(self):
        self.write_source()
        destination = self.root / self.row["path"]
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"user-owned existing data")
        planned, _, failures = restore.plan_restore(self.root, [self.row], self.source)
        self.assertFalse(planned)
        self.assertIn("preserved", failures[0])
        self.assertEqual(destination.read_bytes(), b"user-owned existing data")

    def test_wrong_source_cannot_produce_a_plan(self):
        self.write_source(b"version https://git-lfs.github.com/spec/v1\n")
        planned, _, failures = restore.plan_restore(self.root, [self.row], self.source)
        self.assertFalse(planned)
        self.assertEqual(len(failures), 1)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_source_change_during_copy_never_publishes(self):
        incoming = self.write_source()
        planned, _, _ = restore.plan_restore(self.root, [self.row], self.source)
        incoming.write_bytes(b"changed after validation")
        with self.assertRaisesRegex(ValueError, "changed during copy"):
            restore.restore_missing(self.root, *planned[0])
        destination = self.root / self.row["path"]
        self.assertFalse(destination.exists())
        self.assertEqual(list(destination.parent.iterdir()), [])

    def test_racing_existing_file_is_not_replaced(self):
        self.write_source()
        planned, _, _ = restore.plan_restore(self.root, [self.row], self.source)
        destination = self.root / self.row["path"]
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"created by another process")
        with self.assertRaises(FileExistsError):
            restore.restore_missing(self.root, *planned[0])
        self.assertEqual(destination.read_bytes(), b"created by another process")
        self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_cross_platform_path_escapes_are_rejected(self):
        for path in ("../outside", "/outside", "C:/outside", "C:outside", "a/../../outside", "a\\outside", "."):
            with self.subTest(path=path), self.assertRaises(ValueError):
                restore.project_path(self.root, path)

    def test_duplicate_destination_is_rejected_case_insensitively(self):
        second = dict(self.row, path=self.row["path"].replace("test.bundle", "TEST.bundle"))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            restore.load_records(self.manifest([self.row, second]), {"pokemon"})

    def test_manifest_cannot_restore_source_code_or_private_configuration(self):
        for path in ("src/dittobuffer/__main__.py", ".private/credentials.json", "config/family.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                restore.load_records(self.manifest([dict(self.row, path=path)]), None)

    def test_unknown_group_and_malformed_inactive_group_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown"):
            restore.load_records(self.manifest([self.row]), {"not-a-group"})
        bad = dict(self.row, path="assets/pokemon/local/avatar/bad.bundle", group="avatar", sha256="bad")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            restore.load_records(self.manifest([self.row, bad]), {"pokemon"})

    def test_existing_symlink_cannot_escape_selected_root(self):
        link = self.root / "escape"
        try:
            link.symlink_to(self.source, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks is unavailable to this Windows account")
        with self.assertRaises(ValueError):
            restore.project_path(self.root, "escape/test.bundle")

    def overlay(self):
        content_sha = self.row["sha256"]
        baseline = dict(format=1, local_catalog="historical/catalog.json", local_catalog_sha256="a" * 64,
                        content={content_sha: dict(file="historical/fixture.bundle", bytes=self.row["bytes"])},
                        bundles=[dict(index=3, sha256=content_sha)],
                        variants=[dict(address="pm1", model_paths=["original/Bulbasaur.prefab"])],
                        costumes={"1:2": dict(addresses=["pm1.c2"])})
        raw = json.dumps(baseline).encode()
        (self.root / "base.json").write_bytes(raw)
        overlay = dict(format=1, costumes=baseline["costumes"], path_overlay=dict(
            format=1, base_manifest="base.json", base_manifest_sha256=hashlib.sha256(raw).hexdigest(),
            local_catalog="assets/pokemon/local/pokemon/catalog.json", content={content_sha: self.row["path"]}))
        path = self.root / "portable.json"
        path.write_text(json.dumps(overlay), encoding="utf8")
        return baseline, overlay, path

    def test_portable_overlay_preserves_original_graph_and_bytes(self):
        baseline, _, _ = self.overlay()
        before = (self.root / "base.json").read_bytes()
        loaded = assets.load_manifest(self.root, "portable.json")
        for key in ("local_catalog_sha256", "bundles", "variants", "costumes"):
            self.assertEqual(loaded[key], baseline[key])
        self.assertEqual(loaded["content"][self.row["sha256"]]["file"], self.row["path"])
        self.assertEqual((self.root / "base.json").read_bytes(), before)

    def test_portable_overlay_rejects_changed_base_and_identity_sets(self):
        _, overlay, path = self.overlay()
        (self.root / "base.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "integrity"):
            assets.load_manifest(self.root, "portable.json")
        _, overlay, path = self.overlay()
        overlay["path_overlay"]["content"] = {}
        path.write_text(json.dumps(overlay))
        with self.assertRaisesRegex(ValueError, "every content identity"):
            assets.load_manifest(self.root, "portable.json")

    def test_reviewed_identity_pin_accepts_only_path_relocation(self):
        baseline, overlay, path = self.overlay()
        overlay['path_overlay']['base_manifest_identity_sha256'] = assets.manifest_identity_sha256(baseline)
        path.write_text(json.dumps(overlay))
        baseline['local_catalog'] = 'old/local/catalog.json'
        for row in baseline['content'].values():
            row['file'] = 'independently/repaired/local.bundle'
        source = self.root / 'base.json'
        source.write_text(json.dumps(baseline))
        before = source.read_bytes()
        loaded = assets.load_manifest(self.root, 'portable.json')
        self.assertEqual(loaded['local_catalog'], overlay['path_overlay']['local_catalog'])
        self.assertEqual(loaded['content'][self.row['sha256']]['file'], self.row['path'])
        self.assertEqual(source.read_bytes(), before)
        for key, value in [('bytes', self.row['bytes'] + 1), ('sha256', '0' * 64)]:
            modified = json.loads(before)
            modified['content'][self.row['sha256']][key] = value
            source.write_text(json.dumps(modified))
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'integrity'):
                assets.load_manifest(self.root, 'portable.json')
        modified = json.loads(before)
        modified['costumes'] = {'unreviewed': 1}
        source.write_text(json.dumps(modified))
        with self.assertRaisesRegex(ValueError, 'integrity'):
            assets.load_manifest(self.root, 'portable.json')

    def test_portable_overlay_rejects_changed_costumes_and_unignored_destinations(self):
        _, overlay, path = self.overlay()
        overlay["costumes"] = {}
        path.write_text(json.dumps(overlay))
        with self.assertRaisesRegex(ValueError, "costume"):
            assets.load_manifest(self.root, "portable.json")
        _, overlay, path = self.overlay()
        overlay["path_overlay"]["content"][self.row["sha256"]] = "assets/avatar/new.bundle"
        path.write_text(json.dumps(overlay))
        with self.assertRaisesRegex(ValueError, "ignored local"):
            assets.load_manifest(self.root, "portable.json")

    def test_packaged_portable_manifests_match_restoration_pins(self):
        root = SCRIPT.parents[1]
        records = restore.load_records(root / restore.DEFAULT_MANIFEST, None)
        by_path = {row["path"]: row for row in records}
        for name in ("pokemon-assets.json", "avatar-assets.json"):
            loaded = assets.load_manifest(root, "config/resources/" + name)
            self.assertEqual(by_path[loaded["local_catalog"]]["sha256"], loaded["local_catalog_sha256"])
            for sha, row in loaded["content"].items():
                self.assertEqual((by_path[row["file"]]["sha256"], by_path[row["file"]]["bytes"]), (sha, row["bytes"]))
        for name in ("client-runtime.json", "client-runtime-m23.json"):
            runtime = json.loads((root / "config/resources" / name).read_text())
            for row in runtime["files"].values():
                self.assertEqual((by_path[row["file"]]["sha256"], by_path[row["file"]]["bytes"]),
                                 (row["sha256"], row["bytes"]))


if __name__ == "__main__":
    unittest.main()
