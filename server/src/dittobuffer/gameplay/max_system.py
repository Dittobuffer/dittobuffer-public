"""Opt-in native Max resources and Power Spots, separate from forts and raids.

All writes run in the existing authenticated gameplay transaction. Pokémon stay
in the canonical owned collection; particles have a separate resource wallet.
"""
import functools,hashlib,json,math,random,re,secrets,sqlite3
from contextlib import closing
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from google.protobuf.message import DecodeError
from dittobuffer.gameplay.game import ROOT,p,now_ms,distance

SCHEMA='''
CREATE TABLE IF NOT EXISTS max_wallet(player_id TEXT PRIMARY KEY REFERENCES players(id),balance INTEGER NOT NULL DEFAULT 0 CHECK(balance>=0));
CREATE TABLE IF NOT EXISTS max_daily(player_id TEXT NOT NULL REFERENCES players(id),day TEXT NOT NULL,collected INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(player_id,day));
CREATE TABLE IF NOT EXISTS max_stations(id TEXT PRIMARY KEY,source_id TEXT NOT NULL,slot INTEGER NOT NULL,name TEXT NOT NULL,lat REAL NOT NULL,lng REAL NOT NULL,start_ms INTEGER NOT NULL,end_ms INTEGER NOT NULL,definition TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS max_station_location ON max_stations(lat,lng,end_ms);
CREATE TABLE IF NOT EXISTS max_claims(player_id TEXT NOT NULL REFERENCES players(id),station_id TEXT NOT NULL REFERENCES max_stations(id),day TEXT NOT NULL,response BLOB NOT NULL,PRIMARY KEY(player_id,station_id,day));
CREATE TABLE IF NOT EXISTS max_ledger(player_id TEXT NOT NULL REFERENCES players(id),source TEXT NOT NULL,amount INTEGER NOT NULL,balance INTEGER NOT NULL,created_ms INTEGER NOT NULL,PRIMARY KEY(player_id,source));
CREATE TABLE IF NOT EXISTS max_walks(id TEXT PRIMARY KEY,player_id TEXT NOT NULL REFERENCES players(id),start_m REAL NOT NULL,goal_m INTEGER NOT NULL,reward INTEGER NOT NULL,created_ms INTEGER NOT NULL,claimed_ms INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS max_walk_active ON max_walks(player_id) WHERE claimed_ms=0;
CREATE TABLE IF NOT EXISTS max_particle_holds(player_id TEXT NOT NULL REFERENCES players(id),source TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),expires_ms INTEGER NOT NULL,charged_ms INTEGER NOT NULL DEFAULT 0,released_ms INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(player_id,source));
CREATE TABLE IF NOT EXISTS max_pokemon_locks(pokemon_id INTEGER PRIMARY KEY REFERENCES pokemon(id) ON DELETE CASCADE,player_id TEXT NOT NULL REFERENCES players(id),room_id TEXT NOT NULL,expires_ms INTEGER NOT NULL);
'''
SCHEDULE_SCHEMA='''
CREATE TABLE IF NOT EXISTS max_schedule_segments(station_id TEXT PRIMARY KEY REFERENCES max_stations(id),period_key TEXT NOT NULL,scheduled_start_ms INTEGER NOT NULL,scheduled_end_ms INTEGER NOT NULL,retired_ms INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS max_station_source ON max_stations(source_id,end_ms);
'''
DEFAULTS=dict(enabled=False,reference='data/static/max-reference.json',reference_sha256=None,require_complete_reference=False,candidates='data/world/power-spots.sqlite3',timezone='Europe/Amsterdam',reset_hour=5,daily_limit=800,capacity=1500,station_particles=100,first_visit_bonus=20,rotation_seconds=86400,active_seconds=86400,active_probability=.34,minimum_level=20,battle_enabled=False,seed='local-max-v1',cap_policy='threshold')

MANUAL_PREFIX='manual:'
MANUAL_MAX_SECONDS=30*86400

def enabled(game):return bool(game.config.get('max_system',{}).get('enabled'))

def released_bosses(config):
 """Every explicit pool entry that may appear at a Power Spot right now."""
 raw=config.get('max_system') if isinstance(config,dict) and isinstance(config.get('max_system'),dict) else config
 cfg=DEFAULTS|(raw or {});pools=list(cfg.get('pools') or [])
 schedule=cfg.get('schedule') or {}
 if schedule.get('enabled'):
  from dittobuffer.gameplay.max_schedule import validate_config
  value=validate_config(schedule)
  if value.pool is not None:pools.extend(value.pool)
  for event in value.events:
   if event.enabled and event.pool is not None:pools.extend(event.pool)
 return pools

def _default_form(game,species):
 """The Max reference's base form for a species, independent of the stat catalog."""
 groups=game.max_reference['species_groups']
 if f'{species}:0' in groups:return 0
 for key in groups:
  side,_,form=key.partition(':')
  if side==str(species):return int(form)
 return None

def boss_definition(game,value):
 """Validate and normalize one released or hand-placed Max boss definition."""
 if not isinstance(value,dict):raise ValueError('Invalid Max boss definition')
 try:species=int(value.get('species',0));mode=int(value.get('mode',1))
 except (TypeError,ValueError):raise ValueError('Invalid Max boss definition')
 form=value.get('form')
 if form is None:form=_default_form(game,species)
 if form is not None:
  try:form=int(form)
  except (TypeError,ValueError):raise ValueError('Unsupported explicit Max species/form/mode')
 tier=value.get('tier',1);level=value.get('level',10);weight=value.get('weight',1)
 if species not in game.species or (species,form) not in game.forms or not game.max_reference['species_groups'].get(f'{species}:{form}') or mode not in (1,2):raise ValueError('Unsupported explicit Max species/form/mode')
 if type(weight) is not int or weight<=0 or not 1<=level<=40 or tier not in (1,2,3,4,5,7):raise ValueError('Invalid Max boss definition')
 if mode==2:
  shared=gm(game.max_reference,'BREAD_SHARED_SETTINGS').bread_settings
  if not any(v.pokemon_id==species and form in v.form and v.bread_mode==2 for v in shared.allowed_sourdough_pokemon):raise ValueError('Gigantamax form is not source-allowlisted')
 return dict(species=species,form=form,mode=mode,tier=tier,level=level,weight=weight)

