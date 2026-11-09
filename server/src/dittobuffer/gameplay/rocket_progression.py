"""Ticket-gated hideouts and private balloons, sharing the verified incident engine.

Lineups and rewards are explicit custom server rotations, not claimed official
live rotations. Equipping is a native device preference; possession and one-time
consumption are authoritative inventory transactions.
"""
import hashlib,json,math,random
from contextlib import closing
from s2sphere import CellId,LatLng
from dittobuffer.gameplay.game import ROOT,p,now_ms
from dittobuffer.gameplay.items import quantity

def install(game):
 cfg=game.rocket
 path=(ROOT/cfg.get('leaders','src/dittobuffer/gameplay/rocket-leaders.json')).resolve()
 if not path.is_relative_to(ROOT):raise ValueError('Project-local Rocket definitions required')
 game.rocket_leaders=json.loads(path.read_text())
 probability=cfg.get('hideout_probability',0)
 if type(probability) not in (int,float) or not math.isfinite(probability) or not 0<=probability<=1:raise ValueError('Invalid hideout probability')
 for key,value in game.rocket_leaders.items():
  if value['kind'] not in ('grunt','leader','boss') or value['character'] not in p.EnumWrapper.InvasionCharacter.values() or len(value['species'])!=3 or value['shields'] not in (0,1,2) or value['ticket'] not in (0,1502,1503):raise ValueError('Invalid Rocket character '+key)
  for species in [*value['species'],value['reward_species']]:
   if species not in game.species or f"{species}:{game.species[species]['form']}" not in game.rocket_reference['shadow']:raise ValueError('Unsupported Shadow species '+str(species))
  for field in ('xp','stardust'):
   if type(value[field]) is not int or not 0<=value[field]<=100000:raise ValueError('Invalid Rocket reward')
 for fort,key in cfg.get('hideouts',{}).items():
  if not isinstance(fort,str) or key not in game.rocket_leaders:raise ValueError('Invalid hideout rotation')
 for name,default,low,high in [('hideout_seconds',86400,60,86400),('leader_level',25,1,40),('boss_level',30,1,40),('balloon_period_seconds',21600,60,86400),('balloon_duration_seconds',1200,30,86400)]:
  value=cfg.setdefault(name,default)
  if type(value) is not int or not low<=value<=high:raise ValueError('Invalid Rocket '+name)
 if cfg['balloon_duration_seconds']>cfg['balloon_period_seconds']:raise ValueError('Balloon duration exceeds period')
 themes=cfg.setdefault('balloon_themes',['grass'])
 if not isinstance(themes,list) or not themes or any(v not in game.rocket_teams for v in themes):raise ValueError('Invalid Rocket balloon themes')
 with closing(game.db.connect()) as db:db.executescript('CREATE TABLE IF NOT EXISTS rocket_native_balloons(player_id TEXT NOT NULL REFERENCES players(id),slot INTEGER NOT NULL,incident_id TEXT NOT NULL UNIQUE REFERENCES rocket_incidents(id),PRIMARY KEY(player_id,slot));')

def definition(game,key):
 result=dict(game.rocket_leaders[key],theme=key)
 result['level']=game.rocket['boss_level' if result['kind']=='boss' else 'leader_level']
 return result

def hideout(game,db,player,fort):
 cfg=game.rocket;key=cfg.get('hideouts',{}).get(fort['id'])
 # Optional deterministic spread across existing published stops. No POI edits.
 if not key and cfg.get('hideout_probability',0):
  rng=random.Random(hashlib.sha256(f"hideout:{cfg['seed']}:{fort['id']}:{now_ms()//86400000}".encode()).digest())
  if rng.random()<cfg['hideout_probability']:key=rng.choice(['arlo','cliff','sierra','giovanni','decoy'])
 if not key or fort['kind']!=1:return None
 value=definition(game,key);ticket=value.get('reveal_ticket',value['ticket'])
 if not ticket or quantity(db,player,ticket)<=0:return None
 duration=cfg['hideout_seconds']*1000;slot=now_ms()//duration
 ident='hideout-'+hashlib.sha256(f"{cfg['seed']}:{fort['id']}:{slot}:{key}".encode()).hexdigest()[:24]
 db.execute('INSERT OR IGNORE INTO rocket_incidents VALUES(?,?,?,?,?)',(ident,fort['id'],slot*duration,(slot+1)*duration,json.dumps(value)))
 return db.execute('SELECT * FROM rocket_incidents WHERE id=?',(ident,)).fetchone()

def allowed(db,player,row):
 value=json.loads(row['definition']);ticket=value.get('reveal_ticket',value.get('ticket',0))
 member=db.execute('SELECT completed_ms FROM rocket_players WHERE incident_id=? AND player_id=?',(row['id'],player)).fetchone()
 return not ticket or quantity(db,player,ticket)>0 or bool(member and member['completed_ms'])

