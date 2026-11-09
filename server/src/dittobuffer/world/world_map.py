"""Local Lightship vector bundles derived from a cached ODbL OSM extract.

Only the supplied APK's exercised wire fields are generated. Original opaque
game assets are untouched. The local adapter recognizes our plaintext marker.
"""
import gzip
import hashlib
import math
import os
from pathlib import Path
import shutil
import struct
import threading
import xml.etree.ElementTree as ET
from functools import lru_cache
from protocol.opaque import byte_field,fields,varint

from dittobuffer.paths import ROOT
WORLD=None
BASE_URL='http://127.0.0.1:8080/map/tiles'
# Generated bundles are persisted so a restarted server, a second trainer and
# a re-request after an epoch change all read from disk instead of re-clipping
# every OSM way for the tile.
TILE_CACHE=ROOT/'data/maps/tile-cache'
TILE_CACHE_EPOCHS=3

def configure(store,config=None):
    global WORLD,BASE_URL
    WORLD=store if store and store.path else None
    if config:
        network=config.get('network',{})
        scheme='https' if network.get('mode')=='tls' else 'http'
        host=str(config.get('map_host') or config.get('host','127.0.0.1'))
        if host in ('0.0.0.0','::',''):host='127.0.0.1'
        BASE_URL=f'{scheme}://{host}:{int(config.get("port",8080))}/map/tiles'
    invalidate()


def area_files():
    path=ROOT/'data/maps/areas'
    return sorted(path.glob('*.osm.gz')) if path.is_dir() else []


def invalidate():
    epoch.cache_clear();raw_tile.cache_clear();tile.cache_clear();_has_world_features.cache_clear();_file_roads.cache_clear()
def number(tag,value):return varint(tag*8)+varint(value)
def text(tag,value):return byte_field(tag,value.encode())
def real(tag,value):return varint(tag*8+1)+struct.pack('<d',value)

@lru_cache(maxsize=1)
def epoch():
    # The SDK caches map data by an area epoch.  Newly downloaded OSM cells must
    # change that key, otherwise the client keeps an empty cached area.
    parts=[]
    if WORLD:parts.append(f'snapshot:{WORLD.epoch}')
    path=ROOT/'data/maps/osm.xml'
    if path.exists():parts.append(f'{path.stat().st_size}:{path.stat().st_mtime_ns}')
    for item in area_files():parts.append(f'{item.name}:{item.stat().st_size}:{item.stat().st_mtime_ns}')
    digest=hashlib.sha256(('|'.join(parts)+':native-north-up-v3').encode()).digest()
    return 1+int.from_bytes(digest[:4],'little')%2147483646

def settings(base_url=None):
    if not WORLD and not (ROOT/'data/maps/osm.xml').exists() and not area_files():return b''
    # Field 3 is the path template appended to the base URL.  It must expand
    # to z/x/y: with '/%d/%s' the client could not build a tile address and
    # silently asked for no tiles at all, which read as "roads never render".
    provider=(text(1,'Dittobuffer OSM')+text(2,base_url or BASE_URL)+
        text(3,'/%d/%d/%d')+number(6,5)+number(8,16)+number(9,18)+
        byte_field(10,number(1,16)+number(2,18)))
    bounds=b''.join(real(n,v) for n,v in enumerate((85,-85,180,-180),1))
    area=text(1,'Dittobuffer local map')+number(2,epoch())+text(3,'Dittobuffer OSM')+byte_field(4,bounds)+text(6,'0.429.2')
    # Root cctor 0x9402688: area=1, providers=2, named=3, biome=4.
    return byte_field(1,area)+byte_field(2,provider)

FOOTWAY_KINDS=('footway','steps','path','cycleway','bridleway','corridor','sidewalk')
MAJOR_KINDS=('motorway','trunk','motorway_link','trunk_link')
MIDDLE_KINDS=('primary','secondary','tertiary','primary_link','secondary_link','tertiary_link')


def _road_kind(highway):
    """Native layer 7 only defines kinds 21, 28, 30, 35 and 36.

    Footways, steps and pedestrian streets used to be emitted as kind 15,
    which no client material defines, so small paths silently vanished.  Every
    other small street (residential, service, living_street, track,
    unclassified, road, busway) renders as the 30 material, and linked ramps
    keep the class of the road they belong to.
    """
    if highway in FOOTWAY_KINDS:return 35
    if highway=='pedestrian':return 36
    if highway in MAJOR_KINDS:return 21
    if highway in MIDDLE_KINDS:return 28
    return 30


