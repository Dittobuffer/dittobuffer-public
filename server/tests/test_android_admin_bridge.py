"""Android operator capability stays inside the trusted Python/Java bridge."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def test_admin_url_requires_running_admin_and_never_enters_status():
    # Loading the bootstrap changes these process-wide defaults on a real phone;
    # preserve the host test environment and timezone path around this check.
    import os
    import zoneinfo
    previous_tzpath = zoneinfo.TZPATH
    try:
        with patch.dict(os.environ):
            spec = importlib.util.spec_from_file_location(
                'test_android_bootstrap', ROOT / 'android/app/src/main/python/mobile_server.py')
            mobile = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mobile)
            assert mobile.admin_url() == 'http://127.0.0.1:8080/'
            mobile._state.update(admin_context=object(), admin=object())
            with patch('dittobuffer.admin.access.access', return_value=SimpleNamespace(key='test-only-capability')):
                assert mobile.admin_url() == 'http://127.0.0.1:8080/#admin=test-only-capability'
            assert 'test-only-capability' not in mobile.status()
            assert set(json.loads(mobile.status())) == {'running', 'game', 'admin', 'map'}
            mobile._state['admin'] = None
            assert mobile.admin_url() == 'http://127.0.0.1:8080/'
    finally:
        zoneinfo.reset_tzpath(previous_tzpath)
