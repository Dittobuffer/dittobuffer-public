"""Open the existing private administrator website without a Studio password."""
import argparse,json,sys,webbrowser
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--origin',default='http://127.0.0.1:8080');args=parser.parse_args()
    from urllib.parse import urlsplit
    origin=urlsplit(args.origin)
    if origin.scheme not in ('http','https') or not origin.netloc or origin.username or origin.password or origin.query or origin.fragment or origin.path not in ('','/'):
        parser.error('Use an administrator website origin without credentials, paths or query')
    path=ROOT/'.private/admin-website-access.json'
    if not path.exists():raise SystemExit('Start the administrator service once to create its private access key.')
    key=json.loads(path.read_text(encoding='utf8'))['access_key']
    webbrowser.open(args.origin.rstrip('/')+'/#admin='+key)
    print('Private administrator website opened. Do not share its access link.')
if __name__=='__main__':main()
