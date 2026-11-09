"""Shared pytest configuration.

``tests/legacy`` holds the asset-pipeline suites that need multi-gigabyte build
artifacts (Untiy bundles, Kanto model sources, osmium extracts). They are kept
for reference but skipped unless those fixtures are mounted at ``builds/``.
"""
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

collect_ignore_glob = ["legacy/*"]


@pytest.fixture(scope='session', autouse=True)
def test_directories():
    """A fresh clone has no ignored temporary/runtime directories."""
    for name in ('.private', 'builds', 'logs'):
        (ROOT / name).mkdir(exist_ok=True)
