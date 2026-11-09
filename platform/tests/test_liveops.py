"""Platform-owned live-ops and POI tests, including the no-server admin path."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dittobuffer_platform import liveops
from dittobuffer_platform.http_api import serve
from dittobuffer_platform.pois import PoiImporter
from dittobuffer_platform.presence import room_document
from dittobuffer_platform.rooms import Platform
from dittobuffer_platform.store import Store
from dittobuffer_platform.world import WorldSimulator


class LiveopsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.platform = Platform(Store(self.root / "platform.json"))
        self.platform.create({"name": "standalone", "host": True, "title": "Standalone"})
        self.importer = PoiImporter(self.root, {"enabled": False}, fetcher=lambda *_: [])

    def tearDown(self):
        self.importer.stop()
        self.tmp.cleanup()

    def test_complete_document_round_trips(self):
        value = self.platform.save_liveops({
            "enabled": True,
            "shiny_rates": {"25": 0.02},
            "spawn_percent": {"25": 70, "133": 30},
            "spawn_weights": {"25": 2},
            "spawn_allow": [25, 133],
            "raid_tier_weights": {"1": 0, "3": 10, "5": 80, "6": 10},
            "raid_boss_weights": {"150": 2},
            "events": [{"name": "Raid night", "kind": "raid", "starts_ms": 10,
                        "ends_ms": 20, "species": [150], "raid_tier_weights": {"5": 100}}],
            "community_days": [{"name": "Day", "species": 25, "form": 0,
                                "shiny_rate": 0.04, "starts_ms": 10, "ends_ms": 20}],
            "shadow_overrides": {"25": {"enabled": True, "dust": 1200, "candy": 30}},
        })
        self.assertEqual(value["spawn_percent"]["25"], 70.0)
        self.assertEqual(value["raid_tier_weights"]["5"], 80.0)
        self.assertEqual(len(value["events"]), 1)
        self.assertEqual(len(value["community_days"]), 1)
        document = room_document(self.platform, None, self.importer)
        self.assertEqual(document["state"]["body"]["liveops"]["spawn_percent"]["133"], 30.0)

    def test_invalid_percentages_are_rejected(self):
        with self.assertRaises(ValueError):
            self.platform.save_liveops({"spawn_percent": {"25": 90, "133": 20}})

    def test_manual_poi_is_published_and_kept_after_clear(self):
        row = self.importer.save_row({"name": "Harbour Gym", "lat": 51.5, "lng": -0.12, "kind": 0})
        self.assertFalse(row["auto_osm"])
        self.importer.clear()
        self.assertEqual(self.importer.query()["total"], 1)
        document = room_document(self.platform, None, self.importer)
        self.assertEqual(document["state"]["body"]["poi_count"], 1)
        self.assertEqual(document["state"]["body"]["pois"][0]["name"], "Harbour Gym")

    def test_manual_spawn_can_use_coordinates_without_a_poi(self):
        world = WorldSimulator(self.root, self.importer, self.platform.liveops)
        row = world.add_spawn({"species": 25, "lat": 51.5, "lng": -0.12})
        self.assertEqual((row["lat"], row["lng"]), (51.5, -0.12))
        self.assertEqual(row["poi_id"], "")
        self.assertEqual(len(world.spawns()), 1)

    def test_world_generates_and_publishes_manual_and_automatic_activity(self):
        poi = self.importer.save_row({"name": "Harbour Gym", "lat": 51.5, "lng": -0.12, "kind": 0})
        world = WorldSimulator(self.root, self.importer, self.platform.liveops)
        manual_spawn = world.add_spawn({"species": 25, "level": 12, "poi_id": poi["id"]})
        manual_raid = world.add_raid({"species": 150, "tier": 5, "poi_id": poi["id"]})
        self.assertTrue(manual_spawn["manual"])
        self.assertTrue(manual_raid["manual"])
        world.tick(force=True)
        spawns = world.spawns()
        raids = world.raids()
        self.assertTrue(any(not row["manual"] for row in spawns))
        self.assertTrue(any(not row["manual"] for row in raids))
        document = room_document(self.platform, None, self.importer, world)
        body = document["state"]["body"]
        self.assertEqual(len(body["spawns"]), len(spawns))
        self.assertEqual(len(body["raids"]), len(raids))
        self.assertTrue(world.delete_spawn(manual_spawn["id"]))
        self.assertTrue(world.delete_raid(manual_raid["id"]))
        world.clear_spawns(auto_only=True)
        world.clear_raids(auto_only=True)
        self.assertEqual(world.spawns(), [])
        self.assertEqual(world.raids(), [])


class NoWorkspaceAdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.platform = Platform(Store(root / "platform.json"))
        self.platform.create({"name": "standalone"})
        self.importer = PoiImporter(root, {"enabled": False}, fetcher=lambda *_: [])
        self.world = WorldSimulator(root, self.importer, self.platform.liveops)
        self.server = serve(self.platform, "127.0.0.1", 0, poi_importer=self.importer,
                            world=self.world)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.importer.stop()
        self.tmp.cleanup()

    def request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())

    def test_status_and_liveops_work_without_a_server_workspace(self):
        status, value = self.request("/api/admin/status")
        self.assertEqual(status, 200)
        self.assertFalse(value["available"])
        status, value = self.request("/api/admin/liveops")
        self.assertEqual(status, 200)
        self.assertEqual(value["raid_tier_weights"]["5"], 20.0)
        status, value = self.request("/api/admin/liveops",
                                     {"settings": {"spawn_percent": {"25": 100}}})
        self.assertEqual(status, 200)
        self.assertEqual(value["spawn_percent"]["25"], 100.0)

    def test_poi_crud_works_without_a_server_workspace(self):
        status, row = self.request("/api/admin/pois",
                                   {"name": "Library", "lat": 51.5, "lng": -0.12, "kind": 1})
        self.assertEqual(status, 200)
        self.assertTrue(row["id"])
        status, value = self.request("/api/admin/pois?limit=10")
        self.assertEqual(value["total"], 1)
        status, value = self.request("/api/admin/pois/delete", {"id": row["id"]})
        self.assertTrue(value["deleted"])

    def test_world_endpoints_work_without_a_server_workspace(self):
        status, poi = self.request("/api/admin/pois",
                                   {"name": "Town Gym", "lat": 51.5, "lng": -0.12, "kind": 0})
        self.assertEqual(status, 200)
        status, value = self.request("/api/admin/world")
        self.assertEqual(status, 200)
        self.assertTrue(value["settings"]["spawns"]["enabled"])
        status, spawn = self.request("/api/admin/spawns",
                                     {"species": 25, "poi_id": poi["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(spawn["species"], 25)
        status, raid = self.request("/api/admin/raids",
                                    {"species": 150, "tier": 5, "poi_id": poi["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(raid["tier"], 5)
        status, value = self.request("/api/admin/spawns")
        self.assertEqual(len(value["spawns"]), 1)
        status, value = self.request("/api/admin/world/tick", {})
        self.assertEqual(status, 200)
        self.assertGreater(value["active_spawns"], 0)
        status, value = self.request("/api/admin/spawns/delete", {"id": spawn["id"]})
        self.assertTrue(value["deleted"])


if __name__ == "__main__":
    unittest.main()
