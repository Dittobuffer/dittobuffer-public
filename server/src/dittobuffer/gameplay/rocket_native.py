"""Persistent Rocket incidents, overlaid on the existing immutable world.

All mutations execute inside the authenticated Game.rpc SQLite transaction.
Client battle completion flags are never evidence of a victory.
"""
import hashlib,json,math,random,secrets
from contextlib import closing
from dittobuffer.gameplay.game import ROOT,p,now_ms,distance,RpcError

SCHEMA='''
CREATE TABLE IF NOT EXISTS rocket_incidents(id TEXT PRIMARY KEY,fort_id TEXT NOT NULL REFERENCES forts(id),start_ms INTEGER NOT NULL,end_ms INTEGER NOT NULL,definition TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rocket_players(incident_id TEXT NOT NULL REFERENCES rocket_incidents(id),player_id TEXT NOT NULL REFERENCES players(id),step INTEGER NOT NULL DEFAULT 0,battle_id TEXT,completed_ms INTEGER NOT NULL DEFAULT 0,spawn_id INTEGER UNIQUE REFERENCES spawns(id),balls INTEGER NOT NULL DEFAULT 0 CHECK(balls>=0),receipt BLOB,PRIMARY KEY(incident_id,player_id));
CREATE TABLE IF NOT EXISTS rocket_battles(id TEXT PRIMARY KEY,incident_id TEXT NOT NULL REFERENCES rocket_incidents(id),player_id TEXT NOT NULL REFERENCES players(id),state TEXT NOT NULL,end_ms INTEGER NOT NULL,finalized INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS rocket_actions(battle_id TEXT NOT NULL REFERENCES rocket_battles(id),counter INTEGER NOT NULL,request_hash TEXT NOT NULL,response BLOB NOT NULL,PRIMARY KEY(battle_id,counter));
CREATE TABLE IF NOT EXISTS rocket_purifications(pokemon_id INTEGER PRIMARY KEY,player_id TEXT NOT NULL REFERENCES players(id),at_ms INTEGER NOT NULL,dust INTEGER NOT NULL,candy INTEGER NOT NULL,before BLOB NOT NULL,after BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS rocket_radars(player_id TEXT PRIMARY KEY REFERENCES players(id),item INTEGER NOT NULL DEFAULT 0);
'''
DEFAULTS=dict(enabled=False,reference='assets/pokemon/local/rocket/rocket-reference.json',minimum_level=8,period_seconds=3600,duration_seconds=1800,probability=.02,seed='libre-rocket-1',npc_level=18,npc_iv=10,reward_level=8,encounter_seconds=1800,premier_balls=8,stardust=500,xp=500,force_fort_id='',force_theme='grass',frustration_event_start_ms=0,frustration_event_end_ms=0)

def enabled(game):return bool(game.config.get('rocket',{}).get('enabled',False) and game.config.get('rocket',{}).get('server_combat',False))

def reference(config):
 cfg=DEFAULTS|config.get('rocket',{})
 if not cfg['enabled'] or not cfg.get('server_combat',False):return {}
 path=(ROOT/cfg['reference']).resolve()
 if not path.is_relative_to(ROOT):raise ValueError('Project-local authorized Rocket reference required')
 data=json.loads(path.read_text())
 for name in ('COMBAT_SETTINGS','COMBAT_STAT_STAGE_SETTINGS','CHARACTER_GRASS_GRUNT_MALE'):
  value=p.GameMasterClientTemplateProto.FromString(bytes.fromhex(data['templates'][name]))
  if value.template_id!=name:raise ValueError('Rocket reference identity mismatch')
 return data

