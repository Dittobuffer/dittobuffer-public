"""Platform tests: signatures, room lifecycle, bootstrap and HTTP surface."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dittobuffer_platform import presence, rsa_min
from dittobuffer_platform.bridge import Bridge
from dittobuffer_platform.rooms import Platform, normalise_domain
from dittobuffer_platform.store import Store
from dittobuffer_platform.http_api import serve
from run import _display_path, discover_server_root, make_recovery, setup


class RsaTests(unittest.TestCase):
    def test_sign_verify_and_tamper(self):
        key = rsa_min.generate()
        message = b"room-state"
        signature = rsa_min.sign(key, message)
        self.assertTrue(rsa_min.verify(rsa_min.public(key), message, signature))
        self.assertFalse(rsa_min.verify(rsa_min.public(key), b"other", signature))

    def test_envelope_replay_window(self):
        key = rsa_min.generate()
        value = rsa_min.envelope("state", "fairview", {"a": 1}, key)
        self.assertEqual(rsa_min.open_envelope(value, rsa_min.public(key), domain="fairview"),
                         {"a": 1})
        with self.assertRaises(ValueError):
            rsa_min.open_envelope(value, rsa_min.public(key), domain="other")
        tampered = dict(value)
        tampered["body"] = {"a": 2}
        with self.assertRaises(ValueError):
            rsa_min.open_envelope(tampered, rsa_min.public(key))


class RoomTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "platform.json")
        self.platform = Platform(self.store)
        self.host = rsa_min.generate()
        self.created = self.platform.create({
            "domain": "fairview", "title": "Fairview", "owner": "Ash",
            "host_public": rsa_min.public(self.host),
        })

    def tearDown(self):
        self.tmp.cleanup()

    def test_domain_is_claimed_once(self):
        with self.assertRaises(ValueError):
            self.platform.create({"domain": "fairview", "host_public": rsa_min.public(self.host)})

    def test_room_names_are_names_not_domains(self):
        # Human names are the whole interface: no DNS, tld or reserved list.
        self.assertEqual(normalise_domain("Fairview Community!"), "fairview-community")
        self.assertEqual(normalise_domain("  A B  "), "a-b")
        self.assertEqual(normalise_domain("www"), "www")
        self.assertEqual(normalise_domain("xn--bcher-kva"), "xn-bcher-kva")
        for value in ("", "a", "!!"):
            with self.assertRaises(ValueError):
                normalise_domain(value)

    def test_signed_state_only(self):
        envelope = rsa_min.envelope("state", "fairview", {"version": 1, "data": {"x": 1}}, self.host)
        self.platform.publish_state("fairview", envelope)
        self.assertEqual(self.platform.room("fairview")["state"]["version"], 1)
        forged_key = rsa_min.generate()
        forged = rsa_min.envelope("state", "fairview", {"version": 2}, forged_key)
        with self.assertRaises(ValueError):
            self.platform.publish_state("fairview", forged)

    def test_replay_rejected_and_monotonic_version(self):
        envelope = rsa_min.envelope("state", "fairview", {"version": 1}, self.host)
        self.platform.publish_state("fairview", envelope)
        with self.assertRaises(ValueError):
            self.platform.publish_state("fairview", envelope)
        stale = rsa_min.envelope("state", "fairview", {"version": 1}, self.host)
        self.platform.publish_state("fairview", stale)
        self.assertGreaterEqual(self.platform.room("fairview")["state"]["version"], 2)

    def test_bootstrap_signature_and_public_key(self):
        envelope = rsa_min.envelope("state", "fairview", {"version": 1}, self.host)
        self.platform.publish_state("fairview", envelope)
        boot = self.platform.bootstrap("fairview")
        unsigned = {key: value for key, value in boot.items() if key != "sig"}
        self.assertTrue(rsa_min.verify(boot["platform_public"], rsa_min.canonical(unsigned), boot["sig"]))
        self.assertEqual(boot["host_public"], rsa_min.public(self.host))

    def test_one_click_room_without_host_key(self):
        created = self.platform.create({"name": "Fairview Community"})
        self.assertEqual(created["room"]["domain"], "fairview-community")
        self.assertEqual(created["room"]["host_fingerprint"], "")
        # The first signed room document binds the name to its key.
        document = rsa_min.envelope("room", "fairview-community", {"title": "Fairview"}, self.host)
        self.platform.register_room("fairview-community", document)
        self.assertEqual(self.platform.room("fairview-community")["host_fingerprint"],
                         rsa_min.fingerprint(rsa_min.public(self.host)))
        other = rsa_min.generate()
        forged = rsa_min.envelope("room", "fairview-community", {"title": "Impostor"}, other)
        with self.assertRaises(ValueError):
            self.platform.register_room("fairview-community", forged)

    def test_spawn_percentage_defaults(self):
        saved = self.platform.save_defaults({"spawn_percent": {"16": 20, "66": 35, "112": 6}})
        self.assertEqual(saved["spawn_percent"], {"16": 20.0, "66": 35.0, "112": 6.0})
        self.assertEqual(self.platform.bootstrap("fairview")["defaults"]["spawn_percent"]["66"], 35.0)
        with self.assertRaises(ValueError):
            self.platform.save_defaults({"spawn_percent": {"16": 60, "66": 60}})

    def test_generated_name_when_empty(self):
        created = self.platform.create({})
        self.assertTrue(created["room"]["domain"].startswith("room-"))

    def test_only_the_hosted_room_is_available(self):
        self.platform.create({"name": "Other Room"})
        self.assertEqual(self.platform.available(), [])
        self.platform.host("fairview")
        self.assertEqual([room["domain"] for room in self.platform.available()], ["fairview"])
        with self.assertRaises(PermissionError):
            self.platform.bootstrap("other-room")
        self.assertTrue(self.platform.bootstrap("fairview")["room"]["hosted"])
        self.platform.host("other-room")
        self.assertEqual([room["domain"] for room in self.platform.available()], ["other-room"])
        with self.assertRaises(PermissionError):
            self.platform.bootstrap("fairview")

    def test_create_and_host_in_one_step(self):
        created = self.platform.create({"name": "One Click", "host": True})
        self.assertEqual(self.platform.hosted_domain, "one-click")
        self.assertTrue(created["room"]["hosted"])

    def test_unhost_hides_every_room(self):
        self.platform.host("fairview")
        self.platform.unhost("fairview")
        self.assertEqual(self.platform.available(), [])
        self.assertEqual(self.platform.hosted_domain, "")

    def test_manage_token_required_for_delete(self):
        token = self.created["manage_token"]
        with self.assertRaises(PermissionError):
            self.platform.delete("fairview", "wrong")
        self.assertTrue(self.platform.delete("fairview", token))


class PresenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "platform.json")
        self.platform = Platform(self.store)
        self.platform.create({"name": "koppis", "title": "Koppis", "host": True})

    def tearDown(self):
        self.tmp.cleanup()

    def test_document_is_signed_and_binds_the_hosted_room(self):
        document = presence.room_document(self.platform)
        self.assertEqual(document["room"], "koppis")
        self.assertEqual(document["title"], "Koppis")
        self.assertIn("liveops", document["state"]["body"])
        unsigned = {key: value for key, value in document.items() if key != "sig"}
        self.assertTrue(rsa_min.verify(document["host_public"],
                                       rsa_min.canonical(unsigned), document["sig"]))
        self.platform.register_room("koppis", document)
        self.assertEqual(self.platform.room("koppis")["host_fingerprint"],
                         document["fingerprint"])
        self.assertTrue(self.platform.public_room(self.platform.room("koppis"))["online"])

    def test_bridge_publishes_a_retained_room_document(self):
        class FakeClient:
            alive = True

            def __init__(self):
                self.messages = []

            def publish(self, topic, payload, retain=False):
                self.messages.append((topic, payload, retain))

        client = FakeClient()
        bridge = Bridge(self.platform, enabled=True,
                        room_document=lambda: presence.room_document(self.platform))
        bridge.client = client
        self.assertTrue(bridge.publish_room_now())
        self.assertEqual(len(client.messages), 1)
        topic, payload, retain = client.messages[0]
        self.assertEqual(topic, "dittobuffer/v1/koppis/room")
        self.assertTrue(retain)
        self.assertEqual(json.loads(payload)["room"], "koppis")
        self.assertGreater(self.platform.mqtt["room_published_ms"], 0)


class LayoutTests(unittest.TestCase):
    def test_source_clone_layout_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            platform_dir = base / "platform"
            platform_dir.mkdir()
            (base / "src" / "dittobuffer").mkdir(parents=True)
            (base / "config").mkdir()
            found, valid = discover_server_root("../server", platform_dir)
            self.assertTrue(valid)
            self.assertEqual(found, base.resolve())

    def test_release_layout_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            platform_dir = base / "platform"
            server = base / "server"
            platform_dir.mkdir()
            (server / "src" / "dittobuffer").mkdir(parents=True)
            (server / "config").mkdir()
            found, valid = discover_server_root("../server", platform_dir)
            self.assertTrue(valid)
            self.assertEqual(found, server.resolve())

    def test_empty_server_folder_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            platform_dir = base / "platform"
            platform_dir.mkdir()
            (base / "server").mkdir()
            _, valid = discover_server_root("../server", platform_dir)
            self.assertFalse(valid)

    def test_console_paths_do_not_echo_the_machine_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "platform" / "config.json"
            self.assertNotIn(temporary, _display_path(path))
            self.assertEqual(_display_path(path), "config.json")


class SetupTests(unittest.TestCase):
    def test_first_run_asks_for_a_room_address(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            config = {"port": 8780, "public_url": ""}
            with unittest.mock.patch("builtins.input", return_value="Fairview Community"):
                saved = setup(config, path)
            self.assertEqual(saved["room_address"], "fairview-community")
            self.assertTrue(saved["setup_complete"])
            on_disk = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["room_address"], "fairview-community")


class RecoveryTests(unittest.TestCase):
    def test_reset_rotates_key_clears_rooms_and_hosts_new_address(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            data_path = base / "data" / "platform.json"
            key_path = base / "data" / "platform-key.json"
            config_path = base / "config.json"
            data_path.parent.mkdir()
            old_key = rsa_min.generate()
            key_path.write_text(json.dumps(old_key), encoding="utf-8")
            store = Store(data_path)
            platform = Platform(store, old_key)
            old_fingerprint = platform.status()["fingerprint"]
            platform.create({"name": "old-room", "host": True})
            config = {"room_address": "old-room", "data": str(data_path),
                      "key": str(key_path), "public_url": "http://old"}
            config_path.write_text(json.dumps(config), encoding="utf-8")
            published = []
            recovery = make_recovery(config, config_path, data_path, key_path, platform,
                                     publish=lambda: published.append(True))

            result = recovery({"room_address": "new-room", "public_url": "http://new",
                               "rotate_key": True, "clear_rooms": True})

            self.assertEqual(result["room_address"], "new-room")
            self.assertEqual(platform.hosted_domain, "new-room")
            self.assertIsNone(platform.get("old-room"))
            self.assertNotEqual(result["fingerprint"], old_fingerprint)
            self.assertEqual(json.loads(key_path.read_text(encoding="utf-8"))["n"],
                             platform.host_key["n"])
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["room_address"], "new-room")
            self.assertEqual(saved["public_url"], "http://new")
            self.assertTrue(Path(result["backup"]).is_dir())
            self.assertEqual(published, [True])


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "platform.json")
        self.platform = Platform(self.store)
        self.server = serve(self.platform, "127.0.0.1", 0,
                            recovery=lambda body: {"room_address": body.get("room_address")})
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as response:
            return json.loads(response.read())

    def test_health_and_ui(self):
        self.assertEqual(self.get("/api/health")["status"], "ok")
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=5) as response:
            body = response.read()
            csp = response.headers.get("Content-Security-Policy", "")
        self.assertIn(b"Dittobuffer Admin Studio", body)
        self.assertIn(b"/assets/core.js", body)
        self.assertIn(b'rel="manifest"', body)
        self.assertIn(b"script-src 'self'", csp.encode())
        self.assertIn(b"worker-src 'self'", csp.encode())
        for name, marker in (("app.css", b"--m3-primary"), ("core.js", b"createJsonEditor"),
                             ("app.js", b"renderConfigPage"), ("pages.js", b"renderDatabasePage"),
                             ("manifest.webmanifest", b"Dittobuffer Admin Studio"),
                             ("sw.js", b"network-first"),
                             ("icon-192.png", b"\x89PNG"),
                             ("icon-512.png", b"\x89PNG"),
                             ("icon-maskable-512.png", b"\x89PNG")):
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/assets/{name}", timeout=5) as asset:
                self.assertEqual(asset.status, 200)
                if name == "sw.js":
                    self.assertEqual(asset.headers.get("Service-Worker-Allowed"), "/")
                self.assertIn(marker, asset.read())

    def test_map_tiles_are_served_same_origin(self):
        with unittest.mock.patch("dittobuffer_platform.http_api._fetch_tile",
                                 return_value=b"\x89PNG\r\n\x1a\nproxy"):
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/api/tiles/13/4210/2704.png", timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get("Content-Type"), "image/png")
                self.assertEqual(response.read(), b"\x89PNG\r\n\x1a\nproxy")
        with self.assertRaises(urllib.error.HTTPError) as outside:
            self.get("/api/tiles/1/0/2.png")
        self.assertEqual(outside.exception.code, 404)

    def test_app_protocol_endpoints_stay_json(self):
        """The app-facing platform API keeps its existing JSON contract."""
        self.assertEqual(self.get("/api/status")["version"], 1)
        self.assertEqual(self.get("/api/defaults")["defaults"], {})
        self.assertEqual(self.get("/api/rooms")["rooms"], [])

    def post(self, path, value):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                         data=json.dumps(value).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())

    def test_host_endpoint_limits_visibility(self):
        key = rsa_min.generate()
        for name in ("alpha-room", "beta-room"):
            self.post("/api/rooms", {"domain": name, "host_public": rsa_min.public(key)})
        status, created = self.post("/api/rooms/alpha-room/host", {})
        self.assertEqual(status, 200)
        self.assertTrue(created["room"]["hosted"])
        self.assertEqual([room["domain"] for room in self.get("/api/rooms")["rooms"]],
                         ["alpha-room"])
        self.assertEqual(self.get("/api/status")["hosted"], "alpha-room")
        with self.assertRaises(urllib.error.HTTPError) as missing:
            self.get("/api/rooms/beta-room/bootstrap")
        self.assertEqual(missing.exception.code, 403)
        self.post("/api/rooms/alpha-room/unhost", {})
        self.assertEqual(self.get("/api/rooms")["rooms"], [])

    def test_recovery_endpoint_works_without_server_workspace(self):
        status, value = self.post("/api/admin/recovery", {"room_address": "new-room"})
        self.assertEqual(status, 200)
        self.assertEqual(value["room_address"], "new-room")

    def test_create_bootstrap_and_unknown(self):
        key = rsa_min.generate()
        body = json.dumps({"domain": "fairview", "title": "F",
                           "host_public": rsa_min.public(key)}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/rooms", data=body,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 201)
            created = json.loads(response.read())
        self.assertTrue(created["manage_token"])
        boot = self.get("/api/rooms/fairview/bootstrap")
        self.assertEqual(boot["domain"], "fairview")
        with self.assertRaises(urllib.error.HTTPError):
            self.get("/api/rooms/missing/bootstrap")


if __name__ == "__main__":
    unittest.main()
