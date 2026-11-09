"""Licensed point origins and real Gift schema; no Android acceptance implied."""
import json,random,unittest
from unittest.mock import patch
from tests import test_route_mateo as support
from dittobuffer.gameplay.game import p,Game
from dittobuffer.core.database import Database
from dittobuffer.gameplay import world_postcards as w,vivillon as v,gifts,route_mateo as m

class WorldPostcardTests(unittest.TestCase):
    def setUp(self):
        self.fixture=support.MateoTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.f=self.fixture.f;f=self.f
        f.game.config['vivillon']={'enabled':True};v.install(f.game)
        f.game.routes['mateo']['world_postcards']={'enabled':True,'selection_mode':'RANDOM_WORLD'}
        f.game.config['routes']=dict(f.game.routes)
    def test_all_patterns_reviewed_city_coordinates_and_nonhabitats(self):
        anchors={'ARCHIPELAGO':(18.44,-66.13),'CONTINENTAL':(52.352,4.915),'ELEGANT':(35.687,139.749),'GARDEN':(51.502,-.119),'HIGH_PLAINS':(37.784,-122.4),'ICY_SNOW':(64.198,-51.733),'JUNGLE':(-6.172,106.827),'MARINE':(38.725,-9.147),'MEADOW':(48.858,2.353),'MODERN':(33.739,-84.368),'MONSOON':(25.036,121.568),'OCEAN':(21.303,-157.858),'POLAR':(45.419,-75.702),'RIVER':(-34.933,138.598),'SANDSTORM':(25.215,55.287),'SAVANNA':(-23.557,-46.627),'SUN':(-12.425,130.85),'TUNDRA':(64.143,-21.937)}
        self.assertEqual(set(anchors),set(w.PATTERNS));self.assertEqual(len(w.registry()['origins']),18)
        for key,point in anchors.items():self.assertEqual(v.classify(*point),w.PATTERNS[key],key)
        for point in [(0,0),(-82,0),(0,-140),(float('nan'),0)]:self.assertEqual(v.classify(*point),0)
        self.assertEqual(len({v.form_for(region,666) for region in w.PATTERNS.values()}),18)
    def test_virtual_exchange_needs_no_global_fort_or_other_participant(self):
        f=self.f;fixture=self.fixture
        with f.db.transaction() as db:
            db.execute('DELETE FROM route_gift_pool')
            before=db.execute('SELECT COUNT(*) FROM forts').fetchone()[0]
        with patch('dittobuffer.gameplay.world_postcards.choose',return_value=next(x for x in w.registry()['origins'] if x['region']=='OCEAN')):
            received=fixture.send();self.assertEqual(received.result,1)
        box=received.retrived_giftbox.gift_box
        self.assertTrue(box.fort_id.startswith('world-postcard:'));self.assertFalse(box.sender_id);self.assertFalse(box.sender_codename)
        self.assertIn('Honolulu',box.fort_name);self.assertEqual(fixture.send(),received)
        with f.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM forts').fetchone()[0],before)
            self.assertFalse(db.execute('SELECT 1 FROM forts WHERE id=?',(box.fort_id,)).fetchone())
            self.assertEqual(w.origin(db,box.giftbox_id)['region'],'OCEAN')
            pinned=gifts.postcard(f.game,db,f.players[0],p.CreatePostcardProto(giftbox_id=box.giftbox_id).SerializeToString())
            self.assertEqual(pinned.result,1);self.assertEqual(pinned.butterfly_collector_updated_region.region,w.PATTERNS['OCEAN'])
            self.assertFalse(pinned.postcard.postcard_creator_codename)
            foreign=gifts.postcard(f.game,db,f.players[1],p.CreatePostcardProto(giftbox_id=box.giftbox_id).SerializeToString());self.assertEqual(foreign.result,3)
        self.assertEqual(fixture.open().result,1);self.assertEqual(fixture.open().result,1)
        restarted=Game(f.game.config,Database(f.db.path))
        with restarted.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM world_postcard_gifts').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT progress FROM vivillon_regions WHERE player=?',(f.players[0],)).fetchone()[0],1)
            self.assertEqual(m.send(restarted,db,f.players[0],p.NpcSendGiftProto(encounter_id=fixture.enc,fort_id='route-b').SerializeToString()),received)
    def test_balanced_region_first_selection_and_collection_bias(self):
        f=self.f
        with f.db.transaction() as db:
            self.assertEqual(set(w.distribution(f.game,db,f.players[0]).values()),{1})
            drawn={w.choose(f.game,db,f.players[0],random.Random(seed))['region'] for seed in range(250)}
            self.assertEqual(drawn,set(w.PATTERNS))
            db.execute('INSERT INTO vivillon_regions(player,region,rank,updated_ms) VALUES(?,?,1,0)',(f.players[0],w.PATTERNS['OCEAN']))
            cfg=f.game.routes['mateo']['world_postcards'];cfg.update(selection_mode='MISSING_REGION_BIAS',missing_multiplier=2)
            weights=w.distribution(f.game,db,f.players[0]);self.assertEqual(weights['OCEAN'],1);self.assertEqual(weights['TUNDRA'],2)
            cfg['selection_mode']='STRICT_RANDOM';self.assertEqual(set(w.distribution(f.game,db,f.players[0]).values()),{1})
    def test_bad_selection_configuration_rejected(self):
        self.f.game.routes['mateo']['world_postcards']['selection_mode']='UNKNOWN'
        with self.assertRaises(ValueError):w.settings(self.f.game)

if __name__=='__main__':unittest.main()
