"""Authoritative 500 ms Trainer combat for real Rocket action intentions.

Uses the existing species, reviewed PvP moves, type matrix, ownership and health
transactions. Native display snapshots never supply damage or a winning result.
"""
import hashlib,json,math,secrets
from dittobuffer.gameplay.game import p,now_ms
from dittobuffer.gameplay.species import REFERENCE,make_pokemon,rules_for
from dittobuffer.core.management import owned,update_owned
from dittobuffer.gameplay.battle_master import effectiveness

MOVES={int(k):v for k,v in REFERENCE['pvp_moves'].items()}
TURN_MS=500

def mon(value):return p.PokemonProto.FromString(bytes.fromhex(value['pokemon']))
def entry(value):return dict(pokemon=value.SerializeToString().hex(),hp=value.stamina,energy=0,ready=0,attack_stage=0,defense_stage=0)
def active(state,side):return state['party' if side==0 else 'opponents'][state['active' if side==0 else 'enemy']]

def switch(state,side,index,turn,kind=8):
 old=active(state,side);previous=mon(old).id
 state.setdefault('last_active',{})[str(side)]=dict(old)
 state['active' if side==0 else 'enemy']=index
 value=active(state,side);value['attack_stage']=value['defense_stage']=0
 state['actions'][str(side)]=p.CombatActionProto(type=kind,action_start_turn=turn,duration_turns=1,attacker_index=side,target_index=1-side,active_pokemon_id=previous,target_pokemon_id=mon(value).id).SerializeToString().hex()
def settings(game):return p.GameMasterClientTemplateProto.FromString(bytes.fromhex(game.rocket_reference['templates']['COMBAT_SETTINGS'])).combat_settings

def damage(game,attacker,defender,move,score=1):
 a=mon(attacker);d=mon(defender);ar=rules_for(game,a);dr=rules_for(game,d);m=MOVES[move];s=settings(game)
 stages=[.5,4/7,2/3,.8,1,1.25,1.5,1.75,2]
 attack=(ar['attack']+a.individual_attack)*a.cp_multiplier*stages[attacker['attack_stage']+4]
 defense=(dr['defense']+d.individual_defense)*d.cp_multiplier*stages[defender['defense_stage']+4]
 if a.pokemon_display.alignment==1:attack*=s.shadow_pokemon_attack_bonus_multiplier
 if d.pokemon_display.alignment==1:defense*=s.shadow_pokemon_defense_bonus_multiplier
 stab=s.same_type_attack_bonus_multiplier if m['type'] in ar['types'] else 1
 bonus=s.fast_attack_bonus_multiplier if m['energy_delta']>0 else s.charge_attack_bonus_multiplier
 return max(1,math.floor(.5*m['power']*attack/max(1,defense)*stab*effectiveness(m['type'],dr['types'])*bonus*score)+1)

def hit(game,state,side,move,score=1,shield=False):
 attacker=active(state,side);defender=active(state,1-side)
 defender['hp']=max(0,defender['hp']-(1 if shield else damage(game,attacker,defender,move,score)))
 # Reviewed stat-change definitions, if present; unknown effects aren't invented.
 effects=MOVES[move].get('secondary_effects',{})
 chance=effects.get('buffActivationChance',0)
 if chance and int(hashlib.sha256(f"{state['id']}:{state['hits']}".encode()).hexdigest()[:8],16)/2**32<chance:
  for who,prefix in [(attacker,'attacker'),(defender,'target')]:
   for stat in ('attack','defense'):
    change=effects.get(prefix+stat.title()+'StatStageChange',0)
    who[stat+'_stage']=max(-4,min(4,who[stat+'_stage']+change))
 state['hits']+=1

def terminal(state):
 if all(v['hp']==0 for v in state['opponents']):state['status']='won';state['phase']=6
 elif all(v['hp']==0 for v in state['party']):state['status']='lost';state['phase']=6

def charge(game,state,side,slot,turn):
 value=active(state,side);pokemon=mon(value);move=pokemon.move2 if slot==2 else pokemon.move3
 value['energy']+=MOVES[move]['energy_delta'];state['phase']=4
 state['charge']=dict(side=side,move=move,shield=False,score=None,turn=turn,end_ms=now_ms()+int(settings(game).minigame_duration_seconds*1000))
 state['actions'][str(side)]=p.CombatActionProto(type=2 if slot==2 else 3,action_start_turn=turn,duration_turns=14,attacker_index=side,target_index=1-side,active_pokemon_id=pokemon.id,move=move).SerializeToString().hex()
 state['minigame_actions']={str(side):state['actions'][str(side)]}