def install(game):
 game.rocket_native_enabled=enabled(game)
 game.rocket=DEFAULTS|game.config.get('rocket',{})
 with closing(game.db.connect()) as db:game.has_rocket_state=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rocket_players'").fetchone())
 if not enabled(game):return
 cfg=game.rocket;game.rocket_reference=reference(game.config)
 for name in ('frustration_event_start_ms','frustration_event_end_ms'):
  if type(cfg[name]) is not int or cfg[name]<0:raise ValueError('Invalid bounded Frustration event')
 if cfg['frustration_event_end_ms'] and not 0<cfg['frustration_event_end_ms']-cfg['frustration_event_start_ms']<=14*86400000:raise ValueError('Frustration event must be a bounded reviewed window')
 for key,low,high in [('minimum_level',1,50),('period_seconds',60,86400),('duration_seconds',30,86400),('npc_level',1,40),('npc_iv',0,15),('reward_level',1,40),('encounter_seconds',30,86400),('premier_balls',1,50),('stardust',0,100000),('xp',0,100000)]:
  if type(cfg[key]) is not int or not low<=cfg[key]<=high:raise ValueError('Invalid Rocket rule '+key)
 if cfg['duration_seconds']>cfg['period_seconds'] or type(cfg['probability']) not in (float,int) or not math.isfinite(cfg['probability']) or not 0<=cfg['probability']<=1:raise ValueError('Invalid Rocket schedule')
 if (cfg['force_fort_id'] or cfg.get('force_fort_ids')) and (not game.config.get('profile','').startswith('m17-') or not game.db.path.resolve().is_relative_to(ROOT/'builds')):raise ValueError('Forced Rocket incidents require isolated M17 builds')
 game.rocket_teams=json.loads((ROOT/'src/dittobuffer/gameplay/rocket-teams.json').read_text())
 for theme,value in game.rocket_teams.items():
  if len(value['species'])!=3 or value['character'] not in p.EnumWrapper.InvasionCharacter.values():raise ValueError('Invalid Rocket lineup')
  for species in value['species']:
   if species not in game.species:raise ValueError('Unknown Rocket species')
   if str(species)+':'+str(game.species[species]['form']) not in game.rocket_reference['shadow']:raise ValueError('Unreviewed Shadow form '+str(species))
 if cfg['force_theme'] not in game.rocket_teams:raise ValueError('Unknown Rocket theme')
 with closing(game.db.connect()) as db:db.executescript(SCHEMA)
 game.has_rocket_state=True
 from dittobuffer.gameplay.rocket_progression import install as install_progression
 install_progression(game)

def templates(config):
 if not config.get('rocket',{}).get('enabled') or not config.get('rocket',{}).get('server_combat'):return []
 values=[p.GameMasterClientTemplateProto.FromString(bytes.fromhex(raw)) for raw in reference(config)['templates'].values()]
 from dittobuffer.gameplay.rocket_shadow import client_cost_multiplier
 for value in values:
  if value.template_id=='SHARED_MOVE_SETTINGS':
   for name in ('shadow_third_move_unlock_stardust_multiplier','shadow_third_move_unlock_candy_multiplier','purified_third_move_unlock_stardust_multiplier','purified_third_move_unlock_candy_multiplier'):
    setattr(value.shared_move_settings,name,client_cost_multiplier(getattr(value.shared_move_settings,name)))
 return values

def schedule(game,db,fort):
 if not enabled(game) or fort['kind']!=1:return None
 cfg=game.rocket;stamp=now_ms();slot=stamp//(cfg['period_seconds']*1000)
 # Configuration/rotation changes must not create a second live invasion at
 # this stop. The authenticated RPC transaction serializes this lookup/insert.
 # Hideouts are ticket-gated and balloons belong to one player. Neither may
 # become a public stop invasion just because it shares the same anchor.
 previous=db.execute("SELECT * FROM rocket_incidents WHERE id LIKE 'rocket-%' AND fort_id=? AND start_ms<=? AND end_ms>? ORDER BY start_ms,id LIMIT 1",(fort['id'],stamp,stamp)).fetchone()
 if previous:return previous
 rng=random.Random(hashlib.sha256(f"{cfg['seed']}:{fort['id']}:{slot}".encode()).digest())
 forced=fort['id']==cfg['force_fort_id'] or fort['id'] in cfg.get('force_fort_ids',[])
 if not forced and rng.random()>=cfg['probability']:return None
 start=slot*cfg['period_seconds']*1000;end=start+cfg['duration_seconds']*1000
 if stamp>=end:return None
 theme=cfg['force_theme'] if forced else rng.choice(sorted(game.rocket_teams));definition=dict(game.rocket_teams[theme],theme=theme,kind='grunt',level=cfg['npc_level'])
 ident='rocket-'+hashlib.sha256(f"{cfg['seed']}:{fort['id']}:{slot}".encode()).hexdigest()[:24]
 db.execute('INSERT OR IGNORE INTO rocket_incidents VALUES(?,?,?,?,?)',(ident,fort['id'],start,end,json.dumps(definition)))
 return db.execute('SELECT * FROM rocket_incidents WHERE id=?',(ident,)).fetchone()

