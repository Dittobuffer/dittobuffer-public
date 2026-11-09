import json,tempfile,unittest,hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from dittobuffer.gameplay.game import Game,ROOT,p,wire,RpcError
from dittobuffer.core.database import Database
from dittobuffer.core.accounts import AccountError,digest,stamp
from dittobuffer.core.family_network import listener,peer_allowed

class FamilyAccountsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds');self.db=Database(Path(self.tmp.name)/'family.sqlite3')
        self.cfg=json.loads((ROOT/'config/development.json').read_text(encoding='utf8'));self.cfg.pop('fixture_player',None)
        self.cfg['authentication']={'mode':'family'};self.game=Game(self.cfg,self.db);self.n=0
        self.password='An isolated test password 42'
    def tearDown(self):self.tmp.cleanup()
    def account(self,name='alice'):
        result=self.game.accounts.register(name,self.password,name.title(),self.game.accounts.invite(),peer=name)
        cred=self.game.accounts.login(name,self.password,peer=name)['token']
        ticket=self.game.authenticate(wire.AuthRequest(auth_id='ignored forged identity',token=cred.encode())).ticket
        with self.db.transaction() as db:player=db.execute('SELECT player_id FROM family_accounts WHERE username=?',(name,)).fetchone()[0]
        return player,cred,ticket
    def call(self,ticket,method=2,req=None,typ=p.GetPlayerOutProto):
        self.n+=1;r=wire.RpcRequest(rpc_id=self.n);r.requests.add(method=method,body=(req or p.GetPlayerProto()).SerializeToString())
        return typ.FromString(self.game.rpc(r,ticket).payloads[0].payload)
    def test_two_accounts_concurrent_login_random_principals_and_hashed_credentials(self):
        a=self.account();b=self.account('bobby');self.assertNotEqual(a[0],b[0])
        with ThreadPoolExecutor(2) as ex:tokens=list(ex.map(lambda name:self.game.accounts.login(name,self.password,peer=name)['token'],['alice','bobby']))
        self.assertEqual(len(set(tokens)),2)
        self.assertEqual(self.call(a[2]).player.name,'Alice');self.assertEqual(self.call(b[2]).player.name,'Bobby')
        with self.db.transaction() as db:
            hashes=[r[0] for r in db.execute('SELECT password_hash FROM family_accounts')]
            self.assertTrue(all(v.startswith('$argon2id$v=19$m=19456,t=2,p=1$') for v in hashes));self.assertEqual(len(set(hashes)),2)
            dump='\n'.join(db.iterdump());self.assertNotIn(a[1],dump);self.assertNotIn(a[2],dump);self.assertNotIn(self.password,dump)
    def test_invite_one_use_expiry_revocation_and_no_auto_creation(self):
        code=self.game.accounts.invite();self.game.accounts.register('alice',self.password,'Alice',code)
        with self.assertRaises(AccountError):self.game.accounts.register('ALICE',self.password,'Duplicate',self.game.accounts.invite())
        with self.assertRaises(AccountError):self.game.accounts.register('bobby',self.password,'Bobby',code)
        code=self.game.accounts.invite()
        with self.db.transaction() as db:db.execute('UPDATE family_invites SET expires_ms=0 WHERE digest=?',(digest(code),))
        with self.assertRaises(AccountError):self.game.accounts.register('bobby',self.password,'Bobby',code)
        with self.assertRaises(RpcError):self.game.authenticate(wire.AuthRequest(auth_id='alice',token=b'anything'))
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM players').fetchone()[0],1)
    def test_parent_logout_expiry_disable_and_replay_denied_after_restart(self):
        a=self.account();b=self.account('bobby')
        with self.db.transaction() as db:db.execute('UPDATE items SET count=7 WHERE player_id=? AND item=1',(a[0],))
        self.game.accounts.revoke(a[1]);self.game=Game(self.cfg,Database(self.db.path))
        with self.assertRaises(RpcError):self.call(a[2])
        with self.assertRaises(AccountError):self.game.accounts.exchange(a[1])
        renewed=self.game.accounts.login('alice',self.password,peer='relogin')['token'];fresh=self.game.accounts.exchange(renewed)
        self.assertEqual(self.call(fresh).player.name,'Alice')
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1',(a[0],)).fetchone()[0],7)
        self.assertEqual(self.call(b[2]).player.name,'Bobby')
        with self.db.transaction() as db:db.execute('UPDATE family_sessions SET expires_ms=0 WHERE digest=?',(digest(b[2]),))
        with self.assertRaises(RpcError):self.call(b[2])
        fresh=self.game.accounts.login('bobby',self.password,peer='fresh')['token'];ticket=self.game.accounts.exchange(fresh)
        self.game.accounts.disable('bobby')
        with self.assertRaises(RpcError):self.call(ticket)
        with self.assertRaises(AccountError):self.game.accounts.login('bobby',self.password,peer='fresh')
    def test_wrong_password_and_throttling_fail_closed(self):
        self.account()
        for _ in range(10):
            with self.assertRaises(AccountError):self.game.accounts.login('alice','wrong but long enough',peer='x')
        with self.assertRaisesRegex(AccountError,'limited'):self.game.accounts.login('alice',self.password,peer='x')
    def test_independent_caps_inventory_and_legacy_mode_fail_closed(self):
        a=self.account();b=self.account('bobby')
        with self.db.transaction() as db:
            db.execute('UPDATE family_accounts SET pokemon_capacity=400,item_capacity=450 WHERE player_id=?',(a[0],))
            db.execute('UPDATE items SET count=3 WHERE player_id=? AND item=1',(a[0],))
            self.assertEqual(self.game.capacity(db,a[0],'pokemon_capacity'),400)
            self.assertEqual(self.game.capacity(db,b[0],'pokemon_capacity'),self.cfg['pokemon_capacity'])
            self.assertNotEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1',(a[0],)).fetchone()[0],db.execute('SELECT count FROM items WHERE player_id=? AND item=1',(b[0],)).fetchone()[0])
        legacy=dict(self.cfg,authentication={'mode':'local-development'})
        with self.assertRaises(RpcError):Game(legacy,self.db).authenticate(wire.AuthRequest(auth_id='family:alice',token=a[1].encode()))
    def test_listener_rejects_unsafe_routes_and_untrusted_peers(self):
        for host in ['0.0.0.0','192.168.1.2']:
            with self.assertRaises(ValueError):listener(dict(self.cfg,host=host),ROOT)
        vpn=dict(self.cfg,host='100.90.1.2',network={'mode':'private-vpn','allowed_networks':['100.90.1.0/24'],'vpn_encryption_confirmed':True})
        self.assertIsNone(listener(vpn,ROOT));self.assertTrue(peer_allowed(vpn,'100.90.1.3'));self.assertFalse(peer_allowed(vpn,'8.8.8.8'))
        with self.assertRaises(ValueError):listener(dict(vpn,host='0.0.0.0'),ROOT)
        with self.assertRaises(ValueError):listener(dict(vpn,authentication={'mode':'local-development'}),ROOT)

    def test_admin_password_reset_revokes_old_sessions_and_preserves_player(self):
        a=self.account();self.call(a[2]);password='A different private password 79'
        self.game.accounts.reset_password('alice',password)
        with self.assertRaises(RpcError):self.call(a[2])
        with self.assertRaises(AccountError):self.game.accounts.login('alice',self.password,peer='reset')
        credential=self.game.accounts.login('alice',password,peer='reset')['token']
        self.assertEqual(self.call(self.game.accounts.exchange(credential)).player.name,'Alice')
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT player_id FROM family_accounts WHERE username=?',('alice',)).fetchone()[0],a[0])

if __name__=='__main__':unittest.main()
