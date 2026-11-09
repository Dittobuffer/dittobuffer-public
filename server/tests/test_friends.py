import json,tempfile,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from dittobuffer.gameplay.game import Game,ROOT,p,wire
from dittobuffer.core.database import Database
from dittobuffer.gameplay import friends as f

class FriendsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds')
        self.cfg=json.loads((ROOT/'config/development.json').read_text())
        self.cfg.update(authentication={'mode':'family'},social={'enabled':True})
        self.game=Game(self.cfg,Database(Path(self.tmp.name)/'test.sqlite3'));self.n=0
        self.ids={};self.tickets={}
        for name in ('alice','bobby','carol'):
            self.game.accounts.register(name,'Private fixture password 42',name.title(),self.game.accounts.invite(),peer=name)
            credential=self.game.accounts.login(name,'Private fixture password 42',peer=name)['token']
            self.tickets[name]=self.game.accounts.exchange(credential)
            with self.game.db.transaction() as db:self.ids[name]=db.execute('SELECT player_id FROM family_accounts WHERE username=?',(name,)).fetchone()[0]
    def tearDown(self):self.tmp.cleanup()
    def call(self,name,action,req,typ):
        self.n+=1;request=wire.RpcRequest(rpc_id=self.n)
        request.requests.add(method=5012,body=p.ProxyRequestProto(action=action,payload=req.SerializeToString()).SerializeToString())
        out=p.ProxyResponseProto.FromString(self.game.rpc(request,self.tickets[name]).payloads[0].payload)
        self.assertEqual(out.status,1)
        return typ.FromString(out.payload)
    def code(self,name):return self.call(name,10013,p.GetFriendCodeProto(),p.GetFriendCodeOutProto).friend_code
    def request(self,a='alice',b='bobby',**kw):return self.call(a,10002,p.SendFriendInviteProto(friend_code=self.code(b),**kw),p.SendFriendInviteOutProto)
    def accept(self):return self.call('bobby',10004,p.AcceptFriendInviteProto(nia_account_id=self.ids['alice']),p.AcceptFriendInviteOutProto)
    def befriend(self):self.assertEqual(self.request().result,1);self.assertEqual(self.accept().result,1)
    def test_codes_unique_stable_and_regeneration_invalidates_old(self):
        codes=[self.code(n) for n in self.ids];self.assertEqual(len(set(codes)),3)
        self.assertTrue(all(len(c)==12 and c.isdigit() for c in codes));self.assertEqual(self.code('alice'),codes[0])
        fresh=self.call('alice',10013,p.GetFriendCodeProto(force_generate_code=True),p.GetFriendCodeOutProto).friend_code;self.assertNotEqual(fresh,codes[0])
        out=self.call('bobby',10002,p.SendFriendInviteProto(friend_code=codes[0]),p.SendFriendInviteOutProto);self.assertEqual(out.result,4)
    def test_request_authorization_duplicates_and_self(self):
        self.assertEqual(self.request(b='alice').result,9);self.assertEqual(self.request(initial_friendship_score=180).result,1)
        self.assertEqual(self.request().result,8);self.assertEqual(self.request('bobby','alice').result,10)
        forged=self.call('carol',10004,p.AcceptFriendInviteProto(nia_account_id=self.ids['alice']),p.AcceptFriendInviteOutProto);self.assertEqual(forged.result,3)
        self.assertEqual(self.accept().result,1)
        with self.game.db.transaction() as db:self.assertEqual(f.pair(db,*[self.ids[n] for n in ('alice','bobby')])['points'],0)
        self.assertEqual(self.request().result,3)
    def test_pending_lists_decline_cancel_and_expiry(self):
        self.request();out=self.call('bobby',10008,p.GetIncomingFriendInvitesProto(),p.GetIncomingFriendInvitesOutProto);self.assertEqual(len(out.invites),1)
        out=self.call('alice',10003,p.CancelFriendInviteProto(nia_account_id=self.ids['bobby']),p.CancelFriendInviteOutProto);self.assertEqual(out.result,1);self.assertEqual(self.accept().result,3)
        self.request();self.assertEqual(self.call('bobby',10005,p.DeclineFriendInviteProto(nia_account_id=self.ids['alice']),p.DeclineFriendInviteOutProto).result,1)
        self.request()
        with self.game.db.transaction() as db:db.execute('UPDATE social_requests SET created_ms=0')
        self.assertEqual(self.accept().result,3)
    def test_remove_shared_relationship_and_cap(self):
        self.befriend();self.cfg['social']['friend_capacity']=1
        self.assertEqual(self.request('alice','carol').result,7)
        self.call('alice',10009,p.CancelFriendInviteProto(nia_account_id=self.ids['bobby']),p.RemoveFriendOutProto)
        with self.game.db.transaction() as db:self.assertEqual(f.partners(db,self.ids['bobby']),[])
    def test_daily_shared_progress_and_milestone_replay_after_restart(self):
        self.befriend();a,b=self.ids['alice'],self.ids['bobby']
        with self.game.db.transaction() as db:
            self.assertTrue(f.interact(self.game,db,a,b,'gift','gift:1'))
            self.assertFalse(f.interact(self.game,db,b,a,'raid','raid:1'))
            self.assertFalse(f.interact(self.game,db,a,b,'gift','gift:1'))
            self.assertEqual(f.pair(db,a,b)['points'],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM social_milestones').fetchone()[0],2)
        self.game=Game(self.cfg,Database(self.game.db.path))
        with self.game.db.transaction() as db:
            self.assertEqual(f.pair(db,a,b)['points'],1);self.assertFalse(f.interact(self.game,db,a,b,'gift','gift:1'))
    def test_concurrent_pairs_share_one_daily_point(self):
        self.befriend();a,b=self.ids['alice'],self.ids['bobby']
        def earn(i):
            with self.game.db.transaction() as db:return f.interact(self.game,db,a,b,'raid','raid:'+str(i))
        with ThreadPoolExecutor(6) as executor:result=list(executor.map(earn,range(12)))
        self.assertEqual(sum(result),1)
