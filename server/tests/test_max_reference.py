"""Max GM envelope integrity and native-readiness checks; no asset redistribution."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from protocol.opaque import byte_field
from dittobuffer.gameplay.game import ROOT, p
from dittobuffer.gameplay import max_system as mx


REQUIRED = ('MP_SHARED_SETTINGS', 'BREAD_FEATURE_FLAGS', 'BREAD_SHARED_SETTINGS',
            'BREAD_MOVE_MAPPING_SETTINGS', 'BREAD_MOVE_LEVEL_SETTINGS_GROUP_1')


def document():
    return {'templates': {name: p.GameMasterClientTemplateProto(
        template_id=name).SerializeToString().hex() for name in REQUIRED},
        'species_groups': {'1:0': 1}}


def put(data, name, value):
    data['templates'][name] = value.SerializeToString().hex()


def complete_fixture():
    """Explicit synthetic shape fixture, not official balance or native proof."""
    data = document()
    for name in (*REQUIRED, 'BREAD_BATTLE_CLIENT_SETTINGS', 'BREAD_POKEMON_SCALING_SETTINGS',
                 'VNEXT_BATTLE_CLIENT_SETTINGS'):
        value = p.GameMasterClientTemplateProto(template_id=name)
        getattr(value, mx.SETTINGS_FIELDS[name]).SetInParent()
        if name == 'BREAD_SHARED_SETTINGS':
            value.bread_settings.bread_battle_availability.CopyFrom(
                p.BreadBattleAvailabilityProto(bread_battle_availability_start_minute=1,
                                               bread_battle_availability_end_minute=2))
        if name == 'BREAD_MOVE_MAPPING_SETTINGS':
            value.bread_move_mappings.mappings.add(type=1, move=1)
        if name == 'BREAD_MOVE_LEVEL_SETTINGS_GROUP_1':
            for field in ('a_settings', 'b_settings', 'c_settings'):
                getattr(value.bread_move_level_settings, field).add(mp_cost=1)
        if name == 'VNEXT_BATTLE_CLIENT_SETTINGS':
            value.vnext_battle_config.max_battle_config.SetInParent()
        put(data, name, value)
    return data


class MaxReferenceTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.private').mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=ROOT / '.private')
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'reference.json'
        self.addCleanup(mx.reference.cache_clear)

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding='utf-8')
        return self.path.relative_to(ROOT).as_posix()

    def test_full_wrapper_preserves_exact_unknown_bytes(self):
        name = 'MP_SHARED_SETTINGS'
        value = p.GameMasterClientTemplateProto(template_id=name,
                    mp_settings=p.MpSharedSettingsProto(mp_capacity=7))
        raw = value.SerializeToString() + byte_field(24000, b'original opaque source bytes')
        data = {'templates': {name: raw.hex()}}
        self.assertEqual(mx.gm(data, name).SerializeToString(), raw)
        self.assertEqual(data['templates'][name], raw.hex())

    def test_bare_settings_receive_correct_wrapper_without_losing_unknown_bytes(self):
        name = 'MP_SHARED_SETTINGS'
        raw = p.MpSharedSettingsProto(mp_capacity=7).SerializeToString() + byte_field(24000, b'original settings extension')
        value = mx.gm({'templates': {name: raw.hex()}}, name)
        self.assertEqual(value.template_id, name)
        self.assertEqual(value.WhichOneof('data'), 'mp_settings')
        self.assertEqual(value.mp_settings.SerializeToString(), raw)

    def test_id_only_synthetic_reference_is_not_fabricated_into_settings(self):
        data = document()
        before = copy.deepcopy(data)
        name = 'BREAD_MOVE_MAPPING_SETTINGS'
        value = mx.gm(data, name)
        self.assertIsNone(value.WhichOneof('data'))
        self.assertEqual(value.SerializeToString().hex(), data['templates'][name])
        self.assertTrue(mx.reference_quality(data)['issues'])
        self.assertEqual(data, before)

    def test_wrong_full_wrapper_identity_is_rejected(self):
        wrong = p.GameMasterClientTemplateProto(template_id='ANOTHER_SOURCE_TEMPLATE',
            mp_settings=p.MpSharedSettingsProto(mp_capacity=7))
        with self.assertRaisesRegex(ValueError, 'does not match'):
            mx.gm({'templates': {'MP_SHARED_SETTINGS': wrong.SerializeToString().hex()}}, 'MP_SHARED_SETTINGS')

    def test_wrong_full_wrapper_data_type_is_rejected(self):
        wrong = p.GameMasterClientTemplateProto(template_id='MP_SHARED_SETTINGS',
            bread_settings=p.BreadSharedSettingsProto(upgrade_cost_coin=1))
        with self.assertRaisesRegex(ValueError, 'data type'):
            mx.gm({'templates': {'MP_SHARED_SETTINGS': wrong.SerializeToString().hex()}}, 'MP_SHARED_SETTINGS')

    def test_unknown_full_templates_preserve_source_extensions(self):
        name = 'SOURCE_EXTENSION_TEMPLATE'
        raw = p.GameMasterClientTemplateProto(template_id=name).SerializeToString() + byte_field(24000, b'opaque native definition')
        self.assertEqual(mx.gm({'templates': {name: raw.hex()}}, name).SerializeToString(), raw)
        with self.assertRaisesRegex(ValueError, 'Unknown bare'):
            mx.gm({'templates': {name: '0801'}}, name)

    def test_malformed_hex_and_payload_fail_explicitly(self):
        for raw in ('not hexadecimal', '1280'):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, 'Invalid'):
                mx.gm({'templates': {'MP_SHARED_SETTINGS': raw}}, 'MP_SHARED_SETTINGS')

    def test_normal_move_mapping_rejects_unset_unknown_and_duplicate_types(self):
        for entries in ([(1, 0)], [(99999, 1)], [(1, 1), (1, 2)]):
            value = p.GameMasterClientTemplateProto(template_id='BREAD_MOVE_MAPPING_SETTINGS')
            for kind, move in entries:
                value.bread_move_mappings.mappings.add(type=kind, move=move)
            with self.subTest(entries=entries), self.assertRaisesRegex(ValueError, 'mapping'):
                mx.gm({'templates': {value.template_id: value.SerializeToString().hex()}}, value.template_id)

    def test_gmax_move_mapping_rejects_invalid_override_and_duplicate_forms(self):
        for duplicate in (False, True):
            value = p.GameMasterClientTemplateProto(template_id='SOURDOUGH_MOVE_MAPPING_SETTINGS')
            mapping = value.sourdough_move_mapping_settings.mappings.add(pokemon_id=1, form=0, move=1)
            if duplicate:
                value.sourdough_move_mapping_settings.mappings.add(pokemon_id=1, form=0, move=2)
            else:
                mapping.optional_b_move_override.override = True
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ValueError, 'mapping|override'):
                mx.gm({'templates': {value.template_id: value.SerializeToString().hex()}}, value.template_id)

    def test_complete_native_validation_is_explicit_for_partial_backend_fixtures(self):
        path = self.write(document())
        self.assertTrue(mx.reference(path)['templates'])
        with self.assertRaisesRegex(ValueError, 'Incomplete native Max reference'):
            mx.reference(path, require_complete=True)
        self.assertEqual(json.loads(self.path.read_text()), document())

    def test_complete_shape_and_digest_pin_are_shared_by_gm_entry_points(self):
        data = complete_fixture()
        path = self.write(data)
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(mx.reference_quality(data)['issues'], [])
        self.assertEqual(mx.reference(path, True, digest), data)
        config = {'max_system': {'enabled': True, 'reference': path,
                                'require_complete_reference': True, 'reference_sha256': digest}}
        self.assertTrue(mx.templates(config))
        merged = []
        mx.merge_client_settings(config, merged)
        self.assertEqual(merged[0].template_id, 'VNEXT_BATTLE_CLIENT_SETTINGS')
        for call in (lambda: mx.reference(path, True, '0' * 64),
                     lambda: mx.templates({'max_system': {**config['max_system'], 'reference_sha256': '0' * 64}}),
                     lambda: mx.merge_client_settings({'max_system': {**config['max_system'], 'reference_sha256': '0' * 64}}, [])):
            with self.assertRaisesRegex(ValueError, 'SHA-256 pin differs'):
                call()

    def test_invalid_native_validation_flag_and_digest_are_rejected(self):
        path = self.write(document())
        with self.assertRaisesRegex(ValueError, 'boolean'):
            mx.reference(path, require_complete='yes')
        with self.assertRaisesRegex(ValueError, 'SHA-256 pin'):
            mx.reference(path, expected_sha256='invalid')

    def test_authorized_full_reference_metadata_and_exact_roundtrips_when_available(self):
        path = ROOT / 'assets/pokemon/local/max/m181-max-reference-ball-01.json'
        if not path.is_file():
            self.skipTest('Authorized full Max reference is supplied independently, not redistributed')
        raw = path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         'e43457935016e732a64dbd2a07e20d785025387697ec609cbda00a3b4b366d45')
        data = json.loads(raw)
        report = mx.reference_quality(data)
        self.assertEqual((report['templates'], report['species_groups'], report['max_type_mappings'],
                          report['gmax_allowlists'], report['availability_minutes'], report['max_ball_settings'], report['issues']),
                         (82, 2491, 18, 32, [360, 1260], True, []))
        for name, source in data['templates'].items():
            with self.subTest(template=name):
                self.assertEqual(mx.gm(data, name).SerializeToString().hex(), source)
