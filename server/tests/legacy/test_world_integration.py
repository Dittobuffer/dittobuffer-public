"""Independent geographic, wire, publication-state and reward invariants."""
from datetime import datetime,timezone
import json
from pathlib import Path
import random
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch
from shapely import from_wkb
from shapely.geometry import LineString,Point,Polygon
from protocol.opaque import fields
from dittobuffer.world.world_store import ROOT,SCHEMA,CONTROL_SCHEMA,WorldStore,decision,layers,connect
from dittobuffer.world.world_tiles import polygon_wire
from dittobuffer.world.world_map import xy
from dittobuffer.world.world_weather import condition,daylight
from dittobuffer.world.world_rewards import awards


class WorldIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'builds');self.root=Path(self.temp.name)
        self.path=self.root/'world.sqlite3';self.control=self.root/'control.sqlite3'
        with connect(self.path) as db:db.executescript(SCHEMA)
        with connect(self.control) as db:db.executescript(CONTROL_SCHEMA)
        self.manifest=self.root/'world.json';self.manifest.write_text(json.dumps(dict(database=self.path.relative_to(ROOT).as_posix(),epoch=1401)))
        self.store=WorldStore(dict(enabled=True,manifest=self.manifest.relative_to(ROOT).as_posix(),control=self.control.relative_to(ROOT).as_posix()))

    def tearDown(self):self.temp.cleanup()

    def test_published_snapshot_rejects_changed_bytes_and_reimport(self):
        from dittobuffer.world.world_store import snapshot_sha,require_unpublished
        value=dict(database=self.path.relative_to(self.root).as_posix(),epoch=1401,published='test',sha256=snapshot_sha(self.path))
        pointer=self.root/'data/world/current.json';pointer.parent.mkdir(parents=True);pointer.write_text(json.dumps(value))
        with patch('server.world_store.ROOT',self.root):
            with self.assertRaisesRegex(ValueError,'immutable'):require_unpublished(self.path)
            store=WorldStore(dict(enabled=True));self.assertEqual(store.path,self.path)
            with connect(self.path) as db:db.execute("INSERT INTO metadata VALUES('changed','true')")
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):WorldStore(dict(enabled=True))

    def test_native_palette_preserves_geometry_and_changes_cache_epoch(self):
        palette=self.root/'palette.json';palette.write_text(json.dumps(dict(kinds={'4:33':20})))
        cfg=dict(enabled=True,manifest=self.manifest.relative_to(ROOT).as_posix(),palette=palette.relative_to(ROOT).as_posix())
        selected=WorldStore(cfg)
        self.assertEqual(selected.render_kind(4,33),20);self.assertEqual(selected.render_kind(9,32),32)
        self.assertNotEqual(selected.epoch,self.store.epoch)
        palette.write_text(json.dumps(dict(kinds={'7:30':32})))
        with self.assertRaisesRegex(ValueError,'same semantic layer'):WorldStore(cfg)

    def test_weather_failure_is_backed_off_between_map_requests(self):
        from dittobuffer.world.world_weather import WeatherService
        class Offline:
            name='isolated-offline-test'
            calls=0
            def fetch(self,*args):self.calls+=1;raise ValueError('offline fixture')
        provider=Offline();service=WeatherService(dict(cache=(self.root/'weather.sqlite3').relative_to(ROOT).as_posix()),provider)
        with self.assertLogs(level='WARNING'):
            self.assertEqual(service.snapshot(52.1,5.1)['condition'],0)
            service.pool.shutdown(wait=True)
        self.assertEqual(service.snapshot(52.1,5.1)['condition'],0)
        self.assertEqual(provider.calls,1);self.assertFalse(service.pending)

    def test_supplied_placeholder_is_byte_preserved_and_shared(self):
        if not (ROOT/'pokestopimage.png').is_file():
            self.skipTest('Optional user-supplied image is not distributed with source')
        import hashlib
        from dittobuffer.world.world_images import placeholder
        value=placeholder();self.assertEqual(value['body'],(ROOT/'pokestopimage.png').read_bytes())
        self.assertEqual(value['sha256'],hashlib.sha256(value['body']).hexdigest())
        self.assertEqual((value['width'],value['height']),(1067,1067))
        self.poi();a=self.store.details('osm:n1')
        self.assertEqual(a['url'],value['url']);self.assertEqual(a['name'],'Public sculpture')
        policy=json.loads((ROOT/'server/world-images.json').read_text())
        self.assertIs(policy['automatic_acquisition'],False)

    def test_weather_fixture_rejects_normal_trainer_and_never_writes_api_cache(self):
        from dittobuffer.world.world_weather import WeatherService
        fixture=self.root/'weather-fixture.json';cache=self.root/'weather-cache.sqlite3'
        cfg=dict(cache=cache.relative_to(ROOT).as_posix(),fixture_file=fixture.relative_to(ROOT).as_posix())
        with self.assertRaisesRegex(ValueError,'isolated'):WeatherService(cfg)
        context=dict(profile='m14-isolated-weather',fixture_player='isolated',database=self.path.relative_to(ROOT).as_posix())
        service=WeatherService(cfg,fixture_context=context)
        for code,expected in [(0,1),(3,4),(63,2),(73,6),(45,7)]:
            fixture.write_text(json.dumps(dict(weather_code=code,cloud_cover=100,rain=2,snowfall=2,wind_speed_10m=0,wind_direction_10m=90)))
            sample=service.snapshot(52.1,5.1);self.assertEqual(sample['condition'],expected)
            self.assertEqual(sample['source'],'ISOLATED_RENDER_FIXTURE');self.assertFalse(service.pending)
        fixture.write_text(json.dumps(dict(weather_code=0,cloud_cover=100,rain=-1,snowfall=0,wind_speed_10m=0,wind_direction_10m=0)))
        with self.assertRaisesRegex(ValueError,'Invalid isolated'):service.snapshot(52.1,5.1)
        with connect(cache) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM weather').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0],0)
        service.pool.shutdown()

    def feature(self,identity,layer,kind,shape,tags=None):
        with connect(self.path) as db:
            cursor=db.execute('INSERT INTO features(osm,layer,kind,geometry,tags) VALUES(?,?,?,?,?)',(identity,layer,kind,shape.wkb,json.dumps(tags or {})))
            a,b,c,d=shape.bounds;db.execute('INSERT INTO feature_bounds VALUES(?,?,?,?,?)',(cursor.lastrowid,a,c,b,d))

    def poi(self,kind=1,status='APPROVED'):
        with connect(self.path) as db:db.execute('INSERT INTO pois VALUES(?,?,?,?,?,?,?,?,?,?,?)',('osm:n1','Public sculpture',52.1,5.1,'artwork',95,status,'named_public_poi_near_walkable_way','{}',kind,'PHOTO_PENDING'))

    def test_unsafe_candidates_and_generic_shops_never_auto_approve(self):
        self.assertIsNone(decision(dict(shop='supermarket',name='Generic supermarket')))
        for tags in [dict(access='private'),dict(foot='no'),dict(amenity='school'),dict(building='house')]:
            result=decision(dict(tourism='artwork',name='Unsafe')|tags)
            self.assertEqual(result[2],'REJECTED')
        self.assertEqual(decision(dict(tourism='artwork',name='Actual named sculpture'))[2],'REVIEW')
        self.assertEqual(decision(dict(leisure='sports_centre',sport='yoga',name='Generic yoga studio'))[3],'commercial_sports_access_requires_review')
        self.assertEqual(decision(dict(amenity='arts_centre',name='Ambiguous school'))[3],'possible_school_requires_review')

    def test_native_layer_classification_distinguishes_parks_water_and_buildings(self):
        self.assertEqual(layers(dict(leisure='park'),True),[(4,33)])
        self.assertEqual(layers(dict(natural='water',water='canal'),True),[(9,2)])
        self.assertEqual(layers(dict(building='yes'),True),[(2,63)])
        self.assertEqual(layers(dict(highway='cycleway')),[(7,35)])

    def test_native_triangle_literal_fields_and_hole_area(self):
        z=17;tx,ty=xy(52.1,5.1,z);x=int(tx);y=int(ty)
        from dittobuffer.world.world_tiles import lonlat
        points=[lonlat(x+a,y+b,z) for a,b in [(0.1,.1),(.9,.1),(.9,.9),(.1,.9)]]
        hole=[lonlat(x+a,y+b,z) for a,b in [(.3,.3),(.7,.3),(.7,.7),(.3,.7)]]
        encoded=polygon_wire(Polygon(points,[hole]),z,x,y)
        self.assertEqual(encoded[0],26) # Geometry triangles = field 3, length-delimited.
        triangles=next(v for n,_,_,v in fields(encoded) if n==3)
        values={n:v for n,_,_,v in fields(triangles)}
        self.assertEqual(set(values),{3,4})
        packed=struct.unpack('<'+'I'*(len(values[3])//4),values[3]);self.assertEqual(len(packed),len(values[4])*3)
        coordinates=[((v&65535)/4096,(v>>16)/4096) for v in packed]
        actual=sum(Polygon(coordinates[i:i+3]).area for i in range(0,len(coordinates),3))
        self.assertAlmostEqual(actual,.64-.16,delta=.001)
        self.assertTrue(all(not Polygon(coordinates[i:i+3]).contains(Point(.5,.5)) for i in range(0,len(coordinates),3)))

    def test_spatial_index_bounds_and_feature_budget(self):
        self.feature('w1',7,30,LineString([(5.1,52.1),(5.101,52.101)]))
        self.feature('w2',7,30,LineString([(6.1,53.1),(6.101,53.101)]))
        self.assertEqual([r['osm'] for r in self.store.features((5.09,52.09,5.11,52.11),(7,))],['w1'])
        with self.assertRaises(ValueError):self.store.features((3,50,8,54),limit=1)

    def test_clipped_tile_seam_is_not_a_physical_exterior_edge(self):
        from dittobuffer.world.world_tiles import lonlat
        z=17;tx,ty=xy(52.1,5.1,z);x=int(tx);y=int(ty)
        source=Polygon([lonlat(x+a,y+b,z) for a,b in [(.2,.2),(1.8,.2),(1.8,.8),(.2,.8)]])
        masks=[]
        for offset in (0,1):
            west,north=lonlat(x+offset,y,z);east,south=lonlat(x+offset+1,y+1,z)
            from shapely.geometry import box
            clipped=source.intersection(box(west,south,east,north))
            encoded=polygon_wire(clipped,z,x+offset,y,source.boundary)
            data={n:v for n,_,_,v in fields(next(v for n,_,_,v in fields(encoded) if n==3))}
            values=struct.unpack('<'+'I'*(len(data[3])//4),data[3]);points=[((v&65535)/4096,(v>>16)/4096) for v in values]
            for index,mask in enumerate(data[4]):
                tri=points[index*3:index*3+3]
                for edge,(a,b) in enumerate(zip(tri,tri[1:]+tri[:1])):
                    if a[0]==b[0]==(1 if offset==0 else 0):self.assertFalse(mask&(1<<edge),'Clip seam must not extrude a wall')
                masks.append(mask)
        self.assertTrue(any(masks),'Real source edges must remain marked')

    def test_world_is_opt_in_and_missing_snapshots_cannot_masquerade_as_imported(self):
        self.assertIsNone(WorldStore().path)
        with self.assertRaises(ValueError):WorldStore(dict(enabled=True,manifest=self.manifest.relative_to(ROOT).as_posix()+'-missing-invalid'))

    def test_pending_photos_and_credits_are_explicit(self):
        self.poi();pending=self.store.details('osm:n1')
        self.assertIn('PHOTO_PENDING',pending['description']);self.assertIn('/world/placeholders/',pending['url'])
        record=dict(status='SUBJECT_REVIEW',sha256='a'*64,attribution='Actual photographer / CC BY 4.0',license_url='https://creativecommons.org/licenses/by/4.0/',source_url='https://commons.wikimedia.org/wiki/File:Exact.jpg')
        with connect(self.control) as db:db.execute('INSERT INTO photos VALUES(?,?)',('osm:n1',json.dumps(record)))
        self.assertIsNone(self.store.photo_by_hash('a'*64))
        record['status']='VERIFIED'
        with connect(self.control) as db:db.execute('UPDATE photos SET record=?',(json.dumps(record),))
        accepted=self.store.details('osm:n1');self.assertIn('Actual photographer',accepted['description']);self.assertIn('/world/photos/',accepted['url'])

    def test_manual_retirement_survives_reopening_source(self):
        self.poi()
        with connect(self.control) as db:db.execute('INSERT INTO overrides VALUES(?,?,?,?)',('osm:n1',json.dumps(dict(status='RETIRED',name='Corrected name')),'access no longer public','2026-10-22'))
        self.assertEqual(self.store.pois(52.1,5.1),[])
        again=WorldStore(dict(enabled=True,manifest=self.manifest.relative_to(ROOT).as_posix(),control=self.control.relative_to(ROOT).as_posix()))
        self.assertEqual(again.poi('osm:n1')['name'],'Corrected name')

    def test_materialization_never_changes_attached_fort_kind_or_deletes_history(self):
        self.poi(kind=1)
        db=connect(':memory:');db.row_factory=sqlite3.Row
        db.executescript('CREATE TABLE forts(id TEXT PRIMARY KEY,kind INTEGER,name TEXT,lat REAL,lng REAL); CREATE TABLE history(fort TEXT,event TEXT);')
        db.execute('INSERT INTO forts VALUES(?,0,?,?,?)',('osm:n1','Original gym',52.1,5.1));db.execute('INSERT INTO history VALUES(?,?)',('osm:n1','defender attached'))
        self.store.materialize(db,52.1,5.1)
        self.assertEqual(db.execute('SELECT kind FROM forts').fetchone()[0],0)
        self.assertEqual(db.execute('SELECT event FROM history').fetchone()[0],'defender attached');db.close()

    def test_spawn_anchors_need_walkable_evidence_and_reject_water(self):
        self.assertIsNone(self.store.spawn_point(52.1,5.1,random.Random(1)))
        road=LineString([(5.0998,52.1),(5.1002,52.1)])
        self.feature('w1',7,35,road,dict(highway='footway'))
        self.assertIsNotNone(self.store.spawn_point(52.1,5.1,random.Random(1)))
        self.feature('w2',9,58,Polygon([(5.09,52.09),(5.11,52.09),(5.11,52.11),(5.09,52.11)]))
        self.assertIsNone(self.store.spawn_point(52.1,5.1,random.Random(1)))

    def test_reward_level_gates_and_total_are_source_backed(self):
        class Game:config={}
        for level in (1,4,5,8,12,18,20):
            for stamp in range(30):
                reward=awards(Game(),'trainer','osm:n1',stamp,level)
                self.assertEqual(sum(reward.values()),3);self.assertGreaterEqual(reward[1],1)
                allowed={1}|({703} if level>=4 else set())|({101,201} if level>=5 else set())|({701} if level>=8 else set())|({2} if level>=12 else set())|({705} if level>=18 else set())|({3} if level>=20 else set())
                self.assertLessEqual(set(reward),allowed)
        self.assertEqual(awards(Game(),'t','f',1,20),awards(Game(),'t','f',1,20))

    def test_weather_mapping_and_dutch_solar_time(self):
        for code,wanted in [(0,1),(3,4),(45,7),(65,2),(75,6)]:self.assertEqual(condition(dict(weather_code=code)),wanted)
        self.assertEqual(condition(dict(weather_code=0,wind_speed_10m=40)),5)
        noon=daylight(52.1,5.1,datetime(2026,10,22,12,tzinfo=timezone.utc).timestamp())
        night=daylight(52.1,5.1,datetime(2026,10,22,0,tzinfo=timezone.utc).timestamp())
        self.assertTrue(noon['day']);self.assertFalse(night['day']);self.assertTrue(noon['sunrise'].endswith('+02:00'))
        winter=daylight(52.1,5.1,datetime(2026,11,2,12,tzinfo=timezone.utc).timestamp());self.assertTrue(winter['sunrise'].endswith('+01:00'))

    def geographic_game(self):
        from dittobuffer.gameplay.game import Game,wire
        from dittobuffer.core.database import Database
        cfg=json.loads((ROOT/'server/config.json').read_text())
        cfg['world']=dict(enabled=True,manifest=self.manifest.relative_to(ROOT).as_posix(),control=self.control.relative_to(ROOT).as_posix())
        cfg['weather']={'enabled':False}
        game=Game(cfg,Database(self.root/'trainer.sqlite3'));ticket=game.authenticate(wire.AuthRequest(auth_id='world-invariant')).ticket
        with game.db.transaction() as db:player=db.execute('SELECT player_id FROM sessions WHERE ticket=?',(ticket,)).fetchone()[0]
        return game,player,ticket

    def deploy_geographic_defender(self):
        from dittobuffer.gameplay import gyms
        from dittobuffer.gameplay.game import p,now_ms
        from dittobuffer.gameplay.species import make_pokemon
        self.poi(kind=0);game,player,ticket=self.geographic_game()
        with game.db.transaction() as db:
            game.world.materialize(db,52.1,5.1)
            db.execute('UPDATE players SET xp=10000 WHERE id=?',(player,));db.execute('UPDATE trainer_teams SET team=1 WHERE player_id=?',(player,))
            db.execute('INSERT INTO spawns VALUES(14001,?,7,52.1,5.1,?)',('explicit-test-owned-origin',now_ms()+100000))
            mon=make_pokemon(p,game.species[7],7,15,[10]*3);mon.id=1
            db.execute('INSERT INTO pokemon VALUES(1,?,14001,7,?)',(player,mon.SerializeToString()))
            out=gyms.deploy(game,db,player,p.GymDeployProto(fort_id='osm:n1',pokemon_id=1,player_lat_degrees=52.1,player_lng_degrees=5.1).SerializeToString())
            self.assertEqual(out.result,1)
        return game,player,mon

    def test_source_review_does_not_remove_defenders_or_ownership(self):
        game,player,mon=self.deploy_geographic_defender()
        with connect(self.path) as db:db.execute("UPDATE pois SET status='REVIEW'")
        with game.db.transaction() as db:
            game.world.reconcile_retired(game,db,player)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gym_defenders').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT team FROM gyms WHERE fort_id='osm:n1'").fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gym_returns').fetchone()[0],0)

    def test_explicit_retirement_returns_owned_defender_once_without_fainting(self):
        from dittobuffer.core.management import owned
        game,player,mon=self.deploy_geographic_defender()
        with connect(self.control) as db:db.execute('INSERT INTO overrides VALUES(?,?,?,?)',('osm:n1',json.dumps(dict(status='RETIRED')),'confirmed closed public access','2026-10-22'))
        with game.db.transaction() as db:
            game.world.reconcile_retired(game,db,player);game.world.reconcile_retired(game,db,player)
            returned=owned(db,player,1)
            self.assertEqual(returned.stamina,mon.stamina);self.assertFalse(returned.deployed_fort_id)
            self.assertEqual(returned.cp,mon.cp);self.assertEqual(db.execute('SELECT COUNT(*) FROM pokemon').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gym_returns').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gym_defenders').fetchone()[0],0)

    def test_geographic_spin_wire_replay_distance_cooldown_and_restart(self):
        from dittobuffer.gameplay.game import p,wire,Game
        self.poi();game,player,ticket=self.geographic_game()
        with game.db.transaction() as db:game.world.materialize(db,52.1,5.1)
        far=p.FortSearchProto(id='osm:n1',player_lat_degrees=52.11,player_lng_degrees=5.1)
        with game.db.transaction() as db:self.assertEqual(game.rpc_fort_search(db,player,far.SerializeToString()).result,2)
        near=p.FortSearchProto(id='osm:n1',player_lat_degrees=52.1,player_lng_degrees=5.1)
        request=wire.RpcRequest(rpc_id=1);request.requests.add(method=101,body=near.SerializeToString())
        first=game.rpc(request,ticket);repeat=game.rpc(request,ticket)
        self.assertEqual(first.SerializeToString(),repeat.SerializeToString())
        out=p.FortSearchOutProto.FromString(first.payloads[0].payload);self.assertEqual(sum(v.item_count for v in out.items),3)
        with game.db.transaction() as db:
            before=list(db.execute('SELECT item,count FROM items ORDER BY item'))
            self.assertEqual(game.rpc_fort_search(db,player,near.SerializeToString()).result,3)
            self.assertEqual(db.execute('SELECT stops FROM players WHERE id=?',(player,)).fetchone()[0],1)
        restarted=Game(game.config,game.db)
        with restarted.db.transaction() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT item,count FROM items ORDER BY item')],[tuple(r) for r in before])


if __name__=='__main__':unittest.main()
