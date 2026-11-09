"""Pure, read-only temporal/geographic rule resolution. Intervals are [start,end)."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import math
from zoneinfo import ZoneInfo
from .store import digest

BONUSES=('catch_xp','catch_dust','catch_candy','transfer_candy','evolve_xp')

def instant(value,zone='Europe/Amsterdam',fold=None):
    if type(value) is int:return value
    if not isinstance(value,str):raise ValueError('Date/time is required')
    try:d=datetime.fromisoformat(value.replace('Z','+00:00'));tz=ZoneInfo(zone)
    except (ValueError,KeyError):raise ValueError('Invalid date or time zone') from None
    if d.tzinfo is None:
        options=[]
        for f in (0,1):
            x=d.replace(tzinfo=tz,fold=f)
            if x.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)==d:options.append(x)
        if not options:raise ValueError('This local time does not exist because of daylight saving')
        if len({x.utcoffset() for x in options})>1 and fold not in (0,1):raise ValueError('Ambiguous daylight-saving time: choose earlier or later')
        d=d.replace(tzinfo=tz,fold=fold or 0)
    return int(d.timestamp()*1000)

def interval(document):
    zone=document.get('timezone','Europe/Amsterdam');ZoneInfo(zone)
    start=instant(document.get('start'),zone,document.get('fold'));end=instant(document.get('end'),zone,document.get('fold'))
    if end<=start:raise ValueError('End must be after start')
    return start,end

def validate_scope(scope):
    if not isinstance(scope,dict) or scope.get('type','global') not in ('global','polygon','biome','region'):raise ValueError('Unknown geographic scope')
    if scope.get('type')=='polygon':
        if 'geometry' in scope:
            from shapely.geometry import shape
            from shapely import get_num_coordinates
            geometry=shape(scope['geometry'])
            if geometry.geom_type not in ('Polygon','MultiPolygon') or geometry.is_empty or not geometry.is_valid or get_num_coordinates(geometry)>20000:raise ValueError('Invalid or oversized imported polygon')
            a,b,c,d=geometry.bounds
            if not all(math.isfinite(v) for v in (a,b,c,d)) or not -180<=a<=c<=180 or not -90<=b<=d<=90:raise ValueError('Invalid imported coordinates')
            return
        points=scope.get('points',[])
        if not 3<=len(points)<=500:raise ValueError('Draw a polygon with 3 to 500 vertices')
        for v in points:
            if not isinstance(v,list) or len(v)!=2 or any(type(n) not in (int,float) or not math.isfinite(n) for n in v) or not -180<=v[0]<=180 or not -90<=v[1]<=90:raise ValueError('Invalid polygon coordinates')
        from shapely.geometry import Polygon
        shape=Polygon(points)
        if not shape.is_valid or shape.area==0:raise ValueError('Polygon must not intersect itself or have zero area')
    if scope.get('type')=='region' and not scope.get('id'):raise ValueError('Choose a registered region')
    if scope.get('type')=='biome' and scope.get('biome') not in ('forest','park','urban','water','beach','mountain','grassland'):raise ValueError('Unknown habitat')

def matches(scope,lat,lng,biome=None,regions=()):
    kind=scope.get('type','global')
    if kind=='global':return True
    if lat is None or lng is None:return False
    if kind=='biome':return scope['biome']==biome
    if kind=='region':return any(v.get('id')==scope['id'] and matches(v,lat,lng,biome,()) for v in regions)
    if kind=='polygon':
        from shapely.geometry import Polygon,Point,shape
        area=shape(scope['geometry']) if 'geometry' in scope else Polygon(scope['points'])
        return area.covers(Point(lng,lat))
    return False

def resolve(publications,at,lat=None,lng=None,biome=None,regions=(),entitled=lambda key:False):
    selected=[];upcoming=[]
    by_id={row['id']:row['document'] for row in publications}
    for row in publications:
        spec=row['document']
        if spec['kind']=='research':continue
        start,end=interval(spec)
        parent=spec.get('season_id')
        if parent and (parent not in by_id or not interval(by_id[parent])[0]<=at<interval(by_id[parent])[1]):continue
        if parent and (not matches(by_id[parent].get('scope',{}),lat,lng,biome,regions) or (by_id[parent].get('entitlement') and not entitled(by_id[parent]['entitlement']))):continue
        if start>at:upcoming.append(start)
        if not start<=at<end:continue
        if spec.get('entitlement') and not entitled(spec['entitlement']):continue
        if not matches(spec.get('scope',{}),lat,lng,biome,regions):continue
        rules=deepcopy(spec.get('rules',{}));sources=[row['id']+':'+str(row['revision'])+':'+str(row.get('activation',0))]
        for index,week in enumerate(spec.get('weeks',[])):
            local=datetime.fromtimestamp(start/1000,ZoneInfo(spec.get('timezone','Europe/Amsterdam')))
            ws=int((local+timedelta(weeks=index)).timestamp()*1000);we=int((local+timedelta(weeks=index+1)).timestamp()*1000)
            if ws<=at<min(we,end):rules.update(deepcopy(week.get('rules',{})));sources.append('week:'+str(index+1));upcoming.append(min(we,end))
        for rotation in spec.get('rotations',[]):
            rs=start+rotation['offset_minutes']*60000;re=min(end,rs+rotation['duration_minutes']*60000)
            if rs<=at<re:rules.update(deepcopy(rotation['rules']));sources.append('rotation:'+rotation['id']);upcoming.append(re)
            elif rs>at:upcoming.append(rs)
        selected.append((spec.get('priority',10 if spec['kind']=='season' else 50),row['id'],rules,sources))
        upcoming.append(end)
    selected.sort(key=lambda row:(row[0],row[1]))
    result={'sources':[],'bonuses':{k:1 for k in BONUSES},'research':[],'warnings':[],'generations':{},'next_boundary_ms':min(upcoming) if upcoming else None}
    for _,key,rules,sources in selected:
        result['sources'].extend(sources)
        for name,value in rules.items():
            if name=='bonuses':
                for bonus,factor in value.items():
                    old=result['bonuses'].get(bonus,1)
                    policy=rules.get('bonus_policy','max')
                    result['bonuses'][bonus]=min(100,old*factor) if policy=='multiply' else factor if policy=='replace' else max(old,factor)
            elif name=='research':result['research']=list(dict.fromkeys(result['research']+value))
            elif name in ('spawns','raids','max','eggs','rocket'):
                if name in result:result['warnings'].append(name+' replaced by '+key)
                result[name]=deepcopy(value)
                result['generations'][name]=int(digest({'sources':sources,'rules':value})[:12],16)
    result['generation']=int(digest({k:v for k,v in result.items() if k not in ('next_boundary_ms','warnings')})[:12],16)
    return result
