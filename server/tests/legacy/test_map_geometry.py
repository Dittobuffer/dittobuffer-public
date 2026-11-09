"""Native coordinate contract established by Pixel mesh captures, not wire-only area."""
import struct,unittest
from shapely.geometry import Polygon,LineString,Point,box
from protocol.opaque import fields
from dittobuffer.world.world_map import xy
from dittobuffer.world.world_tiles import lonlat,polygon_wire,raw_tile

class NativeMapGeometryTests(unittest.TestCase):
    z=17
    x,y=map(int,xy(52.0820115,5.1131852,z))

    def polygon(self,points,holes=()):
        return Polygon([lonlat(self.x+a,self.y+b,self.z) for a,b in points],
                       [[lonlat(self.x+a,self.y+b,self.z) for a,b in ring] for ring in holes])

    def decode(self,shape,offset=0):
        wire=polygon_wire(shape,self.z,self.x,self.y+offset)
        tri=next(v for n,_,_,v in fields(wire) if n==3)
        packed=next(v for n,_,_,v in fields(tri) if n==3)
        return [((v&65535)/4096,(v>>16)/4096) for v, in struct.iter_unpack('<I',packed)]

    def test_north_south_clips_meet_in_actual_native_world_coordinates(self):
        source=self.polygon([(.2,.2),(.8,.2),(.8,1.8),(.2,1.8)])
        # Captured 17/67397/43246 and /43247 origins, scale 62.5, positive local Z.
        origins=[-30.2734375,-92.7734375];edges=[]
        for offset in (0,1):
            west,north=lonlat(self.x,self.y+offset,self.z)
            east,south=lonlat(self.x+1,self.y+offset+1,self.z)
            points=self.decode(source.intersection(box(west,south,east,north)),offset)
            world=[origins[offset]+p[1]*62.5 for p in points]
            edges.append(min(world) if offset==0 else max(world))
        self.assertEqual(edges,[-30.2734375,-30.2734375],
                         'Top-origin Slippy Y must become bottom-origin native Z')

    def test_asymmetric_hole_and_triangle_winding_survive_native_projection(self):
        shape=self.polygon([(.1,.1),(.9,.1),(.9,.9),(.1,.9)],
                           [[(.2,.15),(.4,.15),(.4,.3),(.2,.3)]])
        points=self.decode(shape);triangles=[Polygon(points[i:i+3]) for i in range(0,len(points),3)]
        # North-side hole must stay on the north side (large native Z).
        self.assertFalse(any(t.contains(Point(.3,.775)) for t in triangles))
        self.assertTrue(any(t.contains(Point(.3,.225)) for t in triangles))
        for a,b,c in zip(points[::3],points[1::3],points[2::3]):
            # Native decoder emits indices 0,2,1; this signed area gives +Y normals.
            self.assertGreater((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]),0)

    def test_geographic_road_and_waterway_use_same_north_up_contract(self):
        line=LineString([lonlat(self.x+.3,self.y+.2,self.z),lonlat(self.x+.7,self.y+.8,self.z)])
        class Store:
            def features(s,*args):return [dict(geometry=line.wkb,tags='{}',layer=kind,kind=30 if kind==7 else 2) for kind in (7,9)]
            def render_kind(s,layer,kind):return kind
        tile=raw_tile(Store(),self.z,self.x,self.y)
        for layer in [v for n,_,_,v in fields(tile) if n==4]:
            feature=next(v for n,_,_,v in fields(layer) if n==2)
            geometry=next(v for n,_,_,v in fields(feature) if n==2)
            lines=next(v for n,_,_,v in fields(geometry) if n==2)
            line=next(v for n,_,_,v in fields(lines) if n==1)
            data=next(v for n,_,_,v in fields(line) if n==1)
            points=[((v&65535)/4096,(v>>16)/4096) for v, in struct.iter_unpack('<I',data)]
            self.assertAlmostEqual(points[0][1],.8,delta=1/4096)
            self.assertAlmostEqual(points[1][1],.2,delta=1/4096)

if __name__=='__main__':unittest.main()
