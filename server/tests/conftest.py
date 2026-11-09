# Copyright (C) Dittobuffer Contributors
# Copyright (C) chucny
# Copyright (C) gekkedavi
#
# This file is part of Dittobuffer. It is distributed under the GNU General
# Public License v3.0 (see LICENSE). The copyright notices in this file must
# be preserved in all copies, forks, and derivative works. Removing or
# altering these notices violates the license and copyright law.

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
