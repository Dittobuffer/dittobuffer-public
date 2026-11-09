"""Independent native tag evidence: Routes must not replace Power Spots."""
import unittest

from dittobuffer.gameplay.game import p


class SharedMapWireTests(unittest.TestCase):
    def test_routes_and_power_spots_keep_distinct_native_codec_tags(self):
        # Supplied 0.429.2 ARM64 ClientMapCellProto .cctor, RVA 0x8B1AE08:
        # 0x8B1B250 loads 0x8a for static Station codec offset 0x50;
        # 0x8B1B2E0 loads 0xa2 for static SharedRoute codec offset 0x60.
        # The earlier flat-tree exporter incorrectly assigned Routes field17.
        stations = p.ClientMapCellProto()
        stations.stations.add()
        routes = p.ClientMapCellProto()
        routes.routes.add()
        self.assertEqual(stations.SerializeToString(), b'\x8a\x01\x00')
        self.assertEqual(routes.SerializeToString(), b'\xa2\x01\x00')
        self.assertEqual(stations.DESCRIPTOR.fields_by_name['stations'].message_type.full_name,
                         p.StationProto.DESCRIPTOR.full_name)

    def test_collector_encounter_id_keeps_native_fixed64_tag(self):
        # ButterflyCollectorRewardEncounterProtoResponse writer0x8962B1C.
        self.assertEqual(p.ButterflyCollectorRewardEncounterProtoResponse(
            encounter_id=1).SerializeToString(), b'\x31\x01\x00\x00\x00\x00\x00\x00\x00')