def decorate(game,db,player,fort,value,incident=None):
 if enabled(game):
  from dittobuffer.gameplay.rocket_progression import hideout
  incident=incident or hideout(game,db,player,fort)
 incident=incident or schedule(game,db,fort)
 if not incident:return
 row=db.execute('SELECT completed_ms FROM rocket_players WHERE incident_id=? AND player_id=?',(incident['id'],player)).fetchone()
 done=bool(row and row['completed_ms']);definition=json.loads(incident['definition'])
 display=p.PokestopIncidentDisplayProto(incident_id=incident['id'],incident_start_ms=incident['start_ms'],incident_expiration_ms=incident['end_ms'],incident_display_type=definition.get('display',1),incident_completed=done)
 display.character_display.CopyFrom(p.CharacterDisplayProto(style=2 if done else 1,character=definition['character']))
 if done:display.invasion_finished.style=2
 value.pokestop_display.CopyFrom(display);value.pokestop_displays.add().CopyFrom(display)
 value.last_modified_ms=incident['start_ms']

def lookup(game,db,player,req,near=True,completed=False):
 if not enabled(game):return None
 row=db.execute('SELECT i.*,f.lat,f.lng,f.name FROM rocket_incidents i JOIN forts f ON f.id=i.fort_id WHERE i.id=?',(req.incident_id,)).fetchone()
 if not row:return None
 if row['end_ms']<=now_ms():
  # A won encounter has its own lifetime. Stop/balloon expiry must not revoke
  # an already-earned rescue or its immutable reward receipt.
  member=db.execute('SELECT completed_ms,battle_id FROM rocket_players WHERE incident_id=? AND player_id=?',(row['id'],player)).fetchone()
  if not completed or not member:return None
  if not member['completed_ms']:
   # An already opened battle may outlast a stop/balloon's map lifetime.
   # Permit its progress/final result through the fixed battle deadline plus
   # a short delivery grace, without permitting another expired battle start.
   battle=db.execute('SELECT state,end_ms FROM rocket_battles WHERE id=? AND player_id=? AND incident_id=?',(member['battle_id'],player,row['id'])).fetchone()
   if not battle or now_ms()>battle['end_ms']+60000 or json.loads(battle['state'])['start']-3000>=row['end_ms']:return None
 from dittobuffer.gameplay.rocket_progression import allowed
 if not allowed(db,player,row):return None
 if req.context==1:
  if not db.execute('SELECT 1 FROM rocket_native_balloons WHERE player_id=? AND incident_id=?',(player,row['id'])).fetchone():return None
  return row
 if req.context!=0 or req.fort_id!=row['fort_id'] or json.loads(row['definition']).get('balloon'):return None
 if near:
  # Bind to the authenticated server-observed fix, not arbitrary request coords.
  loc=db.execute('SELECT * FROM social_locations WHERE player_id=?',(player,)).fetchone() if game.config.get('social',{}).get('enabled') else db.execute('SELECT * FROM walking WHERE player_id=?',(player,)).fetchone()
  if not loc or loc['lat'] is None or now_ms()-loc['arrival_ms']>120000 or distance(loc['lat'],loc['lng'],row['lat'],row['lng'])>game.config['interaction_range_meters']:return None
 return row

