"""Invite-only family identities over the existing player storage.

Enabled explicitly; legacy localhost development databases are never migrated.
Bearer values are returned only at issuance, and only their SHA256 is stored.
"""
import hashlib
import json
import re
import secrets
import threading
import time
import unicodedata
from contextlib import closing
from contextvars import ContextVar

principal_session=ContextVar('family_principal_session',default=None)
from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

SCHEMA='''
CREATE TABLE IF NOT EXISTS family_accounts(
 id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,player_id TEXT UNIQUE NOT NULL REFERENCES players(id),
 password_hash TEXT NOT NULL,created_ms INTEGER NOT NULL,disabled INTEGER NOT NULL DEFAULT 0,
 pokemon_capacity INTEGER NOT NULL,item_capacity INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS family_invites(
 digest TEXT PRIMARY KEY,created_ms INTEGER NOT NULL,expires_ms INTEGER NOT NULL,
 consumed_ms INTEGER,account_id TEXT REFERENCES family_accounts(id),revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS family_sessions(
 digest TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES family_accounts(id),kind TEXT NOT NULL,
 parent TEXT REFERENCES family_sessions(digest),created_ms INTEGER NOT NULL,expires_ms INTEGER NOT NULL,
 revoked_ms INTEGER NOT NULL DEFAULT 0,label TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS family_sessions_account ON family_sessions(account_id,revoked_ms);
CREATE TABLE IF NOT EXISTS family_auth_limits(bucket TEXT PRIMARY KEY,start_ms INTEGER NOT NULL,count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS family_audit(id INTEGER PRIMARY KEY,at_ms INTEGER NOT NULL,account_id TEXT,event TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS family_battle_sessions(battle_id TEXT NOT NULL,generation INTEGER NOT NULL,session_digest TEXT NOT NULL REFERENCES family_sessions(digest),PRIMARY KEY(battle_id,generation));
'''

def stamp():return time.time_ns()//1_000_000
def digest(value):return hashlib.sha256(value.encode('utf8')).hexdigest()
def password_bytes(value):
    if not isinstance(value,str) or not 12<=len(value)<=128:raise AccountError('Use a password of 12–128 characters')
    return value.encode('utf8')