@functools.lru_cache(maxsize=8)
def reference(path,require_complete=False,expected_sha256=None):
 target=(ROOT/path).resolve()
 if not target.is_relative_to(ROOT):raise ValueError('Project-local authorized Max reference required')
 if type(require_complete) is not bool:raise ValueError('require_complete_reference must be a boolean')
 if expected_sha256 is not None and (not isinstance(expected_sha256,str) or not re.fullmatch('[a-fA-F0-9]{64}',expected_sha256)):raise ValueError('Invalid Max reference SHA-256 pin')
 raw=target.read_bytes()
 if expected_sha256 is not None and hashlib.sha256(raw).hexdigest()!=expected_sha256.lower():raise ValueError('Max reference SHA-256 pin differs; original input preserved')
 data=json.loads(raw);required=('MP_SHARED_SETTINGS','BREAD_FEATURE_FLAGS','BREAD_SHARED_SETTINGS','BREAD_MOVE_MAPPING_SETTINGS','BREAD_MOVE_LEVEL_SETTINGS_GROUP_1')
 if not isinstance(data,dict) or not isinstance(data.get('templates'),dict) or not isinstance(data.get('species_groups'),dict) or not data['species_groups'] or any(k not in data['templates'] for k in required):raise ValueError('Incomplete Max compatibility reference')
 for name in data['templates']:gm(data,name)
 if require_complete:
  issues=reference_quality(data)['issues']
  if issues:raise ValueError('Incomplete native Max reference: '+'; '.join(issues))
 return data

def install(game):
 game.max_config=DEFAULTS|game.config.get('max_system',{})
 if not enabled(game):return
 cfg=game.max_config;ZoneInfo(cfg['timezone'])
 for key,low,high in [('reset_hour',0,23),('daily_limit',1,100000),('capacity',1,100000),('station_particles',1,10000),('first_visit_bonus',0,10000),('rotation_seconds',60,604800),('active_seconds',30,604800),('minimum_level',1,100)]:
  if type(cfg[key]) is not int or not low<=cfg[key]<=high:raise ValueError('Invalid Max '+key)
 if cfg['active_seconds']>cfg['rotation_seconds'] or type(cfg['active_probability']) not in (int,float) or not math.isfinite(cfg['active_probability']) or not 0<=cfg['active_probability']<=1 or cfg['cap_policy'] not in ('threshold','clamp'):raise ValueError('Invalid Max schedule/caps')
 game.max_reference=reference(cfg['reference'],cfg['require_complete_reference'],cfg['reference_sha256']);path=(ROOT/cfg['candidates']).resolve()
 if not path.is_relative_to(ROOT) or not path.is_file():raise ValueError('Separate Power Spot candidate database required')
 with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
  metadata=json.loads(db.execute("SELECT value FROM metadata WHERE key='source'").fetchone()[0])
  if metadata.get('license')!='ODbL-1.0':raise ValueError('Reviewed permitted map provenance required')
 game.max_candidates=path
 pools=cfg.get('pools',[])
 if not pools:raise ValueError('Explicit released Max species pool required')
 from dittobuffer.gameplay.max_schedule import validate_config as validate_schedule
 game.max_schedule=validate_schedule(cfg.get('schedule',{}))
 released=list(pools)
 if game.max_schedule.enabled:
  if game.max_schedule.pool is not None:released.extend(game.max_schedule.pool)
  for event in game.max_schedule.events:
   if event.enabled and event.pool is not None:released.extend(event.pool)
 game.max_released_pools=released
 for value in released:boss_definition(game,value)
 with closing(game.db.connect()) as db:
  db.executescript(SCHEMA)
  from dittobuffer.gameplay.max_battle import install as install_battles,validate_config
  validate_config(game)
  install_battles(db)
  from dittobuffer.gameplay.max_station import install as install_station
  install_station(game,db)
  if game.max_schedule.enabled:db.executescript(SCHEDULE_SCHEMA)
  elif db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='max_schedule_segments'").fetchone():
   # Switching a previously enabled schedule off must not leave event spots
   # beside newly created legacy UTC spots. No rows/history are deleted.
   stamp=now_ms();rows=db.execute('SELECT s.* FROM max_stations s JOIN max_schedule_segments m ON m.station_id=s.id WHERE s.end_ms>?',(stamp,)).fetchall()
   _retire_scheduled(db,rows,stamp);db.commit()

# The authorized reference keys each entry by template id and stores only that
# template's settings message. Rebuilding the wrapper from the key keeps every
# template identifiable and uniquely mergeable into the client's Game Master.
SETTINGS_FIELDS={
 'BREAD_BATTLE_CLIENT_SETTINGS':'bread_battle_client_settings','BREAD_FEATURE_FLAGS':'bread_feature_flags',
 'BREAD_LOBBY_COUNTER_SETTINGS':'bread_lobby_counter_settings','BREAD_LOBBY_UPDATE_SETTINGS':'bread_lobby_update_settings',
 'BREAD_MOVE_MAPPING_SETTINGS':'bread_move_mappings','BREAD_POKEMON_SCALING_SETTINGS':'bread_pokemon_scaling_settings',
 'BREAD_SHARED_SETTINGS':'bread_settings','MP_SHARED_SETTINGS':'mp_settings',
 'SOURDOUGH_MOVE_MAPPING_SETTINGS':'sourdough_move_mapping_settings',
 'STATIONED_POKEMON_POWER_BOOST_TABLE_SETTINGS':'stationed_pokemon_table_settings',
 'VNEXT_BATTLE_CLIENT_SETTINGS':'vnext_battle_config',
 'STATION_REWARD_SETTINGS':'station_reward_settings',
}
for _group in ('1','2','3','4','7','8','Z'):SETTINGS_FIELDS['BREAD_MOVE_LEVEL_SETTINGS_GROUP_'+_group]='bread_move_level_settings'
del _group

