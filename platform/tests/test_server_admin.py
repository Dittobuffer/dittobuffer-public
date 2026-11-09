"""Server workspace and admin API tests."""

from __future__ import annotations

import hashlib
import json
import signal
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dittobuffer_platform.http_api import serve
from dittobuffer_platform.rooms import Platform
from dittobuffer_platform.server_admin import ServerWorkspace, WorkspaceError
from dittobuffer_platform.store import Store


def build_workspace(base: Path) -> tuple[ServerWorkspace, Path]:
    root = base / "server"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "logs").mkdir()
    (root / "assets" / "game-master").mkdir(parents=True)
    (root / "data" / "static").mkdir()
    profile = {
        "host": "127.0.0.1",
        "port": 65531,
        "database": "data/players.sqlite3",
        "network": {"mode": "loopback"},
        "authentication": {"mode": "family", "registration": "open"},
        "profile": "private-transfer-family",
        "world": {"enabled": False},
        "raids": {"enabled": True},
        "pvp": {"enabled": True},
        "max_system": {"enabled": True},
    }
    (root / "config" / "server.json").write_text(json.dumps(profile, indent=2), encoding="utf-8")
    connection = sqlite3.connect(root / "data" / "players.sqlite3")
    connection.execute("CREATE TABLE players (id INTEGER PRIMARY KEY, name TEXT, coins INTEGER)")
    connection.execute("INSERT INTO players (name, coins) VALUES ('Ash', 100)")
    connection.commit()
    connection.close()
    gmts = b"gmts-test-payload"
    settings = b"settings-test-payload"
    (root / "assets" / "game-master" / "gmts.binpb.gz").write_bytes(gmts)
    (root / "assets" / "game-master" / "settings.binpb.gz").write_bytes(settings)
    manifest = {
        "gmts.binpb.gz": {"sha256": hashlib.sha256(gmts).hexdigest()},
        "settings.binpb.gz": {"sha256": hashlib.sha256(settings).hexdigest()},
    }
    (root / "assets" / "game-master" / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    (root / "data" / "static" / "max-reference.json").write_text("{}", encoding="utf-8")
    (root / "logs" / "server-platform.log").write_text("boot ok\n", encoding="utf-8")
    platform_config = base / "platform-config.json"
    platform_config.write_text(json.dumps({
        "name": "Test Platform", "host": "127.0.0.1", "port": 8780,
        "server_root": str(root), "server_profile": "config/server.json",
        "admin_token": "secret-token", "mqtt": {"enabled": False},
    }), encoding="utf-8")
    workspace = ServerWorkspace(root, backup_dir=base / "backups",
                                platform_config_path=platform_config)
    return workspace, platform_config


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.workspace, self.platform_config = build_workspace(self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def test_profiles_validate_and_save_with_backup(self):
        profiles = self.workspace.profiles()
        self.assertEqual(profiles["active"], "server.json")
        profile = self.workspace.profile()
        self.assertEqual(profile["validation"]["errors"], [])
        data = dict(profile["data"])
        data["motd"] = "hello"
        saved = self.workspace.save_profile("server.json", data)
        self.assertEqual(saved["data"]["motd"], "hello")
        self.assertTrue(list((self.base / "backups").rglob("server.json")))
        settings = json.loads(self.platform_config.read_text(encoding="utf-8"))
        self.assertEqual(settings["server_profile"], "config/server.json")

    def test_profile_validation_rejects_bad_port(self):
        profile = self.workspace.profile()
        data = dict(profile["data"])
        data["port"] = "not-a-port"
        with self.assertRaises(WorkspaceError):
            self.workspace.save_profile("server.json", data)
        self.assertIn("port must be an integer", self.workspace.validate_profile(data)["errors"])

    def test_paths_are_confined(self):
        for value in ("../secret", "/etc/passwd", "~/.ssh/id_rsa"):
            with self.assertRaises(WorkspaceError):
                self.workspace.document(value)
        with self.assertRaises(WorkspaceError):
            self.workspace.save_document("../escape.json", value={})

    def test_document_round_trip_and_json_validation(self):
        value = self.workspace.document("config/server.json")
        payload = value["value"]
        payload["new_key"] = {"enabled": True}
        saved = self.workspace.save_document("config/server.json", value=payload)
        self.assertIn("new_key", saved["text"])
        with self.assertRaises(WorkspaceError):
            self.workspace.save_document("config/server.json", text="{broken")
        self.assertTrue(self.workspace.documents(query="server.json")["documents"])

    def test_database_crud_schema_rows_and_sql(self):
        tables = self.workspace.tables("data/players.sqlite3")
        self.assertEqual([row["name"] for row in tables["tables"]], ["players"])
        schema = self.workspace.schema("data/players.sqlite3", "players")
        self.assertEqual(schema["primary_key"], ["id"])
        rows = self.workspace.rows("data/players.sqlite3", "players")
        self.assertEqual(rows["total"], 1)
        key = {"__rowid": rows["rows"][0]["__rowid"]}
        inserted = self.workspace.insert_row("data/players.sqlite3", "players",
                                             {"name": "Misty", "coins": 50})
        self.assertTrue(inserted["inserted"])
        self.workspace.update_row("data/players.sqlite3", "players", key, {"coins": 150})
        result = self.workspace.sql("data/players.sqlite3", "SELECT name, coins FROM players ORDER BY id")
        self.assertEqual(result["rows"][0]["coins"], 150)
        self.workspace.sql("data/players.sqlite3", "DELETE FROM players WHERE name = 'Misty'", write=True)
        self.assertEqual(self.workspace.rows("data/players.sqlite3", "players")["total"], 1)
        self.assertTrue(list((self.base / "backups").rglob("players.sqlite3")))

    def test_game_master_logs_and_backups(self):
        gm = self.workspace.game_master()
        self.assertTrue(gm["gmts_present"])
        self.assertFalse(gm["apk_present"])
        verified = [item for group in gm["groups"] for item in group["files"] if item["present"]]
        self.assertTrue(any(item["verified"] for item in verified))
        logs = self.workspace.logs()["logs"]
        self.assertEqual(logs[0]["name"], "server-platform.log")
        self.assertIn("boot ok", self.workspace.tail_log("server-platform.log")["text"])
        with self.assertRaises(WorkspaceError):
            self.workspace.tail_log("../platform.json")
        self.workspace.clear_log("server-platform.log")
        self.assertEqual(self.workspace.tail_log("server-platform.log")["text"], "")
        backups = self.workspace.backups()["backups"]
        self.assertTrue(backups)
        restored = self.workspace.restore_backup(backups[0]["backup"])
        self.assertTrue(restored["restored"].endswith(".log"))

    def test_platform_settings_never_rewrites_listener_port(self):
        self.workspace.save_platform_settings({"name": "Renamed", "mqtt": {"enabled": False}})
        value = self.workspace.platform_settings()
        self.assertEqual(value["name"], "Renamed")
        self.assertEqual(value["port"], 8780)

    def test_posix_stop_uses_sigterm(self):
        self.workspace._pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace._pid_path.write_text("4242", encoding="utf-8")
        with mock.patch.object(self.workspace, "server_status", return_value={}), \
             mock.patch.object(__import__("os"), "name", "posix"), \
             mock.patch("dittobuffer_platform.server_admin.os.kill") as kill, \
             mock.patch("dittobuffer_platform.server_admin.time.sleep"):
            result = self.workspace.stop_server()
        kill.assert_any_call(4242, signal.SIGTERM)
        self.assertFalse(self.workspace._pid_path.exists())
        self.assertEqual(result["message"], "Server stop requested")


class AdminHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.workspace, _ = build_workspace(self.base)
        store = Store(self.base / "platform-data.json")
        self.platform = Platform(store)
        self.server = serve(self.platform, "127.0.0.1", 0, workspace=self.workspace,
                            admin_token="secret-token")
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, path, value=None, method=None, token=True):
        data = json.dumps(value).encode() if value is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Platform-Token"] = "secret-token"
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                         data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())

    def test_admin_requires_token(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/admin/status", token=False)
        self.assertEqual(error.exception.code, 401)

    def test_every_admin_read_endpoint(self):
        status, value = self.request("/api/admin/status")
        self.assertEqual(status, 200)
        self.assertTrue(value["available"])
        self.assertEqual(self.request("/api/admin/profiles")[1]["active"], "server.json")
        self.assertEqual(self.request("/api/admin/profile?name=server.json")[1]["name"], "server.json")
        self.assertTrue(self.request("/api/admin/documents")[1]["documents"])
        self.assertEqual(self.request("/api/admin/document?path=config/server.json")[1]["name"], "server.json")
        self.assertIsInstance(self.request("/api/admin/backups")[1]["backups"], list)
        self.assertTrue(self.request("/api/admin/databases")[1]["databases"])
        self.assertTrue(self.request("/api/admin/db/tables?db=data/players.sqlite3")[1]["tables"])
        self.assertEqual(self.request("/api/admin/db/schema?db=data/players.sqlite3&table=players")[1]["primary_key"], ["id"])
        self.assertEqual(self.request("/api/admin/db/rows?db=data/players.sqlite3&table=players")[1]["total"], 1)
        self.assertTrue(self.request("/api/admin/game-master")[1]["gmts_present"])
        self.assertTrue(self.request("/api/admin/logs")[1]["logs"])
        self.assertIn("boot ok", self.request("/api/admin/log?name=server-platform.log")[1]["text"])
        self.assertIn("running", self.request("/api/admin/server")[1])
        self.assertEqual(self.request("/api/admin/platform")[1]["name"], "Test Platform")

    def test_every_admin_write_endpoint(self):
        profile = self.request("/api/admin/profile?name=server.json")[1]
        profile["data"]["admin_test"] = 1
        status, saved = self.request("/api/admin/profile", {"name": "server.json", "data": profile["data"]})
        self.assertEqual(status, 200)
        self.assertEqual(saved["data"]["admin_test"], 1)

        status, doc = self.request("/api/admin/document", {
            "path": "config/server.json", "value": saved["data"]})
        self.assertEqual(status, 200)
        self.assertTrue(doc["json"])

        rows = self.request("/api/admin/db/rows?db=data/players.sqlite3&table=players")[1]
        key = {"__rowid": rows["rows"][0]["__rowid"]}
        self.request("/api/admin/db/update", {
            "database": "data/players.sqlite3", "table": "players", "key": key,
            "values": {"coins": 101}})
        result = self.request("/api/admin/db/sql", {
            "database": "data/players.sqlite3", "statement": "SELECT coins FROM players", "write": False})
        self.assertEqual(result[1]["rows"][0]["coins"], 101)
        self.request("/api/admin/db/insert", {
            "database": "data/players.sqlite3", "table": "players",
            "values": {"name": "Brock", "coins": 10}})
        self.request("/api/admin/db/delete", {
            "database": "data/players.sqlite3", "table": "players",
            "key": {"name": "Brock"}})
        self.request("/api/admin/platform", {"name": "Live Test", "mqtt": {"enabled": False}})
        self.assertEqual(self.request("/api/admin/platform")[1]["name"], "Live Test")
        self.request("/api/admin/logs/clear", {"name": "server-platform.log"})
        self.assertEqual(self.request("/api/admin/log?name=server-platform.log")[1]["text"], "")
        backups = self.request("/api/admin/backups")[1]["backups"]
        self.request("/api/admin/restore", {"backup": backups[0]["backup"]})

    def test_admin_room_management_bypasses_hosted_filter(self):
        self.request("/api/rooms", {"name": "Alpha Room"})
        self.request("/api/rooms", {"name": "Beta Room"})
        status, listing = self.request("/api/admin/rooms")
        self.assertEqual(status, 200)
        self.assertEqual(len(listing["rooms"]), 2)
        domain = listing["rooms"][0]["domain"]
        status, detail = self.request("/api/admin/rooms/" + domain)
        self.assertEqual(status, 200)
        self.assertEqual(detail["room"]["domain"], domain)
        self.assertIn("events", detail)


if __name__ == "__main__":
    unittest.main()
