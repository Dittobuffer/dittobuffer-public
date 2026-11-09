"""Commit/rollback visibility and replay semantics for future event consumers."""
import sqlite3,unittest
from tests import test_routes as support
from dittobuffer.gameplay import routes,exploration_events as events
from dittobuffer.gameplay.game import p

class ExplorationEventTests(unittest.TestCase):
    def setUp(self):
        self.f=support.RouteTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.game.config['routes']['event_journal']=True
    def test_events_commit_once_with_authoritative_route_receipts(self):
        f=self.f;self.assertEqual(f.start().status,1);f.start()
        for index in range(1,26):f.fix(index)
        f.progress(True);f.progress(True)
        with f.db.transaction() as db:
            rows=list(db.execute('SELECT kind,COUNT(*) FROM exploration_events GROUP BY kind'))
            self.assertEqual(dict(rows),{'route.started':1,'route.progressed':5,'route.completed':1})
            self.assertTrue(all(r[0] in events.KINDS for r in db.execute('SELECT kind FROM exploration_events')))
    def test_uncommitted_and_rolled_back_events_are_never_visible(self):
        f=self.f
        with self.assertRaisesRegex(RuntimeError,'rollback'):
            with f.db.transaction() as db:
                result=routes.start(f.game,db,f.players[0],p.StartRouteProto(route_id='unit-route',entry_fort_id='route-a').SerializeToString());self.assertEqual(result.status,1)
                with sqlite3.connect(f.db.path) as other:self.assertEqual(other.execute('SELECT COUNT(*) FROM exploration_events').fetchone()[0],0)
                raise RuntimeError('rollback')
        with f.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM exploration_events').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM route_attempts').fetchone()[0],0)

if __name__=='__main__':unittest.main()
