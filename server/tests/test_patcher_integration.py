"""Normal patcher orchestration using owned synthetic files, never APK tools."""
import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dittobuffer.patcher import patch as patcher


def arm64_plugin(marker=b"owned-fixture"):
    header=bytearray(20);header[:6]=b"\x7fELF\x02\x01";header[18:20]=(183).to_bytes(2,"little")
    return bytes(header)+marker


class PatcherIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="patcher-unit-")
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.kit=patch.object(patcher,"KIT",self.root);self.kit.start();self.addCleanup(self.kit.stop)
        self.output=contextlib.redirect_stdout(io.StringIO());self.output.__enter__();self.addCleanup(self.output.__exit__,None,None,None)
        self.write("scripts/build-standalone.py",b"# owned synthetic builder")
        self.write("tools/transfer-runtime.py",b"# owned synthetic provisioner")
        self.write("builds/client-base/classes.dex",b"owned original base input")
        self.write("builds/client-base/lib/arm64-v8a/libNianticLabsPlugin.so",b"original must remain unchanged")
        self.write("builds/client-split/lib/arm64-v8a/libUnrelated.so",b"owned original split input")
        self.write("client/plugin/libNianticLabsPlugin.so",arm64_plugin())
        self.write(".private/ca/ca.pem",b"-----BEGIN CERTIFICATE-----\nowned synthetic public certificate\n-----END CERTIFICATE-----")
        self.write("config/public-profile.json",b'{"origin":"https://original-profile.invalid"}')
        self.profile={"origin":"https://127.0.0.1:8445","ca_file":".private/ca/ca.pem","routes_foreground_creation":True}
        self.write_profile()
        self.commands=[]

    def write(self,relative,data):
        path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);return path

    def write_profile(self):
        self.write("builds/transfer-public-profile.json",json.dumps(self.profile).encode())

    def args(self,**changes):
        values=dict(host="127.0.0.1",port=8445,no_provision=True,rotate_tls=False,plugin=None)
        values.update(changes);return argparse.Namespace(**values)

    def inputs(self):
        return {str(path.relative_to(self.root)):path.read_bytes()
                for folder in (self.root/"builds/client-base",self.root/"builds/client-split",self.root/"config",self.root/"client/plugin")
                for path in folder.rglob("*") if path.is_file()}

    def fake_builder(self,args):
        self.commands.append(args)
        self.assertEqual(Path(args[1]),self.root/"scripts/build-standalone.py")
        output=Path(args[args.index("--output")+1]);profile=Path(args[args.index("--profile")+1])
        self.assertTrue(output.is_relative_to(self.root/".staging"))
        self.assertEqual(profile.parent,output)
        self.assertEqual(json.loads(profile.read_text()),self.profile)
        self.assertEqual((output/"base/classes.dex").read_bytes(),b"owned original base input")
        self.assertEqual((output/"split/lib/arm64-v8a/libUnrelated.so").read_bytes(),b"owned original split input")
        expected=patcher.native_plugin().read_bytes()
        for part in ("base","split"):
            self.assertEqual((output/part/"lib/arm64-v8a/libNianticLabsPlugin.so").read_bytes(),expected)
        (output/"dittobuffer-family.apk").write_bytes(b"owned synthetic APK result")
        (output/"build.json").write_text('{"owned_synthetic_build":true}')

    def build(self,args=None):
        with patch.object(patcher,"run",side_effect=self.fake_builder):
            self.assertEqual(patcher.server(args or self.args()),0)

    def test_fresh_copy_injects_missing_library_preserves_inputs_and_tracked_profile(self):
        original=self.inputs();self.assertFalse((self.root/"apk/built").exists())
        self.build()
        self.assertEqual(self.inputs(),original)
        outputs=list((self.root/"apk/built").glob("*.apk"));self.assertEqual(len(outputs),1)
        self.assertEqual(outputs[0].read_bytes(),b"owned synthetic APK result")
        report=json.loads(outputs[0].with_suffix(".inject.json").read_text())
        self.assertEqual(report["android_install_and_gameplay"],"NOT_TESTED")
        self.assertEqual(report["sha256"],patcher.sha256(outputs[0]))
        self.assertTrue(Path(report["injected"]["profile"]).is_relative_to(self.root/".staging"))
        self.assertEqual(report["injected"]["agent_bundle"],"assets/dittobuffer/agent.js")

    def test_repeated_builds_get_new_copies_and_preserve_previous_outputs(self):
        previous=self.write("apk/built/dittobuffer-family-127.0.0.1.apk",b"existing accepted build")
        original=self.inputs();self.build();self.build()
        self.assertEqual(self.inputs(),original);self.assertEqual(previous.read_bytes(),b"existing accepted build")
        outputs=list((self.root/"apk/built").glob("*.apk"));self.assertEqual(len(outputs),3)
        directories=[Path(args[args.index("--output")+1]) for args in self.commands]
        self.assertEqual(len(set(directories)),2)

    def test_ignored_decoded_inputs_take_precedence_without_mutating_legacy(self):
        original=self.inputs()
        path=self.root/".staging/client-inputs/base"
        self.write(".staging/client-inputs/base/classes.dex",b"private authorized decoded input")
        self.assertEqual(patcher.decoded_input("base"),path)
        self.assertEqual(self.inputs(),original)

    def test_native_lookup_prioritizes_canonical_cargo_then_kit_then_legacy_build(self):
        kit=self.root/"client/plugin/libNianticLabsPlugin.so"
        self.assertEqual(patcher.native_plugin(),kit)
        built=self.write("builds/libNianticLabsPlugin.so",arm64_plugin(b"legacy build"))
        self.assertEqual(patcher.native_plugin(),kit)
        cargo=self.write("client/native/target/aarch64-linux-android/release/libNianticLabsPlugin.so",arm64_plugin(b"canonical maintained build"))
        self.assertEqual(patcher.native_plugin(),cargo)
        self.assertEqual(patcher.native_plugin("builds/libNianticLabsPlugin.so"),built)
        override=self.write(".private/owned-explicit-plugin.so",arm64_plugin(b"explicit"))
        self.assertEqual(patcher.native_plugin(".private/owned-explicit-plugin.so"),override)
        fallback=self.write("legacy-only/builds/libNianticLabsPlugin.so",arm64_plugin(b"legacy fallback"))
        with patch.object(patcher,"KIT",self.root/"legacy-only"):
            self.assertEqual(patcher.native_plugin(),fallback)
        self.build()

    def test_unrestored_lfs_or_wrong_architecture_fails_before_provisioning(self):
        original=self.inputs()
        for data,message in [(b"version https://git-lfs.github.com/spec/v1\noid sha256:fixture","LFS"),(b"not an ARM64 native library","ARM64")]:
            self.write("client/native/target/aarch64-linux-android/release/libNianticLabsPlugin.so",data)
            with patch.object(patcher,"run") as run, self.assertRaisesRegex(SystemExit,message):
                patcher.server(self.args(no_provision=False))
            run.assert_not_called();self.assertFalse((self.root/".staging").exists())
            self.assertEqual(self.inputs(),original)

    def test_missing_inputs_fail_before_provisioning_or_output_changes(self):
        with tempfile.TemporaryDirectory(prefix="empty-patcher-kit-") as folder:
            with patch.object(patcher,"KIT",Path(folder)),patch.object(patcher,"run") as run:
                with self.assertRaisesRegex(SystemExit,"plugin.*missing"):
                    patcher.server(self.args(no_provision=False))
                self.assertEqual(list(Path(folder).iterdir()),[])
                run.assert_not_called()

    def test_public_profile_rejects_account_material_endpoint_mismatch_and_private_key(self):
        cases=[({**self.profile,"accounts":[{"password":"not-to-be-packaged"}]},"credential/account"),
               ({**self.profile,"apiKey":"owned rejected fixture"},"credential/account"),
               ({**self.profile,"nested":{"password_hash":"owned rejected fixture"}},"credential/account"),
               ({**self.profile,"origin":"https://127.0.0.2:8445"},"origin differs"),
               ({**self.profile,"ca_file":"../outside.pem"},"inside the project")]
        for value,message in cases:
            self.profile=value;self.write_profile()
            with patch.object(patcher,"run") as run,self.assertRaisesRegex(SystemExit,message):patcher.server(self.args())
            run.assert_not_called();self.assertFalse((self.root/".staging").exists())
        self.profile={"origin":"https://127.0.0.1:8445","ca_file":".private/not-a-ca.pem"};self.write_profile()
        self.write(".private/not-a-ca.pem",b"-----BEGIN PRIVATE KEY-----\nowned rejected fixture")
        with patch.object(patcher,"run") as run,self.assertRaisesRegex(SystemExit,"private signing material"):patcher.server(self.args())
        run.assert_not_called();self.assertFalse((self.root/".staging").exists())

    def test_builder_failure_preserves_originals_and_retains_ignored_debug_copy(self):
        original=self.inputs()
        with patch.object(patcher,"run",side_effect=SystemExit("synthetic builder failure")):
            with self.assertRaisesRegex(SystemExit,"synthetic builder failure"):patcher.server(self.args())
        self.assertEqual(self.inputs(),original)
        self.assertEqual(len(list((self.root/".staging").glob("patcher-*"))),1)
        self.assertFalse((self.root/"apk/built").exists())

    def test_normal_provisioning_uses_source_checkout_helper_without_transfer_archive(self):
        def command(args):
            self.commands.append(args)
            self.assertEqual([str(v) for v in args[1:4]],[str(self.root/"tools/transfer-runtime.py"),"provision","--destination"])
        with patch.object(patcher,"run",side_effect=command):patcher.provision("127.0.0.1",8445,False)
        args=self.commands[0];self.assertIn("--host",args);self.assertIn("--port",args)
        self.assertNotIn("TRANSFER_MANIFEST.json",str(args))


if __name__=="__main__":unittest.main()