def _read_osm(path,seen=None):
    try:
        if path.suffix=='.gz':
            with gzip.open(path,'rb') as stream:osm=ET.parse(stream).getroot()
        else:osm=ET.parse(path).getroot()
    except (OSError,ET.ParseError):
        return []
    nodes={n.attrib['id']:(float(n.attrib['lat']),float(n.attrib['lon'])) for n in osm.findall('node')}
    result=[]
    for index,way in enumerate(osm.findall('way')):
        ident=way.attrib.get('id') or f'{path.name}:{index}'
        if seen is not None:
            if ident in seen:continue
            seen.add(ident)
        tags={t.attrib['k']:t.attrib['v'] for t in way.findall('tag')}
        highway=tags.get('highway')
        if not highway or highway in ('proposed','construction','raceway'):continue
        kind=_road_kind(highway)
        points=[nodes[n.attrib['ref']] for n in way.findall('nd') if n.attrib['ref'] in nodes]
        if len(points)>=2:result.append((ident,kind,points))
    return result

# Downloaded cells use the same 0.006 degree grid as osm_cache.  Keeping the
# number local avoids importing the downloader while it invalidates this module.
AREA_CELL_DEG=0.006

@lru_cache(maxsize=256)
def _file_roads(filename,stamp):
    return tuple(_read_osm(Path(filename)))

def _area_bounds(path):
    try:lat,lng=(int(part) for part in path.name.split('.')[0].split('_'))
    except (TypeError,ValueError):return None
    return (lat*AREA_CELL_DEG-AREA_CELL_DEG/2,lng*AREA_CELL_DEG-AREA_CELL_DEG/2,
            lat*AREA_CELL_DEG+AREA_CELL_DEG/2,lng*AREA_CELL_DEG+AREA_CELL_DEG/2)

def _area_files_for(bounds):
    south,west,north,east=bounds
    margin=AREA_CELL_DEG
    result=[]
    for path in area_files():
        area=_area_bounds(path)
        if area is None:continue
        bottom,left,top,right=area
        if bottom<=north+margin and top>=south-margin and left<=east+margin and right>=west-margin:
            result.append(path)
    return result

def roads(bounds=None):
    """Roads near one tile; omitting bounds reads the complete extract."""
    seen=set();result=[]
    paths=[]
    path=ROOT/'data/maps/osm.xml'
    if path.exists():paths.append(path)
    paths.extend(_area_files_for(bounds) if bounds else area_files())
    for path in paths:
        stamp=path.stat().st_mtime_ns,path.stat().st_size
        for ident,kind,points in _file_roads(path.as_posix(),stamp):
            if ident in seen:continue
            seen.add(ident);result.append((kind,points))
    return result

