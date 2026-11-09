"""Shadow/Purified ownership and prices; use the existing Pokémon inventory."""
import math,struct
from dittobuffer.gameplay.game import p,now_ms
from dittobuffer.core.management import owned,level_of,candy,add_candy,save
from dittobuffer.gameplay.species import rules_for,apply_stats

def rule(game,mon):
 if not getattr(game,'rocket_reference',None):return None
 entry=game.rocket_reference['shadow'].get(str(mon.pokemon_id)+':'+str(mon.pokemon_display.form))
 # Admin/platform shadow table: enable or disable one species and override
 # its purification prices without editing the shipped reference data.
 try:
  from dittobuffer.gameplay import liveops
  override=liveops.shadow_rule(game,mon.pokemon_id,mon.pokemon_display.form)
 except Exception:
  override=None
 if override:
  if not override.get('enabled',True):return None
  if entry:return {**entry,'dust':int(override.get('dust',entry['dust'])),'candy':int(override.get('candy',entry['candy']))}
 return entry

def client_cost_multiplier(value):
 # The native ceil calculation promotes float32 to double. An encoding just
 # above 1.2 displayed 121 for the local 100-dust price. Bias only that encoding
 # down by one float32 ULP; the authoritative decimal rate stays unchanged.
 packed=struct.unpack('<f',struct.pack('<f',value))[0]
 if packed>round(value,6):
  bits=struct.unpack('<I',struct.pack('<f',packed))[0]
  return struct.unpack('<f',struct.pack('<I',bits-1))[0]
 return packed

def costs(game,mon,dust,candies,third=False):
 if mon.pokemon_display.alignment not in (1,2) or not getattr(game,'rocket_reference',None):return dust,candies
 prefix='shadow' if mon.pokemon_display.alignment==1 else 'purified';r=game.rocket_reference['upgrade']
 if third:
  value=p.GameMasterClientTemplateProto.FromString(bytes.fromhex(game.rocket_reference['templates']['SHARED_MOVE_SETTINGS'])).shared_move_settings
  r={prefix+'_stardust_multiplier':getattr(value,prefix+'_third_move_unlock_stardust_multiplier'),prefix+'_candy_multiplier':getattr(value,prefix+'_third_move_unlock_candy_multiplier')}
 # Avoid ceil(100*float32(1.2)) becoming 121 through serialization noise.
 return math.ceil(dust*round(r[prefix+'_stardust_multiplier'],6)),math.ceil(candies*round(r[prefix+'_candy_multiplier'],6))

def purify(game,db,player,body):
 req=p.PurifyPokemonProto.FromString(body);mon=owned(db,player,req.pokemon_id)
 if mon is None:return p.PurifyPokemonOutProto(status=5)
 if not getattr(game,'rocket_reference',None):return p.PurifyPokemonOutProto(status=6)
 if mon.pokemon_display.alignment==2 and db.execute('SELECT 1 FROM rocket_purifications WHERE player_id=? AND pokemon_id=?',(player,mon.id)).fetchone():return p.PurifyPokemonOutProto(status=1,purified_pokemon=mon)
 if mon.pokemon_display.alignment!=1:return p.PurifyPokemonOutProto(status=6)
 from dittobuffer.gameplay.gyms import battle_busy
 from dittobuffer.gameplay.trading import locked
 from dittobuffer.gameplay.mega import active
 if mon.is_egg or mon.deployed_fort_id or mon.deployed_station_id or battle_busy(db,player,[mon.id]) or locked(game,db,mon.id) or active(db,mon.id):return p.PurifyPokemonOutProto(status=4)
 r=rule(game,mon)
 if not r:return p.PurifyPokemonOutProto(status=6)
 spec=rules_for(game,mon);family=spec['family']
 if game.player(db,player)['stardust']<r['dust'] or candy(db,player,family)<r['candy']:return p.PurifyPokemonOutProto(status=3)
 before=mon.SerializeToString();level=max(25,level_of(db,mon))
 for field in ('individual_attack','individual_defense','individual_stamina'):setattr(mon,field,min(15,getattr(mon,field)+2))
 mon.pokemon_display.alignment=2;mon.move2=r['return_move'];mon.move2_is_purified_exclusive=True
 apply_stats(mon,spec,level,heal=True)
 add_candy(db,player,family,-r['candy']);db.execute('UPDATE players SET stardust=stardust-? WHERE id=?',(r['dust'],player))
 save(game,db,player,mon,level)
 db.execute('INSERT INTO rocket_purifications VALUES(?,?,?,?,?,?,?)',(mon.id,player,now_ms(),r['dust'],r['candy'],before,mon.SerializeToString()))
 from dittobuffer.gameplay.trainer import event,increment
 from dittobuffer.gameplay.research import progress
 if event(game,db,player,'purify:'+str(mon.id),'purify',dict(pokemon=mon.SerializeToString().hex(),dust=r['dust'],candy=r['candy'])):
  increment(db,player,'purify');progress(game,db,player,'purify')
 # Purification is a transformation, not another catch. The durable ledger
 # supplies Purified Dex categories without inflating capture counters.
 return p.PurifyPokemonOutProto(status=1,purified_pokemon=mon)


def augment_template(raw,definition):
 """Replace only ShadowAttributes wire data with the pinned native rule."""
 if definition is None:return raw
 from protocol.opaque import fields,byte_field
 outer=list(fields(raw));data=next((v for n,k,_,v in outer if n==2 and k==2),None)
 if data is None:return raw
 shadow=p.ShadowAttributesProto(purification_stardust_needed=definition['dust'],purification_candy_needed=definition['candy'],purified_charge_move=definition['return_move'],shadow_charge_move=definition['frustration'])
 data=b''.join(wire for n,k,wire,v in fields(data) if n!=45)+byte_field(45,shadow.SerializeToString())
 return b''.join(byte_field(2,data) if n==2 and k==2 else wire for n,k,wire,v in outer)