def start(game,db,player,body):
 req=p.StartIncidentProto.FromString(body);row=lookup(game,db,player,req.incident_lookup)
 if not row:return p.StartIncidentOutProto(status=4)
 from dittobuffer.gameplay.progression import bounds
 if bounds(game.config,game.player(db,player)['xp'])[0]<game.rocket['minimum_level']:return p.StartIncidentOutProto(status=5)
 db.execute('INSERT OR IGNORE INTO rocket_players(incident_id,player_id) VALUES(?,?)',(row['id'],player))
 member=db.execute('SELECT * FROM rocket_players WHERE incident_id=? AND player_id=?',(row['id'],player)).fetchone()
 definition=json.loads(row['definition']);out=p.StartIncidentOutProto(status=1)
 image=game.world.details(row['fort_id'])
 out.incident.CopyFrom(p.ClientIncidentProto(incident_id=row['id'],fort_id=row['fort_id'],fort_name=row['name'],pokestop_image_uri=image['url'] if image else '',current_step=member['step'],context=req.incident_lookup.context))
 out.incident.step.add().invasion_battle.character=definition['character']
 out.incident.step.add().invasion_encounter.SetInParent()
 out.incident.completion_display.CopyFrom(p.PokestopIncidentDisplayProto(incident_id=row['id'],incident_completed=True,incident_expiration_ms=row['end_ms']))
 out.incident.completion_display.invasion_finished.style=2
 return out

def reward(game,db,player,incident,battle):
 member=db.execute('SELECT * FROM rocket_players WHERE incident_id=? AND player_id=?',(incident['id'],player)).fetchone()
 if member['receipt']:return p.UpdateInvasionBattleOutProto.FromString(member['receipt'])
 state=json.loads(battle['state'])
 if state['status']!='won':return p.UpdateInvasionBattleOutProto(status=12)
 cfg=game.rocket;definition=json.loads(incident['definition']);out=p.UpdateInvasionBattleOutProto(status=1)
 ticket=definition.get('ticket',0)
 if ticket and db.execute('UPDATE items SET count=count-1 WHERE player_id=? AND item=? AND count>0',(player,ticket)).rowcount!=1:return p.UpdateInvasionBattleOutProto(status=9)
 xp=definition.get('xp',cfg['xp']);dust=definition.get('stardust',cfg['stardust'])
 out.rewards.loot_item.add(stardust=True,count=dust);out.rewards.loot_item.add(experience=True,count=xp)
 db.execute('UPDATE players SET xp=xp+?,stardust=stardust+? WHERE id=?',(xp,dust,player))
 from dittobuffer.gameplay.items import quantity
 total=db.execute('SELECT COALESCE(SUM(count),0) FROM items WHERE player_id=?',(player,)).fetchone()[0]
 if definition['kind']=='grunt' and quantity(db,player,1502)==0 and total<game.capacity(db,player,'item_capacity'):
  db.execute('INSERT INTO items VALUES(?,1501,1) ON CONFLICT(player_id,item) DO UPDATE SET count=count+1',(player,));out.rewards.loot_item.add(item=1501,count=1)
  if quantity(db,player,1501)>=6:
   db.execute('UPDATE items SET count=count-6 WHERE player_id=? AND item=1501',(player,));db.execute('INSERT INTO items VALUES(?,1502,1) ON CONFLICT(player_id,item) DO UPDATE SET count=count+1',(player,));out.map_fragment_upgraded=True
 db.execute('UPDATE rocket_players SET completed_ms=?,step=1,receipt=? WHERE incident_id=? AND player_id=?',(now_ms(),out.SerializeToString(),incident['id'],player))
 from dittobuffer.gameplay.research import progress
 from dittobuffer.gameplay.trainer import event,increment
 kind='rocket_'+json.loads(incident['definition'])['kind']
 if event(game,db,player,'rocket-win:'+incident['id'],kind,dict(battle=battle['id'],xp=xp,dust=dust)):
  increment(db,player,kind);progress(game,db,player,kind)
  if out.map_fragment_upgraded:
   increment(db,player,'radar_assemble');progress(game,db,player,'radar_assemble')
 game.touch(db,player)
 return out

