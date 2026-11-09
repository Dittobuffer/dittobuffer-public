import json,unittest,threading,sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from tests import test_friends as fixture
from dittobuffer.gameplay.game import p,wire,now_ms
from dittobuffer.gameplay import friends as f, gifts, trading
from dittobuffer.gameplay.species import make_pokemon
from dittobuffer.core.accounts import principal_session

class SocialGameplayTests(unittest.TestCase):
    call=fixture.FriendsTests.call
    code=fixture.FriendsTests.code
    request=fixture.FriendsTests.request
    accept=fixture.FriendsTests.accept
    befriend=fixture.FriendsTests.befriend
    tearDown=fixture.FriendsTests.tearDown
    def setUp(self):
        fixture.FriendsTests.setUp(self);self.befriend();self.cfg['social'].update(gift_rate=1,lucky_rate=0)
        with self.game.db.transaction() as db:
            db.execute('INSERT INTO forts VALUES(?,?,?,?,?)',('social-stop',1,'Original test stop',52.08,5.12))
            self.mons={}
            for name,ident in self.ids.items():
                db.execute('UPDATE players SET xp=210000,stardust=2000000 WHERE id=?',(ident,))
                db.execute('INSERT INTO walking(player_id,lat,lng,arrival_ms,fix_ms) VALUES(?,?,?,?,?)',(ident,52.08,5.12,now_ms(),now_ms()))
                db.execute('INSERT INTO social_locations VALUES(?,?,?,?,?,?)',(ident,52.08,5.12,5,now_ms(),now_ms()))
                spawn=100+len(self.mons);db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',(spawn,'social:'+name,1,52.08,5.12,0))
                mon=make_pokemon(p,self.game.species[1],1,10,[10]*3);mon.creation_time_ms=now_ms()-40*86400000
                mon.id=db.execute('INSERT INTO pokemon(player_id,spawn_id,species,data) VALUES(?,?,1,?)',(ident,spawn,b'')).lastrowid
                db.execute('UPDATE pokemon SET data=? WHERE id=?',(mon.SerializeToString(),mon.id));db.execute('INSERT INTO pokemon_levels VALUES(?,10)',(mon.id,));self.mons[name]=mon
    def gift(self,sender='alice'):
        with self.game.db.transaction() as db:return gifts.award(self.game,db,self.ids[sender],db.execute("SELECT * FROM forts WHERE id='social-stop'").fetchone())
    def action(self,name,method,req,typ):
        self.n+=1;request=wire.RpcRequest(rpc_id=self.n);request.requests.add(method=method,body=req.SerializeToString())
        return typ.FromString(self.game.rpc(request,self.tickets[name],run='social-tests').payloads[0].payload)
    def test_gift_open_replay_and_restart_never_duplicate(self):
        gift=self.gift();a,b=self.ids['alice'],self.ids['bobby']
        self.assertEqual(self.action('alice',950,p.SendGiftProto(giftbox_id=gift.giftbox_id,player_id=b),p.SendGiftOutProto).result,1)
        self.assertEqual(self.action('alice',951,p.OpenGiftProto(giftbox_id=gift.giftbox_id,player_id=a),p.OpenGiftOutProto).result,6)
        req=p.OpenGiftProto(giftbox_id=gift.giftbox_id,player_id=a)
        first=self.action('bobby',951,req,p.OpenGiftOutProto);self.assertEqual(first.result,1)
        with self.game.db.transaction() as db:before=dict(self.game.player(db,b));items=list(db.execute('SELECT item,count FROM items WHERE player_id=? ORDER BY item',(b,)))
        from dittobuffer.gameplay.game import Game
        from dittobuffer.core.database import Database
        self.game=Game(self.cfg,Database(self.game.db.path))
        self.assertEqual(self.action('bobby',951,req,p.OpenGiftOutProto),first)
        with self.game.db.transaction() as db:
            self.assertEqual(dict(self.game.player(db,b)),before);self.assertEqual([tuple(r) for r in db.execute('SELECT item,count FROM items WHERE player_id=? ORDER BY item',(b,))],[tuple(r) for r in items]);self.assertEqual(f.pair(db,a,b)['points'],1)
    def test_gift_capacity_bag_authorization_daily_send_and_stickers(self):
        gift=self.gift();b=self.ids['bobby'];a=self.ids['alice']
        self.assertEqual(self.action('carol',950,p.SendGiftProto(giftbox_id=gift.giftbox_id,player_id=b),p.SendGiftOutProto).result,3)
        req=p.SendGiftProto(giftbox_id=gift.giftbox_id,player_id=b);req.stickers_sent.add(sticker_id='fixture-sticker')
        self.assertEqual(self.action('alice',950,req,p.SendGiftOutProto).result,8)
        with self.game.db.transaction() as db:db.execute('INSERT INTO social_stickers VALUES(?,?,1,0)',(a,'fixture-sticker'))
        sent=self.action('alice',950,req,p.SendGiftOutProto);self.assertEqual(sent.result,1)
        with self.game.db.transaction() as db:
            row=db.execute('SELECT * FROM social_gifts WHERE id=?',(gift.giftbox_id,)).fetchone()
            self.assertEqual(list(gifts.box(db,row).stickers_sent),['fixture-sticker'])
            before=tuple(row);xp=self.game.player(db,a)['xp']
            self.assertEqual(tuple(db.execute('SELECT count,used FROM social_stickers WHERE player_id=? AND sticker=?',(a,'fixture-sticker')).fetchone()),(0,1))
        # A fresh RPC ID replays this Gift's durable receipt, without spending
        # another sticker or granting the send XP a second time.
        self.assertEqual(self.action('alice',950,req,p.SendGiftOutProto),sent)
        with self.game.db.transaction() as db:
            self.assertEqual(tuple(db.execute('SELECT * FROM social_gifts WHERE id=?',(gift.giftbox_id,)).fetchone()),before)
            self.assertEqual(self.game.player(db,a)['xp'],xp)
            self.assertEqual(tuple(db.execute('SELECT count,used FROM social_stickers WHERE player_id=? AND sticker=?',(a,'fixture-sticker')).fetchone()),(0,1))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM social_gifts WHERE owner=? AND receiver IS NOT NULL',(a,)).fetchone()[0],1)
        self.cfg['social']['gift_capacity']=1;self.assertIsNotNone(self.gift());self.assertIsNone(self.gift())
        with self.game.db.transaction() as db:db.execute('UPDATE items SET count=100000 WHERE player_id=? AND item=1',(b,));self.assertEqual(db.execute('SELECT count FROM social_stickers WHERE player_id=?',(a,)).fetchone()[0],0)
        self.assertEqual(self.action('bobby',951,p.OpenGiftProto(giftbox_id=gift.giftbox_id,player_id=a),p.OpenGiftOutProto).result,3)
    def test_gift_send_receipt_survives_restart_and_rejects_conflicting_payloads(self):
        from dittobuffer.gameplay.game import Game
        from dittobuffer.core.database import Database
        a,b,c=[self.ids[name] for name in ('alice','bobby','carol')]
        # Both recipients are valid friends, so a changed recipient must fail
        # because it conflicts with this Gift's receipt, not friendship checks.
        self.assertEqual(self.request('alice','carol').result,1)
        self.assertEqual(self.call('carol',10004,p.AcceptFriendInviteProto(nia_account_id=a),p.AcceptFriendInviteOutProto).result,1)
        gift=self.gift()
        with self.game.db.transaction() as db:
            db.execute('INSERT INTO social_stickers VALUES(?,?,2,0)',(a,'fixture-sticker'))
            db.execute('INSERT INTO social_stickers VALUES(?,?,1,0)',(a,'different-sticker'))
            before_xp=self.game.player(db,a)['xp']
        req=p.SendGiftProto(giftbox_id=gift.giftbox_id,player_id=b)
        req.stickers_sent.add(sticker_id='fixture-sticker')
        first=self.action('alice',950,req,p.SendGiftOutProto)
        self.assertEqual(first.result,1)
        def snapshot():
            with self.game.db.transaction() as db:
                return (tuple(db.execute('SELECT * FROM social_gifts WHERE id=?',(gift.giftbox_id,)).fetchone()),
                        [tuple(row) for row in db.execute('SELECT * FROM social_stickers WHERE player_id=? ORDER BY sticker',(a,))],
                        self.game.player(db,a)['xp'],
                        db.execute('SELECT COUNT(*) FROM social_gifts WHERE owner=? AND receiver IS NOT NULL',(a,)).fetchone()[0])
        state=snapshot()
        self.assertEqual(state[2],before_xp+self.cfg['social']['gift_send_xp'])
        self.assertEqual(state[3],1)
        self.game=Game(self.cfg,Database(self.game.db.path))
        self.assertEqual(self.action('alice',950,req,p.SendGiftOutProto),first)
        self.assertEqual(snapshot(),state)
        conflict=p.SendGiftProto();conflict.CopyFrom(req);conflict.player_id=c
        self.assertEqual(self.action('alice',950,conflict,p.SendGiftOutProto).result,4)
        conflict.CopyFrom(req);conflict.stickers_sent[0].sticker_id='different-sticker'
        self.assertEqual(self.action('alice',950,conflict,p.SendGiftOutProto).result,4)
        self.assertEqual(snapshot(),state)
        # A distinct Gift still observes the one-send-per-recipient/day limit.
        other=self.gift()
        self.assertEqual(self.action('alice',950,p.SendGiftProto(giftbox_id=other.giftbox_id,player_id=b),p.SendGiftOutProto).result,5)
        self.assertEqual(snapshot(),state)
        with self.game.db.transaction() as db:
            self.assertEqual(tuple(db.execute('SELECT count,used FROM social_stickers WHERE player_id=? AND sticker=?',(a,'fixture-sticker')).fetchone()),(1,1))
            self.assertEqual(tuple(db.execute('SELECT count,used FROM social_stickers WHERE player_id=? AND sticker=?',(a,'different-sticker')).fetchone()),(1,0))
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
    def test_postcard_privacy_authorization_and_duplicate_pin(self):
        gift=self.gift();req=p.CreatePostcardProto(giftbox_id=gift.giftbox_id)
        self.assertEqual(self.action('carol',1910,req,p.CreatePostcardOutProto).result,3)
        out=self.action('alice',1910,req,p.CreatePostcardOutProto);self.assertEqual(out.result,1);self.assertFalse(out.postcard.postcard_creator_id);self.assertEqual(out.postcard.fort_name,'Original test stop')
        self.assertEqual(self.action('alice',1910,req,p.CreatePostcardOutProto).postcard.postcard_id,out.postcard.postcard_id)
    def test_postcard_book_filters_favorites_and_atomic_delete(self):
        gift=self.gift();card=self.action('alice',1910,p.CreatePostcardProto(giftbox_id=gift.giftbox_id),p.CreatePostcardOutProto).postcard
        book=self.action('alice',1913,p.GetMementoListProto(),p.GetMementoListOutProto)
        self.assertEqual(book.status,1);self.assertEqual(book.mementos[0].postcard_display,card)
        self.assertEqual(self.action('alice',1913,p.GetMementoListProto(memento_list_hash=book.memento_list_hash),p.GetMementoListOutProto).status,4)
        with self.game.db.transaction() as db:
            req=p.UpdatePostcardProto(postcard_id=card.postcard_id,favorite=True)
            self.assertEqual(gifts.update_postcard(self.game,db,self.ids['bobby'],req.SerializeToString()).result,2)
            self.assertEqual(gifts.update_postcard(self.game,db,self.ids['alice'],req.SerializeToString()).result,1)
            delete=p.DeletePostcardsProto(postcard_ids=[card.postcard_id])
            self.assertEqual(gifts.delete_postcards(self.game,db,self.ids['alice'],delete.SerializeToString()).result,3)
            req.favorite=False;gifts.update_postcard(self.game,db,self.ids['alice'],req.SerializeToString())
            delete.postcard_ids.append('unowned')
            self.assertEqual(gifts.delete_postcards(self.game,db,self.ids['alice'],delete.SerializeToString()).result,2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM social_postcards').fetchone()[0],1)
    def test_gift_invalid_uint64_and_post_send_or_open_pins(self):
        a,b=self.ids['alice'],self.ids['bobby']
        for ident in (0,2**64-1):
            self.assertEqual(self.action('alice',950,p.SendGiftProto(giftbox_id=ident,player_id=b),p.SendGiftOutProto).result,4)
            self.assertEqual(self.action('bobby',951,p.OpenGiftProto(giftbox_id=ident,player_id=a),p.OpenGiftOutProto).result,5)
            self.assertEqual(self.action('alice',1910,p.CreatePostcardProto(giftbox_id=ident),p.CreatePostcardOutProto).result,3)
        gift=self.gift();pin=p.CreatePostcardProto(giftbox_id=gift.giftbox_id)
        self.action('alice',950,p.SendGiftProto(giftbox_id=gift.giftbox_id,player_id=b),p.SendGiftOutProto)
        self.assertEqual(self.action('alice',1910,pin,p.CreatePostcardOutProto).result,3)
        card=self.action('bobby',1910,pin,p.CreatePostcardOutProto);self.assertEqual(card.result,1)
        self.action('bobby',951,p.OpenGiftProto(giftbox_id=gift.giftbox_id,player_id=a),p.OpenGiftOutProto)
        self.assertEqual(self.action('bobby',1910,pin,p.CreatePostcardOutProto).postcard,card.postcard)
    def test_gift_batch_delete_validates_all_owned_ids_before_mutation(self):
        first=self.gift();second=self.gift()
        for ids in ([first.giftbox_id,2**64-1],[first.giftbox_id,first.giftbox_id],[]):
            self.assertEqual(self.action('alice',958,p.DeleteGiftFromInventoryProto(giftbox_id=ids),p.DeleteGiftFromInventoryOutProto).result,3)
        req=p.DeleteGiftFromInventoryProto(giftbox_id=[first.giftbox_id,second.giftbox_id])
        self.assertEqual(self.action('bobby',958,req,p.DeleteGiftFromInventoryOutProto).result,3)
        self.assertEqual(self.action('alice',958,req,p.DeleteGiftFromInventoryOutProto).result,1)
        self.assertEqual(self.action('alice',958,req,p.DeleteGiftFromInventoryOutProto).result,3)
        with self.game.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM social_gifts WHERE deleted=1').fetchone()[0],2)
    def test_trade_waits_for_external_sqlite_writer_without_partial_commit(self):
        token=self.prepare();self.confirm('alice',token);started=threading.Event()
        external=sqlite3.connect(self.game.db.path);external.execute('BEGIN IMMEDIATE')
        def finish():started.set();return self.confirm('bobby',token)
        try:
            with ThreadPoolExecutor(1) as pool:
                future=pool.submit(finish);self.assertTrue(started.wait(2))
                try:
                    with self.assertRaises(TimeoutError):future.result(timeout=.15)
                    self.assertEqual(external.execute('SELECT COUNT(*) FROM social_trade_history').fetchone()[0],0)
                finally:external.rollback()
                self.assertEqual(future.result(timeout=10).result,1)
        finally:external.close()
        with self.game.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM social_trade_history').fetchone()[0],1)
    def prepare(self):
        a,b=self.ids['alice'],self.ids['bobby']
        self.assertEqual(self.action('alice',970,p.OpenTradingProto(player_id=b),p.OpenTradingOutProto).result,1)
        self.assertEqual(self.action('bobby',970,p.OpenTradingProto(player_id=a),p.OpenTradingOutProto).result,1)
        self.assertEqual(self.action('alice',971,p.UpdateTradingProto(player_id=b,pokemon_id=self.mons['alice'].id),p.UpdateTradingOutProto).result,1)
        out=self.action('bobby',971,p.UpdateTradingProto(player_id=a,pokemon_id=self.mons['bobby'].id),p.UpdateTradingOutProto);self.assertEqual(out.result,1)
        return out.trading.transaction_log
    def confirm(self,name,token):
        return self.action(name,972,p.ConfirmTradingProto(player_id=self.ids['bobby' if name=='alice' else 'alice'],transaction_log=token),p.ConfirmTradingOutProto)
    def test_trade_two_consents_atomic_swap_and_replay(self):
        token=self.prepare();a,b=self.ids['alice'],self.ids['bobby']
        first=self.confirm('alice',token);self.assertEqual(first.result,1);self.assertEqual(first.trading.state,4)
        with self.game.db.transaction() as db:self.assertEqual(db.execute('SELECT player_id FROM pokemon WHERE id=?',(self.mons['alice'].id,)).fetchone()[0],a)
        second=self.confirm('bobby',token);self.assertEqual(second.result,1);self.assertEqual(second.trading.state,5)
        self.assertEqual(second.trading.friend.trading_pokemon.traded_pokemon.id,self.mons['alice'].id)
        self.assertEqual(second.trading.player.trading_pokemon.traded_pokemon.id,self.mons['bobby'].id)
        with self.game.db.transaction() as db:
            self.assertEqual(db.execute('SELECT player_id FROM pokemon WHERE id=?',(self.mons['alice'].id,)).fetchone()[0],b)
            self.assertEqual(self.game.player(db,a)['stardust'],1999900);self.assertEqual(self.game.player(db,b)['stardust'],1999900)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM social_trade_history').fetchone()[0],1)
        self.assertEqual(self.confirm('bobby',token).result,1)
        with self.game.db.transaction() as db:self.assertEqual(self.game.player(db,b)['stardust'],1999900)
    def test_trade_cancel_late_confirmation_and_retrade_rejected(self):
        token=self.prepare();b=self.ids['bobby'];a=self.ids['alice']
        self.assertEqual(self.action('alice',973,p.CancelTradingProto(player_id=b),p.CancelTradingOutProto).result,1)
        self.assertNotEqual(self.confirm('bobby',token).result,1)
        fresh=self.prepare();self.assertNotEqual(token,fresh);self.assertEqual(self.confirm('alice',token).result,12)
        self.confirm('alice',fresh);self.confirm('bobby',fresh)
        with self.game.db.transaction() as db:
            with self.assertRaises(trading.Invalid):trading.valid_mon(self.game,db,b,self.mons['alice'].id)
    def test_trade_insufficient_dust_no_partial_swap_or_charge(self):
        token=self.prepare();self.confirm('alice',token);b=self.ids['bobby'];a=self.ids['alice']
        with self.game.db.transaction() as db:db.execute('UPDATE players SET stardust=0 WHERE id=?',(b,))
        self.assertEqual(self.confirm('bobby',token).result,8)
        with self.game.db.transaction() as db:
            self.assertEqual(db.execute('SELECT player_id FROM pokemon WHERE id=?',(self.mons['alice'].id,)).fetchone()[0],a);self.assertEqual(self.game.player(db,a)['stardust'],2000000);self.assertEqual(db.execute('SELECT COUNT(*) FROM social_trade_history').fetchone()[0],0)
    def test_trade_capacity_failure_and_cross_friend_pokemon_lock(self):
        token=self.prepare();self.confirm('alice',token);a,b=[self.ids[n] for n in ('alice','bobby')]
        with self.game.db.transaction() as db:
            with self.assertRaises(trading.Invalid):trading.valid_mon(self.game,db,a,self.mons['alice'].id,'another-trade')
            before=[tuple(r) for r in db.execute('SELECT id,player_id,data FROM pokemon ORDER BY id')]
            db.execute('UPDATE family_accounts SET pokemon_capacity=0 WHERE player_id=?',(b,))
        self.assertEqual(self.confirm('bobby',token).result,14)
        with self.game.db.transaction() as db:
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT id,player_id,data FROM pokemon ORDER BY id')])
            self.assertEqual(db.execute('SELECT SUM(stardust) FROM players').fetchone()[0],6000000)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM social_trade_history').fetchone()[0],0)
    def test_trade_session_binding_and_owned_selection(self):
        token=self.prepare();b=self.ids['bobby']
        self.assertEqual(self.action('alice',971,p.UpdateTradingProto(player_id=b,pokemon_id=self.mons['bobby'].id),p.UpdateTradingOutProto).result,7)
        credentials=self.game.accounts.login('alice','Private fixture password 42',peer='new')['token'];self.tickets['alice']=self.game.accounts.exchange(credentials)
        self.assertEqual(self.confirm('alice',token).result,5)
    def test_lucky_friend_consumed_once_and_iv_floor(self):
        a,b=self.ids['alice'],self.ids['bobby']
        with self.game.db.transaction() as db:db.execute('UPDATE social_pairs SET points=90,lucky=1')
        token=self.prepare();self.confirm('alice',token);self.assertEqual(self.confirm('bobby',token).result,1)
        with self.game.db.transaction() as db:
            self.assertEqual(f.pair(db,a,b)['lucky'],0)
            for row in db.execute('SELECT data FROM pokemon WHERE player_id IN (?,?)',(a,b)):
                mon=p.PokemonProto.FromString(row[0]);self.assertTrue(mon.is_lucky);self.assertTrue(all(12<=x<=15 for x in [mon.individual_attack,mon.individual_defense,mon.individual_stamina]))
    def test_simultaneous_confirmations_commit_once(self):
        token=self.prepare()
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda n:self.confirm(n,token).result,['alice','bobby']))
        self.assertEqual(results,[1,1])
        with self.game.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM social_trade_history').fetchone()[0],1)
    def test_forced_commit_failure_rolls_back_every_resource(self):
        token=self.prepare();self.confirm('alice',token)
        with self.game.db.transaction() as db:before=list(db.execute('SELECT id,player_id,data FROM pokemon ORDER BY id'))
        with patch('dittobuffer.gameplay.pokedex.record',side_effect=RuntimeError('isolated forced transaction failure')):
            with self.assertRaises(RuntimeError):self.confirm('bobby',token)
        with self.game.db.transaction() as db:
            self.assertEqual([tuple(r) for r in before],[tuple(r) for r in db.execute('SELECT id,player_id,data FROM pokemon ORDER BY id')])
            self.assertEqual(db.execute('SELECT SUM(stardust) FROM players').fetchone()[0],6000000)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM social_trade_history').fetchone()[0],0)
        self.assertEqual(self.confirm('bobby',token).result,1)
    def test_stale_session_reentry_rotates_both_consents(self):
        token=self.prepare();self.confirm('alice',token)
        credentials=self.game.accounts.login('alice','Private fixture password 42',peer='new')['token'];self.tickets['alice']=self.game.accounts.exchange(credentials)
        out=self.action('alice',970,p.OpenTradingProto(player_id=self.ids['bobby']),p.OpenTradingOutProto)
        self.assertEqual(out.result,1);self.assertNotEqual(out.trading.transaction_log,token)
        self.assertFalse(out.trading.player.has_confirmed);self.assertFalse(out.trading.friend.has_confirmed);self.assertEqual(self.confirm('bobby',token).result,12)
    def test_remote_tags_ownership_age_and_expiration(self):
        from dittobuffer.gameplay import remote_trading as remote
        a,b=self.ids['alice'],self.ids['bobby'];ident=self.mons['bobby'].id
        with self.game.db.transaction() as db:
            db.execute('UPDATE social_pairs SET points=180,remote_credit=1')
            req=p.MarkRemoteTradableProto();req.pokemon.add(pokemon_uid=ident,remote_tradable=True)
            self.assertEqual(remote.mark(self.game,db,a,req.SerializeToString()).result,4)
            self.assertEqual(remote.mark(self.game,db,b,req.SerializeToString()).result,1)
            proposed=p.ProposeRemoteTradeProto(player_id=b,requested_pokemon_uids=[ident])
            self.assertEqual(remote.propose(self.game,db,a,proposed.SerializeToString()).result,1)
            row=remote.get(db,a,b);deadline=row['expires_ms'];nonce=row['nonce']
            remote.propose(self.game,db,a,proposed.SerializeToString());row=remote.get(db,a,b)
            self.assertEqual(row['expires_ms'],deadline);self.assertEqual(row['nonce'],nonce)
        from dittobuffer.gameplay.game import Game
        from dittobuffer.core.database import Database
        self.game=Game(self.cfg,Database(self.game.db.path))
        with self.game.db.transaction() as db:
            self.assertIsNotNone(remote.get(db,a,b));db.execute('UPDATE social_remote_sessions SET expires_ms=0')
            self.assertEqual(remote.pending(self.game,db,a,p.GetPendingRemoteTradeProto(player_id=b).SerializeToString()).result,6)
            self.assertEqual(f.pair(db,a,b)['remote_credit'],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM social_trade_history').fetchone()[0],0)
    def test_remote_native_completion_fails_closed_and_credit_does_not_stack(self):
        from dittobuffer.gameplay import remote_trading as remote
        a,b=self.ids['alice'],self.ids['bobby']
        with self.game.db.transaction() as db:
            db.execute("UPDATE social_pairs SET points=179,last_day=''")
            self.assertTrue(f.interact(self.game,db,a,b,'gift','threshold:180'));self.assertEqual(f.pair(db,a,b)['remote_credit'],1)
            db.execute('UPDATE social_history SET at_ms=0')
            db.execute("UPDATE social_pairs SET points=269,remote_progress=89,last_day=''")
            f.interact(self.game,db,a,b,'gift','threshold:270');self.assertEqual(f.pair(db,a,b)['remote_credit'],1);self.assertEqual(f.pair(db,a,b)['remote_progress'],0)
            self.assertEqual(remote.respond(self.game,db,a,p.RespondRemoteTradeProto(player_id=b,accept_trade=True).SerializeToString()).result,7)
    def test_friendship_notification_and_claim_once(self):
        a,b=self.ids['alice'],self.ids['bobby']
        with self.game.db.transaction() as db:
            f.interact(self.game,db,a,b,'gift','milestone:1');f.pending_notifications(self.game,db,a);f.pending_notifications(self.game,db,a)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gym_notifications').fetchone()[0],1)
        req=p.GetFriendshipRewardsProto(friend_id=b)
        first=self.action('alice',955,req,p.GetFriendshipRewardsOutProto);self.assertEqual(first.result,1);self.assertEqual(first.xp_reward,3000)
        self.assertEqual(self.action('alice',955,req,p.GetFriendshipRewardsOutProto).result,4)
    def test_stationary_location_stale_and_duplicate_fixes(self):
        a=self.ids['alice'];stamp=now_ms()
        fix=dict(lat=52.08,lng=5.12,accuracy=5,time=(stamp+1)/1000,provider=3,source='device')
        with self.game.db.transaction() as db:
            before=db.execute('SELECT total_m FROM walking WHERE player_id=?',(a,)).fetchone()[0]
            f.observe(self.game,db,a,fix);first=dict(db.execute('SELECT * FROM social_locations WHERE player_id=?',(a,)).fetchone())
            f.observe(self.game,db,a,fix);self.assertEqual(dict(db.execute('SELECT * FROM social_locations WHERE player_id=?',(a,)).fetchone()),first)
            f.observe(self.game,db,a,dict(fix,time=0));self.assertEqual(dict(db.execute('SELECT * FROM social_locations WHERE player_id=?',(a,)).fetchone()),first)
            self.assertEqual(db.execute('SELECT total_m FROM walking WHERE player_id=?',(a,)).fetchone()[0],before)
    def test_best_friend_raid_bonus_and_removed_friend(self):
        a,b=self.ids['alice'],self.ids['bobby']
        with self.game.db.transaction() as db:
            db.execute('UPDATE social_pairs SET points=90');self.assertEqual(f.raid_bonus(self.game,db,a,[a,b]),(5,4,1.1))
            db.execute('UPDATE social_pairs SET active=0');self.assertEqual(f.raid_bonus(self.game,db,a,[a,b]),(1,0,1))
    def test_configured_friendship_event_cap_replay_and_end(self):
        a,b=self.ids['alice'],self.ids['bobby'];today=f.day(self.game)
        self.cfg['social']['friendship_events']=[dict(start_day=today,end_day=today,kinds=['gift'],points=3,daily_cap=4)]
        with self.game.db.transaction() as db:
            self.assertTrue(f.interact(self.game,db,a,b,'gift','event:one'));self.assertEqual(f.pair(db,a,b)['points'],3)
            self.assertFalse(f.interact(self.game,db,a,b,'gift','event:one'))
            self.assertFalse(f.interact(self.game,db,a,b,'trade','event:trade'))
            self.assertTrue(f.interact(self.game,db,a,b,'gift','event:two'));self.assertEqual(f.pair(db,a,b)['points'],4)
            self.assertFalse(f.interact(self.game,db,a,b,'gift','event:three'))
            self.assertEqual(db.execute('SELECT SUM(points) FROM social_history').fetchone()[0],4)
            self.cfg['social']['friendship_events']=[]
            self.assertFalse(f.interact(self.game,db,a,b,'gift','event:ended'))