def _mapping_validation(value,name):
 if name=='BREAD_MOVE_MAPPING_SETTINGS' and value.HasField('bread_move_mappings'):
  seen=set()
  for mapping in value.bread_move_mappings.mappings:
   if mapping.type not in p.HoloPokemonType.values() or mapping.type<=0 or mapping.move<=0 or mapping.type in seen:raise ValueError('Invalid or ambiguous Max type/move mapping: '+name)
   seen.add(mapping.type)
 if name=='SOURDOUGH_MOVE_MAPPING_SETTINGS' and value.HasField('sourdough_move_mapping_settings'):
  seen=set()
  for mapping in value.sourdough_move_mapping_settings.mappings:
   identity=(mapping.pokemon_id,mapping.form)
   if mapping.pokemon_id<=0 or mapping.move<=0 or identity in seen:raise ValueError('Invalid or ambiguous Gmax move mapping: '+name)
   for override in (mapping.optional_b_move_override,mapping.optional_c_move_override):
    if override.override and override.move<=0:raise ValueError('Invalid Gmax move override: '+name)
   seen.add(identity)


def gm(data,name):
 if not isinstance(name,str):raise ValueError('Invalid Max template identity')
 try:raw=bytes.fromhex(data['templates'][name])
 except (KeyError,TypeError,ValueError) as error:raise ValueError('Invalid Max reference template bytes: '+name) from error
 try:stored=p.GameMasterClientTemplateProto.FromString(raw)
 except DecodeError:stored=None
 field=SETTINGS_FIELDS.get(name)
 # Full authorized wrappers already carry native template ids and unknown
 # fields. Return that message intact instead of nesting it inside settings.
 if stored is not None and stored.template_id==name:
  selected=stored.WhichOneof('data')
  if field is not None and selected is not None and selected!=field:raise ValueError('Wrong Max template data type: '+name)
  _mapping_validation(stored,name)
  return stored
 # Bare settings can interpret nested bytes as a control-character string;
 # printable mismatched identities are actual wrong envelopes, not aliases.
 if stored is not None and stored.template_id and stored.template_id.isprintable():raise ValueError('Max template id does not match reference key: '+name)
 if field is None:raise ValueError('Unknown bare Max reference template: '+name)
 value=p.GameMasterClientTemplateProto(template_id=name)
 try:getattr(value,field).ParseFromString(raw)
 except DecodeError as error:raise ValueError('Invalid bare Max settings: '+name) from error
 _mapping_validation(value,name)
 return value


def reference_quality(data):
 """Report native readiness without inventing absent settings or assets.

 Minimal ID-only wrappers remain usable by explicitly synthetic backend tests.
 Native profiles can require completeness and pin their independently supplied
 reference; this keeps a partial checkout from silently posing as full GM data.
 """
 required=('MP_SHARED_SETTINGS','BREAD_FEATURE_FLAGS','BREAD_SHARED_SETTINGS',
           'BREAD_MOVE_MAPPING_SETTINGS','BREAD_MOVE_LEVEL_SETTINGS_GROUP_1',
           'BREAD_BATTLE_CLIENT_SETTINGS','BREAD_POKEMON_SCALING_SETTINGS','VNEXT_BATTLE_CLIENT_SETTINGS')
 issues=[];values={}
 for name in required:
  if name not in data['templates']:
   issues.append('missing '+name);continue
  value=gm(data,name);values[name]=value
  if value.WhichOneof('data')!=SETTINGS_FIELDS[name]:issues.append('missing settings payload for '+name)
 shared=values.get('BREAD_SHARED_SETTINGS')
 shared=shared.bread_settings if shared is not None else None
 availability=None
 if shared is None or not shared.HasField('bread_battle_availability'):issues.append('missing native Power Spot availability')
 else:
  window=shared.bread_battle_availability
  availability=[window.bread_battle_availability_start_minute,window.bread_battle_availability_end_minute]
  if not all(0<=minute<=1440 for minute in availability) or availability[0]==availability[1]:issues.append('invalid native Power Spot availability window')
 mappings=values.get('BREAD_MOVE_MAPPING_SETTINGS')
 mappings=mappings.bread_move_mappings.mappings if mappings is not None else []
 if not mappings:issues.append('missing native Max type/move mappings')
 costs=values.get('BREAD_MOVE_LEVEL_SETTINGS_GROUP_1')
 if costs is None or not all(getattr(costs.bread_move_level_settings,slot) for slot in ('a_settings','b_settings','c_settings')):issues.append('missing native Max move upgrade definitions')
 battle=values.get('VNEXT_BATTLE_CLIENT_SETTINGS')
 if battle is None or not battle.vnext_battle_config.HasField('max_battle_config'):issues.append('missing native Max battle client config')
 return dict(templates=len(data['templates']),species_groups=len(data['species_groups']),
             max_type_mappings=len(mappings),gmax_allowlists=len(shared.allowed_sourdough_pokemon) if shared is not None else 0,
             availability_minutes=availability,max_ball_settings='POKEBALL_THROW_PROPERTY_SETTINGS' in data['templates'],issues=issues)

def templates(config):
 cfg=DEFAULTS|config.get('max_system',{})
 if not cfg['enabled']:return []
 data=reference(cfg['reference'],cfg['require_complete_reference'],cfg['reference_sha256']);out=[]
 for name in data['templates']:
  if name=='VNEXT_BATTLE_CLIENT_SETTINGS':continue  # merged with working raids below
  value=gm(data,name)
  if name=='MP_SHARED_SETTINGS':
   value.mp_settings.mp_capacity=cfg['capacity'];value.mp_settings.mp_base_daily_limit=cfg['daily_limit'];value.mp_settings.num_mp_from_loot_station=cfg['station_particles'];value.mp_settings.num_extra_mp_from_first_loot_station=cfg['first_visit_bonus'];value.mp_settings.debug_allow_remove_walk_quest=False
  elif name=='BREAD_FEATURE_FLAGS':
   flags=value.bread_feature_flags;flags.enabled=True;flags.discovery_enabled=True;flags.mp_enabled=True;flags.minimum_player_level=cfg['minimum_level'];flags.battle_enabled=cfg['battle_enabled'];flags.bread_post_battle_recovery_enabled=True;flags.debug_rpc_enabled=False;flags.power_spot_edits_enabled=False;flags.lobby_push_update_enabled=False;flags.nearby_lobby_counter_enabled=False;flags.boost_item_enabled=False;flags.can_use_master_ball_post_battle=False
  elif name=='BREAD_BATTLE_CLIENT_SETTINGS':
   settings=value.bread_battle_client_settings;settings.remote_bread_battle_enabled=False;settings.max_players_per_bread_lobby=4;settings.max_players_per_bread_dough_lobby=20;settings.friend_requests_enabled=False;settings.fetch_profile_from_social_enabled=False
  out.append(value)
 return out

