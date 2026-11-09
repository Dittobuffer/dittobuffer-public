import importlib.util
import unittest
from pathlib import Path
from tests import test_route_resource_overlay as existing_overlay_tests

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('m23_resources', ROOT / 'scripts/m23-encounter-resources.py')
resources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resources)


class NativeResourceContractTests(unittest.TestCase):
    DUMP = '''// Namespace: Native
public class Base // TypeDefIndex: 1
{
 private int inherited; // 0x10
}
// Namespace: Native
public class Base.Nested // TypeDefIndex: 2
{
 private int unrelated; // 0x10
}
// Namespace: Native
public class Scene : Base // TypeDefIndex: 3
{
 private int <Speed>k__BackingField; // 0x18
}
'''

    def test_inherited_and_compiler_generated_fields_are_audited(self):
        result = resources.script_contract(self.DUMP, 'Scene', 'Native',
                                            {'inherited': 1, '<Speed>k__BackingField': 2, 'm_Name': ''})
        self.assertEqual(result['missing_client_fields'], [])
        self.assertEqual(result['native_abi_compatibility'], 'NOT_TESTED')

    def test_unrelated_nested_class_and_new_fields_are_rejected(self):
        for field in ('unrelated', 'newer_client_only'):
            with self.assertRaises(ValueError):
                resources.script_contract(self.DUMP, 'Scene', 'Native', {field: 1})

    def test_namespace_and_absent_script_are_rejected(self):
        with self.assertRaises(ValueError):
            resources.script_contract(self.DUMP, 'Absent', 'Native', {'x': 1})
        with self.assertRaises(ValueError):
            resources.script_contract(self.DUMP, 'Scene', 'Different', {'inherited': 1})

    def test_nested_scene_overlay_preserves_prior_private_and_public_graphs(self):
        helper = existing_overlay_tests.module
        baseline = helper.overlay(existing_overlay_tests.fixture('combat_explorer', 'accepted-mateo.bundle'),
                                  existing_overlay_tests.fixture('pm9999', 'accepted-pokemon.bundle'))
        before = resources.semantic_keys(baseline)
        result = helper.overlay(existing_overlay_tests.fixture('DiancieScene', 'new-scene.bundle'), baseline,
                                namespace='libre-m23-overlay-dependencies:', component_namespace='libre-m23-component:')
        after = resources.semantic_keys(result)
        self.assertTrue(any(k.startswith('libre-m202-component:') for k in before))
        for key in before:
            self.assertEqual(after[key], before[key], key)
        self.assertIn('DiancieScene', after)
