"""Reorganized transfer/provision paths without real TLS, accounts or devices."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
import zipfile
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("transfer_runtime_under_test", ROOT / "tools/transfer-runtime.py")
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class FakeDatabase:
    def __init__(self, owned=0):
        self.owned = owned

    @contextlib.contextmanager
    def transaction(self):
        yield self

    def execute(self, query):
        if "count(*) FROM family_accounts" in query:
            return types.SimpleNamespace(fetchone=lambda: (0,))
        if "count(*) FROM pokemon" in query:
            return types.SimpleNamespace(fetchone=lambda: (self.owned,))
        if "SELECT player_id FROM family_accounts" in query:
            return [("fixture-one",), ("fixture-two",)]
        raise AssertionError("Unexpected test SQL: " + query)


class TransferRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        original_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), original_path))

    def fixture(self, owned=0):
        (self.root / "config").mkdir()
        source = dict(database="data/normal-owned.sqlite3", profile="normal-family",
                      authentication=dict(mode="family"), network=dict(mode="loopback"),
                      client_resources=dict(enabled=True, registry="config/resources/client-runtime.json"),
                      pvp=dict(enabled=True), max_system=dict(enabled=True))
        (self.root / "config/family.json").write_text(json.dumps(source), encoding="utf8")
        world = self.root / "src/dittobuffer/world"
        world.mkdir(parents=True)
        (world / "world-style.json").write_text('{"test_owned_style":true}', encoding="utf8")
        (self.root / "data").mkdir()
        (self.root / "data/normal-owned.sqlite3").write_bytes(b"normal collection preservation sentinel")
        accounts = Mock()
        accounts.invite.return_value = "fixture-invite"
        accounts.login.return_value = dict(token="lf1_fixture_only")
        game = types.SimpleNamespace(db=FakeDatabase(owned), accounts=accounts)
        factory = Mock(return_value=game)
        module = types.ModuleType("dittobuffer.gameplay.game")
        module.Game = factory
        return source, game, factory, module

    @staticmethod
    def fake_protect(raw, decrypt=False):
        if decrypt:
            assert raw.startswith(b"test-cipher:")
            return raw[len(b"test-cipher:"):]
        return b"test-cipher:" + raw

    def configure(self, module, **kwargs):
        creator = Mock(side_effect=lambda host, path: path.mkdir(parents=True))
        with patch.dict(sys.modules, {"dittobuffer.gameplay.game": module}), \
                patch.object(runtime, "protect", side_effect=self.fake_protect), \
                patch.object(runtime, "load_module", return_value=types.SimpleNamespace(create=creator)), \
                patch.object(runtime.secrets, "token_urlsafe", return_value="fixture-password-not-a-real-secret"):
            result = runtime.configure(self.root, "127.0.0.1", 8445, **kwargs)
        return result, creator

    def test_fresh_provision_uses_new_package_and_isolated_database(self):
        source, game, factory, module = self.fixture()
        original_profile = (self.root / "config/family.json").read_bytes()
        result, creator = self.configure(module)
        generated = factory.call_args.args[0]
        self.assertEqual(generated["database"], "data/private-transfer-family.sqlite3")
        self.assertEqual(generated["profile"], "private-transfer-family")
        self.assertEqual(generated["network"]["private_key"], ".private/transfer-tls/server-key.pem")
        self.assertEqual((self.root / "config/family.json").read_bytes(), original_profile)
        self.assertEqual((self.root / "data/normal-owned.sqlite3").read_bytes(), b"normal collection preservation sentinel")
        self.assertEqual(game.accounts.register.call_count, 2)
        self.assertEqual(result["owned_pokemon"], 0)
        self.assertEqual(creator.call_count, 1)
        public = json.loads((self.root / "builds/transfer-public-profile.json").read_text())
        self.assertEqual(public["ca_file"], ".private/transfer-tls/ca.pem")
        self.assertEqual(public["runtime_registry"], "config/resources/client-runtime.json")
        self.assertTrue(public["pvp_server_combat"])
        self.assertTrue(public["max_server"])
        self.assertNotIn("password", public)
        self.assertFalse((self.root / "server").exists())

    def test_rerun_preserves_existing_private_database_and_accounts(self):
        _, game, factory, module = self.fixture()
        self.configure(module)
        game.db.owned = 12
        profile = self.root / ".private/family.json"
        saved = json.loads(profile.read_text())
        saved["database"] = "data/prior-private-collection.sqlite3"
        profile.write_text(json.dumps(saved))
        game.accounts.reset_mock()
        result, creator = self.configure(module)
        self.assertEqual(factory.call_args.args[0]["database"], "data/prior-private-collection.sqlite3")
        game.accounts.register.assert_not_called()
        creator.assert_not_called()
        self.assertEqual(result["owned_pokemon"], 12)

    def test_secure_storage_failure_precedes_any_provision_writes(self):
        with patch.object(runtime, "protect", side_effect=ValueError("DPAPI unavailable")):
            with self.assertRaisesRegex(ValueError, "DPAPI"):
                runtime.configure(self.root, "127.0.0.1", 8445)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_public_host_and_invalid_port_are_rejected_before_writes(self):
        with patch.object(runtime, "protect") as protect:
            for host, port in [("8.8.8.8", 8445), ("127.0.0.1", 0)]:
                with self.assertRaises(ValueError):
                    runtime.configure(self.root, host, port)
            protect.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_normal_provision_cli_needs_no_private_archive_manifest(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["transfer-runtime.py", "provision", "--destination", str(self.root)]), \
                patch.object(runtime, "transfer_manifest", side_effect=AssertionError("must not read private archive metadata")), \
                patch.object(runtime, "configure", return_value=dict(status="MATCH")) as configure, \
                contextlib.redirect_stdout(output):
            runtime.main()
        configure.assert_called_once_with(self.root.resolve(), "127.0.0.1", 8445, False)
        self.assertEqual(json.loads((self.root / ".transfer/restoration-report.json").read_text())["provision"], {"status": "MATCH"})
        self.assertIn("provision completed", output.getvalue())

    def test_archive_only_actions_report_missing_metadata_clearly(self):
        with patch.object(runtime, "PKG", self.root):
            self.assertEqual(runtime.transfer_manifest(required=False), {})
            with self.assertRaisesRegex(ValueError, "normal provisioning does not"):
                runtime.transfer_manifest()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_subprocesses_receive_src_and_protocol_import_paths(self):
        with patch.object(runtime.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as run:
            runtime.run(["python", "-m", "dittobuffer.runtime.http"], self.root, io.StringIO())
        environment = run.call_args.kwargs["env"]
        self.assertIn(str(self.root / "src"), environment["PYTHONPATH"])
        self.assertIn(str(self.root / "protocol"), environment["PYTHONPATH"])
        self.assertEqual(runtime.reference_path(self.root, "world-item-rules"), self.root / "src/dittobuffer/world/world-item-rules.json")
        self.assertEqual(runtime.reference_path(self.root, "pokemon-reference"), self.root / "src/dittobuffer/gameplay/pokemon-reference.json")

    def test_native_archive_prerequisites_fail_before_external_actions(self):
        with patch.object(runtime.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "client/native"):
                runtime.native(self.root, io.StringIO())
            run.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    @unittest.skipUnless(runtime.os.name == "nt", "Windows CurrentUser DPAPI is required")
    def test_dpapi_round_trip_uses_only_original_test_bytes(self):
        original = b"Original fixture bytes; no account or credential data."
        protected = runtime.protect(original)
        self.assertNotEqual(protected, original)
        self.assertEqual(runtime.protect(protected, decrypt=True), original)

    def test_native_artifact_prefers_ignored_current_build(self):
        relative = "client/native/target/aarch64-linux-android/release/libNianticLabsPlugin.so"
        built = self.root / relative
        historical = self.root / "builds/libNianticLabsPlugin.so"
        for path in (built, historical):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"Original synthetic library selection fixture")
        self.assertEqual(runtime.native_artifact(self.root), built)

    def android_fixture(self):
        files = {
            "scripts/build-standalone.py": b"# Original synthetic builder source\n",
            "builds/client-base/AndroidManifest.xml": b"<manifest/>",
            "builds/client-base/classes.dex": b"Original fixture DEX 1",
            "builds/client-base/classes2.dex": b"Original fixture DEX 2",
            "builds/client-base/classes3.dex": b"Original fixture DEX 3",
            "builds/client-split/lib/arm64-v8a/libNianticLabsPlugin.so": b"Original untouched decoded plugin fixture",
            "builds/client-split/lib/arm64-v8a/libFixture.so": b"Original untouched other library fixture",
            "client/native/target/aarch64-linux-android/release/libNianticLabsPlugin.so": b"\x7fELFOriginal synthetic replacement fixture",
            ".private/test-ca.pem": b"Original synthetic public certificate fixture",
            ".tools/local-test.jks": b"Original synthetic signer fixture; not a real key",
        }
        public = dict(origin="https://127.0.0.1:8445", ca_file=".private/test-ca.pem")
        files["builds/transfer-public-profile.json"] = json.dumps(public).encode()
        for relative, payload in files.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        return files, public

    def test_android_attempts_only_copy_to_ignored_outputs_and_preserve_inputs(self):
        files, public = self.android_fixture()

        def build(args, root, log):
            self.assertEqual(Path(args[1]), root / "scripts/build-standalone.py")
            output = Path(args[args.index("--output") + 1])
            self.assertTrue(output.is_relative_to(root / ".staging"))
            self.assertNotEqual(output, root / "builds/transfer-standalone")
            with zipfile.ZipFile(output / "dittobuffer-family.apk", "w") as archive:
                for suffix in ("", "2", "3"):
                    archive.write(root / ("builds/client-base/classes" + suffix + ".dex"), "classes" + suffix + ".dex")
                for library in (output / "split/lib/arm64-v8a").glob("*.so"):
                    archive.write(library, "lib/arm64-v8a/" + library.name)
                archive.writestr("classes4.dex", b"Original synthetic launcher fixture")
                archive.writestr("assets/dittobuffer/profile.json", json.dumps(dict(origin=public["origin"], ca_asset="dittobuffer/family-ca.pem")))
                archive.writestr("assets/dittobuffer/family-ca.pem", files[".private/test-ca.pem"])
                archive.writestr("assets/dittobuffer/libdittobuffer_runtime.config.so", '{"interaction":{"type":"script"}}')
            (output / "build.json").write_text('{"fixture":"original synthetic build"}')

        with patch.object(runtime, "java_home", return_value=self.root / "fixture-jdk"), patch.object(runtime, "run", side_effect=build):
            first = runtime.android(self.root, io.StringIO())
            first_bytes = (self.root / first["artifact"]).read_bytes()
            second = runtime.android(self.root, io.StringIO())
        self.assertNotEqual(first["artifact"], second["artifact"])
        for result in (first, second):
            self.assertTrue(result["artifact"].startswith("apk/built/"))
            self.assertTrue(result["working_directory"].startswith(".staging/"))
            self.assertTrue(result["artifact_state"].startswith(".private/"))
            self.assertEqual(result["android_install_and_gameplay"], "NOT_TESTED")
            self.assertFalse(result["fresh_signer"])
        self.assertEqual((self.root / first["artifact"]).read_bytes(), first_bytes)
        self.assertFalse((self.root / "builds/transfer-standalone").exists())
        for relative, payload in files.items():
            self.assertEqual((self.root / relative).read_bytes(), payload, relative)

    def test_android_missing_inputs_fail_before_keygen_or_output_creation(self):
        with patch.object(runtime, "java_home") as java, patch.object(runtime, "run") as run:
            with self.assertRaisesRegex(ValueError, "Missing Android build input"):
                runtime.android(self.root, io.StringIO())
            java.assert_not_called()
            run.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