def merge_client_settings(config,values):
 if not config.get('max_system',{}).get('enabled'):return
 cfg=DEFAULTS|config['max_system'];data=reference(cfg['reference'],cfg['require_complete_reference'],cfg['reference_sha256'])
 if 'VNEXT_BATTLE_CLIENT_SETTINGS' not in data['templates']:return
 target=next((v for v in values if v.template_id=='VNEXT_BATTLE_CLIENT_SETTINGS'),None)
 if target is None:target=p.GameMasterClientTemplateProto(template_id='VNEXT_BATTLE_CLIENT_SETTINGS');values.append(target)
 target.vnext_battle_config.max_battle_config.CopyFrom(gm(data,'VNEXT_BATTLE_CLIENT_SETTINGS').vnext_battle_config.max_battle_config)

def day(game,stamp=None):
 local=datetime.fromtimestamp((now_ms() if stamp is None else stamp)/1000,ZoneInfo(game.max_config['timezone']))
 return (local.date()-timedelta(days=local.hour<game.max_config['reset_hour'])).isoformat()

def next_reset(game):
 current=datetime.fromisoformat(day(game)).date()+timedelta(days=1)
 return int(datetime(current.year,current.month,current.day,game.max_config['reset_hour'],tzinfo=ZoneInfo(game.max_config['timezone'])).timestamp()*1000)

def balance(db,player):
 db.execute('INSERT OR IGNORE INTO max_wallet(player_id) VALUES(?)',(player,))
 return db.execute('SELECT balance FROM max_wallet WHERE player_id=?',(player,)).fetchone()[0]

def available(db,player):
 held=db.execute('SELECT COALESCE(SUM(amount),0) FROM max_particle_holds WHERE player_id=? AND expires_ms>? AND charged_ms=0 AND released_ms=0',(player,now_ms())).fetchone()[0]
 return balance(db,player)-held

def reserve(game,db,player,source,amount,expires_ms):
 if type(amount) is not int or amount<=0 or expires_ms<=now_ms():raise ValueError('Invalid Max reservation')
 old=db.execute('SELECT * FROM max_particle_holds WHERE player_id=? AND source=?',(player,source)).fetchone()
 if old:
  if old['amount']!=amount:raise ValueError('Max reservation identity reused')
  return not old['released_ms'] and (bool(old['charged_ms']) or old['expires_ms']>now_ms())
 if available(db,player)<amount:return False
 db.execute('INSERT INTO max_particle_holds(player_id,source,amount,expires_ms) VALUES(?,?,?,?)',(player,source,amount,expires_ms));return True

def release_hold(db,player,source):
 db.execute('UPDATE max_particle_holds SET released_ms=? WHERE player_id=? AND source=? AND charged_ms=0 AND released_ms=0',(now_ms(),player,source))

def charge_hold(game,db,player,source):
 row=db.execute('SELECT * FROM max_particle_holds WHERE player_id=? AND source=?',(player,source)).fetchone()
 if not row or row['released_ms'] or not row['charged_ms'] and row['expires_ms']<=now_ms():raise ValueError('No valid Max reservation')
 if row['charged_ms']:return False
 db.execute('UPDATE max_particle_holds SET charged_ms=? WHERE player_id=? AND source=?',(now_ms(),player,source))
 mutate(game,db,player,-row['amount'],'battle:'+source);return True

def mutate(game,db,player,amount,source):
 old=db.execute('SELECT * FROM max_ledger WHERE player_id=? AND source=?',(player,source)).fetchone()
 if old:
  if old['amount']!=amount:raise ValueError('Max resource identity reused for another amount')
  return False
 before=balance(db,player)
 if before+amount<0 or amount<0 and available(db,player)+amount<0:raise ValueError('Insufficient unreserved Max Particles')
 db.execute('UPDATE max_wallet SET balance=balance+? WHERE player_id=?',(amount,player));db.execute('INSERT INTO max_ledger VALUES(?,?,?,?,?)',(player,source,amount,before+amount,now_ms()));game.touch(db,player);return True

def inventory(game,db,player):
 if not enabled(game):return []
 value=p.HoloInventoryItemProto();value.item.item=1506;value.item.count=balance(db,player);return [value]

def summary(game,db,player,body):
 p.GetMpSummaryProto.FromString(body)
 if not enabled(game):return p.GetMpSummaryOutProto()
 row=db.execute('SELECT collected FROM max_daily WHERE player_id=? AND day=?',(player,day(game))).fetchone()
 return p.GetMpSummaryOutProto(mp_collected_today=row[0] if row else 0,mp_daily_limit=game.max_config['daily_limit'])

def capable(game,pokemon,mode=1):
 if pokemon.pokemon_id not in game.species:return False
 raw_form=pokemon.pokemon_display.form;default_form=game.species[pokemon.pokemon_id]['form']
 # Zero is both the protobuf's unset value and a source-backed base identity.
 # Try that explicit identity before the historical unset/default fallback;
 # nonzero forms never gain aliases, and the released pool remains authority.
 forms=(raw_form,) if raw_form else (0,default_form)
 return any(v['species']==pokemon.pokemon_id and v.get('form',default_form) in forms and v.get('mode',1)==mode for v in getattr(game,'max_released_pools',game.max_config.get('pools',[])))

def attach(game,pokemon,mode=1):
 if not capable(game,pokemon,mode):raise ValueError('Max eligibility is not explicitly released')
 pokemon.pokemon_display.bread_mode_enum=mode;del pokemon.bread_moves[:]
 for slot,level in ((1,1),(2,0),(3,0)):pokemon.bread_moves.add(move_type=slot,move_level=level)
 return pokemon