def xy(lat,lng,z):
    n=2**z
    return (lng+180)/360*n,(1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n

def _lonlat(x,y,z):
    n=2**z
    lng=x/n*360-180
    lat=math.degrees(math.atan(math.sinh(math.pi*(1-2*y/n))))
    return lat,lng

def clip(a,b):
    # Liang-Barsky clipping preserves the actual OSM road at tile boundaries.
    dx,dy=b[0]-a[0],b[1]-a[1];lo,hi=0.,1.
    for p,q in ((-dx,a[0]),(dx,1-a[0]),(-dy,a[1]),(dy,1-a[1])):
        if p==0:
            if q<0:return None
        else:
            t=q/p
            if p<0:lo=max(lo,t)
            else:hi=min(hi,t)
    if lo>=hi:return None
    return [(a[0]+lo*dx,a[1]+lo*dy),(a[0]+hi*dx,a[1]+hi*dy)]

@lru_cache(maxsize=512)
def _has_world_features(store,bounds):
    try:return bool(store.features(bounds,(2,4,7,9),limit=1))
    except (OSError,ValueError):return False

@lru_cache(maxsize=2048)
def raw_tile(z,x,y):
    if not 16<=z<=18 or not 0<=x<2**z or not 0<=y<2**z:raise ValueError('Invalid map tile')
    if WORLD:
        from dittobuffer.world.world_tiles import raw_tile as geographic_tile,lonlat
        west,north=lonlat(x,y,z);east,south=lonlat(x+1,y+1,z)
        # A regional snapshot only covers its own extract.  Outside it, fall
        # back to the per-area OSM cache instead of returning an empty green
        # tile for the whole world.
        if _has_world_features(WORLD,(west,south,east,north)):
            return geographic_tile(WORLD,z,x,y)
    features=[]
    south,west=_lonlat(x,y+1,z);north,east=_lonlat(x+1,y,z)
    for kind,points in roads((south,west,north,east)):
        points=[(a-x,b-y) for a,b in (xy(lat,lng,z) for lat,lng in points)]
        for a,b in zip(points,points[1:]):
            segment=clip(a,b)
            if not segment:continue
            # Native local Z runs north from the tile's south edge.
            coords=[round(max(0,min(1,a))*4096)|(round(max(0,min(1,1-b))*4096)<<16) for a,b in segment]
            # Polyline cctor 0x9417000 is ForFixed32(tag=10), not uint32.
            line=byte_field(1,b''.join(struct.pack('<I',v) for v in coords))
            geometry=byte_field(2,byte_field(1,line))
            features.append(number(1,kind)+byte_field(2,geometry)+byte_field(5,b''))
    layer=number(1,7)+b''.join(byte_field(2,f) for f in features)
    return number(1,z)+number(2,x)+number(3,y)+byte_field(4,layer)

def _cache_file(z,x,y,epoch,tile_zoom):
    return TILE_CACHE/f'{int(epoch)}/{int(z)}/{int(x)}/{int(y)}.{int(tile_zoom)}.bin'


def _read_cache(path):
    try:
        payload=path.read_bytes()
        return payload or None
    except OSError:
        return None


def _write_cache(path,payload):
    # Concurrent tile requests for the same bundle race here; a per-thread
    # temporary plus an atomic replace keeps every reader seeing a full file.
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_name(f'{path.name}.{os.getpid()}-{threading.get_ident()}.tmp')
        temporary.write_bytes(payload)
        os.replace(temporary,path)
        return True
    except OSError:
        return False


def tile_cache_status():
    """What the persisted tile cache on disk currently holds."""
    if not TILE_CACHE.is_dir():
        return {'files':0,'bytes':0,'epochs':[],'directory':'data/maps/tile-cache'}
    files=list(TILE_CACHE.glob('*/*/*/*.bin'))
    epochs=sorted((item.name for item in TILE_CACHE.iterdir() if item.is_dir() and item.name.isdigit()),
                  key=int,reverse=True)
    return {'files':len(files),'bytes':sum(item.stat().st_size for item in files),
            'epochs':epochs[:TILE_CACHE_EPOCHS],'directory':'data/maps/tile-cache'}


def prune_tile_cache(keep=TILE_CACHE_EPOCHS):
    """Drop superseded epochs; a new OSM cell changes the epoch anyway."""
    if not TILE_CACHE.is_dir():
        return []
    epochs=sorted((item for item in TILE_CACHE.iterdir() if item.is_dir() and item.name.isdigit()),
                  key=lambda item:int(item.name),reverse=True)
    removed=[]
    for item in (epochs[keep:] if keep>0 else epochs):
        shutil.rmtree(item,ignore_errors=True)
        removed.append(item.name)
    return removed


def clear_tile_cache():
    removed=prune_tile_cache(0)
    shutil.rmtree(TILE_CACHE,ignore_errors=True)
    return {'removed':removed,**tile_cache_status()}


@lru_cache(maxsize=64)
def tile(z,x,y,epoch=1,tile_zoom=None):
    tile_zoom=z if tile_zoom is None else tile_zoom
    if not 13<=z<=18 or not 16<=tile_zoom<=18 or not 0<=tile_zoom-z<=3 or not 0<=x<2**z or not 0<=y<2**z:raise ValueError('Invalid tile bundle')
    path=_cache_file(z,x,y,epoch,tile_zoom)
    cached=_read_cache(path)
    if cached is not None:return cached
    width=2**(tile_zoom-z)
    children=[raw_tile(tile_zoom,a,b) for a in range(x*width,(x+1)*width) for b in range(y*width,(y+1)*width)]
    bundle=number(1,1)+number(2,tile_zoom)+number(3,z)+number(4,x)+number(5,y)+number(6,epoch)+b''.join(byte_field(7,t) for t in children)
    # Own public-data encoding, deliberately distinguished from private feeds.
    payload=number(1,1)+number(2,z)+number(3,x)+number(4,y)+number(5,epoch)+byte_field(6,b'LNLPMAP1'+bundle)
    # Never persist a featureless tile: the client may request a tile before
    # its OSM cells arrive, and a saved empty bundle would then hide the roads
    # for the rest of that epoch.
    if _has_features(children):
        _write_cache(path,payload)
    return payload


def _has_features(children):
    for child in children:
        for tag,_,_,layer in fields(child):
            if tag!=4:continue
            if any(inner==2 for inner,_,_,_ in fields(layer)):return True
    return False
