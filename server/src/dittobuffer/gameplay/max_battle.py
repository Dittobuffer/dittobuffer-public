"""Persistent, account-scoped Max lobbies and authoritative PvE combat.

The native Bread RPC/PBS surface is separate from raid membership and passes.
Until complete source combat tuning is available, explicit development balance
is required; these configurable numbers are never described as official rules.
"""
import hashlib,json,logging,math,secrets
from copy import deepcopy
from contextlib import contextmanager
import time
from types import SimpleNamespace
from dittobuffer.gameplay.game import p,now_ms,RpcError
from dittobuffer.gameplay import max_system as resources,gym_battle as engine
from dittobuffer.core.management import owned,update_owned
from dittobuffer.gameplay.battle_master import MOVES

SCHEMA='''
CREATE TABLE IF NOT EXISTS max_rooms(id INTEGER PRIMARY KEY,station_id TEXT NOT NULL REFERENCES max_stations(id),seed INTEGER NOT NULL,created_ms INTEGER NOT NULL,join_end_ms INTEGER NOT NULL,start_ms INTEGER NOT NULL,end_ms INTEGER NOT NULL,status INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS max_room_station ON max_rooms(station_id,created_ms);
CREATE TABLE IF NOT EXISTS max_members(room_id INTEGER NOT NULL REFERENCES max_rooms(id),player_id TEXT NOT NULL REFERENCES players(id),joined_ms INTEGER NOT NULL,ready INTEGER NOT NULL DEFAULT 0,left_ms INTEGER NOT NULL DEFAULT 0,party TEXT NOT NULL DEFAULT '[]',token TEXT NOT NULL UNIQUE,session_digest TEXT NOT NULL DEFAULT '',reward BLOB,spawn_id INTEGER,balls INTEGER NOT NULL DEFAULT 0,damage INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(room_id,player_id));
CREATE TABLE IF NOT EXISTS max_event_receipts(room_id INTEGER NOT NULL,player_id TEXT NOT NULL,sequence INTEGER NOT NULL,request_hash TEXT NOT NULL,response BLOB NOT NULL,PRIMARY KEY(room_id,player_id,sequence));
CREATE TABLE IF NOT EXISTS max_session_event_receipts(room_id INTEGER NOT NULL,player_id TEXT NOT NULL,credential_hash TEXT NOT NULL,sequence INTEGER NOT NULL,request_hash TEXT NOT NULL,response BLOB NOT NULL,PRIMARY KEY(room_id,player_id,credential_hash,sequence));
CREATE TABLE IF NOT EXISTS max_victories(station_id TEXT NOT NULL,player_id TEXT NOT NULL,room_id INTEGER NOT NULL,created_ms INTEGER NOT NULL,PRIMARY KEY(station_id,player_id));
'''

def install(db):db.executescript(SCHEMA)
def validate_config(game):
 cfg=rules(game)
 if not isinstance(cfg,dict):raise ValueError('Max development balance must be an object')
 if not cfg:return
 from dittobuffer.gameplay.max_cheer import validate
 validate(cfg.get('cheering',{}))
 if not isinstance(cfg,dict) or not isinstance(cfg.get('provenance'),str) or len(cfg['provenance'].strip())<20:raise ValueError('Explicit Max development balance provenance required')
 for key,low,high in [('lobby_seconds',8,180),('battle_seconds',30,3600),('boss_hp',1,10000000),('boss_interval_ms',1000,60000),('meter_goal',1,1000),('meter_per_attack',1,1000),('xp',0,1000000),('dust',0,1000000),('premier_balls',1,100)]:
  if type(cfg.get(key)) is not int or not low<=cfg[key]<=high:raise ValueError('Invalid Max development balance '+key)
 if cfg['meter_per_attack']>cfg['meter_goal']:raise ValueError('Max meter increment exceeds capacity')
 for key,low,high in [('max_attack_power',1,10000),('guard_hp',1,10000),('spirit_fraction',.01,1)]:
  values=cfg.get(key)
  if not isinstance(values,list) or len(values)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) or not low<=v<=high for v in values):raise ValueError('Invalid Max move balance '+key)
  if key=='guard_hp' and any(type(v) is not int for v in values):raise ValueError('Max Guard HP must be integral')
 if type(cfg.get('boss_damage_multiplier')) not in (int,float) or not math.isfinite(cfg['boss_damage_multiplier']) or not .01<=cfg['boss_damage_multiplier']<=100:raise ValueError('Invalid Max boss damage multiplier')
 supported_items={1,2,3,101,102,103,104,201,202,701}
 if not isinstance(cfg.get('items',{}),dict) or any(not str(k).isdigit() or int(k) not in supported_items or type(v) is not int or not 0<v<=10000 for k,v in cfg.get('items',{}).items()):raise ValueError('Invalid Max reward items')
 profiles=game.max_config.get('balance_profiles',{})
 if not isinstance(profiles,dict):raise ValueError('Max balance_profiles must be an object')
 for key,value in profiles.items():
  if key not in ('1:1','1:2','1:3','1:4','1:5','2:7'):raise ValueError('Unsupported Max mode/tier profile')
  validate_config(SimpleNamespace(max_config={'development_balance':value}))
def rules(game):return game.max_config.get('development_balance',{})
def combat_rules(game,state):return state.get('rules') or rules(game)

def snapshot_rules(game,definition):
 """Explicit custom factors affect a NEW room only, never an old attempt."""
 profile_key=str(definition.get('mode',1))+':'+str(definition.get('tier',1))
 result=deepcopy(game.max_config.get('balance_profiles',{}).get(profile_key,rules(game)))
 result['profile_key']=profile_key;factors=definition.get('difficulty_multipliers',{})
 allowed={'boss_hp','boss_damage','player_damage','battle_duration'}
 if not isinstance(factors,dict) or set(factors)-allowed:raise RpcError('Unsupported Max schedule difficulty multiplier')
 if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<v<=1000 for v in factors.values()):raise RpcError('Invalid Max schedule difficulty multiplier')
 result['boss_hp']=max(1,round(result['boss_hp']*factors.get('boss_hp',1)))
 result['battle_seconds']=max(1,round(result['battle_seconds']*factors.get('battle_duration',1)))
 result['boss_damage_multiplier']*=factors.get('boss_damage',1)
 result['player_damage_multiplier']=factors.get('player_damage',1)
 if result['boss_hp']>10000000 or result['battle_seconds']>3600:raise RpcError('Max schedule difficulty exceeds server bounds')
 return result

def source_capacity(game,boss_mode):
 maximum=20 if boss_mode in (2,3) else 4
 name='BREAD_BATTLE_CLIENT_SETTINGS'
 if name in game.max_reference['templates']:
  source=resources.gm(game.max_reference,name).bread_battle_client_settings
  declared=source.max_players_per_bread_dough_lobby if boss_mode in (2,3) else source.max_players_per_bread_lobby
  if declared>0:maximum=min(maximum,declared)
 return maximum