def materialize(game,db,bounds):
 if not enabled(game):return
 if hasattr(game,'liveops_max_window') or (getattr(game,'max_schedule',None) is not None and game.max_schedule.enabled):
  # DDL is transactional here; executescript would commit the enclosing RPC.
  for statement in SCHEDULE_SCHEMA.split(';'):
   if statement.strip():db.execute(statement)
  return _materialize_scheduled(game,db,bounds)
 if _table(db,'max_schedule_segments'):
  old=db.execute("SELECT s.* FROM max_stations s JOIN max_schedule_segments m ON m.station_id=s.id WHERE m.period_key LIKE 'studio:%' AND s.end_ms>? AND s.lat BETWEEN ? AND ? AND s.lng BETWEEN ? AND ?",(now_ms(),*bounds)).fetchall()
  _retire_scheduled(db,old,now_ms())
 cfg=game.max_config;period=cfg['rotation_seconds']*1000;slot=now_ms()//period;start=slot*period;end=start+cfg['active_seconds']*1000
 if end<=now_ms():return
 with closing(sqlite3.connect(game.max_candidates.as_uri()+'?mode=ro',uri=True)) as source:
  source.row_factory=sqlite3.Row
  for row in source.execute('SELECT * FROM candidates WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?',bounds):
   rng=random.Random(hashlib.sha256(f"{cfg['seed']}:{row['id']}:{slot}".encode()).digest())
   if rng.random()>=cfg['active_probability']:continue
   identity=f"{cfg['seed']}:{row['id']}:{slot}"
   if _table(db,'max_schedule_segments'):
    previous=db.execute("SELECT m.station_id FROM max_stations s JOIN max_schedule_segments m ON m.station_id=s.id WHERE s.source_id=? AND m.period_key LIKE 'studio:%' AND m.retired_ms>0 ORDER BY m.rowid DESC LIMIT 1",(row['id'],)).fetchone()
    if previous:identity+=':return:'+previous[0]
   ident='power-'+hashlib.sha256(identity.encode()).hexdigest()[:24];pool=rng.choices(cfg['pools'],weights=[v.get('weight',1) for v in cfg['pools']])[0]
   db.execute('INSERT OR IGNORE INTO max_stations VALUES(?,?,?,?,?,?,?,?,?)',(ident,row['id'],slot,row['name'],row['lat'],row['lng'],start,end,json.dumps(pool,separators=(',',':'))))

def _retire_scheduled(db,rows,stamp):
 # Keep original interval metadata and canonical definitions before shortening
 # availability. Battle rooms, claims, victories and stationing FKs stay valid.
 for row in rows:
  definition=json.loads(row['definition']);key=definition.get('schedule',{}).get('key','legacy-utc:'+str(row['slot']))
  db.execute('INSERT OR IGNORE INTO max_schedule_segments VALUES(?,?,?,?,0)',(row['id'],key,row['start_ms'],row['end_ms']))
  db.execute('UPDATE max_stations SET end_ms=MIN(end_ms,?) WHERE id=?',(stamp,row['id']))
  db.execute('UPDATE max_schedule_segments SET retired_ms=? WHERE station_id=? AND retired_ms=0',(stamp,row['id']))

def _materialize_scheduled(game,db,bounds):
 from dittobuffer.gameplay.max_schedule import select_window
 stamp=now_ms();cfg=game.max_config;schedule=game.max_schedule
 window=getattr(game,'liveops_max_window',None) or select_window(schedule,stamp,base_pool=cfg['pools'])
 start,end=window.start_ms,window.end_ms
 if window.source=='regular':
  origin=schedule.anchor_ms+((stamp-schedule.anchor_ms)//schedule.rotation_ms)*schedule.rotation_ms
  end=min(end,origin+cfg['active_seconds']*1000)
 # Track natural expiration without altering the original station definition.
 db.execute('UPDATE max_schedule_segments SET retired_ms=(SELECT end_ms FROM max_stations s WHERE s.id=station_id) WHERE retired_ms=0 AND station_id IN (SELECT id FROM max_stations WHERE end_ms<=?)',(stamp,))
 if end<=stamp:
  # Hand-placed spots are never part of the rotating candidate schedule.
  rows=db.execute("SELECT * FROM max_stations WHERE end_ms>? AND source_id NOT LIKE 'manual:%' AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",(stamp,*bounds)).fetchall()
  _retire_scheduled(db,rows,stamp);return
 with closing(sqlite3.connect(game.max_candidates.as_uri()+'?mode=ro',uri=True)) as source:
  source.row_factory=sqlite3.Row
  for row in source.execute('SELECT * FROM candidates WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?',bounds):
   identity=f"{cfg['seed']}:{row['id']}:{window.key}"
   rng=random.Random(hashlib.sha256(identity.encode()).digest())
   active=rng.random()<cfg['active_probability'];ident='power-'+hashlib.sha256(identity.encode()).hexdigest()[:24]
   old=db.execute('SELECT * FROM max_stations WHERE source_id=? AND end_ms>? AND id<>?',(row['id'],stamp,ident if active else '')).fetchall()
   _retire_scheduled(db,old,stamp)
   if not active:continue
   pool=dict(rng.choices(window.pool,weights=[v.get('weight',1) for v in window.pool])[0])
   pool['difficulty_multipliers']=dict(window.difficulty_multipliers)
   pool['schedule']=dict(key=window.key,source=window.source,timezone=window.timezone,start_ms=start,end_ms=end)
   db.execute('INSERT OR IGNORE INTO max_stations VALUES(?,?,?,?,?,?,?,?,?)',(ident,row['id'],window.slot,row['name'],row['lat'],row['lng'],start,end,json.dumps(pool,separators=(',',':'))))
   db.execute('INSERT OR IGNORE INTO max_schedule_segments VALUES(?,?,?,?,0)',(ident,window.key,start,end))

def station(game,db,player,row):
 loot=db.execute('SELECT 1 FROM max_claims WHERE player_id=? AND station_id=? AND day=?',(player,row['id'],day(game))).fetchone()
 won=db.execute('SELECT 1 FROM max_victories WHERE station_id=? AND player_id=?',(row['id'],player)).fetchone()
 out=p.StationProto(id=row['id'],lat=row['lat'],lng=row['lng'],name=row['name'],start_time_ms=row['start_ms'],end_time_ms=row['end_ms'],cooldown_complete_ms=next_reset(game) if loot else 0,is_bread_battle_available=game.max_config['battle_enabled'],max_battle_remote_eligibility=1,player_edits_disabled=True)
 if won:out.player_battle_status=2
 definition=json.loads(row['definition'])
 if game.max_config['battle_enabled']:
  from dittobuffer.gameplay.species import make_pokemon
  species=definition['species'];mon=make_pokemon(p,game.forms[species,definition.get('form',game.species[species]['form'])],species,definition.get('level',10),[15]*3);attach(game,mon,definition.get('mode',1))
  from dittobuffer.gameplay.max_battle import source_capacity
  scheduled=definition.get('schedule',{}).get('source','').startswith('event:')
  out.battle_details.CopyFrom(p.BreadBattleDetailProto(bread_battle_seed=int(hashlib.sha256(row['id'].encode()).hexdigest()[:15],16),battle_spawn_ms=row['start_ms'],battle_window_start_ms=row['start_ms'],battle_window_end_ms=row['end_ms'],battle_pokemon=mon,reward_pokemon=mon,battle_level=definition.get('tier',1),min_recommended_player_count=1,max_recommended_player_count=source_capacity(game,mon.pokemon_display.bread_mode_enum),schedule_source=1 if scheduled else 3))
  out.battle_details.complete=bool(won)
 return out

def near(game,db,player,row):
 loc=db.execute('SELECT * FROM social_locations WHERE player_id=?',(player,)).fetchone() if game.config.get('social',{}).get('enabled') else db.execute('SELECT * FROM walking WHERE player_id=?',(player,)).fetchone()
 return bool(loc and loc['lat'] is not None and now_ms()-loc['arrival_ms']<=120000 and distance(loc['lat'],loc['lng'],row['lat'],row['lng'])<=game.config['interaction_range_meters'])

def lookup(game,db,ident):
 if not enabled(game):return None
 return db.execute('SELECT * FROM max_stations WHERE id=? AND start_ms<=? AND end_ms>?',(ident,now_ms(),now_ms())).fetchone()

def _table(db,name):
 return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())

