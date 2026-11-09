"""Daily/streak custom tuning is independently persisted and never replayed."""
import unittest
from unittest.mock import patch
from tests import test_routes as support
from dittobuffer.gameplay import route_rewards as r
from dittobuffer.gameplay.game import Game
from dittobuffer.core.database import Database

class RouteRewardTests(unittest.TestCase):
    def setUp(self):
        self.f=support.RouteTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f;value=patch('dittobuffer.gameplay.route_rewards.now_ms',lambda:f.clock);value.start();self.addCleanup(value.stop)
        f.game.routes['daily_rewards']={'first':{'xp':50},'repeat':{'stardust':20},'streak':{'days':2,'items':{'2':1}}}
        f.game.config['routes']=dict(f.game.routes)
    def complete(self):
        f=self.f;f.start()
        for i in range(1,26):f.fix(i)
        return f.progress(True)
    def test_first_repeat_streak_restart_and_receipts(self):
        f=self.f;one=self.complete();self.assertTrue(one.route_play.is_first_travel_today)
        self.assertEqual(sum(v.count for v in one.route_loot.loot_item if v.experience),1050)
        self.assertEqual(f.progress(True).SerializeToString(),one.SerializeToString())
        f.fix(0,advance=200000);two=self.complete();self.assertFalse(two.route_play.is_first_travel_today)
        self.assertEqual(sum(v.count for v in two.route_loot.loot_item if v.stardust),520)
        f.fix(0,advance=86400000);three=self.complete()
        self.assertEqual(sum(v.count for v in three.route_loot.loot_item if v.item==2),1)
        restarted=Game(f.game.config,Database(f.db.path))
        with restarted.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM route_completion_days').fetchone()[0],2)
            self.assertFalse(r.rewards(restarted,db,f.players[0])[1]);self.assertTrue(r.rewards(restarted,db,f.players[1])[1])
    def test_full_bag_does_not_record_a_day_or_consume_streak(self):
        f=self.f
        with f.db.transaction() as db:db.execute('UPDATE items SET count=9999 WHERE player_id=?',(f.players[0],))
        self.assertEqual(self.complete().status,2)
        with f.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM route_completion_days').fetchone()[0],0)

    def test_field_and_story_progress_only_once_for_committed_route_claim(self):
        from dittobuffer.gameplay import research,stories
        from dittobuffer.gameplay.game import p
        f=self.f;player=f.players[0]
        objective={'event':'route','target':2,'description':'Complete two Routes','rewards':[{'type':'xp','amount':10}]}
        f.game.research.update(enabled=True,field_tasks={'routes':objective},field_pool=[{'task':'routes','weight':1}],stories=[{'id':'route-story','title':'Park observations','stages':[{'objectives':[objective],'rewards':[{'type':'xp','amount':20}]}]}])
        with f.db.transaction() as db:
            research.ensure(f.game,db,player)
            fort=dict(db.execute("SELECT * FROM forts WHERE id='route-a'").fetchone())
            field=research.award_field(f.game,db,player,fort).quest.quest_id
            story=stories.child_id(stories.root_id('route-story',0),0)
            rows=db.execute('SELECT * FROM research_tasks WHERE player_id=? AND id IN (?,?)',(player,field,story)).fetchall()
            self.assertEqual([research.task_proto(f.game,row).quest_type for row in rows],[65,65])
        f.start()
        for i in range(1,26):f.fix(i)
        with f.db.transaction() as db:db.execute('UPDATE items SET count=9999 WHERE player_id=?',(player,))
        self.assertEqual(f.progress(True).status,2)
        with f.db.transaction() as db:
            self.assertEqual(db.execute('SELECT SUM(progress) FROM research_tasks WHERE player_id=? AND id IN (?,?)',(player,field,story)).fetchone()[0],0)
            db.execute('UPDATE items SET count=0 WHERE player_id=?',(player,))
        claimed=f.progress(True);self.assertEqual(claimed.status,1)
        self.assertEqual(f.progress(True),claimed)
        with f.db.transaction() as db:
            progress=[row[0] for row in db.execute('SELECT progress FROM research_tasks WHERE player_id=? AND id IN (?,?)',(player,field,story))]
            self.assertEqual(progress,[1,1])
            self.assertEqual(db.execute('SELECT SUM(progress) FROM research_tasks WHERE player_id=?',(f.players[1],)).fetchone()[0],None)

if __name__=='__main__':unittest.main()
