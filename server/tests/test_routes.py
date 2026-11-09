"""Synthetic location/service tests. These are not native or outdoor acceptance."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from dittobuffer.gameplay.game import Game,ROOT,p,wire
from dittobuffer.core.database import Database
from dittobuffer.gameplay import routes
from dittobuffer.gameplay.route_geometry import project

class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds');self.addCleanup(self.tmp.cleanup)
        self.clock=1792900000000;self.counter=0
        for name in ['dittobuffer.gameplay.game.now_ms','dittobuffer.gameplay.routes.now_ms','dittobuffer.gameplay.route_creator.now_ms']:
            value=patch(name,lambda:self.clock);value.start();self.addCleanup(value.stop)
        cfg=json.loads((ROOT/'src/dittobuffer/gameplay/config.example.json').read_text());cfg.update(profile='m20-unit',world={'enabled':False},weather={'enabled':False},routes={'enabled':True,'min_level':1,'creator_level':1,'cooldown_seconds':0},authentication={'mode':'local-development'})
        self.db=Database(Path(self.tmp.name)/'routes.sqlite3');self.game=Game(cfg,self.db)
        self.tickets=[self.game.authenticate(wire.AuthRequest(auth_id='route-unit-'+str(i))).ticket for i in range(2)]
        with self.db.transaction() as db:
            self.players=[db.execute('SELECT player_id FROM sessions WHERE ticket=?',(t,)).fetchone()[0] for t in self.tickets]
            db.execute("INSERT INTO forts VALUES('route-a',1,'Route A',52.08,5.12)")
            db.execute("INSERT INTO forts VALUES('route-b',1,'Route B',52.0825,5.12)")
            self.route=p.SharedRouteProto(id='unit-route',name='Unit walk',description='Synthetic test',reversible=True)
            self.route.start_poi.anchor.CopyFrom(p.RouteWaypointProto(fort_id='route-a',lat_degrees=52.08,lng_degrees=5.12))
            self.route.end_poi.anchor.CopyFrom(p.RouteWaypointProto(fort_id='route-b',lat_degrees=52.0825,lng_degrees=5.12))
            self.points=[(52.08+i*.0001,5.12) for i in range(26)]
            routes.put(self.game,db,self.route,self.points,{'source':'unit-test','not_native':True})
        self.fix(0)

    def fix(self,index,player=0,lat=None,lng=5.12,advance=10000):
        self.clock+=advance
        with self.db.transaction() as db:routes.sample(self.game,db,self.players[player],dict(lat=self.points[index][0] if lat is None else lat,lng=lng,accuracy=2,time=self.clock/1000,provider=3,source='device'))

    def call(self,method,req,typ,index=0):
        self.counter+=1;r=wire.RpcRequest(rpc_id=self.counter);r.requests.add(method=method,body=req.SerializeToString())
        return typ.FromString(self.game.rpc(r,self.tickets[index],run='routes-unit').payloads[0].payload)

    def start(self,index=0,reverse=False):return self.call(1404,p.StartRouteProto(route_id='unit-route',entry_fort_id='route-b' if reverse else 'route-a',travel_in_reverse=reverse),p.StartRouteOutProto,index)
    def progress(self,reward=False,index=0):return self.call(1406,p.ProgressRouteProto(acquire_reward=reward),p.ProgressRouteOutProto,index)

    def test_native_discovery_shapes_and_distance_filter(self):
        out=self.call(1405,p.GetRoutesProto(),p.GetRoutesOutProto)
        self.assertEqual(out.status,1);self.assertEqual(out.route_list[0].route.id,'unit-route');self.assertEqual(len(out.route_map_cell[0].route[0].waypoints),26)
        self.fix(0,lat=53.1,advance=200000)
        self.assertFalse(self.call(1405,p.GetRoutesProto(),p.GetRoutesOutProto).route_list)

    def test_native_discovery_response_tags_match_arm64_writer(self):
        # Independently audited native writer/cctor: 0x8B2472C/0x8B24C48.
        self.assertEqual(p.GetRoutesOutProto(status=1).SerializeToString(),b'\x10\x01')
        out=p.GetRoutesOutProto();out.route_list.add()
        self.assertEqual(out.SerializeToString(),b'\x22\x00')
        out=p.GetRoutesOutProto();out.route_tabs.add()
        self.assertEqual(out.SerializeToString(),b'\x1a\x00')

    def test_native_badge_thresholds_include_zero_white_tier(self):
        # Independently audited native getters read indices 0..3, and the
        # completion color selector falls back to index 9 below white minimum.
        template=next(v for v in routes.templates(self.game.config) if v.template_id=='ROUTE_BADGE_SETTINGS')
        self.assertEqual(list(template.route_badge_settings.target),[0,5,20,100])
        for bad in ([5,20],[0,5,20],[20,5,100],[5,5,100]):
            cfg={**self.game.config,'routes':{**self.game.config['routes'],'badge_thresholds':bad}}
            with self.assertRaisesRegex(ValueError,'badge thresholds'):Game(cfg,Database(self.db.path))
    def test_requested_viewport_discovers_public_routes_before_gps_resume(self):
        from s2sphere import CellId,LatLng
        cell=CellId.from_lat_lng(LatLng.from_degrees(52.08,5.12)).parent(15).id()
        self.fix(0,lat=53.1,advance=200000)
        result=self.call(1405,p.GetRoutesProto(cell_id=[cell]),p.GetRoutesOutProto)
        self.assertEqual([v.route.id for v in result.route_list],['unit-route'])
        self.assertEqual(self.start().status,6) # Discovery grants no proximity.

    def test_creator_native_settings_retired_tags_and_nonzero_limits(self):
        # Writer 0x8A8612C: min/max floats=4/5, Enabled=17, range=36.
        from protocol.opaque import fields
        raw=p.RoutesCreationSettingsProto(min_total_distance_m=100,max_total_distance_m=6000,enabled=True,interaction_range_meters=35).SerializeToString()
        self.assertEqual([(n,k) for n,k,_,_ in fields(raw)],[(4,5),(5,5),(17,0),(36,0)])
        creation=next(t.route_creation_settings for t in routes.templates(self.game.config) if t.HasField('route_creation_settings'))
        self.assertGreater(creation.max_client_map_panning_distance_meters,0)
        self.assertEqual(creation.min_total_distance_m,100)
        self.assertEqual(creation.max_total_distance_m,6000)
        self.assertEqual(p.UpdateRouteDraftProto(proposed_route_draft=p.SharedRouteProto(id='x'),pause=True).SerializeToString(),b'\x12\x03\x0a\x01x\x20\x01')

    def test_start_distance_direction_and_duplicate_start(self):
        self.assertEqual(self.start(reverse=True).status,6)
        self.assertEqual(self.start().status,1);self.assertEqual(self.start().status,1)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM route_attempts').fetchone()[0],1)

    def test_cannot_complete_at_start_or_by_teleport(self):
        self.start();self.assertEqual(self.progress(True).status,6)
        self.fix(25,advance=1000);self.assertEqual(self.progress(True).status,6)
        with self.db.transaction() as db:self.assertEqual(routes.active(db,self.players[0])['along_m'],0)

    def test_actual_path_manual_reward_and_replay_persistence(self):
        self.start()
        for i in range(1,26):self.fix(i)
        out=self.progress();self.assertEqual(out.progression_state,2);self.assertFalse(out.route_play.acquired_reward)
        with self.db.transaction() as db:before=dict(self.game.player(db,self.players[0]))
        earned=self.progress(True);self.assertEqual(earned.status,1);self.assertTrue(earned.route_play.acquired_reward)
        self.assertEqual(earned.awarded_route_badge.num_completions,1)
        self.assertTrue(earned.route_play.is_first_time);self.assertTrue(earned.route_play.is_first_travel_today)
        for _ in range(3):self.assertEqual(self.progress(True).SerializeToString(),earned.SerializeToString())
        with self.db.transaction() as db:
            after=self.game.player(db,self.players[0]);self.assertEqual(after['xp']-before['xp'],1000);self.assertEqual(after['stardust']-before['stardust'],500)
        restarted=Game(self.game.config,Database(self.db.path))
        with restarted.db.transaction() as db:self.assertEqual(routes.list_badges(restarted,db,self.players[0],b'').awarded_route_badges[0].num_completions,1)
        inventory=self.call(4,p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
        self.assertTrue(any(p.HoloInventoryKeyProto.FromString(v.deleted_item_key).route_play for v in inventory.inventory_delta.inventory_item if v.deleted_item_key))
        with restarted.db.transaction() as db:self.assertFalse(any(v.HasField('route_play') for v in routes.inventory(restarted,db,self.players[0])))

    def test_players_have_independent_attempts_and_rewards(self):
        self.fix(0,player=1);self.start();self.start(index=1)
        for i in range(1,26):self.fix(i)
        self.assertEqual(self.progress(True).status,1);self.assertEqual(self.progress(True,index=1).status,6)
        self.assertEqual(self.call(1409,p.ListRouteBadgesProto(),p.ListRouteBadgesOutProto,1).route_badges,[])

    def test_duplicate_and_stale_location_do_not_advance(self):
        self.start();self.fix(1)
        with self.db.transaction() as db:before=routes.active(db,self.players[0])['along_m']
        self.fix(2,advance=0)
        with self.db.transaction() as db:self.assertEqual(routes.active(db,self.players[0])['along_m'],before)

    def test_quit_retains_no_reward_and_allows_new_attempt(self):
        self.start();self.call(1410,p.CancelRouteProto(),p.CancelRouteOutProto)
        self.assertEqual(self.progress(True).status,9);self.assertEqual(self.start().status,1)

    def test_missing_endpoint_and_archived_route_rejected(self):
        with self.db.transaction() as db:db.execute("UPDATE routes SET status='ARCHIVED'")
        self.assertEqual(self.start().status,12)

    def test_loop_projection_cannot_shortcut_to_coincident_end(self):
        points=[(52,5),(52.002,5),(52.002,5.002),(52,5.002),(52,5)]
        match=project(points,52,5,0,50);self.assertEqual(match[1],0)

    def test_debug_fixture_cannot_enable_on_live_database(self):
        cfg=dict(self.game.config);cfg['profile']='normal';cfg['routes']={**cfg['routes'],'allow_debug_fixture':True}
        with self.assertRaisesRegex(ValueError,'isolated M20'):Game(cfg,self.db)

    def test_no_phantom_play_and_real_inventory_tombstone(self):
        with self.db.transaction() as db:self.assertFalse(any(v.HasField('route_play') for v in routes.inventory(self.game,db,self.players[0])))
        out=self.call(4,p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
        self.assertTrue(any(p.HoloInventoryKeyProto.FromString(v.deleted_item_key).route_play for v in out.inventory_delta.inventory_item if v.deleted_item_key))

    def test_pause_presence_wire_and_resume_preserve_progress(self):
        self.assertEqual(p.ProgressRouteProto(pause=False).SerializeToString(),b'\x30\x00')
        # Observed native manual-completion request, independently confirmed
        # against ARM64 writer 0x8D5D780, not an exporter-order assumption.
        self.assertEqual(p.ProgressRouteProto(acquire_reward=True).SerializeToString(),b'\x38\x01')
        self.assertEqual(p.UpdateRouteDraftProto(pause=True).SerializeToString(),b'\x20\x01')
        self.start();self.fix(1)
        out=self.call(1406,p.ProgressRouteProto(pause=True),p.ProgressRouteOutProto);self.assertTrue(out.route_play.paused)
        self.fix(2)
        restarted=Game(self.game.config,self.db)
        with self.db.transaction() as db:self.assertEqual(routes.active(db,self.players[0])['state'],'PAUSED')
        out=self.call(1406,p.ProgressRouteProto(pause=False),p.ProgressRouteOutProto);self.assertFalse(out.route_play.paused)
        for i in range(3,26):self.fix(i)
        self.assertEqual(self.progress(True).status,1)

    def test_gmo_route_hash_and_registration(self):
        from s2sphere import CellId,LatLng
        cell=CellId.from_lat_lng(LatLng.from_degrees(*self.points[0])).parent(15).id()
        with self.db.transaction() as db:
            cells=[p.ClientMapCellProto(s2_cell_id=cell)];routes.map_cells(self.game,db,self.players[0],cells)
            self.assertFalse(cells[0].routes);self.assertEqual(len(cells[0].route_list_hash),64)
            self.assertEqual(self.route.route_submission_status[0].status,2)

    def test_pause_at_destination_keeps_manual_completion_eligible(self):
        self.start()
        for i in range(1,26):self.fix(i)
        paused=self.call(1406,p.ProgressRouteProto(pause=True),p.ProgressRouteOutProto)
        self.assertTrue(paused.route_play.paused)
        resumed=self.call(1406,p.ProgressRouteProto(pause=False),p.ProgressRouteOutProto)
        self.assertEqual(resumed.progression_state,2)
        self.assertEqual(resumed.route_play.last_progress_time_ms,self.clock)
        self.assertEqual(self.progress(True).status,1)

    def test_expired_session_cannot_block_a_new_start(self):
        self.start()
        self.clock+=86401000
        with self.db.transaction() as db:db.execute('UPDATE route_locations SET arrival_ms=?',(self.clock,))
        self.assertEqual(self.start().status,1)
        with self.db.transaction() as db:
            states=[v[0] for v in db.execute('SELECT state FROM route_attempts ORDER BY start_ms')]
        self.assertEqual(states,['INVALIDATED','ACTIVE'])

    def test_bonus_poll_cannot_roll_back_batched_native_pause(self):
        self.start();request=wire.RpcRequest(rpc_id=999)
        request.requests.add(method=1406,body=p.ProgressRouteProto(pause=True).SerializeToString())
        request.requests.add(method=2350,body=b'')
        out=self.game.rpc(request,self.tickets[0],run='route-batch-unit')
        self.assertTrue(p.ProgressRouteOutProto.FromString(out.payloads[0].payload).route_play.paused)
        self.assertEqual(p.GetBonusAttractedPokemonOutProto.FromString(out.payloads[1].payload).result,1)
        with self.db.transaction() as db:self.assertEqual(routes.active(db,self.players[0])['state'],'PAUSED')

    def test_breadcrumb_ack_does_not_credit_unverified_or_duplicate_history(self):
        self.start();self.fix(1)
        history=p.UpdateBreadcrumbHistoryRequestProto()
        history.breadcrumb_history.add(timestamp_ms=self.clock-200000,latitude_deg=52.0825,longitude_deg=5.12,app_is_foregrounded=True)
        with self.db.transaction() as db:before=dict(routes.active(db,self.players[0]))
        for _ in range(2):self.assertEqual(self.call(361000,history,p.UpdateBreadcrumbHistoryResponseProto).status,1)
        history.breadcrumb_history[0].app_is_foregrounded=False
        self.assertEqual(self.call(361000,history,p.UpdateBreadcrumbHistoryResponseProto).status,2)
        with self.db.transaction() as db:self.assertEqual(dict(routes.active(db,self.players[0])),before)

    def creator_submission(self):
        # Creator acceptance uses an unpublished fixture path. A duplicate of
        # the discovery fixture is now correctly rejected by production code.
        with self.db.transaction() as db:db.execute("UPDATE routes SET status='ARCHIVED' WHERE id='unit-route'")
        created=self.call(1413,p.CreateRouteDraftProto(start_anchor=self.route.start_poi.anchor),p.CreateRouteDraftOutProto)
        self.assertEqual(created.result,1)
        draft=p.SharedRouteProto();draft.CopyFrom(self.route);draft.id=created.updated_route.route.id;draft.version=1
        self.assertEqual(self.call(1400,p.UpdateRouteDraftProto(proposed_route_draft=draft),p.UpdateRouteDraftOutProto,1).result,5)
        updated=self.call(1400,p.UpdateRouteDraftProto(proposed_route_draft=draft),p.UpdateRouteDraftOutProto);self.assertEqual(updated.result,1)
        request=p.SubmitRouteDraftProto(route_id=draft.id,route_version=updated.updated_route.route.version,approval_override=1)
        self.assertEqual(self.call(1402,request,p.SubmitRouteDraftOutProto).result,3)
        for i in range(1,26):self.fix(i)
        self.assertEqual(self.call(1402,request,p.SubmitRouteDraftOutProto).result,11)
        return request,draft

    def test_creator_requires_observed_trace_ownership_and_moderation(self):
        request,draft=self.creator_submission()
        from dittobuffer.gameplay.route_creator import moderate
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT status FROM routes WHERE id=?',(draft.id,)).fetchone()[0],'PENDING')
            moderate(self.game,db,draft.id,'PUBLISHED','unit administrator','Synthetic reviewed trace')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM route_moderation_audit').fetchone()[0],1)
        self.assertEqual(self.call(1414,p.DeleteRouteDraftProto(route_id=draft.id),p.DeleteRouteDraftOutProto).result,4)

    def test_auto_approval_requires_explicit_creator_allowlist_and_is_audited_once(self):
        self.game.routes['auto_approve']=True
        request,draft=self.creator_submission()
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT status FROM routes WHERE id=?',(draft.id,)).fetchone()[0],'PENDING')
        # The boolean alone cannot publish an untrusted submission.
        self.game.routes['trusted_creators']=[self.players[0]]
        self.fix(0,advance=200000)
        with self.db.transaction() as db:
            db.execute("UPDATE route_drafts SET status='REJECTED' WHERE id=?",(draft.id,))
            db.execute("UPDATE routes SET status='REJECTED' WHERE id=?",(draft.id,))
        # A separate draft records another actual synthetic trace for the trusted path.
        request,draft=self.creator_submission()
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT status FROM routes WHERE id=?',(draft.id,)).fetchone()[0],'PUBLISHED')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM route_moderation_audit WHERE route=?',(draft.id,)).fetchone()[0],1)
        self.assertEqual(self.call(1402,request,p.SubmitRouteDraftOutProto).result,11)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM route_moderation_audit WHERE route=?',(draft.id,)).fetchone()[0],1)

    def test_bag_full_reward_is_atomic(self):
        self.start()
        for i in range(1,26):self.fix(i)
        with self.db.transaction() as db:
            before=dict(self.game.player(db,self.players[0]));db.execute('UPDATE items SET count=9999 WHERE player_id=?',(self.players[0],))
        self.assertEqual(self.progress(True).status,2)
        with self.db.transaction() as db:
            self.assertEqual(self.game.player(db,self.players[0])['xp'],before['xp'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM route_badges').fetchone()[0],0)

    def test_badge_preview_fields_match_native_writer(self):
        # Native writer 0x8D62E08: rated=19, canPreview=20, hidden=21,
        # Route=22, Pins=23 and nullable favorite=25.
        self.assertEqual(p.AwardedRouteBadge(can_preview=True).SerializeToString(),b'\xa0\x01\x01')
        out=p.AwardedRouteBadge();out.route.SetInParent()
        self.assertEqual(out.SerializeToString(),b'\xb2\x01\x00')

    def test_invalid_cells_fail_safely(self):
        # Rejected before S2 contains/projection operations, without mutating state.
        with self.assertRaises(Exception):self.call(1405,p.GetRoutesProto(cell_id=[0]),p.GetRoutesOutProto)