def advance(game,state):
 stamp=now_ms();target=max(0,(min(stamp,state['end'])-state['start'])//TURN_MS)
 if state['status']!='active':return
 if stamp<state['start']:return
 if state['phase']==2:state['phase']=3
 if state.get('charge'):
  c=state['charge']
  # The native defensive finish arrives after the displayed countdown. Allow
  # its bounded submit window; do not discard it on the preceding poll.
  # NPC attacks may be announced while the native client is still playing
  # the preceding fly-out. Give its first defensive-start notification a
  # bounded arrival window, then time the choice from that actual UI start.
  deadline=c['end_ms']+(8000 if c['side']==1 and not c.get('client_start_ms') else 1000)
  if stamp<deadline and c['score'] is None:return
  shield=c['shield']
  if c['side']==0 and state['enemy_shields']>0:
   state['enemy_shields']-=1;shield=True
  before_hp=active(state,1-c['side'])['hp']
  hit(game,state,c['side'],c['move'],c['score'] if c['score'] is not None else .25,shield)
  state['charge_history']=(state.get('charge_history',[])+[dict(side=c['side'],move=c['move'],shield=shield,before_hp=before_hp,after_hp=active(state,1-c['side'])['hp'],player_shields=state['shields'],client_start_ms=c.get('client_start_ms'),resolved_ms=stamp)])[-32:]
  # Native IsFinishedWithSpecialAttack reads MinigameAction, not CombatState.
  # The server-controlled NPC must finish its half of the minigame as well;
  # leaving its start action here strands the native charge coroutine.
  for side,kind,score in [(c['side'],4,c['score'] if c['score'] is not None else .25),(1-c['side'],6,1 if shield else 0)]:
   state['minigame_actions'][str(side)]=p.CombatActionProto(type=kind,action_start_turn=target,attacker_index=side,target_index=1-side,active_pokemon_id=mon(active(state,side)).id,minigame_score=score).SerializeToString().hex()
  c['fly_out_turn']=target+3;state['last_charge']=c
  state['charge']=None;state['phase']=3
  active(state,0)['ready']=target+1;active(state,1)['ready']=target+4
  state['clock']=target
  terminal(state)
 while state['clock']<=target and state['status']=='active' and not state.get('charge'):
  turn=state['clock'];state['clock']+=1
  terminal(state)
  if state['status']!='active':break
  if active(state,1)['hp']==0:
   if not state.get('enemy_swap_at'):state['enemy_swap_at']=turn+4
   if turn<state['enemy_swap_at']:continue
   switch(state,1,next(i for i,v in enumerate(state['opponents']) if v['hp']>0),turn);state['enemy_swap_at']=0;active(state,1)['ready']=turn+4
  if active(state,0)['hp']==0:
   state['phase']=5
   if not state.get('change_until'):state['change_until']=stamp+12000
   if stamp<state['change_until']:continue
   switch(state,0,next(i for i,v in enumerate(state['party']) if v['hp']>0),turn);state['phase']=3;state['change_until']=0;active(state,0)['ready']=turn+1
  due=[e for e in state['pending'] if e['turn']<=turn];state['pending']=[e for e in state['pending'] if e['turn']>turn]
  for event in due:
   if mon(active(state,event['side'])).id==event['source'] and active(state,event['side'])['hp']>0 and mon(active(state,1-event['side'])).id==event['target']:
    hit(game,state,event['side'],event['move']);terminal(state)
   if state['status']!='active':break
  if active(state,0)['hp']==0 or active(state,1)['hp']==0:continue
  if state['status']!='active':break
  enemy=active(state,1);pokemon=mon(enemy)
  if enemy['hp']>0 and active(state,0)['hp']>0 and enemy['ready']<=turn and state['phase']==3:
   if enemy['energy']>=-MOVES[pokemon.move2]['energy_delta']:
    charge(game,state,1,2,turn);break
   move=pokemon.move1;duration=max(1,MOVES[move]['duration_turns']);enemy['ready']=turn+duration;enemy['energy']=min(100,enemy['energy']+MOVES[move]['energy_delta'])
   state['actions']['1']=p.CombatActionProto(type=1,action_start_turn=turn,duration_turns=duration,attacker_index=1,target_index=0,active_pokemon_id=pokemon.id,move=move).SerializeToString().hex()
   state['pending'].append(dict(side=1,source=pokemon.id,target=mon(active(state,0)).id,move=move,turn=turn+duration))
 if stamp>=state['end'] and state['status']=='active':state['status']='timeout';state['phase']=8

def checkpoint(game,db,b,state):
 for value in state['party']:
  pokemon=owned(db,b['player_id'],mon(value).id)
  if pokemon and pokemon.stamina>value['hp']:
   pokemon.stamina=value['hp'];update_owned(game,db,b['player_id'],pokemon)
 db.execute('UPDATE rocket_battles SET state=?,finalized=? WHERE id=?',(json.dumps(state,separators=(',',':')),int(state['status']!='active'),b['id']))

def refresh(game,db,b):
 state=json.loads(b['state'])
 if state['status']=='active' and now_ms()>=state['end']:
  # Expired sessions no longer lock their party. Never replay offline AI/HP
  # checkpoints over a later Revive, Potion or other legitimate inventory edit.
  state['status']='timeout';state['phase']=8
  db.execute('UPDATE rocket_battles SET state=?,finalized=1 WHERE id=?',(json.dumps(state,separators=(',',':')),b['id']))
 elif state['status']=='active':advance(game,state);checkpoint(game,db,b,state)
 return db.execute('SELECT * FROM rocket_battles WHERE id=?',(b['id'],)).fetchone()

def pokemon(value):
 source=mon(value);out=p.RocketCombatPokemonProto(pokemon_id=source.id,pokedex_id=source.pokemon_id,cp=source.cp,cp_multiplier=source.cp_multiplier,stamina=value['hp'],max_stamina=source.max_stamina,move1=source.move1,move2=source.move2,move3=source.move3,energy=value['energy'],pokemon_display=source.pokemon_display,individual_attack=source.individual_attack,individual_defense=source.individual_defense,individual_stamina=source.individual_stamina,attack_stat_stage=value['attack_stage'],defense_stat_stage=value['defense_stage'],nickname=source.nickname,pokeball=source.pokeball or 1,height_m=source.height_m,weight_kg=source.weight_kg)
 return out

def projection(game,db,b):
 state=json.loads(b['state']);stamp=now_ms();turn=max(0,(min(stamp,state['end'])-state['start'])//TURN_MS)
 out=p.CombatProto(combat_state=state['phase'],combat_id=b['id'],combat_start_ms=state['start'],combat_end_ms=state['end'],server_ms=stamp,current_turn=turn,turn_start_ms=state['start']+turn*TURN_MS,quick_swap_cooldown_duration_ms=int(settings(game).quick_swap_cooldown_duration_seconds*1000),combat_request_counter=state['counter'],change_pokemon_end_ms=state.get('change_until',0))
 from dittobuffer.gameplay.gyms import public
 out.player.public_profile.CopyFrom(public(game,db,b['player_id']))
 out.opponent.public_profile.CopyFrom(p.PlayerPublicProfileProto(name='Team GO Rocket',player_id='rocket-npc',level=game.rocket['npc_level'],team=4))
 out.opponent.public_profile.avatar.avatar=2
 for side,target,values,index in [(0,out.player,state['party'],state['active']),(1,out.opponent,state['opponents'],state['enemy'])]:
  target.active_pokemon.CopyFrom(pokemon(values[index]));target.last_active_pokemon.CopyFrom(pokemon(state.get('last_active',{}).get(str(side),values[index])))
  target.minigame_defense_chances_left=state['shields' if side==0 else 'enemy_shields'];target.last_updated_turn=turn;target.lockstep_ack=True;target.quick_swap_available_ms=state['swap_available'] if side==0 else 0
  for i,value in enumerate(values):
   if i!=index:getattr(target,'reserve_pokemon' if value['hp'] else 'fainted_pokemon').add().CopyFrom(pokemon(value))
  if str(side) in state['actions']:
   target.current_action.ParseFromString(bytes.fromhex(state['actions'][str(side)]));target.last_snapshot_action_type=target.current_action.type
  if str(side) in state.get('minigame_actions',{}):target.minigame_action.ParseFromString(bytes.fromhex(state['minigame_actions'][str(side)]))
 c=state.get('charge') or state.get('last_charge')
 if c:
  out.minigame_end_ms=c['end_ms'];out.minigame_submit_score_end_ms=c['end_ms']+1000;out.opponent_triggered=bool(c['side'])
  out.minigame_data.CopyFrom(p.MinigameProto(minigame_end_ms=c['end_ms'],minigame_submit_score_end_ms=c['end_ms']+1000,fly_in_completion_turn=c['turn']+3,fly_out_completion_turn=c.get('fly_out_turn',c['turn']+20)))
 return out

def open_invasion(game,db,player,body):
 from dittobuffer.gameplay import rocket_native as rocket
 req=p.OpenInvasionCombatSessionProto.FromString(body);incident=rocket.lookup(game,db,player,req.incident_lookup)
 fail=lambda n:p.OpenInvasionCombatSessionOutProto(status=n)
 if not incident:return fail(4)
 member=db.execute('SELECT * FROM rocket_players WHERE incident_id=? AND player_id=?',(incident['id'],player)).fetchone()
 if not member or req.step!=0 or member['completed_ms']:return fail(6)
 ids=list(req.attacking_pokemon_id)
 if len(ids)!=3 or len(set(ids))!=3:return fail(30)
 old=db.execute('SELECT * FROM rocket_battles WHERE id=? AND player_id=?',(member['battle_id'],player)).fetchone()
 if old:
  old=refresh(game,db,old);state=json.loads(old['state'])
  if state['status']=='active':return p.OpenInvasionCombatSessionOutProto(status=1,combat=projection(game,db,old)) if [mon(v).id for v in state['party']]==ids else fail(12)
 party=[owned(db,player,i) for i in ids]
 from dittobuffer.gameplay.gyms import battle_busy
 from dittobuffer.gameplay.trading import locked
 if any(not v or v.is_egg or v.deployed_fort_id or v.deployed_station_id or v.pokemon_display.current_temp_evolution or not v.stamina or v.is_fusion or v.is_component for v in party):return fail(30)
 if battle_busy(db,player,ids) or any(locked(game,db,i) for i in ids):return fail(30)
 if any(v.move1 not in MOVES or v.move2 not in MOVES for v in party):return fail(30)
 stamp=now_ms();start=stamp+3000;ident='rocket-battle-'+secrets.token_hex(12);definition=json.loads(incident['definition'])
 opponents=[]
 for i,species in enumerate(definition['species']):
  value=make_pokemon(p,game.species[species],species,definition['level'],[game.rocket['npc_iv']]*3);value.id=2**60+i;value.pokemon_display.alignment=1;value.move2=game.species[species]['moves'][1]
  if value.move1 not in MOVES or value.move2 not in MOVES:return fail(30)
  opponents.append(entry(value));opponents[-1]['ready']=8
 state=dict(id=ident,status='active',phase=2,start=start,end=start+int(settings(game).round_duration_seconds*1000),clock=0,active=0,enemy=0,party=[entry(v) for v in party],opponents=opponents,actions={},pending=[],shields=2,enemy_shields=definition.get('shields',0),swap_available=start,counter=0,hits=0,charge=None)
 db.execute('INSERT INTO rocket_battles VALUES(?,?,?,?,?,0)',(ident,incident['id'],player,json.dumps(state),state['end']))
 db.execute('UPDATE rocket_players SET battle_id=? WHERE incident_id=? AND player_id=?',(ident,incident['id'],player))
 b=db.execute('SELECT * FROM rocket_battles WHERE id=?',(ident,)).fetchone()
 return p.OpenInvasionCombatSessionOutProto(status=1,combat=projection(game,db,b))

def open_session(game,db,player,body):
 req=p.OpenCombatSessionProto.FromString(body);b=db.execute('SELECT * FROM rocket_battles WHERE id=? AND player_id=?',(req.combat_id,player)).fetchone() if getattr(game,'rocket_reference',None) else None
 if not b:return p.OpenCombatSessionOutProto(result=3)
 state=json.loads(b['state'])
 if list(req.attacking_pokemon_id)!=[mon(v).id for v in state['party']]:return p.OpenCombatSessionOutProto(result=3)
 b=refresh(game,db,b);return p.OpenCombatSessionOutProto(result=1,combat=projection(game,db,b))

def update(game,db,player,body):
 req=p.UpdateCombatProto.FromString(body);b=db.execute('SELECT * FROM rocket_battles WHERE id=? AND player_id=?',(req.combat_id,player)).fetchone() if getattr(game,'rocket_reference',None) else None
 if not b:return p.UpdateCombatOutProto(result=3)
 a=req.action;digest=hashlib.sha256(body).hexdigest()
 if a.type:
  old=db.execute('SELECT * FROM rocket_actions WHERE battle_id=? AND counter=?',(b['id'],req.combat_request_counter)).fetchone()
  if old:return p.UpdateCombatOutProto.FromString(old['response']) if old['request_hash']==digest else p.UpdateCombatOutProto(result=5)
 b=refresh(game,db,b);state=json.loads(b['state']);result=1;stamp=now_ms();turn=max(0,(stamp-state['start'])//TURN_MS);actor=active(state,0);source=mon(actor)
 if a.type:
  if state['status']!='active' or stamp<state['start'] or req.combat_request_counter<=state['counter'] or a.attacker_index not in (0,1) or a.active_pokemon_id not in (0,source.id) or abs(a.action_start_turn-turn)>8:result=5
  elif a.type in (1,2,3):
   move={1:source.move1,2:source.move2,3:source.move3}[a.type]
   if state['phase']!=3 or actor['ready']>turn or actor['hp']<=0 or move not in MOVES or a.move not in (0,move):result=5
   elif a.type!=1 and actor['energy']<-MOVES[move]['energy_delta']:result=10
   elif a.type==1:
    duration=max(1,MOVES[move]['duration_turns']);actor['ready']=turn+duration;actor['energy']=min(100,actor['energy']+MOVES[move]['energy_delta'])
    action=p.CombatActionProto(type=1,action_start_turn=turn,duration_turns=duration,active_pokemon_id=source.id,attacker_index=0,target_index=1,move=move)
    state['actions']['0']=action.SerializeToString().hex();state['pending'].append(dict(side=0,source=source.id,target=mon(active(state,1)).id,move=move,turn=turn+duration))
   else:charge(game,state,0,2 if a.type==2 else 3,turn)
  elif a.type in (4,5,6):
   c=state.get('charge')
   if not c or not math.isfinite(a.minigame_score) or not 0<=a.minigame_score<=1:result=13
   elif a.type==4 and c['side']==0:c['score']=a.minigame_score
   elif a.type in (5,6) and c['side']==1:
    # Type 5 announces the defensive minigame. The actual native choice is
    # carried by type 6's Score, not by the automatic start notification.
    if a.type==5 and not c.get('client_start_ms'):
     c['client_start_ms']=stamp;c['end_ms']=stamp+int(settings(game).minigame_duration_seconds*1000)
    if a.type==6:
     if a.minigame_score>0 and not c['shield'] and state['shields']>0:state['shields']-=1;c['shield']=True
     c['score']=1
   else:result=13
   if result==1:
    state.setdefault('minigame_actions',{})['0']=a.SerializeToString().hex()
  elif a.type==7:
   if actor['hp']>0:result=5
  elif a.type in (8,9):
   choice=next((i for i,v in enumerate(state['party']) if mon(v).id==a.target_pokemon_id and v['hp']>0),None)
   if choice is None or choice==state['active'] or (a.type==8 and state['phase']!=5) or (a.type==9 and stamp<state['swap_available']) or state['phase']==4:result=14
   else:
    switch(state,0,choice,turn,a.type);state['swap_available']=stamp+int(settings(game).quick_swap_cooldown_duration_seconds*1000);state['phase']=3;state['change_until']=0;active(state,0)['ready']=turn+1;active(state,1)['ready']=turn+4
  else:result=5
  if result==1:state['counter']=req.combat_request_counter;advance(game,state);checkpoint(game,db,b,state)
 b=db.execute('SELECT * FROM rocket_battles WHERE id=?',(b['id'],)).fetchone();out=p.UpdateCombatOutProto(result=result,combat=projection(game,db,b))
 # Native communication numbers every poll, including polls with no action.
 # Echo this request's sequence so valid new snapshots are not discarded as
 # an old action acknowledgement. Keep action receipts independently durable.
 out.combat.combat_request_counter=req.combat_request_counter
 if a.type:db.execute('INSERT INTO rocket_actions VALUES(?,?,?,?)',(b['id'],req.combat_request_counter,digest,out.SerializeToString()))
 return out

def quit(game,db,player,body):
 req=p.QuitCombatProto.FromString(body);b=db.execute('SELECT * FROM rocket_battles WHERE id=? AND player_id=?',(req.combat_id,player)).fetchone() if getattr(game,'rocket_reference',None) else None
 if not b:return p.QuitCombatOutProto(result=2)
 b=refresh(game,db,b);state=json.loads(b['state'])
 if state['status']=='active':state['status']='quit';state['phase']=7;checkpoint(game,db,b,state)
 return p.QuitCombatOutProto(result=1,combat=projection(game,db,db.execute('SELECT * FROM rocket_battles WHERE id=?',(b['id'],)).fetchone()))
