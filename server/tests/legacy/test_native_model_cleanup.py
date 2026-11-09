import copy
import importlib.util
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def module(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/file);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
defaults=module('native_defaults','scripts/resolve-native-model-defaults.py')
batch=module('native_batch','scripts/native-model-batch.py')


class NativeModelCleanupTests(unittest.TestCase):
    def setUp(self):
        self.row=dict(species=327,supplied_first_form=37,client_default_holo=37,client_default_int=37,default_address='pm327.f00',forms=[dict(form=37,normal_address='pm327.f00')])
        self.metadata={(327,37):dict(is_costume=False),(327,38):dict(is_costume=False)}
        self.available={'pm327.f00','pm327.f01'}

    def test_native_execution_resolves_shared_stat_ambiguity(self):
        result=defaults.validated_native_default(327,dict(reason='ambiguous'),self.row,self.metadata,self.available)
        self.assertEqual((result['form'],result['address']),(37,'pm327.f00'))

    def test_first_entry_alone_is_not_native_evidence(self):
        for key in ['client_default_holo','client_default_int','supplied_first_form']:
            altered=dict(self.row);altered.pop(key)
            with self.assertRaises(ValueError):defaults.validated_native_default(327,{},altered,self.metadata,self.available)

    def test_native_overloads_and_supplied_definition_must_agree(self):
        for key in ['client_default_holo','client_default_int','supplied_first_form']:
            altered=dict(self.row);altered[key]=38
            with self.assertRaises(ValueError):defaults.validated_native_default(327,{},altered,self.metadata,self.available)

    def test_default_cannot_be_costume_other_species_missing_or_max(self):
        for address in ['pm150.f00','pm327.fMISSING','pm327.fETERNAMAX','pm327.fLOAF']:
            with self.assertRaises(ValueError):defaults.validated_native_default(327,{},dict(self.row,default_address=address),self.metadata,self.available|{address})
        with self.assertRaises(ValueError):defaults.validated_native_default(327,{},self.row,{(327,37):dict(is_costume=True)},self.available)

    def test_probe_is_bound_to_version_methods_actual_process_and_game_master(self):
        good=dict(device_process_id=123,game_master_sha256='supplied',client_methods=dict(holo='0x7679A28',integer='0x767A5F4',normal='0x7EABAF0'))
        defaults.validate_probe_document(good,'supplied')
        for key,value in [('device_process_id',True),('device_process_id',0),('game_master_sha256','other'),('client_methods',{})]:
            with self.assertRaises(ValueError):defaults.validate_probe_document(dict(good,**{key:value}),'supplied')

    def test_reliability_switching_preserves_species_and_explicit_scope(self):
        requested=['pm25','pm25.g2','pm150.fMEGA_X']
        batch.validate_transitions({'pm25':'pm25.g2'},requested)
        for mapping in [{},None,{'pm25':'pm150.fMEGA_X'},{'pm25':'pm25.s'}]:
            with self.assertRaises(ValueError):batch.validate_transitions(mapping,requested)


if __name__=='__main__':unittest.main()
