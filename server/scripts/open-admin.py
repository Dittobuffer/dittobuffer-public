# Copyright (C) Dittobuffer Contributors
# Copyright (C) chucny
# Copyright (C) gekkedavi
#
# This file is part of Dittobuffer. It is distributed under the GNU General
# Public License v3.0 (see LICENSE). The copyright notices in this file must
# be preserved in all copies, forks, and derivative works. Removing or
# altering these notices violates the license and copyright law.

"""Open the local administrator website. No password or access key exists."""
import argparse, webbrowser
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', default='http://127.0.0.1:8080')
    args = parser.parse_args()
    origin = urlsplit(args.origin)
    if (origin.scheme not in ('http', 'https') or not origin.netloc or origin.username
            or origin.password or origin.query or origin.fragment or origin.path not in ('', '/')):
        parser.error('Use an administrator website origin without credentials, paths or query')
    webbrowser.open(args.origin.rstrip('/') + '/')


if __name__ == '__main__':
    main()
