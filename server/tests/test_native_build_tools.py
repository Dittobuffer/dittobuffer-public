"""Synthetic native-tool safety checks; no WSL, downloads, builds or game assets."""
import importlib.util
import contextlib
import io
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ndk = load("fix-ndk-modes")
verify = load("verify-native")


class NativeBuildToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tools = self.root / ".tools"
        self.tools.mkdir()
        self.archive = self.tools / "ndk.zip"

    def archive_with(self, entries):
        with zipfile.ZipFile(self.archive, "w") as archive:
            for name, payload, mode in entries:
                info = zipfile.ZipInfo(name)
                info.filename = name  # Retain deliberately hostile spellings on Windows.
                info.create_system = 3
                info.external_attr = mode << 16
                archive.writestr(info, payload)
        return self.archive

    def test_shell_helpers_use_pinned_channel_and_ignored_native_target(self):
        for name in ("setup-native.sh", "build-native.sh", "test-native.sh"):
            content = (ROOT / "scripts" / name).read_text()
            self.assertIn("client/native/rust-toolchain.toml", content)
            self.assertNotIn("/mnt/e", content)
            self.assertNotIn("client/librenlp", content)
            self.assertNotIn("builds/native-target", content)
            if name != "setup-native.sh":
                self.assertIn('CARGO_TARGET_DIR="$ROOT/client/native/target"', content)
        setup = (ROOT / "scripts/setup-native.sh").read_text()
        self.assertIn('mkdir -p "$ROOT/.tools"', setup)
        self.assertIn('fix-ndk-modes.py" --extract', setup)

    def test_extract_original_fixture_and_idempotent_mode_repair(self):
        path = self.archive_with([("android-ndk-r27d/bin/tool", b"Original fixture bytes", stat.S_IFREG | 0o755)])
        self.assertEqual(ndk.repair(path, self.tools, extract=True), 1)
        destination = self.tools / "android-ndk-r27d/bin/tool"
        self.assertEqual(destination.read_bytes(), b"Original fixture bytes")
        self.assertEqual(ndk.repair(path, self.tools), 1)

    def test_archive_traversal_and_absolute_windows_paths_fail_before_writes(self):
        for name in ("../outside", "/outside", "C:/outside", "android-ndk-r27d/../../outside", "android-ndk-r27d\\outside"):
            with self.subTest(name=name):
                self.archive_with([(name, b"bad", stat.S_IFREG | 0o644)])
                with self.assertRaises(ValueError):
                    ndk.repair(self.archive, self.tools, extract=True)
                self.assertFalse((self.tools / "android-ndk-r27d").exists())

    def test_duplicate_special_file_and_escaping_symlinks_are_rejected(self):
        examples = [
            [("android-ndk-r27d/a", b"a", stat.S_IFREG | 0o644), ("android-ndk-r27d/A", b"b", stat.S_IFREG | 0o644)],
            [("android-ndk-r27d/fifo", b"", stat.S_IFIFO | 0o644)],
            [("android-ndk-r27d/link", b"../../outside", stat.S_IFLNK | 0o777)],
            [("android-ndk-r27d/link", b"/absolute", stat.S_IFLNK | 0o777)],
        ]
        for entries in examples:
            with self.subTest(entries=entries):
                self.archive_with(entries)
                with self.assertRaises(ValueError):
                    ndk.repair(self.archive, self.tools, extract=True)
                self.assertFalse((self.tools / "android-ndk-r27d").exists())

    def test_symlink_cycles_and_members_under_links_are_rejected(self):
        examples = [
            [("android-ndk-r27d/a", b"b", stat.S_IFLNK | 0o777), ("android-ndk-r27d/b", b"a", stat.S_IFLNK | 0o777)],
            [("android-ndk-r27d/a", b"directory", stat.S_IFLNK | 0o777), ("android-ndk-r27d/a/data", b"x", stat.S_IFREG | 0o644)],
        ]
        for entries in examples:
            self.archive_with(entries)
            with self.assertRaises(ValueError):
                ndk.repair(self.archive, self.tools, extract=True)
            self.assertFalse((self.tools / "android-ndk-r27d").exists())

    def test_existing_different_file_is_preserved_before_extracting_others(self):
        self.archive_with([("android-ndk-r27d/new", b"new", stat.S_IFREG | 0o644), ("android-ndk-r27d/owned", b"archive", stat.S_IFREG | 0o644)])
        owned = self.tools / "android-ndk-r27d/owned"
        owned.parent.mkdir()
        owned.write_bytes(b"preserve user tool")
        with self.assertRaisesRegex(ValueError, "preserved"):
            ndk.repair(self.archive, self.tools, extract=True)
        self.assertEqual(owned.read_bytes(), b"preserve user tool")
        self.assertFalse((owned.parent / "new").exists())

    def test_symlink_placeholder_survives_permission_failure(self):
        self.archive_with([("android-ndk-r27d/link", b"target", stat.S_IFLNK | 0o777)])
        placeholder = self.tools / "android-ndk-r27d/link"
        placeholder.parent.mkdir()
        placeholder.write_bytes(b"target")
        with patch.object(Path, "symlink_to", side_effect=PermissionError("synthetic denied permission")):
            with self.assertRaises(PermissionError):
                ndk.repair(self.archive, self.tools)
        self.assertEqual(placeholder.read_bytes(), b"target")
        self.assertEqual(list(placeholder.parent.iterdir()), [placeholder])

    def test_archive_outside_tools_is_rejected(self):
        path = self.root / "outside.zip"
        with zipfile.ZipFile(path, "w"):
            pass
        with self.assertRaisesRegex(ValueError, "inside project .tools"):
            ndk.repair(path, self.tools, extract=True)

    def test_abi_checks_reject_wrong_architecture_exports_and_soname(self):
        good = dict(architecture="EM_AARCH64", exports=list(verify.REQUIRED), soname=["libNianticLabsPlugin.so"])
        verify.validate(good)
        for bad in [dict(good, architecture="EM_X86_64"), dict(good, exports=[]), dict(good, soname=[])]:
            with self.assertRaises(ValueError):
                verify.validate(bad)

    def test_reports_are_exclusive_and_confined_to_ignored_run(self):
        report = self.root / "run/native.json"
        verify.write_report(self.root, report, dict(status="fixture"))
        original = report.read_bytes()
        with self.assertRaises(FileExistsError):
            verify.write_report(self.root, report, dict(status="replacement"))
        self.assertEqual(report.read_bytes(), original)
        with self.assertRaises(ValueError):
            verify.write_report(self.root, self.root / "docs/native-build.json", {})
        self.assertFalse((self.root / "docs").exists())

    def test_verifier_no_report_has_no_writes_or_implicit_original_lookup(self):
        result = dict(architecture="EM_AARCH64", exports=list(verify.REQUIRED), soname=["libNianticLabsPlugin.so"])
        with patch.object(verify, "inspect", return_value=result) as inspect, \
                patch.object(verify, "write_report", side_effect=AssertionError("must not write")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(verify.main(["--artifact", str(self.root / "fixture.so"), "--no-report"]), 0)
        inspect.assert_called_once_with((self.root / "fixture.so").resolve())

    def test_verifier_rejects_pointer_stubs_without_writing_a_report(self):
        pointer = self.root / "pointer.so"
        pointer.write_bytes(b"version https://git-lfs.github.com/spec/v1\n")
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as raised:
            verify.main(["--artifact", str(pointer), "--no-report"])
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("Native verification failed", error.getvalue())
        self.assertFalse((self.root / "run").exists())


if __name__ == "__main__":
    unittest.main()