def battle_update(game,db,player,body):
 req=p.UpdateInvasionBattleProto.FromString(body);row=lookup(game,db,player,req.incident_lookup,completed=True)
 if not row:return p.UpdateInvasionBattleOutProto(status=4)
 member=db.execute('SELECT * FROM rocket_players WHERE incident_id=? AND player_id=?',(row['id'],player)).fetchone()
 if not member:return p.UpdateInvasionBattleOutProto(status=6)
 b=db.execute('SELECT * FROM rocket_battles WHERE id=? AND player_id=?',(member['battle_id'],player)).fetchone()
 if not b:return p.UpdateInvasionBattleOutProto(status=12)
 from dittobuffer.gameplay.rocket_battle import refresh
 b=refresh(game,db,b)
 if req.update_type==1:return reward(game,db,player,row,b)
 if req.update_type==2:
  return p.UpdateInvasionBattleOutProto(status=1 if json.loads(b['state'])['status'] in ('lost','quit','timeout') else 12)
 if req.complete_battle:return p.UpdateInvasionBattleOutProto(status=12)
 return p.UpdateInvasionBattleOutProto(status=1)

def encounter(game,db,player,body):
 req=p.InvasionEncounterProto.FromString(body);row=lookup(game,db,player,req.incident_lookup,completed=True)
 if not row:return p.InvasionEncounterOutProto(status=4)
 member=db.execute('SELECT * FROM rocket_players WHERE incident_id=? AND player_id=?',(row['id'],player)).fetchone()
 if not member or not member['completed_ms']:return p.InvasionEncounterOutProto(status=6)
 if req.step!=1:return p.InvasionEncounterOutProto(status=6)
 from dittobuffer.gameplay.eggs import count
 if db.execute('SELECT COUNT(*) FROM pokemon WHERE player_id=?',(player,)).fetchone()[0]+count(db,player)>=game.capacity(db,player,'pokemon_capacity'):return p.InvasionEncounterOutProto(status=10)
 if not member['spawn_id']:
  from dittobuffer.gameplay.species import make_pokemon
  definition=json.loads(row['definition']);species=definition.get('reward_species',definition['species'][0]);rules=game.species[species];mon=make_pokemon(p,rules,species,game.rocket['reward_level'],[secrets.randbelow(16) for _ in range(3)])
  mon.pokemon_display.alignment=1;mon.move2=322
  spawn=secrets.randbits(61) or 1;point='rocket-reward:'+secrets.token_hex(12);end=now_ms()+game.rocket['encounter_seconds']*1000
  db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',(spawn,point,species,row['lat'],row['lng'],end))
  db.execute('INSERT INTO spawn_attributes VALUES(?,?,?)',(spawn,mon.SerializeToString(),game.rocket['reward_level']))
  db.execute('INSERT INTO encounters(player_id,spawn_id) VALUES(?,?)',(player,spawn))
  db.execute('UPDATE rocket_players SET spawn_id=?,balls=? WHERE incident_id=? AND player_id=?',(spawn,game.rocket['premier_balls'],row['id'],player))
  member=db.execute('SELECT * FROM rocket_players WHERE incident_id=? AND player_id=?',(row['id'],player)).fetchone()
 spawn=db.execute('SELECT s.*,a.data pokemon_data FROM spawns s JOIN spawn_attributes a ON a.spawn_id=s.id WHERE s.id=?',(member['spawn_id'],)).fetchone();state=db.execute('SELECT state FROM encounters WHERE player_id=? AND spawn_id=?',(player,spawn['id'])).fetchone()[0]
 if spawn['expires_ms']<=now_ms() or state!='active' or not member['balls']:return p.InvasionEncounterOutProto(status=8)
 from dittobuffer.gameplay.items import probabilities,active_berry
 return p.InvasionEncounterOutProto(status=1,encounter_pokemon=game.pokemon(spawn,0),encounter_id=spawn['id'],spawn_point_guid=spawn['spawnpoint'],throws_remaining=member['balls'],invasion_ball=5,capture_probability=probabilities(game,active_berry(db,player,spawn['id'])))
