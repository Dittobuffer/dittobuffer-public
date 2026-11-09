"""Mocked startup and device safety checks; never contact ADB or live services."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from dittobuffer.cli import services
from dittobuffer.cli.console import Console


SOURCE_ROOT = Path(__file__).resolve().parents[1]


class ServiceSafetyTests(unittest.TestCase):
    def setUp(self):
        (SOURCE_ROOT/'.private').mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=SOURCE_ROOT/'.private')
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.patches = patch.multiple(services, ROOT=self.root, SRC=self.root/'src',
                                     RUN=self.root/'run', LOGS=self.root/'logs', TOOLS=self.root/'tools')
        self.patches.start()
        self.addCleanup(self.patches.stop)
        self.environment = patch.dict(os.environ, {'DITTOBUFFER_ADB_SERIAL':''})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.manager = services.ServiceManager(config=self.root/'family.json')

    def mock_adb(self, listing):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            output = listing() if command[1:] == ['devices', '-l'] else ''
            return subprocess.CompletedProcess(command, 0, output, '')
        return calls, run

    def test_unique_phone_is_pinned_and_every_device_command_has_serial(self):
        listing = ['List of devices attached\nPIXEL device usb:1 model:Pixel_5\n']
        calls, run = self.mock_adb(lambda:listing[0])
        with patch.object(services.shutil, 'which', return_value='adb'), patch.object(services.subprocess, 'run', side_effect=run):
            self.manager.adb('shell', 'getprop', 'ro.product.model')
            self.assertEqual(calls[-1][:3], ['adb', '-s', 'PIXEL'])
            listing[0] = 'List of devices attached\nSECOND device usb:2 model:Other\n'
            with self.assertRaisesRegex(services.DeviceSelectionError, 'no other phone was chosen'):
                self.manager.adb('reverse', 'tcp:8445', 'tcp:8445')
        self.assertEqual(sum('shell' in command for command in calls), 1)
        self.assertFalse(any('reverse' in command for command in calls))

    def test_multiple_or_only_emulated_devices_never_select_a_phone(self):
        for listing in ('List of devices attached\nA device usb:1\nB device usb:2\n',
                        'List of devices attached\nemulator-5554 device product:sdk\n',
                        'List of devices attached\nA unauthorized usb:1\n'):
            calls, run = self.mock_adb(lambda:listing)
            with self.subTest(listing=listing), patch.object(services.subprocess, 'run', side_effect=run), patch.object(self.manager, 'start') as start:
                result = self.manager.reconnect()
                self.assertIn('no unique physical device', result[0])
                start.assert_not_called()
                self.assertTrue(all(command[1:] == ['devices', '-l'] for command in calls))

    def test_map_origin_tunnel_is_scoped_to_selected_device(self):
        self.manager.config.write_text(json.dumps({'world': {'map_tiles_port': 8081}}))
        calls, run = self.mock_adb(lambda:'List of devices attached\nPIXEL device usb:1\n')
        with patch.object(services.subprocess, 'run', side_effect=run):
            self.manager.reverse()
        self.assertEqual(sum(command[3:] == ['reverse', 'tcp:8081', 'tcp:8081'] for command in calls), 1)
        self.assertTrue(all(command[1:3] == ['-s', 'PIXEL'] for command in calls if 'reverse' in command))

    def test_explicit_unavailable_serial_never_falls_back_to_other_phone(self):
        self.manager.serial = 'MISSING'
        with patch.object(self.manager, 'devices', return_value=['OTHER']), patch.object(services.subprocess, 'run') as run:
            with self.assertRaisesRegex(services.DeviceSelectionError, 'unavailable'):
                self.manager.adb('shell', 'am', 'start', '-n', 'anything')
            run.assert_not_called()

    def test_global_adb_resets_are_rejected(self):
        with patch.object(services.subprocess, 'run') as run:
            for command in ('kill-server', 'start-server'):
                with self.assertRaises(services.DeviceSelectionError):
                    self.manager.adb(command)
            run.assert_not_called()

    def test_reconnect_scopes_tunnels_and_only_relaunches_development_package(self):
        calls, run = self.mock_adb(lambda:'List of devices attached\nPIXEL device usb:1\n')
        with patch.object(services.subprocess, 'run', side_effect=run), patch.object(self.manager, 'start', return_value=(True, 'relay started')), patch.object(services.time, 'sleep'):
            self.manager.reconnect()
        device_calls = [command for command in calls if command[1:] != ['devices', '-l']]
        self.assertTrue(device_calls)
        self.assertTrue(all(command[1:3] == ['-s', 'PIXEL'] for command in device_calls))
        self.assertTrue(any(command[3:] == ['reverse', 'tcp:8445', 'tcp:8445'] for command in device_calls))
        app_calls = [command for command in device_calls if 'am' in command]
        self.assertEqual(len(app_calls), 2)
        self.assertTrue(all(services.APP_ID in ' '.join(command) for command in app_calls))
        self.assertFalse(any('--remove-all' in command or 'clear' in command or 'uninstall' in command for command in calls))

    def test_busy_unowned_port_does_not_start_or_kill_any_process(self):
        with patch.object(services, 'port_open', return_value=True), patch.object(services.subprocess, 'Popen') as popen, patch.object(services.subprocess, 'run') as run:
            ok, message = self.manager.start('server')
            self.assertFalse(ok)
            self.assertIn('left untouched', message)
            popen.assert_not_called()
            run.assert_not_called()

    def test_unverified_live_pid_record_is_not_overwritten_even_with_free_port(self):
        spec = self.manager.spec('server')
        spec.pid_file.write_text('12345')
        with patch.object(services, '_pid_alive', return_value=True), patch.object(services, 'port_open', return_value=False), patch.object(services.subprocess, 'Popen') as popen:
            ok, message = self.manager.start('server')
            self.assertFalse(ok)
            self.assertIn('record left untouched', message)
            self.assertEqual(spec.pid_file.read_text(), '12345')
            popen.assert_not_called()

    def test_gnirehtet_command_uses_selected_serial_and_records_process_identity(self):
        executable = services.TOOLS/'gnirehtet'/('gnirehtet.exe' if os.name == 'nt' else 'gnirehtet')
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b'owned-test-fixture-not-executable')
        process = Mock(pid=12345)
        with patch.object(services, 'port_open', side_effect=[False, True]), patch.object(self.manager, 'select_device', return_value='PIXEL'), patch.object(services, '_process_identity', return_value='exact-lifetime'), patch.object(services.subprocess, 'Popen', return_value=process) as popen:
            ok, _ = self.manager.start('gnirehtet')
        self.assertTrue(ok)
        self.assertEqual(popen.call_args.args[0][-2:], ['run', 'PIXEL'])
        record = json.loads(self.manager.spec('gnirehtet').pid_file.read_text())
        self.assertEqual(record['identity'], 'exact-lifetime')
        self.assertEqual(record['root'], str(self.root.resolve()))
        if os.name == 'nt':
            self.assertTrue(popen.call_args.kwargs['creationflags'] & 0x8, 'background process is detached')

    def record(self, identity='old-process'):
        spec = self.manager.spec('server')
        spec.pid_file.write_text(json.dumps({'pid':12345,'identity':identity,'root':str(self.root.resolve()),
                                             'service':'server','command':spec.command}))
        return spec

    def test_legacy_or_reused_pid_cannot_terminate_other_process(self):
        spec = self.manager.spec('server')
        for record in ('12345', json.dumps({'pid':12345,'identity':'old-process','root':str(self.root.resolve()),
                                           'service':'server','command':spec.command})):
            spec.pid_file.write_text(record)
            with self.subTest(record=record), patch.object(services, '_pid_alive', return_value=True), patch.object(services, '_process_identity', return_value='new-process'), patch.object(services.subprocess, 'run') as run:
                ok, message = self.manager.stop('server')
                self.assertFalse(ok)
                self.assertIn('ownership', message)
                self.assertEqual(spec.pid_file.read_text(), record)
                run.assert_not_called()

    def test_verified_stop_targets_only_owned_pid_without_process_tree_kill(self):
        spec = self.record('exact-lifetime')
        with patch.object(services, '_pid_alive', side_effect=[True, False, False]), patch.object(services, '_process_identity', return_value='exact-lifetime'), patch.object(services.subprocess, 'run') as run:
            self.assertTrue(self.manager.stop('server')[0])
        command = run.call_args.args[0]
        self.assertEqual(command[-1], '12345')
        self.assertNotIn('/IM', command)
        self.assertNotIn('/T', command)
        self.assertFalse(spec.pid_file.exists())

    def test_refused_stop_also_blocks_restart(self):
        with patch.object(self.manager, 'stop', return_value=(False, 'ownership not verified')), patch.object(self.manager, 'start') as start:
            self.assertEqual(self.manager.restart('server'), ['ownership not verified'])
            start.assert_not_called()

    def test_device_inventory_reads_each_explicit_serial(self):
        with patch.object(self.manager, 'devices', return_value=['FIRST','SECOND']), patch.object(self.manager, 'adb', return_value=subprocess.CompletedProcess([],0,'fixture','')) as adb:
            Console(self.manager, self.manager.config).cmd_devices([])
        self.assertEqual([call.kwargs['serial'] for call in adb.call_args_list], ['FIRST','FIRST','SECOND','SECOND'])

    def test_doctor_and_safe_batch_do_not_reset_devices_or_kill_by_name_or_port(self):
        with patch.object(services.subprocess, 'run') as run, patch.object(services.subprocess, 'Popen') as popen:
            self.assertTrue(services.doctor(self.manager.config))
            run.assert_not_called()
            popen.assert_not_called()
        # main replaced the Windows batch file with a POSIX launcher. Execute
        # its embedded Python against a mock manager: only owned server/admin
        # starts are allowed; no device, process or port cleanup is performed.
        launcher = (SOURCE_ROOT/'start-server.sh').read_text()
        for forbidden in ('taskkill','kill-server','--remove-all','netstat','stop-process','del /q'):
            self.assertNotIn(forbidden, launcher.lower())
        embedded = launcher.split(".venv/bin/python - <<'PY'",1)[1].split('\nPY',1)[0]
        with patch.object(services,'ServiceManager') as factory:
            factory.return_value.start.return_value=(True,'isolated mock start')
            exec(compile(embedded,'start-server.sh','exec'),{})
            self.assertEqual(factory.return_value.method_calls,[unittest.mock.call.start('server'),unittest.mock.call.start('admin')])


if __name__ == '__main__':
    unittest.main()
