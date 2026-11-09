"""Synthetic OSM fixtures; graph correctness is not a physical safety survey."""
import json,sqlite3,tempfile,unittest
from pathlib import Path
from dittobuffer.gameplay.game import ROOT
from dittobuffer.gameplay.route_graph import build,Graph,suitability

class RouteGraphTests(unittest.TestCase):
    def test_access_rules_are_conservative(self):
        for tags in [dict(highway='motorway'),dict(highway='path',foot='no'),dict(highway='footway',access='private'),dict(highway='cycleway'),dict(highway='service'),dict(highway='tertiary'),dict(highway='footway',**{'foot:conditional':'no @ night'})]:
            self.assertIsNone(suitability(tags)[0],tags)
        self.assertIsNotNone(suitability(dict(highway='cycleway',foot='yes'))[0])

    def test_osm_node_identity_directed_edges_barriers_and_resume(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'builds') as tmp:
            root=Path(tmp);source=root/'walk.osm'
            source.write_text('''<osm version="0.6">
<node id="1" lat="52" lon="5"/><node id="2" lat="52.001" lon="5"/><node id="3" lat="52.002" lon="5"/>
<node id="4" lat="52.001" lon="4.999"/><node id="5" lat="52.001" lon="5"/><node id="6" lat="52.001" lon="5.001"/>
<node id="7" lat="52.003" lon="5"><tag k="barrier" v="gate"/></node>
<way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><tag k="highway" v="footway"/><tag k="oneway:foot" v="yes"/></way>
<way id="11"><nd ref="4"/><nd ref="5"/><nd ref="6"/><tag k="highway" v="footway"/><tag k="bridge" v="yes"/><tag k="layer" v="1"/></way>
<way id="12"><nd ref="3"/><nd ref="7"/><tag k="highway" v="footway"/></way>
</osm>''',encoding='utf8')
            output=root/'graph.sqlite3';report=build(source,output);graph=Graph(output);self.addCleanup(graph.db.close)
            self.assertTrue(report['complete']);self.assertEqual(len(graph.path(1,3)['points']),3)
            self.assertFalse(graph.path(1,3)['reversible']);self.assertIsNone(graph.path(3,1))
            self.assertIsNone(graph.path(1,6));self.assertIsNone(graph.path(3,7))
            self.assertIsNone(graph.snap(53,6));self.assertEqual(graph.snap(52,5),1)
            count=report['directed_edges'];graph.db.close();self.assertEqual(build(source,output)['directed_edges'],count)

    def test_loop_uses_different_return_edges(self):
        from dittobuffer.gameplay.route_graph import SCHEMA
        with tempfile.TemporaryDirectory(dir=ROOT/'builds') as tmp:
            path=Path(tmp)/'graph.sqlite3'
            with sqlite3.connect(path) as db:
                db.executescript(SCHEMA)
                for i,lat,lng in [(1,52,5),(2,52.001,5),(3,52.001,5.001),(4,52,5.001)]:db.execute('INSERT INTO nodes VALUES(?,?,?)',(i,lat,lng))
                for a,b in [(1,2),(2,3),(3,4),(4,1)]:
                    for src,dst in [(a,b),(b,a)]:db.execute('INSERT INTO edges VALUES(?,?,?,?,?,?)',(src,dst,a,100,100,'{}'))
            db.close()
            graph=Graph(path)
            try:
                loop=graph.loop(1,3,600);self.assertEqual(loop['length_m'],400);self.assertEqual(loop['nodes'][0],loop['nodes'][-1]);self.assertEqual(len(set(loop['nodes'])),4)
            finally:graph.db.close()