def username(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{2,31}',value):raise AccountError('Invalid username')
    return value.lower()
def hash_password(value):
    return Argon2id(salt=secrets.token_bytes(16),length=32,iterations=2,lanes=1,memory_cost=19456).derive_phc_encoded(password_bytes(value))

class AccountError(Exception):pass

class Accounts:
    def __init__(self,game):
        self.game=game;self.cfg=game.config.get('authentication',{})
        self.lifetime=int(self.cfg.get('credential_seconds',604800))
        self.rpc_lifetime=int(self.cfg.get('session_seconds',3600))
        if not 300<=self.rpc_lifetime<=86400 or not self.rpc_lifetime<=self.lifetime<=2592000:raise ValueError('Invalid family token expiry')
        self.workers=threading.BoundedSemaphore(2)
        # A random dummy hash gives unknown usernames the same password work.
        self.dummy=hash_password(secrets.token_urlsafe(32))
        with closing(game.db.connect()) as db:db.executescript(SCHEMA)

    def audit(self,db,event,account=None):
        db.execute('INSERT INTO family_audit(at_ms,account_id,event) VALUES(?,?,?)',(stamp(),account,event))
        db.execute('DELETE FROM family_audit WHERE id NOT IN (SELECT id FROM family_audit ORDER BY id DESC LIMIT 10000)')

    def invite(self,seconds=86400):
        if type(seconds) is not int or not 300<=seconds<=604800:raise AccountError('Invalid invitation expiry')
        token='invite_'+secrets.token_urlsafe(32);now=stamp()
        with self.game.db.transaction() as db:
            db.execute('INSERT INTO family_invites(digest,created_ms,expires_ms) VALUES(?,?,?)',(digest(token),now,now+seconds*1000))
            self.audit(db,'invite-created')
        return token

    def limit(self,name,peer):
        # Called in its own committed transaction: failed auth must retain the limit.
        now=stamp();window=300000
        with self.game.db.transaction() as db:
            db.execute('DELETE FROM family_auth_limits WHERE start_ms<?',(now-window,))
            for key,maximum in [('account:'+digest(name),12),('peer:'+digest(peer),40)]:
                row=db.execute('SELECT * FROM family_auth_limits WHERE bucket=?',(key,)).fetchone()
                if row and row['count']>=maximum:raise AccountError('Authentication temporarily limited')
                db.execute('INSERT INTO family_auth_limits VALUES(?,?,1) ON CONFLICT(bucket) DO UPDATE SET count=count+1',(key,now))

    def register(self,login,password,trainer,invitation,peer='local'):
        login=username(login);raw=password_bytes(password)
        trainer=unicodedata.normalize('NFC',trainer) if isinstance(trainer,str) else ''
        if not re.fullmatch(r'[\w -]{2,20}',trainer) or not trainer.strip():raise AccountError('Invalid trainer name')
        open_registration=self.cfg.get('registration','invite')=='open'
        invitation='' if invitation is None else invitation
        if not isinstance(invitation,str) or len(invitation)>200:raise AccountError('Invalid invitation')
        self.limit(login,peer)
        if not self.workers.acquire(blocking=False):raise AccountError('Authentication temporarily busy')
        try:encoded=hash_password(password)
        finally:self.workers.release()
        now=stamp();account=secrets.token_hex(16);player=secrets.token_hex(32)
        with self.game.db.transaction() as db:
            if open_registration:
                invite=None
            else:
                invite=db.execute('SELECT * FROM family_invites WHERE digest=?',(digest(invitation),)).fetchone()
                if not invite or invite['revoked'] or invite['consumed_ms'] is not None or invite['expires_ms']<=now:raise AccountError('Invitation is invalid or expired')
            if db.execute('SELECT 1 FROM family_accounts WHERE username=?',(login,)).fetchone():raise AccountError('Username is unavailable')
            self.game.create_player(db,player,trainer)
            db.execute('INSERT INTO family_accounts VALUES(?,?,?,?,?,0,?,?)',(account,login,player,encoded,now,self.game.config['pokemon_capacity'],self.game.config['item_capacity']))
            if invite is not None:
                db.execute('UPDATE family_invites SET consumed_ms=?,account_id=? WHERE digest=?',(now,account,digest(invitation)))
            self.audit(db,'registered-open' if open_registration else 'registered',account)
        return dict(account_id=account,username=login,trainer_name=trainer)

    def issue(self,db,account,kind,seconds,parent=None,label=''):
        token='lf1_'+secrets.token_urlsafe(32);key=digest(token);now=stamp()
        label=re.sub(r'[^A-Za-z0-9_. -]','',str(label))[:40]
        db.execute('INSERT INTO family_sessions VALUES(?,?,?,?,?,?,0,?)',(key,account,kind,parent,now,now+seconds*1000,label))
        if kind=='rpc':
            player=db.execute('SELECT player_id FROM family_accounts WHERE id=?',(account,)).fetchone()[0]
            db.execute('INSERT INTO sessions VALUES(?,?)',('family:'+key,player))
            self.game.touch(db,player)
        return dict(token=token,expires_ms=now+seconds*1000)

    def login(self,login,password,label='',peer='local'):
        login=username(login);raw=password_bytes(password);self.limit(login,peer)
        with self.game.db.transaction() as db:row=db.execute('SELECT * FROM family_accounts WHERE username=?',(login,)).fetchone()
        if not self.workers.acquire(blocking=False):raise AccountError('Authentication temporarily busy')
        valid=True
        try:
            try:Argon2id.verify_phc_encoded(raw,row['password_hash'] if row else self.dummy)
            except InvalidKey:valid=False
        finally:self.workers.release()
        if not row or row['disabled'] or not valid:raise AccountError('Invalid account credentials')
        with self.game.db.transaction() as db:
            if db.execute('SELECT disabled FROM family_accounts WHERE id=?',(row['id'],)).fetchone()[0]:raise AccountError('Invalid account credentials')
            self.audit(db,'login',row['id'])
            return self.issue(db,row['id'],'credential',self.lifetime,label=label)|dict(username=login,trainer_name=self.game.player(db,row['player_id'])['name'])

    def authorize(self,db,token,kind='rpc'):
        token=token.removeprefix('Bearer ') if isinstance(token,str) else ''
        if not re.fullmatch(r'lf1_[A-Za-z0-9_-]{43}',token):raise AccountError('Session required')
        return self.authorize_digest(db,digest(token),kind)

    def authorize_digest(self,db,key,kind='rpc'):
        now=stamp()
        row=db.execute('SELECT s.*,a.player_id,a.disabled FROM family_sessions s JOIN family_accounts a ON a.id=s.account_id WHERE s.digest=?',(key,)).fetchone()
        if not row or row['kind']!=kind or row['disabled'] or row['revoked_ms'] or row['expires_ms']<=now:raise AccountError('Session invalid or expired')
        if row['parent']:
            parent=db.execute('SELECT * FROM family_sessions WHERE digest=?',(row['parent'],)).fetchone()
            if not parent or parent['revoked_ms'] or parent['expires_ms']<=now:raise AccountError('Session invalid or expired')
        return row

    def exchange(self,token):
        with self.game.db.transaction() as db:
            row=self.authorize(db,token,'credential')
            seconds=min(self.rpc_lifetime,max(1,(row['expires_ms']-stamp())//1000))
            return self.issue(db,row['account_id'],'rpc',seconds,row['digest'])['token']

    def revoke(self,token,all_sessions=False):
        with self.game.db.transaction() as db:
            row=self.authorize(db,token,'credential');now=stamp()
            if all_sessions:db.execute('UPDATE family_sessions SET revoked_ms=? WHERE account_id=? AND revoked_ms=0',(now,row['account_id']))
            else:db.execute('UPDATE family_sessions SET revoked_ms=? WHERE digest=? OR parent=?',(now,row['digest'],row['digest']))
            self.audit(db,'logout-all' if all_sessions else 'logout',row['account_id'])

    def disable(self,login):
        with self.game.db.transaction() as db:
            row=db.execute('SELECT id FROM family_accounts WHERE username=?',(username(login),)).fetchone()
            if not row:raise AccountError('Account not found')
            db.execute('UPDATE family_accounts SET disabled=1 WHERE id=?',(row['id'],))
            db.execute('UPDATE family_sessions SET revoked_ms=? WHERE account_id=?',(stamp(),row['id']))
            self.audit(db,'disabled',row['id'])

    def reset_password(self,login,password):
        # Local administrator console only; never exposed as an HTTP endpoint.
        login=username(login);encoded=hash_password(password)
        with self.game.db.transaction() as db:
            row=db.execute('SELECT id FROM family_accounts WHERE username=?',(login,)).fetchone()
            if not row:raise AccountError('Account not found')
            db.execute('UPDATE family_accounts SET password_hash=? WHERE id=?',(encoded,row['id']))
            db.execute('UPDATE family_sessions SET revoked_ms=? WHERE account_id=? AND revoked_ms=0',(stamp(),row['id']))
            self.audit(db,'password-reset',row['id'])

    def capacity(self,db,player,name):
        if name not in ('pokemon_capacity','item_capacity'):raise ValueError('Unknown storage capacity')
        row=db.execute('SELECT '+name+' FROM family_accounts WHERE player_id=?',(player,)).fetchone()
        return row[0] if row else self.game.config[name]
