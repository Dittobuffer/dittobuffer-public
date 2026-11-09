import hashlib,unittest
from unittest.mock import patch
from protocol import game_pb2 as p
from protocol.opaque import byte_field
from dittobuffer.gameplay import route_npc_resources as resources

class RouteNpcResourceTests(unittest.TestCase):
    def fixture(self):
        template=p.GameMasterClientTemplateProto(template_id='CHARACTER_EXPLORER')
        settings=template.invasion_npc_display_settings;settings.model_name='combat_explorer';settings.avatar.avatar=2
        raw=template.SerializeToString();outer=byte_field(1,b'CHARACTER_EXPLORER')+byte_field(2,raw)
        return raw,byte_field(2,outer)

    def test_disabled_mateo_does_not_require_private_compatibility_input(self):
        self.assertEqual(resources.templates({}),[])
        self.assertEqual(resources.templates({'routes':{'enabled':True,'mateo':{'enabled':False}}}),[])

    def test_exact_inner_record_and_identity_required(self):
        raw,cache=self.fixture()
        with patch.object(resources,'EXPLORER_TEMPLATE_SHA256',hashlib.sha256(raw).hexdigest()):
            value=resources.explorer_record(cache);self.assertEqual(value.invasion_npc_display_settings.model_name,'combat_explorer')
            with self.assertRaisesRegex(ValueError,'Exactly one'):resources.explorer_record(cache+cache)
            with self.assertRaisesRegex(ValueError,'Exactly one'):resources.explorer_record(b'')
        with self.assertRaisesRegex(ValueError,'reviewed'):resources.explorer_record(cache)

    def test_actual_pinned_source_has_required_model_and_local_only_projection(self):
        source=resources.ROOT/resources.DEFAULT_SOURCE
        if not source.exists():self.skipTest('Authorized Explorer source is independently restored, not distributed')
        value=resources.templates({'routes':{'enabled':True,'mateo':{'enabled':True}}})[0]
        settings=value.invasion_npc_display_settings
        self.assertEqual(settings.model_name,'combat_explorer');self.assertEqual(settings.avatar.avatar,2)
        self.assertTrue(settings.display_on_map)
        self.assertFalse(settings.icon_url);self.assertFalse(settings.trainer_quote);self.assertFalse(settings.backdrop_image_bundle)

if __name__=='__main__':unittest.main()
