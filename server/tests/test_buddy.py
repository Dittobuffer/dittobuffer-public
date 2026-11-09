import json,unittest
from unittest.mock import patch
from tests import test_game as base
from dittobuffer.gameplay.game import Game,p,now_ms
from dittobuffer.core.database import Database
from dittobuffer.gameplay import buddy, eggs


class BuddyTests(unittest.TestCase):
    setUp=base.GameTests.setUp
    tearDown=base.GameTests.tearDown
    call=base.GameTests.call
    spawn=base.GameTests.spawn
    encounter=base.GameTests.encounter
    catch=base.GameTests.catch
    inventory=base.GameTests.inventory

    def player(self):
        with self.database.transaction() as db:return db.execute('SELECT player_id FROM sessions WHERE ticket=?',(self.ticket,)).fetchone()[0]

    def select(self,ident):return self.call(152,p.SetBuddyPokemonProto(pokemon_id=ident),p.SetBuddyPokemonOutProto)

    def test_selection_ownership_same_selection_and_restart(self):
        ident=self.catch(self.encounter()).captured_pokemon_id
        self.assertEqual(self.select(ident+1000).result,3)
        out=self.select(ident);self.assertEqual(out.result,1);self.assertEqual(out.km_remaining,3)
        self.assertEqual(self.select(ident),out)
        self.assertEqual(self.call(112,p.ReleasePokemonProto(pokemon_id=ident),p.ReleasePokemonOutProto).status,2)
        self.game=Game(self.config,Database(self.database.path))
        value=self.call(2,p.GetPlayerProto(),p.GetPlayerOutProto).player
        self.assertEqual(value.buddy_pokemon_proto.buddy_pokemon_id,ident)
        self.assertEqual(self.call(1355,p.GetBuddyHistoryProto(),p.GetBuddyHistoryOutProto).buddy_history[0].pokemon_id,ident)

    def test_feed_heart_item_consumption_duplicate_full_and_daily_caps(self):
        ident=self.catch(self.encounter()).captured_pokemon_id;self.select(ident)
        player=self.player()
        request=p.BuddyFeedingProto(item=701,count=3)
        def count():
            with self.database.transaction() as db:return db.execute('SELECT count FROM items WHERE player_id=? AND item=701',(player,)).fetchone()[0]
        before=count();out=self.call(1352,request,p.BuddyFeedingOutProto)
        self.assertEqual((out.result,out.shown_hearts),(1,1));self.assertEqual(count(),before-3)
        self.assertEqual(self.call(1352,request,p.BuddyFeedingOutProto).result,4);self.assertEqual(count(),before-3)
        self.assertEqual(self.call(1352,p.BuddyFeedingProto(item=1,count=1),p.BuddyFeedingOutProto).result,4)
        self.assertEqual(self.call(1354,p.BuddyPettingProto(),p.BuddyPettingOutProto).shown_hearts,1)
        self.assertEqual(self.call(1354,p.BuddyPettingProto(),p.BuddyPettingOutProto).shown_hearts,0)
        self.assertEqual(self.call(1351,p.BuddyStatsProto(),p.BuddyStatsOutProto).observed_data.current_points_earned,2)
        self.game=Game(self.config,Database(self.database.path))
        self.assertEqual(self.call(1351,p.BuddyStatsProto(),p.BuddyStatsOutProto).observed_data.total_stats.berries_fed,3)

    def test_snapshot_and_committed_activity_receipts_survive_retry_and_restart(self):
        ident=self.catch(self.encounter()).captured_pokemon_id;self.select(ident);player=self.player()
        self.call(1352,p.BuddyFeedingProto(item=701,count=3),p.BuddyFeedingOutProto)
        request=p.CompleteSnapshotSessionProto(photo_pokemon_id=ident,num_photos_taken=1,snapshot_session_start_time=now_ms())
        self.assertEqual(self.call(1110,request,p.CompleteSnapshotSessionOutProto).status,1)
        self.assertEqual(self.call(1110,request,p.CompleteSnapshotSessionOutProto).status,1)
        self.game=Game(self.config,Database(self.database.path))
        with self.database.transaction() as db:
            self.assertFalse(buddy.activity(self.game,db,player,'battle','no-buddy',[ident+1]))
            self.assertTrue(buddy.activity(self.game,db,player,'battle','battle:1',[ident]))
            self.assertFalse(buddy.activity(self.game,db,player,'battle','battle:1',[ident]))
            self.assertTrue(buddy.activity(self.game,db,player,'explore','new-stop'))
            self.assertFalse(buddy.activity(self.game,db,player,'explore','new-stop'))
        out=self.call(1351,p.BuddyStatsProto(),p.BuddyStatsOutProto).observed_data
        self.assertEqual((out.total_stats.photos,out.total_stats.battles,out.total_stats.new_visits),(1,1,1))
        self.assertEqual(out.current_points_earned,4)
        self.assertEqual(len(out.today_stats_shown_hearts.buddy_shown_hearts_per_category),8)
        self.assertEqual(self.call(1110,p.CompleteSnapshotSessionProto(photo_pokemon_id=ident,num_photos_taken=0),p.CompleteSnapshotSessionOutProto).status,3)

    def test_only_validated_new_samples_credit_candy_once(self):
        ident=self.catch(self.encounter()).captured_pokemon_id;self.select(ident);player=self.player();stamp=now_ms()
        self.call(1352,p.BuddyFeedingProto(item=701,count=3),p.BuddyFeedingOutProto)
        with self.database.transaction() as db:
            _,mon,state=buddy.active(db,player);state['candy_m']=2990;state['heart_walk_m']=1990;state['walk_day']=buddy.day()
            buddy.save(self.game,db,player,mon,state)
        def sample(at,lat=52.37):
            fix=dict(lat=lat,lng=4.89,accuracy=2,time=(stamp+at*1000)/1000,provider=3,source='device')
            with patch('dittobuffer.gameplay.eggs.now_ms',return_value=stamp+at*1000),self.database.transaction() as db:eggs.sample(self.game,db,player,fix)
        sample(0);sample(10,52.3701);sample(10,52.3701);sample(20,54)
        first=self.call(153,p.GetBuddyWalkedProto(),p.GetBuddyWalkedOutProto)
        self.assertEqual(first.candy_earned_count,1)
        self.assertEqual(self.call(153,p.GetBuddyWalkedProto(),p.GetBuddyWalkedOutProto).candy_earned_count,0)
        self.game=Game(self.config,Database(self.database.path))
        stats=self.call(1351,p.BuddyStatsProto(),p.BuddyStatsOutProto).observed_data
        self.assertAlmostEqual(stats.total_stats.km_walked,.0111195,places=5)
        self.assertEqual(stats.current_points_earned,2)
        journal=self.call(801,p.GetActionLogRequest(),p.GetActionLogResponse)
        self.assertEqual(sum(x.HasField('buddy_pokemon') for x in journal.log),1)
        with self.database.transaction() as db:self.assertEqual(db.execute('SELECT count FROM candy WHERE player_id=? AND family=1',(player,)).fetchone()[0],4)