def place(game,db,lat,lng,name,entry,duration_s):
 """Persist one hand-placed Power Spot at an explicit location."""
 try:lat=float(lat);lng=float(lng);duration=int(duration_s)
 except (TypeError,ValueError):raise ValueError('Invalid Power Spot coordinates or duration')
 if not math.isfinite(lat) or not math.isfinite(lng) or not -90<=lat<=90 or not -180<=lng<=180:raise ValueError('Invalid Power Spot coordinates')
 if not 60<=duration<=MANUAL_MAX_SECONDS:raise ValueError('Duration must be between 60 and 2592000 seconds')
 if not isinstance(entry,dict):raise ValueError('Invalid Max boss definition')
 try:species=int(entry.get('species',0));requested_mode=int(entry.get('mode',1))
 except (TypeError,ValueError):raise ValueError('Invalid Max boss definition')
 if species not in game.species:raise ValueError('Unsupported explicit Max species/form/mode')
 requested=entry.get('form')
 try:requested=None if requested is None else int(requested)
 except (TypeError,ValueError):raise ValueError('Unsupported explicit Max species/form/mode')
 pool=[v for v in released_bosses(game.max_config)
       if _boss_key(game,v)[0]==species and _boss_key(game,v)[2]==requested_mode
       and (requested is None or _boss_key(game,v)[1]==requested)]
 if not pool:raise ValueError('Add this species, form and Max mode to the released Max pool first')
 form=_boss_key(game,pool[0])[1]
 definition=boss_definition(game,{**entry,'form':form,'tier':entry.get('tier',pool[0].get('tier',1)),'level':entry.get('level',pool[0].get('level',10))})
 title=str(name or '').strip()[:60] or 'Power Spot'
 stamp=now_ms();ident='power-'+hashlib.sha256(f'manual:{lat:.7f}:{lng:.7f}:{stamp}:{secrets.token_hex(16)}'.encode()).hexdigest()[:24]
 source=MANUAL_PREFIX+hashlib.sha256(ident.encode()).hexdigest()[:12]
 db.execute('INSERT INTO max_stations VALUES(?,?,?,?,?,?,?,?,?)',(ident,source,0,title,lat,lng,stamp,stamp+duration*1000,json.dumps(definition,separators=(',',':'))))
 return dict(id=ident,source_id=source,name=title,lat=lat,lng=lng,start_ms=stamp,end_ms=stamp+duration*1000,definition=definition)

def _boss_key(game,value):
 species=int(value['species']);form=value.get('form',_default_form(game,species))
 try:form=None if form is None else int(form)
 except (TypeError,ValueError):form=None
 return species,form,int(value.get('mode',1))

def remove(db,ident):
 """Delete one stored spot; a spot still holding Pokémon must be ended instead."""
 row=db.execute('SELECT source_id FROM max_stations WHERE id=?',(ident,)).fetchone()
 if not row:raise ValueError('Unknown Power Spot')
 if _table(db,'max_stationed') and db.execute('SELECT 1 FROM max_stationed WHERE station_id=? AND returned_ms=0 LIMIT 1',(ident,)).fetchone():
  raise ValueError('Pokémon are still stationed here; end the spot instead')
 db.execute('DELETE FROM max_claims WHERE station_id=?',(ident,))
 if _table(db,'max_schedule_segments'):db.execute('DELETE FROM max_schedule_segments WHERE station_id=?',(ident,))
 db.execute('DELETE FROM max_stations WHERE id=?',(ident,))
 return {'deleted':ident,'manual':str(row['source_id']).startswith(MANUAL_PREFIX)}

def end_now(db,ident):
 if not db.execute('SELECT 1 FROM max_stations WHERE id=?',(ident,)).fetchone():raise ValueError('Unknown Power Spot')
 db.execute('UPDATE max_stations SET end_ms=MIN(end_ms,?) WHERE id=?',(now_ms(),ident))
 return {'id':ident,'ended':True}

def extend(db,ident,seconds):
 try:seconds=int(seconds)
 except (TypeError,ValueError):raise ValueError('Invalid extension')
 if not 60<=seconds<=MANUAL_MAX_SECONDS:raise ValueError('Extension must be between 60 and 2592000 seconds')
 if not db.execute('SELECT 1 FROM max_stations WHERE id=?',(ident,)).fetchone():raise ValueError('Unknown Power Spot')
 stamp=now_ms();limit=stamp+MANUAL_MAX_SECONDS
 db.execute('UPDATE max_stations SET end_ms=MIN(?,MAX(end_ms,?)+?) WHERE id=?',(limit,stamp,seconds*1000,ident))
 return {'id':ident,'end_ms':db.execute('SELECT end_ms FROM max_stations WHERE id=?',(ident,)).fetchone()[0]}