def sync_group_aliases(state):
 # Preserve the established single-group serialized fields for old clients,
 # diagnostics and saved attempts. Group0 is never a global G-Max phase.
 if '0' in state.get('groups',{}):
  for key in ('meter','phase','phase_due'):state[key]=state['groups']['0'][key]

def ensure_groups(game,db,room,state):
 """Migrate legacy single-party state without resetting HP, meter or turns."""
 if not state.get('rules'):
  state['rules']=deepcopy(rules(game));state['rules_provenance']='Legacy attempt: first loaded balance snapshot; original unsaved tuning is unavailable.'
  if state.get('boss'):state['rules']['boss_hp']=engine.mon(state['boss']).max_stamina
  state['rules'].setdefault('player_damage_multiplier',1)
 if 'boss_mode' not in state:
  if state.get('boss'):state['boss_mode']=engine.mon(state['boss']).pokemon_display.bread_mode_enum
  else:
   row=db.execute('SELECT definition FROM max_stations WHERE id=?',(room['station_id'],)).fetchone();state['boss_mode']=json.loads(row[0]).get('mode',1)
 state.setdefault('max_participants',source_capacity(game,state['boss_mode']))
 members=db.execute('SELECT player_id,left_ms FROM max_members WHERE room_id=? ORDER BY joined_ms,rowid',(room['id'],)).fetchall()
 state.setdefault('join_order',[v['player_id'] for v in members])
 groups=state.setdefault('groups',{'0':{key:state.get(key,0) for key in ('meter','phase','phase_due')}})
 mapping=state.setdefault('player_groups',{})
 for row in members:
  player=row['player_id']
  if player not in state['join_order']:state['join_order'].append(player)
  if player not in mapping:
   occupied={key:sum(not v['left_ms'] and mapping.get(v['player_id'])==key for v in members) for key in groups}
   group='0' if state['max_participants']<=4 else next((str(n) for n in range(5) if occupied.get(str(n),0)<4),None)
   if group is None:raise RpcError('No Max subgroup capacity')
   mapping[player]=group;groups.setdefault(group,dict(meter=0,phase=0,phase_due=0))
  if player in state['players']:state['players'][player]['group']=mapping[player]
 sync_group_aliases(state)
 return state

def group_for(state,player):return state['groups'][state['player_groups'][player]]
def group_players(state,player):
 group=state['player_groups'][player]
 return [(key,value) for key,value in state['players'].items() if state['player_groups'][key]==group]