def map_forts(game,db,player,body):
 req=p.GetMapFortsProto.FromString(body);out=p.GetMapFortsOutProto(status=1)
 if not game.config.get('rocket',{}).get('enabled'):return out
 if len(req.cell_id)>100:return p.GetMapFortsOutProto(status=2)
 try:
  cells=[CellId(i) for i in req.cell_id]
  if any(not c.is_valid() or c.level()<10 for c in cells):return p.GetMapFortsOutProto(status=2)
 except (ValueError,AssertionError):return p.GetMapFortsOutProto(status=2)
 from dittobuffer.gameplay.rocket_native import decorate
 for fort in db.execute('SELECT * FROM forts WHERE kind=1'):
  if not any(c.contains(CellId.from_lat_lng(LatLng.from_degrees(fort['lat'],fort['lng']))) for c in cells):continue
  incident=hideout(game,db,player,fort)
  if not incident:continue
  value=out.fort.add(fort_id=fort['id'],fort_type=1,latitude=fort['lat'],longitude=fort['lng'],enabled=True)
  decorate(game,db,player,fort,value,incident=incident)
 return out

def balloon(game,db,player,body):
 req=p.GetRocketBalloonProto.FromString(body);cfg=game.rocket
 if not game.config.get('rocket',{}).get('enabled') or not cfg.get('balloons_enabled',False):return p.GetRocketBalloonOutProto(status=4)
 if req.equipped_item not in (0,1502,1503) or (req.equipped_item and quantity(db,player,req.equipped_item)<=0):return p.GetRocketBalloonOutProto(status=5)
 from dittobuffer.gameplay.progression import bounds
 if bounds(game.config,game.player(db,player)['xp'])[0]<cfg['minimum_level']:return p.GetRocketBalloonOutProto(status=4)
 stamp=now_ms();duration=cfg['balloon_period_seconds']*1000;slot=stamp//duration
 # Keep an active balloon across a slot/configuration boundary. Never create a
 # competing incident while its battle or rescue is still in progress.
 old=db.execute('SELECT i.* FROM rocket_native_balloons b JOIN rocket_incidents i ON i.id=b.incident_id WHERE b.player_id=? AND i.end_ms>? ORDER BY i.start_ms DESC LIMIT 1',(player,stamp)).fetchone()
 old=old or db.execute('SELECT i.* FROM rocket_native_balloons b JOIN rocket_incidents i ON i.id=b.incident_id WHERE b.player_id=? AND b.slot=?',(player,slot)).fetchone()
 if old:
  done=db.execute('SELECT completed_ms FROM rocket_players WHERE incident_id=? AND player_id=?',(old['id'],player)).fetchone()
  if old['end_ms']<=now_ms() or (done and done[0]):return p.GetRocketBalloonOutProto(status=2)
  # 0.429.2 BalloonIncidentRpcService.bent only keeps a balloon for status 1;
  # even the schema's "SuccessBalloonAlreadyExists" (6) despawns it. Return
  # the same persisted identity with ordinary success on every active poll.
  row=old;status=1
 else:
  table='social_locations' if game.config.get('social',{}).get('enabled') else 'walking'
  loc=db.execute('SELECT * FROM '+table+' WHERE player_id=?',(player,)).fetchone()
  if not loc or loc['lat'] is None or stamp-(loc['arrival_ms'] or 0)>120000:
   # Authenticated map fixes also position balloons. This is deliberately not
   # walking credit: standalone fixed-location development profiles cannot and
   # must not qualify as physical distance samples.
   loc=db.execute('SELECT lat,lng,seen_ms arrival_ms FROM admin_locations WHERE player_id=?',(player,)).fetchone()
  if not loc or stamp-(loc['arrival_ms'] or 0)>120000:return p.GetRocketBalloonOutProto(status=3)
  fort=db.execute('SELECT *,((lat-?)*(lat-?)+(lng-?)*(lng-?)) d FROM forts WHERE kind=1 ORDER BY d LIMIT 1',(loc['lat'],loc['lat'],loc['lng'],loc['lng'])).fetchone()
  if not fort:return p.GetRocketBalloonOutProto(status=3)
  if req.equipped_item:value=definition(game,'giovanni' if req.equipped_item==1503 else ['arlo','cliff','sierra'][slot%3])
  else:
   theme=cfg['balloon_themes'][slot%len(cfg['balloon_themes'])]
   value=dict(game.rocket_teams[theme],theme=theme,kind='grunt',level=cfg['npc_level']);value['character']=70
  value['balloon']=True
  ident='balloon-'+hashlib.sha256(f"{player}:{slot}:{cfg['seed']}".encode()).hexdigest()[:24]
  db.execute('INSERT INTO rocket_incidents VALUES(?,?,?,?,?)',(ident,fort['id'],now_ms(),now_ms()+cfg['balloon_duration_seconds']*1000,json.dumps(value)))
  db.execute('INSERT INTO rocket_native_balloons VALUES(?,?,?)',(player,slot,ident));row=db.execute('SELECT * FROM rocket_incidents WHERE id=?',(ident,)).fetchone();status=1
 display=p.RocketBalloonDisplayProto(type=0,incident_display=p.RocketBalloonIncidentDisplayProto(incident_id=row['id'],incident_display_type=json.loads(row['definition']).get('display',1)))
 return p.GetRocketBalloonOutProto(status=status,display=display)
