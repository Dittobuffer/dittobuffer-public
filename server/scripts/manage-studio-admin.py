#!/usr/bin/env python3
"""Set a local Studio editor/publisher without placing passwords on a command line."""
import argparse,getpass,json,os,re,secrets,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dittobuffer.admin.studio import Security

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('username');parser.add_argument('--role',choices=['viewer','editor','publisher'],required=True)
    parser.add_argument('--set-password',action='store_true',help='Prompt privately for a new password')
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',args.username):parser.error('Use 1-64 letters, digits, dots, dashes or underscores')
    file=ROOT/'.private/studio-admin.json';file.parent.mkdir(parents=True,exist_ok=True)
    users=json.loads(file.read_text(encoding='utf-8')) if file.exists() else {}
    row=dict(users.get(args.username,{}));row['role']=args.role
    if args.set_password or 'hash' not in row:
        password=getpass.getpass('New Studio password (12+ characters): ')
        if len(password)<12 or len(password)>1024:parser.error('Password must contain 12 to 1024 characters')
        if password!=getpass.getpass('Repeat password: '):parser.error('Passwords differ')
        row['salt']=secrets.token_hex(16);row['hash']=Security.hash(password,row['salt'])
    users[args.username]=row
    temporary=file.with_suffix('.tmp');temporary.write_text(json.dumps(users,indent=2)+'\n',encoding='utf-8');os.replace(temporary,file)
    print('Local Studio administrator updated. Restart the admin service to load roles and revoke existing sessions.')

if __name__=='__main__':main()