def revert_group(state,player,stamp):
 turn=max(0,(stamp-state['start'])//500)
 for key,unused in group_players(state,player):
  emit(state,31,key,stamp,cinematic=p.MaxPbsCinematic(event_type=4,begin_turn=turn,end_turn=turn+8));emit(state,33,key,stamp+4000)
 group_for(state,player).update(phase=3,meter=0,phase_due=stamp+4000)
 sync_group_aliases(state)

def frozen_station(game,db,room,state,player):
 if state.get('station_snapshot'):return p.StationProto.FromString(bytes.fromhex(state['station_snapshot']))
 row=db.execute('SELECT * FROM max_stations WHERE id=?',(room['station_id'],)).fetchone()
 station=resources.station(game,db,player,row);state['station_snapshot']=station.SerializeToString().hex()
 return station

def ready(game):return resources.enabled(game) and game.max_config['battle_enabled'] and bool(rules(game))
def fee(game,station):
 return next((v.bread_battle_catch_mp_cost for v in resources.gm(game.max_reference,'MP_SHARED_SETTINGS').mp_settings.battle_mp_cost_per_tier if v.battle_level==station.battle_details.battle_level),0)
def eligible(mon):
 return bool(mon and not mon.is_egg and mon.stamina>0 and not mon.deployed_fort_id and not mon.deployed_station_id and mon.pokemon_display.bread_mode_enum in (1,2) and mon.move1 in MOVES and mon.move2 in MOVES)
def party_for(db,player):return [mon for mon in (p.PokemonProto.FromString(v[0]) for v in db.execute('SELECT data FROM pokemon WHERE player_id=? ORDER BY id',(player,))) if eligible(mon)]
def member(db,room,player):return db.execute('SELECT * FROM max_members WHERE room_id=? AND player_id=?',(room['id'],player)).fetchone()
def room_for(db,station,player):
 return db.execute('SELECT r.* FROM max_rooms r JOIN max_members m ON m.room_id=r.id WHERE r.station_id=? AND m.player_id=? AND m.left_ms=0 ORDER BY r.created_ms DESC LIMIT 1',(station,player)).fetchone()
def save(db,room,state):db.execute('UPDATE max_rooms SET state=?,status=? WHERE id=?',(json.dumps(state,separators=(',',':')),state['status'],room['id']))
def connection(m):return p.RvnConnectionProto(base_uri='/max/',token=m['token'])

def lobby(game,db,room,player):
 from dittobuffer.gameplay.gyms import public
 # Callers may have just started/checkpointed a room. Never migrate/save a
 # stale sqlite Row over its newly persisted combat state.
 room=db.execute('SELECT * FROM max_rooms WHERE id=?',(room['id'],)).fetchone()
 own=member(db,room,player);state=ensure_groups(game,db,room,json.loads(room['state']));save(db,room,state)
 out=p.BreadLobbyProto(bread_lobby_id=room['id'],player_join_end_ms=room['join_end_ms'],pokemon_selection_end_ms=room['start_ms'],bread_battle_start_ms=room['start_ms'],bread_battle_end_ms=room['end_ms'],bread_battle_id=str(room['id']),creation_ms=room['created_ms'],rvn_version=1,weather_condition=0,bread_dough_mode=state['boss_mode'] in (2,3))
 if own:out.rvn_connection.CopyFrom(connection(own))
 members=db.execute('SELECT * FROM max_members WHERE room_id=? AND left_ms=0',(room['id'],)).fetchall()
 for index,m in enumerate(sorted(members,key=lambda v:state['join_order'].index(v['player_id']))):
  ids=json.loads(m['party']);mon=owned(db,m['player_id'],ids[0]) if ids else next(iter(party_for(db,m['player_id'])),None)
  # Native lobby UI uses (PlayerNumber-1)//4 and ceil(max(PlayerNumber)/4).
  # PlayerNumber is one-based; combat actor party_id remains zero-based.
  v=out.players.add(trainer_public_profile=public(game,db,m['player_id']),player_id=m['player_id'][:20],bread_lobby_join_time_ms=m['joined_ms'],last_player_join_time_ms=m['joined_ms'],last_update_time_ms=now_ms(),prepare_bread_battle_state=bool(m['ready']),player_number=index+1)
  if mon:v.bread_lobby_pokemon.add(id=mon.id,pokedex_id=mon.pokemon_id,cp=mon.cp,percent_health=mon.stamina/max(1,mon.max_stamina))
  if index==0:out.owner_nickname=game.player(db,m['player_id'])['name']
 return out

def join(game,db,player,body):
 req=p.JoinBreadLobbyProto.FromString(body);fail=lambda n:p.JoinBreadLobbyOutProto(result=n)
 row=resources.lookup(game,db,req.station_id)
 if not row or not ready(game):return fail(3)
 if not resources.near(game,db,player,row):return fail(2)
 station=resources.station(game,db,player,row)
 if req.bread_battle_seed!=station.battle_details.bread_battle_seed:return fail(3)
 if req.use_remote_pass or req.use_power_crystal or req.is_battle_assist or req.bread_lobby_id:return fail(28)
 from dittobuffer.gameplay.progression import bounds
 if bounds(game.config,game.player(db,player)['xp'])[0]<game.max_config['minimum_level']:return fail(6)
 if db.execute('SELECT 1 FROM max_victories WHERE station_id=? AND player_id=?',(row['id'],player)).fetchone():return fail(4)
 old=room_for(db,row['id'],player)
 if old and old['end_ms']>now_ms() and old['status'] in (0,1):
  from dittobuffer.core.accounts import principal_session
  session=principal_session.get() or '';previous=member(db,old,player)
  # The RPC already authenticated this player. Rebind a genuine account
  # reconnect without resetting the saved party, countdown or shared boss.
  if session and session!=previous['session_digest']:
   db.execute('UPDATE max_members SET token=?,session_digest=? WHERE room_id=? AND player_id=?',(secrets.token_urlsafe(32),session,old['id'],player))
  return p.JoinBreadLobbyOutProto(result=1,bread_lobby=lobby(game,db,old,player),server_timestamp_ms=now_ms())
 if not party_for(db,player):return fail(28)
 if db.execute('SELECT 1 FROM max_rooms r JOIN max_members m ON r.id=m.room_id WHERE m.player_id=? AND m.left_ms=0 AND r.end_ms>? AND r.status IN (0,1)',(player,now_ms())).fetchone():return fail(18)
 amount=fee(game,station)
 if not amount or resources.available(db,player)<amount:return fail(16)
 room=db.execute('SELECT r.* FROM max_rooms r WHERE station_id=? AND status=0 AND join_end_ms>? AND NOT EXISTS(SELECT 1 FROM max_members m WHERE m.room_id=r.id AND m.player_id=?) ORDER BY created_ms LIMIT 1',(row['id'],now_ms(),player)).fetchone()
 if room:
  state=ensure_groups(game,db,room,json.loads(room['state']));save(db,room,state)
  if db.execute('SELECT COUNT(*) FROM max_members WHERE room_id=? AND left_ms=0',(room['id'],)).fetchone()[0]>=state['max_participants']:return fail(13)
  amount=state.get('particle_fee',amount)
 if not room:
  definition=json.loads(row['definition']);cfg=snapshot_rules(game,definition);mode=station.battle_details.battle_pokemon.pokemon_display.bread_mode_enum
  from dittobuffer.gameplay.max_station import boost
  assist=boost(game,db,row['id']);cfg['station_assist_multiplier']=1+assist['hundredths_of_percent']/10000
  stamp=now_ms();join_end=stamp+int(cfg.get('lobby_seconds',30)*1000);start=join_end+3000;end=min(row['end_ms'],start+int(cfg.get('battle_seconds',300)*1000));ident=secrets.randbits(60) or 1
  if end<=start:return fail(14)
  initial=dict(status=0,players={},events=[],meter=0,phase=0,phase_due=0,groups={'0':dict(meter=0,phase=0,phase_due=0)},join_order=[],player_groups={},boss_mode=mode,max_participants=source_capacity(game,mode),rules=cfg,rules_provenance='Snapshot at room creation, including explicit schedule difficulty.',particle_fee=amount,station_snapshot=station.SerializeToString().hex(),station_assist=assist,difficulty_multipliers=definition.get('difficulty_multipliers',{}))
  db.execute('INSERT INTO max_rooms VALUES(?,?,?,?,?,?,?,?,?)',(ident,row['id'],station.battle_details.bread_battle_seed,stamp,join_end,start,end,0,json.dumps(initial)))
  room=db.execute('SELECT * FROM max_rooms WHERE id=?',(ident,)).fetchone()
 if not resources.reserve(game,db,player,str(room['id']),amount,room['end_ms']+120000):return fail(16)
 from dittobuffer.core.accounts import principal_session
 session=principal_session.get() or ''
 db.execute('INSERT INTO max_members(room_id,player_id,joined_ms,token,session_digest) VALUES(?,?,?,?,?)',(room['id'],player,now_ms(),secrets.token_urlsafe(32),session))
 return p.JoinBreadLobbyOutProto(result=1,bread_lobby=lobby(game,db,room,player),server_timestamp_ms=now_ms())

def prepare(game,db,player,body):
 req=p.PrepareBreadLobbyProto.FromString(body);room=db.execute('SELECT * FROM max_rooms WHERE id=? AND station_id=?',(req.bread_lobby_id,req.station_id)).fetchone()
 if not ready(game):return p.PrepareBreadLobbyOutProto(result=2)
 if not room or not member(db,room,player) or member(db,room,player)['left_ms']:return p.PrepareBreadLobbyOutProto(result=3)
 if room['status'] not in (0,1) or room['end_ms']<=now_ms():return p.PrepareBreadLobbyOutProto(result=4)
 db.execute('UPDATE max_members SET ready=1 WHERE room_id=? AND player_id=?',(room['id'],player))
 # All actual participants ready: shorten only the lobby, never an active clock.
 if room['status']==0 and not db.execute('SELECT 1 FROM max_members WHERE room_id=? AND left_ms=0 AND ready=0',(room['id'],)).fetchone():
  start=min(room['start_ms'],now_ms()+8000);db.execute('UPDATE max_rooms SET join_end_ms=MIN(join_end_ms,?),start_ms=? WHERE id=?',(start-3000,start,room['id']));room=db.execute('SELECT * FROM max_rooms WHERE id=?',(room['id'],)).fetchone()
 return p.PrepareBreadLobbyOutProto(result=1,bread_lobby=lobby(game,db,room,player),server_timestamp_ms=now_ms())

def leave(game,db,player,body):
 req=p.LeaveBreadLobbyProto.FromString(body);room=db.execute('SELECT * FROM max_rooms WHERE id=? AND station_id=? AND seed=?',(req.bread_lobby_id,req.station_id,req.bread_battle_seed)).fetchone()
 if not room or not member(db,room,player):return p.LeaveBreadLobbyOutProto(result=3)
 state=json.loads(room['state']);advance(game,db,room,state,now_ms())
 db.execute('UPDATE max_members SET left_ms=? WHERE room_id=? AND player_id=?',(now_ms(),room['id'],player));resources.release_hold(db,player,str(room['id']));db.execute('DELETE FROM max_pokemon_locks WHERE room_id=? AND player_id=?',(str(room['id']),player))
 if player in state['players']:state['players'][player]['quit']=True;save(db,room,state)
 if not db.execute('SELECT 1 FROM max_members WHERE room_id=? AND left_ms=0',(room['id'],)).fetchone():state['status']=3;save(db,room,state)
 return p.LeaveBreadLobbyOutProto(result=1)

def start(game,db,player,body):
 req=p.StartBreadBattleProto.FromString(body);fail=lambda n:p.StartBreadBattleOutProto(result=n)
 row=resources.lookup(game,db,req.station_id)
 if not row or not ready(game):return fail(3)
 if not resources.near(game,db,player,row):return fail(7)
 room=room_for(db,row['id'],player)
 if not room or room['seed']!=req.bread_battle_seed:return fail(10)
 if room['start_ms']>now_ms()+1000:return fail(16)
 if room['end_ms']<=now_ms() or room['status'] not in (0,1):return fail(17)
 ids=list(req.attacking_pokemon_id)
 if not 1<=len(ids)<=3 or len(set(ids))!=len(ids):return fail(5)
 state=ensure_groups(game,db,room,json.loads(room['state']));m=member(db,room,player)
 if not m or m['left_ms']:return fail(10)
 if player in state['players']:
  # Reconnecting is not a relobby: retain the exact authoritative party,
  # including fainted members. Never replace it with healed/new Pokemon.
  if ids!=json.loads(m['party']):return fail(5)
  return p.StartBreadBattleOutProto(result=1,session_player_id=player[:20],server_timestamp_ms=now_ms(),lobby=lobby(game,db,room,player),rvn_connection=connection(m))
 party=[owned(db,player,i) for i in ids]
 if not all(eligible(v) for v in party):return fail(5)
 if player not in state['players']:
  from dittobuffer.gameplay.gyms import battle_busy
  if battle_busy(db,player,ids):return fail(5)
  if state['status']==0:
   cfg=combat_rules(game,state);boss=frozen_station(game,db,room,state,player).battle_details.battle_pokemon;boss.id=(1<<62)+room['id'];boss.max_stamina=int(cfg['boss_hp']);boss.stamina=boss.max_stamina
   state.update(status=1,start=room['start_ms'],end=room['end_ms'],clock=room['start_ms'],boss=engine.entry(boss),ai_due=max(now_ms(),room['start_ms'])+int(cfg['boss_interval_ms']),pending=[])
  state['players'][player]=dict(party=[engine.entry(v) for v in party],active=0,damage=0,quit=False,max_turns=0,guard=0,group=state['player_groups'][player])
  if group_for(state,player)['phase']==2:
   # A genuine lobby member whose asset load finished late joins its current
   # subgroup phase; it is not a newly inserted or fabricated participant.
   stamp=now_ms();turn=max(0,(stamp-state['start'])//500);state['players'][player]['max_turns']=3
   emit(state,31,player,stamp,cinematic=p.MaxPbsCinematic(event_type=2,begin_turn=turn,end_turn=turn+8));select_move(state,player,stamp+4000)
  db.execute('UPDATE max_members SET party=? WHERE room_id=? AND player_id=?',(json.dumps(ids),room['id'],player))
  # Expired attempts cannot own a newly selected party. Their later polls
  # cannot persist injuries without this exact room's still-valid lock.
  db.execute('DELETE FROM max_pokemon_locks WHERE expires_ms<=?',(now_ms(),))
  for v in party:db.execute('INSERT INTO max_pokemon_locks VALUES(?,?,?,?)',(v.id,player,str(room['id']),room['end_ms']))
  emit(state,1,player,state['start'],start_battle=p.RaidPbsStart());save(db,room,state)
 return p.StartBreadBattleOutProto(result=1,session_player_id=player[:20],server_timestamp_ms=now_ms(),lobby=lobby(game,db,room,player),rvn_connection=connection(m))

def emit(state,kind,actor,stamp,**payload):
 event=p.BattleEventProto(type=kind,actor_id=actor[:20],turn=max(0,(stamp-state.get('start',stamp))//500),serial=len(state['events'])+1,**payload);state['events'].append(event.SerializeToString().hex())

def checkpoint_injuries(game,db,room,state,stamp):
 # A departed/expired attempt must never replay its stale HP over medicine,
 # transfers or a new battle. Persist final live injuries before releasing
 # locks, and require the exact unexpired room ownership generation.
 for player,ps in state['players'].items():
  if ps['quit']:continue
  for value in ps['party']:
   if value.get('last_persisted_hp')==value['hp']:continue
   ident=engine.mon(value).id
   if not db.execute('SELECT 1 FROM max_pokemon_locks WHERE pokemon_id=? AND player_id=? AND room_id=? AND expires_ms>?',(ident,player,str(room['id']),stamp)).fetchone():continue
   mon=owned(db,player,ident)
   if mon and mon.stamina!=value['hp']:mon.stamina=value['hp'];update_owned(game,db,player,mon)
   if mon:value['last_persisted_hp']=value['hp']

def advance(game,db,room,state,stamp):
 ensure_groups(game,db,room,state)
 if state['status']!=1:
  save(db,room,state);return
 until=min(stamp,state['end']);cfg=combat_rules(game,state)
 for group_id,group in state['groups'].items():
  members=[(player,ps) for player,ps in state['players'].items() if state['player_groups'][player]==group_id]
  if group['phase']==1 and until>=group['phase_due']:
   group['phase']=2
   for player,ps in members:
    if ps['quit'] or not any(v['hp']>0 for v in ps['party']):continue
    ps['max_turns']=3;turn=(group['phase_due']-state['start'])//500
    emit(state,31,player,group['phase_due'],cinematic=p.MaxPbsCinematic(event_type=2,begin_turn=turn,end_turn=turn+8));emit(state,23,player,group['phase_due']);select_move(state,player,group['phase_due']+4000)
  if group['phase']==3 and until>=group['phase_due']:group['phase']=0
  if group['phase']==2:
   # Same 30-turn selection deadline advertised by native Consensus. A
   # missing client cannot hold its subgroup in Max mode indefinitely.
   for player,ps in members:
    while state['boss']['hp']>0 and not ps['quit'] and any(v['hp']>0 for v in ps['party']) and ps['max_turns']>0 and ps.get('max_choice_due',until+1)<=until:
     at=max(ps['max_choice_due'],ps['party'][ps['active']]['ready'])
     if at>until:break
     execute_max(game,state,player,1,at)
     ps['automatic_max_choices']=ps.get('automatic_max_choices',0)+1
  if group['phase']==2 and members and all(ps['quit'] or ps['max_turns']==0 or not any(v['hp']>0 for v in ps['party']) for unused,ps in members):
   revert_group(state,members[0][0],until)
 while state['ai_due']<=until and state['status']==1:
  at=state['ai_due'];boss=state['boss'];mon=engine.mon(boss)
  targets=[(player,ps) for player,ps in state['players'].items() if not ps['quit'] and ps['party'][ps['active']]['hp']>0 and group_for(state,player)['phase']==0]
  if not targets:
   state['ai_due']=until+int(cfg['boss_interval_ms']);break
  move=mon.move2 if boss['energy']>=abs(MOVES[mon.move2][3]) else mon.move1;boss['energy']=max(0,min(100,boss['energy']+MOVES[move][3]))
  # One boss and clock; normal-phase subgroups remain targetable while another
  # subgroup Dynamaxes. Never replay paused attacks as a burst after a phase.
  affected=targets if state.get('ai_count',0)%3==2 else [targets[state.get('ai_count',0)%len(targets)]]
  for player,ps in affected:
   target=ps['party'][ps['active']];damage=max(1,math.ceil(engine.damage(game,boss,target,move)*cfg['boss_damage_multiplier']));dodge=at<=target['dodge_until']
   if dodge:damage=max(1,math.ceil(damage*.25));emit(state,21,player,at)
   blocked=min(ps['guard'],damage);ps['guard']-=blocked;target['hp']=max(0,target['hp']-damage+blocked)
   emit(state,4,'max-boss',at,attack=p.RaidPbsAttack(attack_type=1 if move==mon.move1 else 2,move=move,type=MOVES[move][0],source_pokemon_id=mon.id,target_id=player[:20]))
   if not target['hp']:
    alive=next((i for i,v in enumerate(ps['party']) if v['hp']>0),None)
    if alive is not None:
     old=engine.mon(target).id;ps['active']=alive;ps['guard']=0;emit(state,7,player,at,swap_pokemon=p.RaidPbsSwap(outgoing_pokemon_id=old,incoming_pokemon_id=engine.mon(ps['party'][alive]).id))
  state['ai_count']=state.get('ai_count',0)+1;state['ai_due']+=int(cfg['boss_interval_ms'])
 if state['boss']['hp']<=0:state['status']=2
 elif stamp>=state['end'] or not any(not v['quit'] and any(m['hp']>0 for m in v['party']) for v in state['players'].values()):state['status']=3
 state['clock']=until
 checkpoint_injuries(game,db,room,state,stamp)
 if state['status']!=1:
  for player,ps in state['players'].items():
   emit(state,18,player,until,battle_end=p.RaidPbsEnd(reason=1 if state['status']==2 else 2 if stamp>=state['end'] else 3,result=1 if state['status']==2 and not ps['quit'] else 2))
   if state['status']==2 and not ps['quit'] and (ps['damage']>0 or ps.get('support_actions',0)>0):
    resources.charge_hold(game,db,player,str(room['id']))
    db.execute('INSERT OR IGNORE INTO max_victories VALUES(?,?,?,?)',(room['station_id'],player,room['id'],until))
   else:resources.release_hold(db,player,str(room['id']))
   db.execute('DELETE FROM max_pokemon_locks WHERE room_id=? AND player_id=?',(str(room['id']),player))
 sync_group_aliases(state);save(db,room,state)

def max_move(game,mon):
 if mon.pokemon_display.bread_mode_enum in (2,3):
  form=mon.pokemon_display.form or game.species[mon.pokemon_id]['form']
  mappings=resources.gm(game.max_reference,'SOURDOUGH_MOVE_MAPPING_SETTINGS').sourdough_move_mapping_settings.mappings
  match=next((v for v in mappings if v.pokemon_id==mon.pokemon_id and v.form==form),None)
  if match is None:raise RpcError('No source Gigantamax move for this species/form')
  return match.move
 kind=MOVES[mon.move1][0]
 return next(v.move for v in resources.gm(game.max_reference,'BREAD_MOVE_MAPPING_SETTINGS').bread_move_mappings.mappings if v.type==kind)

def max_type(game,mon):
 if mon.pokemon_display.bread_mode_enum not in (2,3):return MOVES[mon.move1][0]
 move=max_move(game,mon)
 for name in game.max_reference['templates']:
  if name.startswith('VN_BM_'):
   value=resources.gm(game.max_reference,name).move
   if value.unique_id==move:return value.type
 raise RpcError('Missing source Gigantamax move metadata')

def select_move(state,player,stamp):
 turn=max(0,(stamp-state['start'])//500)
 emit(state,32,player,stamp,consensus=p.MaxPbsConsensus(begin_turn=turn,end_turn=turn+30,vote_end_turn=turn+30,consensus_event_subtype=2,unblocked_event_type=24,unblocked_event_types=[4,24]))
 state['players'][player]['max_choice_due']=stamp+15000

def begin_max(state,player,stamp):
 group=group_for(state,player)
 if group['phase']!=0 or group['meter']<state['rules']['meter_goal']:return
 group['phase']=1;group['phase_due']=stamp+5000;turn=(stamp-state['start'])//500
 for key,ally in group_players(state,player):
  if not ally['quit'] and any(v['hp']>0 for v in ally['party']):emit(state,31,key,stamp,cinematic=p.MaxPbsCinematic(event_type=1,begin_turn=turn,end_turn=turn+10))

def execute_max(game,state,player,kind,stamp):
 ps=state['players'][player];value=ps['party'][ps['active']];mon=engine.mon(value);cfg=combat_rules(game,state)
 level=next((v.move_level for v in mon.bread_moves if v.move_type==kind),0)
 if kind not in (1,2,3) or not 1<=level<=3:raise RpcError('Locked Max move')
 if kind==1:
  amount=max_damage(game,value,state['boss'],level,state);state['boss']['hp']=max(0,state['boss']['hp']-amount);ps['damage']+=amount
 elif kind==2:
  # Per-active-Pokemon shield, capped at three applications. A switch loses
  # the outgoing guard; it never migrates to another Pokemon.
  ps['guard']=min(cfg['guard_hp'][level-1]*3,ps['guard']+cfg['guard_hp'][level-1]);ps['support_actions']=ps.get('support_actions',0)+1
 else:
  for unused,ally in group_players(state,player):
   target=ally['party'][ally['active']]
   if not ally['quit'] and target['hp']>0:target['hp']=min(engine.mon(target).max_stamina,target['hp']+max(1,int(engine.mon(target).max_stamina*cfg['spirit_fraction'][level-1])))
  ps['support_actions']=ps.get('support_actions',0)+1
 ps['max_turns']-=1;value['ready']=stamp+2500;turn=(stamp-state['start'])//500
 emit(state,31,player,stamp,cinematic=p.MaxPbsCinematic(event_type=3,begin_turn=turn,end_turn=turn+5,bread_move_metadata=p.MaxPbsMoveMetadata(move_type=kind+4)))
 emit(state,4,player,stamp,attack=p.RaidPbsAttack(attack_type=kind+4,move=max_move(game,mon) if kind==1 else 460 if kind==2 else 461,type=max_type(game,mon) if kind==1 else 1,source_pokemon_id=mon.id,target_id='max-boss' if kind==1 else player[:20]))
 if ps['max_turns']>0:select_move(state,player,stamp+2500)

def max_damage(game,source,target,level,state=None):
 # Same species/IV/CPm/type/STAB formula as existing PvE, with explicitly
 # configured custom Max base power rather than invented official GM values.
 from dittobuffer.gameplay.battle_master import effectiveness,settings
 a=engine.mon(source);d=engine.mon(target);kind=max_type(game,a)
 av=game.forms[a.pokemon_id,a.pokemon_display.form or game.species[a.pokemon_id]['form']];dv=game.forms[d.pokemon_id,d.pokemon_display.form or game.species[d.pokemon_id]['form']]
 attack=(av['attack']+a.individual_attack)*a.cp_multiplier;defense=(dv['defense']+d.individual_defense)*d.cp_multiplier;stab=settings(game.config).same_type_attack_bonus_multiplier if kind in av['types'] else 1
 cfg=combat_rules(game,state or {})
 return max(1,math.floor((.5*cfg['max_attack_power'][level-1]*attack/max(1,defense)*stab*effectiveness(kind,dv['types'])+1)*cfg.get('player_damage_multiplier',1)*cfg.get('station_assist_multiplier',1)))

def snapshot(game,db,room,state,player):
 from dittobuffer.gameplay.raid_transport import pokemon
 ensure_groups(game,db,room,state);cfg=combat_rules(game,state);local_group=state['player_groups'][player];local_phase=group_for(state,player)['phase']
 stamp=now_ms();start=state['start'];turn=max(0,(stamp-start)//500);out=p.BattleStateOutProto(turn=turn,time_ms=stamp,serial=stamp,full_state=False)
 s=out.battle_state;s.turn_start_ms=start+turn*500;s.turn=turn;s.ms_per_turn=500;s.current_actor_id=player[:20];s.state=3 if state['status']==1 else 5;s.battle_end_turn=max(1,(state['end']-start)//500);s.ui_mode=local_phase if local_phase in (0,1,2) else 0;s.active_actor_count=len(state['players']);s.team_actor_count[1]=len(state['players']);s.team_actor_count[2]=1
 from dittobuffer.gameplay.max_cheer import eligible as can_cheer
 if can_cheer(state,player):s.ui_mode=4 if local_phase in (1,2) else 3
 for actor,team,typ,values,active in [('max-boss',2,5,[state['boss']],0)]+[(key[:20],1,1,value['party'],value['active']) for key,value in state['players'].items()]:
  a=s.actors[actor];a.id=actor;a.type=typ;a.team=team;a.party_id=actor;a.active_pokemon_id=engine.mon(values[active]).id;a.pokemon_roster.extend(engine.mon(v).id for v in values);s.party_member_count[actor]=1
  if team==1:
   owner=next(k for k in state['players'] if k[:20]==actor);ps=state['players'][owner];group=group_for(state,owner)
   a.party_id='max-group-'+str(room['id'])+'-'+state['player_groups'][owner]
   s.party_member_count[a.party_id]=sum(not value['quit'] and state['player_groups'][key]==state['player_groups'][owner] for key,value in state['players'].items())
   a.resources[6].CopyFrom(p.BattleResourceProto(type=6,quantity=group['meter'],max_quantity=cfg['meter_goal']));a.resources[7].CopyFrom(p.BattleResourceProto(type=7,quantity=ps['max_turns'],max_quantity=3))
   if can_cheer(state,owner):
    a.type=8
    a.resources[9].CopyFrom(p.BattleResourceProto(type=9,quantity=ps.get('cheer',{}).get('power',0),max_quantity=cfg['cheering']['taps_required']))
  for value in values:
   mon=engine.mon(value);entry=pokemon(value)
   if team==1:
    slots={v.move_type:v.move_level for v in mon.bread_moves}
    for kind in (1,2,3):
     if slots.get(kind,0):entry.moves[kind+4].CopyFrom(p.MoveData(move=max_move(game,mon) if kind==1 else 460 if kind==2 else 461,type=max_type(game,mon) if kind==1 else 1,duration_turns=5,level=slots[kind],energy_delta=-1))
    entry.resources[7].CopyFrom(p.BattleResourceProto(type=7,quantity=ps['max_turns'],max_quantity=3));entry.resources[8].CopyFrom(p.BattleResourceProto(type=8,quantity=ps['guard'],max_quantity=cfg['guard_hp'][2]*3))
   s.pokemon[mon.id].CopyFrom(entry)
 for raw in state['events']:
  event=p.BattleEventProto.FromString(bytes.fromhex(raw))
  owner=next((key for key in state['players'] if key[:20]==event.actor_id),None)
  # Native cinematic managers control the local model. Another subgroup's
  # transformation/selection must never transform this client's own Pokemon.
  if event.type in (23,24,31,32,33) and owner is not None and state['player_groups'][owner]!=local_group:continue
  s.events.add().CopyFrom(event)
 s.allied_pokemon_remaining=sum(v['hp']>0 for key,ps in group_players(state,player) if not ps['quit'] for v in ps['party'])
 return out

@contextmanager
def measured_transaction(game):
 start=time.perf_counter();changes=0;committed=False
 try:
  with game.db.transaction() as db:
   before=db.total_changes
   try:yield db
   finally:changes=db.total_changes-before
  committed=True
 finally:
  if game.max_config.get('diagnostics',False):
   logging.info('MAX_METRIC %s',json.dumps(dict(processing_ms=round((time.perf_counter()-start)*1000,3),row_changes=changes,committed=committed)))

def handle(game,path,headers,body=b''):
 with measured_transaction(game) as db:
  token=headers.get('Authorization','').removeprefix('Bearer ');m=db.execute('SELECT * FROM max_members WHERE token=? AND left_ms=0',(token,)).fetchone()
  if not m:raise RpcError('Invalid Max combat session')
  room=db.execute('SELECT * FROM max_rooms WHERE id=?',(m['room_id'],)).fetchone();player=m['player_id']
  if str(room['id'])!=headers.get('nbl-battle-id') or headers.get('nbl-player-id') not in (player,player[:20]):raise RpcError('Max session ownership mismatch')
  if game.accounts:
   from dittobuffer.core.accounts import AccountError
   try:game.accounts.authorize_digest(db,m['session_digest'])
   except AccountError:raise RpcError('Max account session expired') from None
  if path.endswith('/get-time'):return p.BattleClockSyncOutProto(result=1,server_time_ms=now_ms())
  state=ensure_groups(game,db,room,json.loads(room['state']))
  if player not in state['players']:raise RpcError('Max party has not started')
  if path.endswith('/get-state'):
   advance(game,db,room,state,now_ms());return snapshot(game,db,room,state,player)
  req=p.SendBattleEventProto.FromString(body)
  if req.battle_id!=str(room['id']) or req.player_id not in (player,player[:20]) or req.sequence_id<0 or req.battle_event.actor_id not in ('',player[:20]):raise RpcError('Invalid Max event owner')
  credential_hash=hashlib.sha256(token.encode()).hexdigest()
  digest=hashlib.sha256(body).hexdigest();old=db.execute('SELECT * FROM max_session_event_receipts WHERE room_id=? AND player_id=? AND credential_hash=? AND sequence=?',(room['id'],player,credential_hash,req.sequence_id)).fetchone()
  if old:
   if old['request_hash']!=digest:raise RpcError('Max sequence reused')
   return p.SendBattleEventOutProto.FromString(old['response'])
  stamp=now_ms();advance(game,db,room,state,stamp);ps=state['players'][player];group=group_for(state,player);cfg=combat_rules(game,state);value=ps['party'][ps['active']];mon=engine.mon(value);event=req.battle_event;accepted=event.type in (1,2,41,45,47)
  if event.type==25:
   from dittobuffer.gameplay.max_cheer import apply
   accepted=apply(game,state,player,event,stamp,emit)
  if state['status']==1 and event.type==3 and not ps['quit']:
   ps['quit']=True;resources.release_hold(db,player,str(room['id']));db.execute('DELETE FROM max_pokemon_locks WHERE room_id=? AND player_id=?',(str(room['id']),player));accepted=True
  if state['status']==1 and value['hp']>0 and not ps['quit']:
   if event.type==4 and group['phase']==0 and value['ready']<=stamp:
    move={1:mon.move1,2:mon.move2,3:mon.move3}.get(event.attack.attack_type)
    if move not in MOVES or event.attack.move not in (0,move) or event.attack.source_pokemon_id not in (0,mon.id):raise RpcError('Invalid Max normal move')
    if value['energy']+MOVES[move][3]>=0:
     amount=max(1,math.floor(engine.damage(game,value,state['boss'],move)*cfg.get('player_damage_multiplier',1)*cfg.get('station_assist_multiplier',1)));state['boss']['hp']=max(0,state['boss']['hp']-amount);ps['damage']+=amount;value['energy']=min(100,value['energy']+MOVES[move][3]);value['ready']=stamp+MOVES[move][2];group['meter']=min(cfg['meter_goal'],group['meter']+cfg['meter_per_attack']);emit(state,4,player,stamp,attack=p.RaidPbsAttack(attack_type=event.attack.attack_type,move=move,type=MOVES[move][0],source_pokemon_id=mon.id,target_id='max-boss'));accepted=True
     if event.attack.attack_type in (2,3):
      from dittobuffer.gameplay.battle_master import effectiveness
      from dittobuffer.gameplay.mega import combat_rules as pokemon_rules
      target=engine.mon(state['boss']);definition=game.forms.get((target.pokemon_id,target.pokemon_display.form),game.species[target.pokemon_id])
      if effectiveness(MOVES[move][0],pokemon_rules(target,definition)['types'])>1:
       from dittobuffer.gameplay.trainer import event as source_event
       source_event(game,db,player,'max-charge:'+str(room['id'])+':'+str(len(state['events'])),'super_effective_charge',{'super_effective':True,'move':move,'source':'max'})
     begin_max(state,player,stamp)
   elif event.type==4 and event.attack.attack_type in (5,6,7) and group['phase']==2 and ps['max_turns']>0 and value['ready']<=stamp:
    execute_max(game,state,player,event.attack.attack_type-4,stamp);accepted=True
    if all(v['max_turns']==0 or v['quit'] or not any(m['hp'] for m in v['party']) for unused,v in group_players(state,player)):
     revert_group(state,player,stamp+2500)
   elif event.type==24:
    accepted=event.bread_move.type in (1,2,3)
   elif event.type==5 and group['phase']==0:
    value['dodge_until']=stamp+1000;emit(state,5,player,stamp,dodge=p.RaidPbsDodge(direction=event.dodge.direction or 1));accepted=True
   elif event.type==7 and group['phase']!=2:
    index=next((i for i,v in enumerate(ps['party']) if engine.mon(v).id==event.swap_pokemon.incoming_pokemon_id and v['hp']>0),None)
    if index is not None and index!=ps['active']:ps['active']=index;ps['guard']=0;emit(state,7,player,stamp,swap_pokemon=p.RaidPbsSwap(outgoing_pokemon_id=mon.id,incoming_pokemon_id=event.swap_pokemon.incoming_pokemon_id));accepted=True
   elif event.type==3:
    ps['quit']=True;resources.release_hold(db,player,str(room['id']));db.execute('DELETE FROM max_pokemon_locks WHERE room_id=? AND player_id=?',(str(room['id']),player));accepted=True
  advance(game,db,room,state,stamp)
  response=p.SendBattleEventOutProto(result=1 if accepted else 2,sequence_id=req.sequence_id,accepted_turn=max(0,(stamp-state['start'])//500));db.execute('INSERT INTO max_session_event_receipts VALUES(?,?,?,?,?,?)',(room['id'],player,credential_hash,req.sequence_id,digest,response.SerializeToString()))
  logging.info('MAX PBS event=%d sequence=%d accepted=%s boss_hp=%d meter=%d phase=%d',event.type,req.sequence_id,accepted,state['boss']['hp'],state['meter'],state['phase']);return response

def catch_member(game,db,player,spawn):
 if not resources.enabled(game):return None
 return db.execute('SELECT * FROM max_members WHERE player_id=? AND spawn_id=? AND reward IS NOT NULL',(player,spawn)).fetchone()

def rejoin_status(game,db,player,body):
 """Native RPC3062, scoped to the authenticated account; never refills a party."""
 p.GetBattleRejoinStatusProto.FromString(body)
 missing=lambda:p.GetBattleRejoinStatusOutProto(result=2)
 if not ready(game):return missing()
 rows=db.execute('SELECT r.* FROM max_rooms r JOIN max_members m ON m.room_id=r.id WHERE m.player_id=? AND m.left_ms=0 AND r.created_ms>? ORDER BY r.created_ms DESC',(player,now_ms()-86400000)).fetchall()
 for room in rows:
  state=ensure_groups(game,db,room,json.loads(room['state']))
  if player not in state['players'] or state['players'][player]['quit']:continue
  advance(game,db,room,state,now_ms());m=member(db,room,player)
  if state['status']==1 and room['end_ms']>now_ms():result=1
  elif state['status']==2 and db.execute('SELECT 1 FROM max_victories WHERE player_id=? AND room_id=?',(player,room['id'])).fetchone():
   caught=db.execute('SELECT state FROM encounters WHERE player_id=? AND spawn_id=?',(player,m['spawn_id'])).fetchone()
   if caught and caught['state']!='active':continue
   result=3
  else:continue
  from dittobuffer.core.accounts import principal_session
  session=principal_session.get() or ''
  if session and session!=m['session_digest']:
   db.execute('UPDATE max_members SET token=?,session_digest=? WHERE room_id=? AND player_id=?',(secrets.token_urlsafe(32),session,room['id'],player));m=member(db,room,player)
  return p.GetBattleRejoinStatusOutProto(result=result,battle_type=2,battle_seed=room['seed'],poi_id=room['station_id'],rvn_connection=connection(m),session_player_id=player[:20])
 return missing()

def bonus(game,db,m,station):
 from dittobuffer.gameplay.items import probabilities
 spawn=db.execute('SELECT s.*,a.data pokemon_data FROM spawns s JOIN spawn_attributes a ON a.spawn_id=s.id WHERE s.id=?',(m['spawn_id'],)).fetchone()
 return p.RaidEncounterProto(pokemon=game.pokemon(spawn),encounter_id=m['spawn_id'],spawnpoint_id=spawn['spawnpoint'],capture_probabilities=probabilities(game),throws_remaining=m['balls'],raid_level=station.battle_details.battle_level,fort_id=station.id,raid_ball=5,appraisal_star=3)

def complete(game,db,player,body):
 req=p.CompleteBreadBattleProto.FromString(body);fail=lambda n:p.CompleteBreadBattleOutProto(result=n)
 if not resources.enabled(game):return fail(2)
 if not req.bread_battle_id:
  # Native post-battle recovery submits station_id alone. Resolve only this
  # authenticated trainer's committed victory; never create a new result.
  pending=db.execute('SELECT r.id FROM max_rooms r JOIN max_victories v ON v.room_id=r.id WHERE r.station_id=? AND v.player_id=? ORDER BY r.created_ms DESC LIMIT 1',(req.station_id,player)).fetchone()
  if not pending:return fail(2)
  req.bread_battle_id=str(pending['id'])
 if not req.bread_battle_id.isdigit():return fail(2)
 room=db.execute('SELECT * FROM max_rooms WHERE id=? AND station_id=?',(int(req.bread_battle_id),req.station_id)).fetchone()
 if not room:return fail(2)
 m=member(db,room,player)
 if not m:return fail(5)
 row=db.execute('SELECT * FROM max_stations WHERE id=?',(room['station_id'],)).fetchone()
 if m['reward']:
  out=p.CompleteBreadBattleOutProto.FromString(m['reward'])
  if out.is_victory:out.battle_results.post_battle_encounter.throws_remaining=m['balls']
  return out
 state=ensure_groups(game,db,room,json.loads(room['state']));station=frozen_station(game,db,room,state,player);advance(game,db,room,state,now_ms())
 if state['status'] in (0,1):return fail(3)
 ps=state['players'].get(player)
 if not ps or ps['quit'] or m['left_ms']:return fail(5)
 result=p.BreadBattleResultsProto(station_state=station,battle_duration_ms=max(0,state['clock']-state['start']))
 result.attackers.CopyFrom(engine.participant(game,db,player,dict(party=ps['party'],active=ps['active'])))
 out=p.CompleteBreadBattleOutProto(result=1,is_victory=state['status']==2,battle_results=result)
 if not out.is_victory:
  resources.release_hold(db,player,str(room['id']));db.execute('UPDATE max_members SET reward=? WHERE room_id=? AND player_id=?',(out.SerializeToString(),room['id'],player));return out
 if ps['damage']<=0 and not ps.get('support_actions',0):return fail(5)
 victory=db.execute('SELECT room_id FROM max_victories WHERE station_id=? AND player_id=?',(row['id'],player)).fetchone()
 if not victory or victory['room_id']!=room['id']:return fail(5)
 cfg=combat_rules(game,state);resources.charge_hold(game,db,player,str(room['id']))
 balls=int(cfg['premier_balls']);spawn_id=secrets.randbits(61) or 1;point='max:'+str(room['id'])+':'+player;stamp=now_ms()
 mon=p.PokemonProto();mon.CopyFrom(station.battle_details.reward_pokemon);mon.id=0;mon.creation_time_ms=stamp
 level=json.loads(row['definition']).get('level',10)
 db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',(spawn_id,point,mon.pokemon_id,row['lat'],row['lng'],stamp+86400000));db.execute('INSERT INTO spawn_attributes VALUES(?,?,?)',(spawn_id,mon.SerializeToString(),level));db.execute('INSERT INTO encounters(player_id,spawn_id) VALUES(?,?)',(player,spawn_id));db.execute('UPDATE max_members SET spawn_id=?,balls=? WHERE room_id=? AND player_id=?',(spawn_id,balls,room['id'],player))
 result=out.battle_results;result.player_xp_awarded=int(cfg['xp']);result.participation.base_pokeballs=balls;result.participant_pokemon_ids.extend(engine.mon(v).id for v in ps['party']);result.station_state.player_battle_status=2;result.station_state.battle_details.complete=True
 for item,count in cfg.get('items',{}).items():
  db.execute('INSERT INTO items VALUES(?,?,?) ON CONFLICT(player_id,item) DO UPDATE SET count=count+excluded.count',(player,int(item),count));result.battle_item_rewards.loot_item.add(item=int(item),count=count)
 if cfg['dust']:result.battle_item_rewards.loot_item.add(stardust=True,count=cfg['dust'])
 db.execute('UPDATE players SET xp=xp+?,stardust=stardust+? WHERE id=?',(cfg['xp'],cfg['dust'],player));game.touch(db,player)
 result.post_battle_encounter.CopyFrom(bonus(game,db,member(db,room,player),station));db.execute('UPDATE max_members SET reward=?,damage=? WHERE room_id=? AND player_id=?',(out.SerializeToString(),ps['damage'],room['id'],player))
 from dittobuffer.gameplay.research import progress
 progress(game,db,player,'max_win')
 return out
