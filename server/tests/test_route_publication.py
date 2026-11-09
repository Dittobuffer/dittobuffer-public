"""Real graph validation and isolated audited publication/rollback contracts."""
import json,sqlite3,unittest
from contextlib import closing
from pathlib import Path
from tests import test_routes as support
from dittobuffer.gameplay import route_publication as pub
from dittobuffer.gameplay.route_graph import Graph,SCHEMA

class RoutePublicationTests(unittest.TestCase):
    def setUp(self):
        self.f=support.RouteTests();self.f.setUp();self.addCleanup(self.f.doCleanups);f=self.f
        self.world=Path(f.tmp.name)/'world.sqlite3';self.graphpath=Path(f.tmp.name)/'graph.sqlite3'
        with closing(sqlite3.connect(self.world)) as db,db:
            db.execute('CREATE TABLE pois(id TEXT PRIMARY KEY,lat REAL,lng REAL,status TEXT)')
            db.executemany('INSERT INTO pois VALUES(?,?,?,?)',[('route-a',52.08,5.12,'APPROVED'),('route-b',52.0825,5.12,'APPROVED')])
        with closing(sqlite3.connect(self.graphpath)) as db,db:
            db.executescript(SCHEMA)
            for i,(lat,lng) in enumerate(f.points):db.execute('INSERT INTO nodes VALUES(?,?,?)',(i,lat,lng))
            for i in range(25):
                for a,b in [(i,i+1),(i+1,i)]:db.execute('INSERT INTO edges VALUES(?,?,1,11,11,?)',(a,b,'{"highway":"footway"}'))
        self.graph=Graph(self.graphpath);self.addCleanup(self.graph.db.close)
        self.candidate=dict(id='batch-route',name='Reviewed synthetic route',points=f.points,nodes=list(range(26)),reversible=True,start=dict(id='route-a',lat=52.08,lng=5.12),end=dict(id='route-b',lat=52.0825,lng=5.12),provenance={'source':'osm-pedestrian-graph-v1'})
        # Existing manual route is different. Its ownership/geometry is retained.
        with f.db.transaction() as db:db.execute("UPDATE routes SET geometry='[[52.08,5.12],[52.081,5.12]]' WHERE id='unit-route'")
    def test_graph_endpoints_direction_and_geometry_tampering(self):
        with closing(sqlite3.connect(self.world)) as world,world:
            self.assertTrue(pub.verify(self.graph,world,self.candidate,100,6000))
            bad=dict(self.candidate);bad['nodes']=[0,25]
            with self.assertRaises(ValueError):pub.verify(self.graph,world,bad,100,6000)
            world.execute("UPDATE pois SET status='REJECTED' WHERE id='route-b'")
            with self.assertRaises(ValueError):pub.verify(self.graph,world,self.candidate,100,6000)
    def test_stage_retry_review_and_rollback_preserve_manual_route(self):
        f=self.f;manifest={'world':pub.identity(self.world),'region':'synthetic'}
        with f.db.transaction() as db:
            batch=pub.stage(f.game,db,[self.candidate],self.graph,self.world,manifest)
            self.assertEqual(pub.stage(f.game,db,[self.candidate],self.graph,self.world,manifest),batch)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM route_generation_members').fetchone()[0],1)
            pub.decide(f.game,db,batch,{'batch-route':'PUBLISHED'},'test-reviewer','Explicit synthetic geometry review')
            pub.decide(f.game,db,batch,{'batch-route':'PUBLISHED'},'test-reviewer','Retry')
            pub.rollback(f.game,db,batch,'test-reviewer','Rollback synthetic batch')
            self.assertEqual(db.execute("SELECT status FROM routes WHERE id='batch-route'").fetchone()[0],'ARCHIVED')
            self.assertEqual(db.execute("SELECT status FROM routes WHERE id='unit-route'").fetchone()[0],'PUBLISHED')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM route_moderation_audit').fetchone()[0],2)
    def test_reversed_geometry_deduplicated_and_changed_review_refused(self):
        f=self.f
        with f.db.transaction() as db:
            batch=pub.stage(f.game,db,[self.candidate],self.graph,self.world,{'region':'one'})
            reverse=dict(self.candidate,id='reversed-route',points=f.points[::-1],nodes=list(range(26))[::-1],start=self.candidate['end'],end=self.candidate['start'])
            second=pub.stage(f.game,db,[reverse],self.graph,self.world,{'region':'two'})
            self.assertEqual(db.execute('SELECT COUNT(*) FROM route_generation_members WHERE batch=?',(second,)).fetchone()[0],0)
            db.execute("UPDATE routes SET proto=X'00' WHERE id='batch-route'")
            with self.assertRaises(ValueError):pub.decide(f.game,db,batch,{'batch-route':'PUBLISHED'},'reviewer','Changed after review')

if __name__=='__main__':unittest.main()