def loot(game,db,player,body):
 req=p.LootStationProto.FromString(body);fail=lambda n:p.LootStationOutProto(status=n)
 if not enabled(game):return fail(5)
 row=lookup(game,db,req.station_id)
 if not row:return fail(4)
 if not all(math.isfinite(v) for v in (req.station_lat_degrees,req.station_lng_degrees)) or not -90<=req.station_lat_degrees<=90 or not -180<=req.station_lng_degrees<=180 or distance(req.station_lat_degrees,req.station_lng_degrees,row['lat'],row['lng'])>game.config['interaction_range_meters'] or not near(game,db,player,row):return fail(6)
 from dittobuffer.gameplay.progression import bounds
 if bounds(game.config,game.player(db,player)['xp'])[0]<game.max_config['minimum_level']:return fail(5)
 today=day(game);old=db.execute('SELECT response FROM max_claims WHERE player_id=? AND station_id=? AND day=?',(player,row['id'],today)).fetchone()
 if old:return p.LootStationOutProto.FromString(old['response'])
 current=balance(db,player);collected=summary(game,db,player,b'').mp_collected_today;cfg=game.max_config
 if current>=cfg['capacity']:return fail(3)
 if collected>=cfg['daily_limit']:return fail(7)
 amount=cfg['station_particles']+cfg['first_visit_bonus']
 if cfg['cap_policy']=='clamp':amount=min(amount,cfg['capacity']-current,cfg['daily_limit']-collected)
 base=min(cfg['station_particles'],amount);bonus=amount-base;out=p.LootStationOutProto(status=1)
 out.loot.loot_item.add(item=1506,count=base)
 if bonus:out.bonus_loot.loot_item.add(item=1506,count=bonus)
 mutate(game,db,player,amount,'collect:'+row['id']+':'+today)
 db.execute('INSERT INTO max_daily VALUES(?,?,?) ON CONFLICT(player_id,day) DO UPDATE SET collected=collected+excluded.collected',(player,today,amount));db.execute('INSERT INTO max_claims VALUES(?,?,?,?)',(player,row['id'],today,out.SerializeToString()))
 from dittobuffer.gameplay.research import progress
 progress(game,db,player,'max_collect',amount=amount)
 progress(game,db,player,'max_loot')
 return out

def info(game,db,player,body):
 req=p.GetStationInfoProto.FromString(body);row=lookup(game,db,req.station_id)
 return p.GetStationInfoOutProto(result=2 if not enabled(game) else 3) if not row else p.GetStationInfoOutProto(result=1,station_proto=station(game,db,player,row))

def details(game,db,player,body):
 req=p.GetStationedPokemonDetailsProto.FromString(body);row=lookup(game,db,req.station_id)
 return p.GetStationedPokemonDetailsOutProto(result=2 if not row else 3 if not near(game,db,player,row) else 1)

def lobby_details(game,db,player,body):
 req=p.GetBreadLobbyDetailsProto.FromString(body);row=lookup(game,db,req.station_id)
 if not row or not game.max_config['battle_enabled']:return p.GetBreadLobbyDetailsOutProto(result=4)
 if not near(game,db,player,row):return p.GetBreadLobbyDetailsOutProto(result=2)
 value=station(game,db,player,row)
 if req.bread_battle_seed!=value.battle_details.bread_battle_seed:return p.GetBreadLobbyDetailsOutProto(result=4)
 from dittobuffer.gameplay.progression import bounds
 if bounds(game.config,game.player(db,player)['xp'])[0]<game.max_config['minimum_level']:return p.GetBreadLobbyDetailsOutProto(result=5)
 eligible=any(not mon.is_egg and not mon.deployed_fort_id and not mon.deployed_station_id and mon.stamina>0 and mon.pokemon_display.bread_mode_enum in (1,2) for mon in (p.PokemonProto.FromString(v[0]) for v in db.execute('SELECT data FROM pokemon WHERE player_id=?',(player,))))
 cost=next((v.bread_battle_catch_mp_cost for v in gm(game.max_reference,'MP_SHARED_SETTINGS').mp_settings.battle_mp_cost_per_tier if v.battle_level==value.battle_details.battle_level),0)
 out=p.GetBreadLobbyDetailsOutProto(result=1,bread_battle_detail=value.battle_details,player_can_join_bread_lobby=eligible and cost>0 and available(db,player)>=cost,server_timestamp_ms=now_ms())
 from dittobuffer.gameplay.max_battle import room_for,lobby,member
 room=room_for(db,row['id'],player)
 if value.battle_details.complete:
  out.player_can_join_bread_lobby=False
  m=member(db,room,player) if room else None
  encounter=db.execute('SELECT state FROM encounters WHERE player_id=? AND spawn_id=?',(player,m['spawn_id'])).fetchone() if m else None
  out.received_rewards=bool(m and m['reward'])
  out.is_fully_completed=bool(encounter and encounter['state']!='active')
  # Recovery signals describe pending work. Native 0.429.2 prioritizes these
  # over IsFullyCompleted and otherwise reopens even an already-caught result.
  if not out.is_fully_completed:
   out.rvn_battle_completed=True;out.rvn_battle_flushed=True;out.rvn_battle_is_victory=True
   if room:out.bread_lobby.CopyFrom(lobby(game,db,room,player));out.rvn_connection.CopyFrom(out.bread_lobby.rvn_connection)
 if room and room['end_ms']>now_ms() and room['status'] in (0,1):
  out.bread_lobby.CopyFrom(lobby(game,db,room,player));out.num_players_in_bread_lobby=len(out.bread_lobby.players);out.rvn_connection.CopyFrom(out.bread_lobby.rvn_connection)
  out.player_can_join_bread_lobby=True  # Resume the held party, not a new entry.
 return out

def start_walk(game,db,player,body):
 p.StartMpWalkQuestProto.FromString(body)
 if not enabled(game):return p.StartMpWalkQuestOutProto(status=3)
 if db.execute('SELECT 1 FROM max_walks WHERE player_id=? AND claimed_ms=0',(player,)).fetchone():return p.StartMpWalkQuestOutProto(status=2)
 import secrets
 from dittobuffer.gameplay.eggs import ensure
 ensure(game,db,player);total=db.execute('SELECT total_m FROM walking WHERE player_id=?',(player,)).fetchone()[0];settings=gm(game.max_reference,'MP_SHARED_SETTINGS').mp_settings
 db.execute('INSERT INTO max_walks(id,player_id,start_m,goal_m,reward,created_ms) VALUES(?,?,?,?,?,?)',('max-walk-'+secrets.token_hex(16),player,total,settings.num_meters_goal or 2000,settings.num_mp_from_walk_quest or 300,now_ms()));game.touch(db,player)
 return p.StartMpWalkQuestOutProto(status=1)

