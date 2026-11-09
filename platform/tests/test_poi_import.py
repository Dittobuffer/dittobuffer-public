"""Platform POI importer tests: no network, no background surprises."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dittobuffer_platform.http_api import serve
from dittobuffer_platform.pois import PoiImporter
from dittobuffer_platform.rooms import Platform
from dittobuffer_platform.store import Store


def place(identity: str, name: str, kind: int = 1) -> dict:
    return {
        "id": identity,
        "name": name,
        "lat": 51.5074,
        "lng": -0.1278,
        "kind": kind,
        "category": "gym" if kind == 0 else "stop",
        "score": 100,
        "status": "APPROVED",
        "reason": "OpenStreetMap leisure",
        "tags": json.dumps({"osm": "leisure", "name": name}),
        "photo_status": "PHOTO_PENDING",
        "auto_osm": True,
    }


class PoiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fetched = [place("admin:osm:node/1", "Harbour Park", 0),
                        place("admin:osm:node/2", "Corner Cafe", 1)]
        self.importer = PoiImporter(
            self.root,
            {"enabled": True, "lat": 51.5074, "lng": -0.1278,
             "radius_m": 1200, "interval_s": 60, "limit": 80, "gym_percent": 20},
            fetcher=lambda lat, lng, settings: [dict(row) for row in self.fetched],
        )

    def tearDown(self):
        self.importer.stop()
        self.tmp.cleanup()

    def test_scan_merges_and_stable_roles(self):
        status = self.importer.scan_once()
        self.assertEqual(status["auto_pois"], 2)
        self.assertEqual(status["auto_gyms"], 1)
        self.assertEqual(status["auto_stops"], 1)
        self.assertEqual(status["last_error"], "")
        # A later scan cannot turn an existing stop into a gym.
        self.fetched = [place("admin:osm:node/2", "Corner Cafe", 0)]
        self.importer.scan_once()
        types = {row["id"]: row["kind"] for row in self.importer.query()["pois"]}
        self.assertEqual(types["admin:osm:node/2"], 1)

    def test_query_distance_kind_and_text(self):
        self.importer.scan_once()
        result = self.importer.query(lat=51.5074, lng=-0.1278, radius=5000,
                                     kind="gym", q="harbour")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["pois"][0]["name"], "Harbour Park")
        self.assertGreaterEqual(result["pois"][0]["distance_m"], 0)
        with self.assertRaises(ValueError):
            self.importer.query(lat=51.5074)

    def test_disable_clears_generated_rows_and_settings_persist(self):
        self.importer.scan_once()
        self.assertTrue(self.importer.status()["auto_pois"])
        self.importer.save({"enabled": False})
        self.assertEqual(self.importer.status()["auto_pois"], 0)
        saved = json.loads((self.root / "poi-import.json").read_text(encoding="utf8"))
        self.assertFalse(saved["enabled"])
        with self.assertRaises(ValueError):
            self.importer.save({"lat": 999, "lng": 20})
        with self.assertRaises(ValueError):
            self.importer.save({"radius_m": "not-a-number"})

    def test_settings_are_clamped(self):
        value = self.importer.save({"radius_m": 99999, "interval_s": 1,
                                    "limit": 1, "gym_percent": 900})
        self.assertEqual(value["radius_m"], 5000)
        self.assertEqual(value["interval_s"], 15.0)
        self.assertEqual(value["limit"], 10)
        self.assertEqual(value["gym_percent"], 100.0)

    def test_default_centre_means_import_needs_no_opt_in(self):
        importer = PoiImporter(self.root, {"enabled": True}, fetcher=lambda *_: [])
        status = importer.status()
        self.assertEqual(status["center"], [52.3676, 4.9041])
        self.assertEqual(status["auto_pois"], 0)
        importer.stop()

    def test_an_explicitly_empty_centre_waits_for_the_admin(self):
        importer = PoiImporter(self.root, {"enabled": True, "center": None},
                               fetcher=lambda *_: [])
        status = importer.scan_once()
        self.assertIn("centre", status["last_error"])
        self.assertEqual(status["auto_pois"], 0)
        importer.stop()


class PoiHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.importer = PoiImporter(
            root,
            {"enabled": False, "lat": 51.5074, "lng": -0.1278},
            fetcher=lambda lat, lng, settings: [place("admin:osm:node/3", "Library", 0)],
        )
        platform = Platform(Store(root / "platform.json"))
        self.server = serve(platform, "127.0.0.1", 0, poi_importer=self.importer)
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

    def test_status_settings_and_listing(self):
        status, value = self.request("/api/poi-import")
        self.assertEqual(status, 200)
        self.assertFalse(value["enabled"])
        status, value = self.request("/api/poi-import", {
            "enabled": True, "lat": 51.5074, "lng": -0.1278,
            "radius_m": 1200, "interval_s": 60, "limit": 80, "gym_percent": 20,
        })
        self.assertEqual(status, 200)
        self.assertTrue(value["enabled"])
        status, value = self.request("/api/pois?limit=10")
        self.assertEqual(status, 200)
        self.assertEqual(value["pois"], [])

    def test_scan_and_clear(self):
        self.request("/api/poi-import", {"enabled": True, "lat": 51.5074,
                                        "lng": -0.1278, "interval_s": 15})
        status, _ = self.request("/api/poi-import/scan", {})
        self.assertEqual(status, 202)
        # The scan runs on the worker; clear invalidates it either way.
        status, value = self.request("/api/poi-import/clear", {})
        self.assertEqual(status, 200)
        self.assertEqual(value["auto_pois"], 0)


if __name__ == "__main__":
    unittest.main()
