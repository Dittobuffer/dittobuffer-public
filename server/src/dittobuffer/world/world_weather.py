"""Shared coarse-cell weather cache; no per-player trails or blocking network RPCs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
import logging
import math
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo
from dittobuffer.world.world_store import project_path,connect


def daylight(lat,lng,stamp=None):
    from astral import Observer
    from astral.sun import sun
    now=datetime.fromtimestamp(stamp if stamp is not None else time.time(),timezone.utc)
    zone=ZoneInfo('Europe/Amsterdam')
    solar=sun(Observer(lat,lng),date=now.astimezone(zone).date(),tzinfo=zone)
    return dict(day=solar['sunrise']<=now<solar['sunset'],timezone='Europe/Amsterdam',
                sunrise=solar['sunrise'].isoformat(),sunset=solar['sunset'].isoformat())


def condition(current):
    code=current.get('weather_code',0);wind=current.get('wind_speed_10m',0)
    if code in (45,48):return 7
    if code in (71,73,75,77,85,86):return 6
    if code in (51,53,55,56,57,61,63,65,66,67,80,81,82,95,96,99):return 2
    if wind>=30:return 5
    return {0:1,1:3,2:3,3:4}.get(code,1)


class OpenMeteo:
    name='Open-Meteo'
    def fetch(self,lat,lng):
        query=urllib.parse.urlencode(dict(latitude=lat,longitude=lng,current='weather_code,cloud_cover,precipitation,rain,snowfall,wind_speed_10m,wind_direction_10m,is_day',timezone='Europe/Amsterdam'))
        request=urllib.request.Request('https://api.open-meteo.com/v1/forecast?'+query,headers={'User-Agent':'Dittobuffer/14 private noncommercial geographic research'})
        with urllib.request.urlopen(request,timeout=12) as response:payload=json.load(response)
        current=payload.get('current',{})
        if not isinstance(current.get('weather_code'),int):raise ValueError('Invalid weather provider payload')
        for key in ('cloud_cover','rain','snowfall','wind_speed_10m','wind_direction_10m'):
            if not isinstance(current.get(key),(int,float)) or not math.isfinite(current[key]):raise ValueError('Invalid weather measurement')
        return payload


class WeatherService:
    def __init__(self,cfg=None,provider=None,fixture_context=None):
        self.cfg=cfg or {};self.provider=provider or OpenMeteo()
        self.fixture=None
        if self.cfg.get('fixture_file'):
            context=fixture_context or {};path=project_path(self.cfg['fixture_file'])
            from dittobuffer.world.world_store import ROOT
            database=project_path(context.get('database','data/invalid.sqlite3'))
            if not context.get('profile','').startswith('m14-') or not context.get('fixture_player') or not database.is_relative_to(ROOT/'builds') or not path.is_relative_to(ROOT/'builds'):
                raise ValueError('Weather presentation fixture requires an isolated M14 builds trainer')
            self.fixture=path
        self.path=project_path(self.cfg.get('cache','data/world/weather.sqlite3'))
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.Lock();self.pending=set();self.retry_after={};self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='world-weather')
        with connect(self.path) as db:
            db.executescript('CREATE TABLE IF NOT EXISTS weather(cell TEXT PRIMARY KEY,updated REAL NOT NULL,payload TEXT NOT NULL); CREATE TABLE IF NOT EXISTS requests(stamp REAL NOT NULL);')

    def snapshot(self,lat,lng):
        cell=f'{round(lat,1):.1f},{round(lng,1):.1f}';now=time.time()
        if self.fixture:
            current=json.loads(self.fixture.read_text(encoding='utf8'))
            limits={'weather_code':(0,99),'cloud_cover':(0,100),'rain':(0,20),'snowfall':(0,20),'wind_speed_10m':(0,150),'wind_direction_10m':(0,360)}
            for name,(low,high) in limits.items():
                value=current.get(name)
                if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:raise ValueError('Invalid isolated weather fixture: '+name)
            return dict(cell=cell,source='ISOLATED_RENDER_FIXTURE',stale=False,current=current,condition=condition(current),**daylight(lat,lng))
        with connect(self.path) as db:row=db.execute('SELECT * FROM weather WHERE cell=?',(cell,)).fetchone()
        if not row or now-row[1]>self.cfg.get('refresh_seconds',900):
            with self.lock:
                if cell not in self.pending and len(self.pending)<64 and now>=self.retry_after.get(cell,0):
                    self.pending.add(cell);self.pool.submit(self._refresh,cell)
        current=json.loads(row[2])['current'] if row and now-row[1]<self.cfg.get('stale_seconds',7200) else None
        return dict(cell=cell,source=self.provider.name if current else 'unavailable',stale=bool(row and now-row[1]>900),
            current=current,condition=condition(current) if current else 0,**daylight(lat,lng))

    def _refresh(self,cell):
        try:
            now=time.time()
            with connect(self.path) as db:
                db.execute('DELETE FROM requests WHERE stamp<?',(now-86400,));db.execute('DELETE FROM weather WHERE updated<?',(now-259200,))
                total=db.execute('SELECT COUNT(*) FROM requests').fetchone()[0]
                last=db.execute('SELECT MAX(stamp) FROM requests').fetchone()[0] or 0
                if total>=self.cfg.get('daily_limit',9000) or now-last<2:return
                db.execute('INSERT INTO requests VALUES(?)',(now,))
            lat,lng=map(float,cell.split(','));payload=self.provider.fetch(lat,lng)
            with connect(self.path) as db:db.execute('INSERT OR REPLACE INTO weather VALUES(?,?,?)',(cell,time.time(),json.dumps(payload)))
        except Exception as error:
            logging.warning('World weather refresh unavailable: %s',type(error).__name__)
        finally:
            with self.lock:
                self.pending.discard(cell)
                # Failed/offline requests and quota refusals must not resubmit on
                # each map RPC. Bound this non-player geographic retry cache too.
                self.retry_after={k:v for k,v in self.retry_after.items() if v>time.time()}
                self.retry_after[cell]=time.time()+300

    def apply(self,out,p,lat,lng):
        from s2sphere import CellId,LatLng
        sample=self.snapshot(lat,lng);out.time_of_day=1 if sample['day'] else 2
        if sample['current']:
            current=sample['current'];value=out.client_weather.add(s2_cell_id=CellId.from_lat_lng(LatLng.from_degrees(lat,lng)).parent(10).id())
            value.gameplay_weather.gameplay_condition=sample['condition']
            display=value.display_weather
            display.cloud_level=min(3,round(current.get('cloud_cover',0)/33))
            display.rain_level=min(3,math_level(current.get('rain',0)))
            display.snow_level=min(3,math_level(current.get('snowfall',0)))
            display.wind_level=min(3,int(current.get('wind_speed_10m',0)//15))
            display.fog_level=2 if sample['condition']==7 else 0
            display.wind_direction=round(current.get('wind_direction_10m',0))%360
        return sample


def math_level(value):return 0 if value<=0 else 1 if value<1 else 2 if value<4 else 3
