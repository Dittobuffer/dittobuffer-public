"""OSM geometry to the native Lightship triangle/polyline wire format."""
from collections import defaultdict
import math
import struct
from dittobuffer.world.world_map import number,byte_field,xy


def lonlat(x,y,z):
    n=2**z
    return x/n*360-180,math.degrees(math.atan(math.sinh(math.pi*(1-2*y/n))))


def packed(points):
    # Slippy tile Y increases south; native tile-local Z increases north from
    # the tile's south-west origin. The tile transforms themselves already
    # move south along negative Z, so top-origin vertices mirror every tile.
    return b''.join(struct.pack('<I',round(max(0,min(1,x))*4096)|(round(max(0,min(1,1-y))*4096)<<16)) for x,y in points)


def polygon_wire(shape,z,x,y,boundary=None):
    from shapely import constrained_delaunay_triangles
    from shapely.geometry import LineString
    vertices=[];exterior=[]
    boundary=shape.boundary if boundary is None else boundary
    for triangle in constrained_delaunay_triangles(shape).geoms:
        points=list(triangle.exterior.coords)[:3]
        projected=[(a-x,b-y) for a,b in (xy(lat,lng,z) for lng,lat in points)]
        area=(projected[1][0]-projected[0][0])*(projected[2][1]-projected[0][1])-(projected[2][0]-projected[0][0])*(projected[1][1]-projected[0][1])
        if abs(area)<1e-12:continue
        # packed() reflects Y. Keep positive XZ winding after reflection: the
        # native decoder emits indices 0,2,1 to produce upward-facing roofs.
        if area>0:points.reverse();projected.reverse()
        vertices.extend(projected)
        mask=0
        for index,(a,b) in enumerate(zip(points,points[1:]+points[:1])):
            if boundary.covers(LineString([a,b])):mask|=1<<index
        exterior.append(mask)
    # APK TriangleList cctor 0x9418450: packed fixed32 at field 3 (tag 26).
    # InternalWriteTo 0x941801C: exterior-edge byte mask at field 4 (tag 34).
    return byte_field(3,byte_field(3,packed(vertices))+byte_field(4,bytes(exterior))) if vertices else b''


def raw_tile(store,z,x,y):
    from shapely import from_wkb
    from shapely.geometry import box
    import json
    west,north=lonlat(x,y,z);east,south=lonlat(x+1,y+1,z)
    bounds=(west,south,east,north);clip=box(*bounds);layers=defaultdict(list)
    for row in store.features(bounds,(2,4,7,9)):
        original=from_wkb(row['geometry'])
        shape=original.intersection(clip)
        if shape.is_empty:continue
        tags=json.loads(row['tags'])
        geometries=list(shape.geoms) if shape.geom_type in ('MultiPolygon','MultiLineString','GeometryCollection') else [shape]
        for geometry in geometries:
            encoded=b''
            if geometry.geom_type=='Polygon':
                # Tile clipping creates artificial edges. The native builder
                # extrudes/uses flagged edges as real feature boundaries;
                # marking the clip seam makes false walls and area outlines.
                encoded=polygon_wire(geometry,z,x,y,original.boundary)
            elif geometry.geom_type=='LineString':
                points=[(a-x,b-y) for a,b in (xy(lat,lng,z) for lng,lat in geometry.coords)]
                encoded=byte_field(2,byte_field(1,byte_field(1,packed(points))))
            if not encoded:continue
            metadata=b''
            if row['layer']==2:
                try:height=float(tags.get('height','').removesuffix(' m'))
                except ValueError:height=0
                metadata=byte_field(4,number(1,round(height)) if 0<height<=500 else b'')
            elif row['layer']==7:
                metadata=byte_field(5,b'')
            layers[row['layer']].append(number(1,store.render_kind(row['layer'],row['kind']))+byte_field(2,encoded)+metadata)
    return number(1,z)+number(2,x)+number(3,y)+b''.join(byte_field(4,number(1,kind)+b''.join(byte_field(2,f) for f in features)) for kind,features in sorted(layers.items()))
