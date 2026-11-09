"""Prepare writable directories and verify checked-in inputs; no downloads or services."""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_power_spots(root: Path = ROOT) -> None:
    path = root / 'data/world/power-spots.sqlite3'
    if not path.is_file():
        raise ValueError('Missing tracked Power Spot index; restore it with git lfs pull '
                         '--include="data/world/power-spots.sqlite3"')
    with path.open('rb') as stream:
        if stream.read(16) != b'SQLite format 3\x00':
            raise ValueError('Power Spot index is not SQLite (possibly a Git LFS pointer); '
                             'run git lfs pull --include="data/world/power-spots.sqlite3"')
    # Reviewed ODbL candidate index already pinned by the destination repository.
    expected = 'f8a04c646038372727340aa0d3c3372bc61713664edd57b79a486eab5f05cab5'
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError('Tracked Power Spot index digest differs; preserve it and review '
                         'before selecting a different candidate index')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Verify only; create no directories')
    parser.add_argument('--regenerate-protocol', action='store_true',
                        help='Explicitly regenerate checked-in Python bindings from .proto files')
    args = parser.parse_args()
    if args.check and args.regenerate_protocol:
        parser.error('--check cannot regenerate files')
    check_power_spots()
    if not args.check:
        for name in ('builds', 'logs', 'run', '.private', 'data/admin'):
            (ROOT / name).mkdir(parents=True, exist_ok=True)
    if args.regenerate_protocol:
        subprocess.run([sys.executable, '-m', 'grpc_tools.protoc', '-Iprotocol',
                        '--python_out=protocol', 'protocol/game.proto', 'protocol/devrpc.proto'],
                       cwd=ROOT, check=True)
    print('Checkout inputs verified. No profiles, player databases, assets or devices changed.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(str(error))
