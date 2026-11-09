import struct,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from protocol.opaque import fields
from dittobuffer.world import world_map as maps

class WorldMapTests(unittest.TestCase):
    def tearDown(self):maps.raw_tile.cache_clear();maps.tile.cache_clear();maps.epoch.cache_clear()
    def test_composition_root_uses_apk_provider_tag_two(self):
        with patch('dittobuffer.world.world_map.Path.exists',return_value=True),patch('dittobuffer.world.world_map.epoch',return_value=123):raw=maps.settings()
        self.assertEqual([tag for tag,_,_,_ in fields(raw)],[1,2])
        area=next(value for tag,_,_,value in fields(raw) if tag==1)
        self.assertEqual(next(value for tag,_,_,value in fields(area) if tag==2),123)
        provider=next(value for tag,_,_,value in fields(raw) if tag==2)
        self.assertEqual(next(value for tag,_,_,value in fields(provider) if tag==6),5)

    def test_provider_can_advertise_the_plain_http_tile_origin(self):
        maps.epoch.cache_clear()
        with patch('dittobuffer.world.world_map.Path.exists',return_value=True),patch('dittobuffer.world.world_map.epoch',return_value=321):
            raw=maps.settings('http://127.0.0.1:8080/map/tiles')
        provider=next(value for tag,_,_,value in fields(raw) if tag==2)
        self.assertEqual(next(value for tag,_,_,value in fields(provider) if tag==2),b'http://127.0.0.1:8080/map/tiles')

    def test_changed_extract_changes_sdk_cache_epoch_after_restart(self):
        with tempfile.TemporaryDirectory(dir=maps.ROOT/'builds') as directory:
            root=Path(directory);source=root/'data/maps/osm.xml';source.parent.mkdir(parents=True)
            with patch('dittobuffer.world.world_map.ROOT',root):
                source.write_bytes(b'<osm version="0.6"/>');maps.epoch.cache_clear()
                first=maps.epoch();self.assertEqual(first,maps.epoch())
                source.write_bytes(b'<osm version="0.6"><node id="1"/></osm>')
                maps.epoch.cache_clear() # A new server process after refresh.
                second=maps.epoch();self.assertNotEqual(first,second)
                self.assertTrue(0<first<2**31 and 0<second<2**31)

    def test_polyline_coordinates_are_packed_fixed32(self):
        maps.raw_tile.cache_clear()
        with patch('dittobuffer.world.world_map.roads',return_value=[(30,[(0,0),(1,1)])]),patch('dittobuffer.world.world_map.xy',side_effect=[(.25,.25),(.75,.75)]):raw=maps.raw_tile(16,0,0)
        layer=next(v for n,_,_,v in fields(raw) if n==4)
        feature=next(v for n,_,_,v in fields(layer) if n==2)
        geometry=next(v for n,_,_,v in fields(feature) if n==2)
        lines=next(v for n,_,_,v in fields(geometry) if n==2)
        line=next(v for n,_,_,v in fields(lines) if n==1)
        self.assertEqual(line,b'\x0a\x08\x00\x04\x00\x0c\x00\x0c\x00\x04')
        self.assertEqual(maps.clip((-.5,.25),(1.5,.25)),[(0,.25),(1,.25)])

    def test_small_streets_and_footpaths_keep_real_native_kinds(self):
        source=('<osm version="0.6">'
                '<node id="1" lat="52.000" lon="4.000"/><node id="2" lat="52.001" lon="4.001"/><node id="3" lat="52.002" lon="4.002"/>'
                '<way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/></way>'
                '<way id="11"><nd ref="2"/><nd ref="3"/><tag k="highway" v="service"/></way>'
                '<way id="12"><nd ref="1"/><nd ref="3"/><tag k="highway" v="living_street"/></way>'
                '<way id="13"><nd ref="1"/><nd ref="3"/><tag k="highway" v="track"/></way>'
                '<way id="14"><nd ref="1"/><nd ref="3"/><tag k="highway" v="footway"/></way>'
                '<way id="15"><nd ref="1"/><nd ref="3"/><tag k="highway" v="steps"/></way>'
                '<way id="16"><nd ref="1"/><nd ref="3"/><tag k="highway" v="pedestrian"/></way>'
                '<way id="17"><nd ref="1"/><nd ref="3"/><tag k="highway" v="motorway_link"/></way>'
                '<way id="18"><nd ref="1"/><nd ref="3"/><tag k="highway" v="construction"/></way>'
                '<way id="19"><nd ref="1"/><nd ref="3"/><tag k="building" v="yes"/></way>'
                '</osm>')
        with tempfile.TemporaryDirectory(dir=maps.ROOT/'builds') as directory:
            path=Path(directory)/'cell.osm';path.write_text(source,encoding='utf8')
            kinds=[kind for _,kind,_ in maps._read_osm(path)]
        # Kind 15 was never a layer 7 material, so footways and pedestrian
        # streets used to vanish; small carriageways are the 30 class and ramps
        # keep the class of the road they belong to.
        self.assertEqual(kinds,[30,30,30,30,35,35,36,21])

    def test_generated_tiles_are_saved_and_served_from_disk(self):
        with tempfile.TemporaryDirectory(dir=maps.ROOT/'builds') as directory:
            cache=Path(directory)/'tile-cache'
            maps.tile.cache_clear();maps.raw_tile.cache_clear()
            # The mocked way must land inside the tile: a featureless bundle is
            # deliberately not persisted, so only real geometry is cached.
            with patch.object(maps,'TILE_CACHE',cache),patch('dittobuffer.world.world_map.roads',return_value=[(30,[(85.045,-179.997),(85.045,-179.99)])]),patch('dittobuffer.world.world_map.xy',side_effect=[(.25,.25),(.75,.75)]):first=maps.tile(16,0,0,7,16)
            saved=list(cache.glob('7/16/0/0.16.bin'))
            self.assertEqual(len(saved),1)
            self.assertEqual(saved[0].read_bytes(),first)
            # A later process with no OSM data at hand still serves the saved bundle.
            maps.tile.cache_clear()
            with patch.object(maps,'TILE_CACHE',cache),patch('dittobuffer.world.world_map.roads',return_value=[]):
                self.assertEqual(maps.tile(16,0,0,7,16),first)
            # An empty bundle must never be written: a tile requested before its
            # OSM cells arrive cannot hide the streets for the rest of the epoch.
            maps.tile.cache_clear()
            with patch.object(maps,'TILE_CACHE',cache),patch('dittobuffer.world.world_map.roads',return_value=[]):maps.tile(16,1,1,7,16)
            self.assertEqual(list(cache.glob('7/16/1/1.16.bin')),[])
            with patch.object(maps,'TILE_CACHE',cache):
                value=maps.tile_cache_status()
                self.assertEqual((value['files'],value['epochs']),(1,['7']))
                self.assertTrue(maps.clear_tile_cache()['removed'])
                self.assertEqual(maps.tile_cache_status()['files'],0)

    def test_bundle_contains_all_sixteen_neighbor_tiles(self):
        maps.raw_tile.cache_clear();maps.tile.cache_clear()
        with patch('dittobuffer.world.world_map.roads',return_value=[]):outer=maps.tile(15,16000,10000,1,17)
        encoded=next(v for n,_,_,v in fields(outer) if n==6)
        self.assertTrue(encoded.startswith(b'LNLPMAP1'))
        tiles=[dict((n,v) for n,_,_,v in fields(raw)) for tag,_,_,raw in fields(encoded[8:]) if tag==7]
        self.assertEqual(len(tiles),16)
        self.assertEqual({(t[1],t[2],t[3]) for t in tiles},{(17,x,y) for x in range(64000,64004) for y in range(40000,40004)})
        with self.assertRaises(ValueError):maps.tile(1,0,0,1,17)

if __name__=='__main__':unittest.main()
