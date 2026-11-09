"""Native empty-waypoint recording contract, recovery and authority checks."""
import unittest,importlib.util
from tests import test_routes as support
from dittobuffer.gameplay.game import p,ROOT
from dittobuffer.gameplay import route_creator as c

class RouteRecordingTests(unittest.TestCase):
    def setUp(self):
        self.f=support.RouteTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f;self.draft=f.call(1413,p.CreateRouteDraftProto(start_anchor=f.route.start_poi.anchor),p.CreateRouteDraftOutProto).updated_route.route
    def update(self,pause=None):
        request=p.UpdateRouteDraftProto(proposed_route_draft=self.draft)
        if pause is not None:request.pause=pause
        out=self.f.call(1400,request,p.UpdateRouteDraftOutProto)
        if out.result==1:self.draft.CopyFrom(out.updated_route.route)
        return out
    def test_empty_native_draft_projects_observed_trace_pause_and_resume(self):
        f=self.f
        with f.db.transaction() as db:db.execute("UPDATE routes SET status='ARCHIVED' WHERE id='unit-route'")
        for i in range(1,9):f.fix(i)
        self.assertEqual(self.update(True).result,1);count=len(self.draft.waypoints)
        self.assertGreater(count,2)
        f.fix(9);self.assertEqual(self.update(False).result,1)
        for i in range(10,26):f.fix(i)
        # Actual native requests carry the previous server projection while
        # new accepted transport samples continue extending the recording.
        self.draft.name='Native projected test';self.draft.end_poi.CopyFrom(f.route.end_poi)
        self.assertEqual(self.update().result,1);self.assertGreater(len(self.draft.waypoints),count)
        self.assertAlmostEqual(self.draft.route_distance_meters,278,delta=10)
        submitted=f.call(1402,p.SubmitRouteDraftProto(route_id=self.draft.id,route_version=self.draft.version),p.SubmitRouteDraftOutProto)
        self.assertEqual(submitted.result,11)
    def test_explicit_unobserved_geometry_is_not_replaced_or_approved(self):
        self.draft.name='Malicious fixture';self.draft.end_poi.CopyFrom(self.f.route.end_poi)
        self.draft.waypoints.add(lat_degrees=52.08,lng_degrees=5.12)
        self.draft.waypoints.add(lat_degrees=52.081,lng_degrees=5.121)
        self.draft.waypoints.add(lat_degrees=52.0825,lng_degrees=5.12)
        self.assertEqual(self.update().result,1)
        out=self.f.call(1402,p.SubmitRouteDraftProto(route_id=self.draft.id,route_version=self.draft.version),p.SubmitRouteDraftOutProto)
        self.assertEqual(out.result,3)
    def test_identical_published_path_is_rejected_without_overwriting_it(self):
        f=self.f
        for i in range(1,26):f.fix(i)
        self.draft.name='Duplicate fixture';self.draft.end_poi.CopyFrom(f.route.end_poi)
        self.assertEqual(self.update().result,1)
        out=f.call(1402,p.SubmitRouteDraftProto(route_id=self.draft.id,route_version=self.draft.version),p.SubmitRouteDraftOutProto)
        self.assertEqual(out.result,3)
        with f.db.transaction() as db:self.assertEqual(db.execute("SELECT status FROM routes WHERE id='unit-route'").fetchone()[0],'PUBLISHED')
    def test_normalization_is_idempotent_and_matches_checked_in_bindings(self):
        spec=importlib.util.spec_from_file_location('routes_normalizer',ROOT/'scripts/normalize-routes-protocol.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        original=(ROOT/'protocol/game.proto').read_text();self.assertEqual(module.normalize_routes(original),original)
        self.assertEqual(p.UpdateRouteDraftProto.DESCRIPTOR.fields_by_name['proposed_route_draft'].number,2)

if __name__=='__main__':unittest.main()
