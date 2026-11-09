"""Private native rehearsal proves proximity without walking credit."""
import sqlite3
from types import SimpleNamespace
import pytest
from dittobuffer.gameplay import friends, game as game_module
from dittobuffer.gameplay.max_system import near

@pytest.fixture
def rehearsal(tmp_path,monkeypatch):
 monkeypatch.setattr(game_module,'ROOT',tmp_path);monkeypatch.setattr(friends,'now_ms',lambda:100000)
 cfg=dict(profile='m25-isolated',interaction_range_meters=80,social=dict(enabled=True,allow_fixture_location=True),geographic_fixture_locations=[dict(lat=52.0888245,lng=5.1198434)])
 g=SimpleNamespace(config=cfg,db=SimpleNamespace(path=tmp_path/'.private/m251/trainer.sqlite3'))
 db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
 db.execute('create table social_locations(player_id text primary key,lat real,lng real,accuracy real,fix_ms integer,arrival_ms integer)')
 db.execute('create table walking(player_id text, distance integer)');db.execute("insert into walking values('p',0)")
 yield g,db,dict(lat=52.0888245,lng=5.1198434,accuracy=5,time=100,provider=3,source='debug')
 db.close()

def test_declared_private_fix_proves_proximity_without_walking(rehearsal,monkeypatch):
 g,db,fix=rehearsal;friends.observe(g,db,'p',fix)
 from dittobuffer.gameplay import max_system
 monkeypatch.setattr(max_system,'now_ms',lambda:100000)
 assert near(g,db,'p',dict(lat=52.08896,lng=5.12020))
 assert db.execute('select distance from walking').fetchone()[0]==0
 friends.observe(g,db,'p',dict(fix,time=99));assert db.execute('select fix_ms from social_locations').fetchone()[0]==100000

@pytest.mark.parametrize('change',['normal_database','wrong_profile','undeclared_site','stale','inaccurate','disabled'])
def test_invalid_debug_fixes_cannot_establish_presence(rehearsal,change):
 g,db,fix=rehearsal
 if change=='normal_database':g.db.path=g.db.path.parents[2]/'family.sqlite3'
 elif change=='wrong_profile':g.config['profile']='family'
 elif change=='undeclared_site':fix['lat']+=.001
 elif change=='stale':fix['time']=69
 elif change=='inaccurate':fix['accuracy']=26
 elif change=='disabled':g.config['social']['allow_fixture_location']=False
 friends.observe(g,db,'p',fix)
 assert db.execute('select count(*) from social_locations').fetchone()[0]==0
 assert db.execute('select distance from walking').fetchone()[0]==0
