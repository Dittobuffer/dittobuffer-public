"""Geographic route projection; coordinates are latitude/longitude, never tile axes."""
import math
from dittobuffer.gameplay.game import distance

def lengths(points):
    result=[0.0]
    for a,b in zip(points,points[1:]):result.append(result[-1]+distance(*a,*b))
    return result

def project(points,lat,lng,minimum=0,maximum=float('inf')):
    cumulative=lengths(points);best=None
    scale=111195;longitude_scale=scale*math.cos(math.radians(lat))
    for index,(a,b) in enumerate(zip(points,points[1:])):
        if cumulative[index+1]<minimum or cumulative[index]>maximum:continue
        ax=(a[1]-lng)*longitude_scale;ay=(a[0]-lat)*scale
        bx=(b[1]-lng)*longitude_scale;by=(b[0]-lat)*scale
        dx=bx-ax;dy=by-ay;den=dx*dx+dy*dy
        t=max(0,min(1,-(ax*dx+ay*dy)/den)) if den else 0
        along=cumulative[index]+t*(cumulative[index+1]-cumulative[index])
        if not minimum<=along<=maximum:continue
        gap=math.hypot(ax+t*dx,ay+t*dy)
        if best is None or gap<best[0]:best=(gap,along,index)
    return best

def validate(points,min_m,max_m):
    if not 2<=len(points)<=20000:raise ValueError('Route needs 2–20000 points')
    if any(len(v)!=2 or any(type(n) not in (int,float) or not math.isfinite(n) for n in v) or not -85<=v[0]<=85 or not -180<=v[1]<=180 for v in points):raise ValueError('Invalid route coordinates')
    total=lengths(points)[-1]
    if not min_m<=total<=max_m:raise ValueError('Route distance outside configured limits')
    if any(distance(*a,*b)>250 for a,b in zip(points,points[1:])):raise ValueError('Disconnected route geometry')
    return total