def walking_quest(game,db,player):
 if not enabled(game):return None
 row=db.execute('SELECT * FROM max_walks WHERE player_id=? AND claimed_ms=0',(player,)).fetchone()
 if not row:return None
 total=db.execute('SELECT total_m FROM walking WHERE player_id=?',(player,)).fetchone();progress=max(0,min(row['goal_m'],int((total[0] if total else 0)-row['start_m'])))
 out=p.QuestProto(quest_id=row['id'],template_id='LIBRE_MP_WALK',quest_context=13,quest_type=75,progress=progress,status=1,creation_timestamp_ms=row['created_ms'],last_update_timestamp_ms=now_ms())
 out.quest_walk.quest_start_km_walked=row['start_m']/1000;out.goal.target=row['goal_m'];out.quest_rewards.add(type=2).item.CopyFrom(p.ItemRewardProto(item=1506,amount=row['reward']))
 if progress>=row['goal_m']:out.completion_timestamp_ms=now_ms()
 return out

def claim_walk(game,db,player,req):
 if not enabled(game):return None
 row=db.execute('SELECT * FROM max_walks WHERE id=? AND player_id=?',(req.quest_id,player)).fetchone()
 if not row:return None
 if row['claimed_ms']:return p.CompleteQuestOutProto(status=4)
 if req.sub_quest_id or req.choice_id not in (-1,0) or req.claim_all_enum:return p.CompleteQuestOutProto(status=12)
 quest=walking_quest(game,db,player)
 if not quest or quest.progress<row['goal_m']:return p.CompleteQuestOutProto(status=3)
 cfg=game.max_config;current=balance(db,player);collected=summary(game,db,player,b'').mp_collected_today
 if current>=cfg['capacity'] or collected>=cfg['daily_limit']:return p.CompleteQuestOutProto(status=11)
 amount=row['reward'] if cfg['cap_policy']=='threshold' else min(row['reward'],cfg['capacity']-current,cfg['daily_limit']-collected)
 mutate(game,db,player,amount,'walk:'+row['id']);db.execute('INSERT INTO max_daily VALUES(?,?,?) ON CONFLICT(player_id,day) DO UPDATE SET collected=collected+excluded.collected',(player,day(game),amount));db.execute('UPDATE max_walks SET claimed_ms=? WHERE id=?',(now_ms(),row['id']))
 out=p.CompleteQuestOutProto(status=1);out.quest.quest.CopyFrom(quest);out.quest.quest.status=2;out.rewards.add(type=2).item.item=1506;out.rewards[0].item.amount=amount
 from dittobuffer.gameplay.research import progress
 progress(game,db,player,'max_collect',amount=amount)
 return out

def enhance(game,db,player,body):
 req=p.EnhanceBreadMoveProto.FromString(body);fail=lambda n:p.EnhanceBreadMoveOutProto(result=n)
 if not enabled(game):return fail(5)
 from dittobuffer.core.management import owned,candy,add_candy,update_owned
 mon=owned(db,player,req.pokemon_id)
 if mon is None or mon.is_egg or mon.pokemon_display.bread_mode_enum not in (1,2) or mon.deployed_fort_id or mon.deployed_station_id:return fail(5)
 from dittobuffer.gameplay.gyms import battle_busy
 if battle_busy(db,player,[mon.id]):return fail(5)
 if req.move_type not in (1,2,3) or req.target_move_level not in (1,2,3):return fail(4)
 slots={s.move_type:s for s in mon.bread_moves};slot=slots.get(req.move_type);current=slot.move_level if slot else 0
 # A target level is a durable operation identity, including retries after
 # losing the original RPC response. Never charge an already-applied upgrade.
 if req.target_move_level<=current:return p.EnhanceBreadMoveOutProto(result=1,bread_move_slot=slot) if req.target_move_level==current else fail(3)
 if req.target_move_level!=current+1:return fail(4)
 form=mon.pokemon_display.form or game.species[mon.pokemon_id]['form'];group=game.max_reference['species_groups'].get(f'{mon.pokemon_id}:{form}')
 name='BREAD_MOVE_LEVEL_SETTINGS_GROUP_'+str(group)
 if name not in game.max_reference['templates']:return fail(4)
 table=gm(game.max_reference,name).bread_move_level_settings;levels=getattr(table,{1:'a_settings',2:'b_settings',3:'c_settings'}[req.move_type])
 if len(levels)<req.target_move_level:return fail(4)
 cost=levels[req.target_move_level-1];family=game.forms[mon.pokemon_id,form]['family'];xl=db.execute('SELECT count FROM mega_catch_xl WHERE player_id=? AND family=?',(player,family)).fetchone()
 if available(db,player)<cost.mp_cost or candy(db,player,family)<cost.candy_cost or (xl[0] if xl else 0)<cost.xl_candy_cost or game.player(db,player)['stardust']<cost.stardust_cost:return fail(2)
 mutate(game,db,player,-cost.mp_cost,f'upgrade:{mon.id}:{req.move_type}:{req.target_move_level}')
 if cost.candy_cost:add_candy(db,player,family,-cost.candy_cost)
 if cost.xl_candy_cost:db.execute('UPDATE mega_catch_xl SET count=count-? WHERE player_id=? AND family=?',(cost.xl_candy_cost,player,family))
 db.execute('UPDATE players SET xp=xp+?,stardust=stardust-? WHERE id=?',(cost.xp_reward,cost.stardust_cost,player))
 if slot is None:slot=mon.bread_moves.add(move_type=req.move_type)
 slot.move_level=req.target_move_level;update_owned(game,db,player,mon)
 from dittobuffer.gameplay.research import progress
 progress(game,db,player,'max_unlock' if current==0 else 'max_upgrade')
 return p.EnhanceBreadMoveOutProto(result=1,bread_move_slot=slot)
