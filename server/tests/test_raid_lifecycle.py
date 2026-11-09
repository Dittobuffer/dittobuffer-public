"""Persisted raid/map revision and ordinary Gym recovery (host tests)."""
import unittest
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tests import test_raids as fixture
from dittobuffer.gameplay.game import Game, p
from dittobuffer.gameplay import raids,gyms
from dittobuffer.gameplay.species import make_pokemon

class LifecycleTests(unittest.TestCase):
    setUp=fixture.RaidTests.setUp
    tearDown=fixture.RaidTests.tearDown
    call=fixture.RaidTests.call
    join=fixture.RaidTests.join
    start=fixture.RaidTests.start
    win=fixture.RaidTests.win

    def published_world(self, status=None):
        # A real, isolated snapshot next to the existing authoritative raid DB.
        # The local gym intentionally has no OSM entry, as on the normal phone.
        from dittobuffer.world.world_store import SCHEMA, WorldStore
        root=Path(self.tmp.name);snapshot=root/'world.sqlite3'
        with closing(sqlite3.connect(snapshot)) as world:
            world.executescript(SCHEMA)
        manifest=root/'world.json'
        manifest.write_text(json.dumps({'database':str(snapshot),'epoch':1,'published':False}))
        self.config['world']={'enabled':True,'manifest':str(manifest),'control':str(root/'control.sqlite3')}
        self.config['weather']={'enabled':False}
        self.game=Game(self.config,self.db)
        self.assertIsInstance(self.game.world,WorldStore)
        if status is not None:
            # Moderated OSM gyms still obey the snapshot's explicit status.
            with closing(sqlite3.connect(snapshot)) as world:
                world.execute("INSERT INTO pois VALUES('osm:raid-test','Gym',52.37,4.89,'gym',100,?,'test','{}',0,'PHOTO_PENDING')",(status,))
                world.commit()
            with self.db.transaction() as db:
                db.execute("INSERT INTO forts VALUES('osm:raid-test',0,'Moderated Gym',52.37,4.89)")
                db.execute("UPDATE raids SET fort_id='osm:raid-test'")

    def test_local_raid_entry_with_published_world_survives_repeat_and_restart(self):
        self.published_world()
        req=p.GetRaidDetailsProto(gym_id='gym',raid_seed=self.seed,player_lat_degrees=52.37,player_lng_degrees=4.89)
        for _ in range(3):
            details=self.call(163,req,p.GetRaidDetailsOutProto)
            self.assertEqual(details.result,1)
            self.assertEqual(details.raid_info.raid_seed,self.seed)
            self.assertTrue(details.is_within_plfe_range)
        simple=p.GetSimpleRaidDetailsProto(gym_id='gym',raid_seed=self.seed,gym_lat_degrees=52.37,gym_lng_degrees=4.89)
        self.assertEqual(self.call(3091,simple,p.GetSimpleRaidDetailsOutProto).result,1)
        self.assertEqual(self.join().result,1)
        self.assertEqual(self.start().result,1)
        self.game=Game(self.config,self.db)
        self.assertEqual(self.call(163,req,p.GetRaidDetailsOutProto).result,1)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM raid_pass_receipts').fetchone()[0],1)

    def test_local_raid_still_rejects_distant_or_invalid_locations(self):
        self.published_world()
        for lat,lng in [(0,0),(91,4.89),(52.37,float('nan'))]:
            details=p.GetRaidDetailsProto(gym_id='gym',raid_seed=self.seed,player_lat_degrees=lat,player_lng_degrees=lng)
            self.assertEqual(self.call(163,details,p.GetRaidDetailsOutProto).result,2)
            join=p.JoinLobbyProto(gym_id='gym',raid_seed=self.seed,player_lat_degrees=lat,player_lng_degrees=lng)
            self.assertEqual(self.call(159,join,p.JoinLobbyOutProto).result,2)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM raid_members').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM raid_pass_receipts').fetchone()[0],0)

    def test_published_moderation_still_blocks_raid_entry(self):
        for status in ['RETIRED','REJECTED','PENDING']:
            with self.subTest(status=status):
                if self.game.world.path is None:self.published_world(status)
                with closing(sqlite3.connect(self.game.world.path)) as world:
                    world.execute('UPDATE pois SET status=?',(status,))
                    world.commit()
                req=p.GetRaidDetailsProto(gym_id='osm:raid-test',raid_seed=self.seed,player_lat_degrees=52.37,player_lng_degrees=4.89)
                self.assertEqual(self.call(163,req,p.GetRaidDetailsOutProto).result,2)
                join=p.JoinLobbyProto(gym_id='osm:raid-test',raid_seed=self.seed,player_lat_degrees=52.37,player_lng_degrees=4.89)
                self.assertEqual(self.call(159,join,p.JoinLobbyOutProto).result,2)
                simple=p.GetSimpleRaidDetailsProto(gym_id='osm:raid-test',raid_seed=self.seed)
                self.assertEqual(self.call(3091,simple,p.GetSimpleRaidDetailsOutProto).result,6)

    def test_egg_active_expiry_monotonic_revision_defenders_battle_disc_restart(self):
        with self.db.transaction() as db:
            db.execute('UPDATE raids SET battle_ms=?,end_ms=?',(self.clock+1000,self.clock+3000))
            fort=gyms.gym(db,'gym');db.execute('UPDATE trainer_teams SET team=1 WHERE player_id=?',(self.players[0],))
            deployed=gyms.deploy(self.game,db,self.players[0],p.GymDeployProto(fort_id='gym',pokemon_id=1,player_lat_degrees=52.37,player_lng_degrees=4.89).SerializeToString())
            self.assertEqual(deployed.result,1)
            egg=gyms.map_fort(self.game,db,fort);self.assertTrue(egg.HasField('raid_info'))
            db.execute('UPDATE trainer_teams SET team=2 WHERE player_id=?',(self.players[1],))
            mon=make_pokemon(p,self.game.species[4],4,35,[15]*3);mon.id=2
            db.execute("INSERT INTO spawns VALUES(101,'opposing-party',4,52.37,4.89,?)",(self.clock+86400000,))
            db.execute('INSERT INTO pokemon VALUES(2,?,101,4,?)',(self.players[1],mon.SerializeToString()))
        req=p.GymStartSessionProto(gym_id='gym',attacking_pokemon_id=[2],defending_pokemon_id=1,player_lat_degrees=52.37,player_lng_degrees=4.89)
        self.clock+=1001
        self.assertEqual(self.call(157,req,p.GymStartSessionOutProto,1).result,15)
        with self.db.transaction() as db:active=gyms.map_fort(self.game,db,fort)
        self.assertGreater(active.last_modified_ms,egg.last_modified_ms)
        self.clock+=2000
        with self.db.transaction() as db:
            recovered=gyms.map_fort(self.game,db,fort)
            self.assertFalse(recovered.HasField('raid_info'));self.assertGreater(recovered.last_modified_ms,active.last_modified_ms)
            self.assertEqual(recovered.team,1);self.assertEqual(len(gyms.defenders(db,'gym')),1)
        self.game=Game(self.config,self.db)
        info=self.call(156,p.GymGetInfoProto(gym_id='gym'),p.GymGetInfoOutProto)
        self.assertEqual(len(info.gym_status_and_defenders.gym_defender),1)
        self.assertFalse(info.gym_status_and_defenders.pokemon_fort_proto.HasField('raid_info'))
        self.assertEqual(self.call(157,req,p.GymStartSessionOutProto,1).result,1)
        spin=p.FortSearchProto(id='gym',player_lat_degrees=52.37,player_lng_degrees=4.89)
        out=self.call(101,spin,p.FortSearchOutProto,1)
        self.assertEqual(out.result,1);self.assertEqual(out.xp_awarded,25);self.assertTrue(out.HasField('awarded_gym_badge'))
        self.assertEqual(self.call(101,spin,p.FortSearchOutProto,1).result,3)

    def test_empty_gym_expiry_revision_survives_restart(self):
        with self.db.transaction() as db:
            fort=gyms.gym(db,'gym');active=gyms.map_fort(self.game,db,fort)
            db.execute('UPDATE raids SET end_ms=?',(self.clock+5000,))
        self.clock+=5001;self.game=Game(self.config,self.db)
        with self.db.transaction() as db:
            out=gyms.map_fort(self.game,db,fort)
            self.assertFalse(out.HasField('raid_info'))
            self.assertGreater(out.last_modified_ms,active.last_modified_ms)
            self.assertEqual(out.last_modified_ms,self.clock-1)
            self.assertEqual(gyms.map_fort(self.game,db,fort).last_modified_ms,out.last_modified_ms)

    def test_expired_constant_development_seed_is_not_resurrected(self):
        self.game.raids['dev_raid']={'gym_prefix':'g','species':16,'active_days':1}
        with self.db.transaction() as db:
            db.execute('UPDATE raids SET end_ms=?',(self.clock-1,));fort=gyms.gym(db,'gym')
            row=raids.schedule(self.game,db,fort);self.assertGreater(row['end_ms'],self.clock)
        self.clock+=86400001
        with self.db.transaction() as db:
            self.assertIsNone(raids.schedule(self.game,db,fort))
            self.assertFalse(gyms.map_fort(self.game,db,fort).HasField('raid_info'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM raids').fetchone()[0],2)
            self.game.raids['dev_raid']['instance']='reviewed-next-window'
            next_raid=raids.schedule(self.game,db,fort)
            self.assertNotEqual(next_raid['seed'],row['seed'])
            self.assertGreater(next_raid['end_ms'],self.clock)

    def test_individual_victory_does_not_remove_the_global_raid(self):
        self.win()
        with self.db.transaction() as db:
            fort=gyms.gym(db,'gym');self.assertEqual(raids.schedule(self.game,db,fort)['seed'],self.seed)
            out=gyms.map_fort(self.game,db,fort)
            self.assertTrue(out.HasField('raid_info'));self.assertFalse(out.raid_info.complete)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM raid_members WHERE reward IS NOT NULL').fetchone()[0],1)

if __name__=='__main__':unittest.main()
