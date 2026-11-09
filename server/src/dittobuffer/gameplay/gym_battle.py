"""Timed, persisted PvE combat. Client actions are intentions, never damage claims."""
import hashlib
import json
import math
import secrets
import logging
from dittobuffer.gameplay.game import p,now_ms
from dittobuffer.gameplay import gyms
from dittobuffer.gameplay.battle_master import MOVES,DEFINITIONS,effectiveness,settings
from dittobuffer.core.management import owned,update_owned
from dittobuffer.gameplay.progression import bounds

def mon(entry):return p.PokemonProto.FromString(bytes.fromhex(entry['pokemon']))

def entry(pokemon,hp=None):
    return {'pokemon':pokemon.SerializeToString().hex(),'hp':pokemon.stamina if hp is None else hp,
            'energy':0,'ready':0,'dodge_until':0}

def info(value):
    return p.PokemonInfo(pokemon=mon(value),current_health=value['hp'],current_energy=value['energy'])

def participant(game,db,player,state,defender=False):
    values=[state['defender']] if defender else state['party'];active=0 if defender else state['active']
    owner=state['owner'] if defender else player
    out=p.BattleParticipantProto(active_pokemon=info(values[active]),trainer_public_profile=gyms.public(game,db,owner),player_id=owner[:20])
    megas=[mon(value) for value in values if mon(value).pokemon_display.current_temp_evolution]
    if megas:
        out.has_active_mega_evolved_pokemon=True
        out.battle_mega_pokemon_id=megas[0].id
    for index,value in enumerate(values):
        if index!=active:getattr(out,'reserve_pokemon' if value['hp'] else 'defeated_pokemon').add().CopyFrom(info(value))
    return out

def event(state,kind,at,source=0,target=-1,**fields):
    value=p.BattleActionProto(type=kind,action_start_ms=at,attacker_index=source,target_index=target,**fields)
    state['events'].append(value.SerializeToString().hex())
    return value

def log(state,stamp):
    out=p.BattleLogProto(state=state['status'],battle_type=state.get('battle_type',1),server_ms=stamp,battle_start_ms=state['start'],battle_end_ms=state['end'])
    for raw in state['events']:out.battle_actions.add().ParseFromString(bytes.fromhex(raw))
    return out

def start(game,db,player,body):
    request=p.GymStartSessionProto.FromString(body);fort=gyms.gym(db,request.gym_id)
    if game.config.get('profile','').startswith('m7-'):
        logging.info('GYM start party=%s defender=%d',list(request.attacking_pokemon_id),request.defending_pokemon_id)
    def fail(result):return p.GymStartSessionOutProto(result=result)
    if not fort:return fail(2)
    if not gyms.near(game,fort,request.player_lat_degrees,request.player_lng_degrees):return fail(13)
    if gyms.active_raid(game,db,fort):return fail(15)
    team=gyms.team(db,player)['team'];gymteam=db.execute('SELECT team FROM gyms WHERE fort_id=?',(fort['id'],)).fetchone()[0]
    if not gymteam:return fail(3)
    if not team or team==gymteam:return fail(4)
    if bounds(game.config,game.player(db,player)['xp'])[0]<gyms.rules(game)['minimum_level']:return fail(12)
    if any(json.loads(row['state'])['status']==1 for row in db.execute(
        'SELECT state FROM raid_battles WHERE player_id=? AND settled=0 AND end_ms>?',(player,now_ms()))):return fail(9)
    gyms.map_fort(game,db,fort) # Apply elapsed motivation before copying combat stats.
    rows=gyms.defenders(db,fort['id'])
    row=next((r for r in rows if r['pokemon_id']==request.defending_pokemon_id),None)
    if not rows:return fail(5)
    if row is None:return fail(6)
    ids=list(request.attacking_pokemon_id)
    if not 1<=len(ids)<=6 or len(set(ids))!=len(ids):return fail(7)
    party=[owned(db,player,pid) for pid in ids]
    if any(not value or value.is_egg or value.deployed_fort_id or value.deployed_station_id for value in party):return fail(7)
    if any(not value.stamina for value in party):return fail(8)
    from dittobuffer.gameplay.mega import project
    party=[project(game,db,value,combat=True) for value in party]
    defender=p.PokemonProto.FromString(row['data'])
    if any(value.move1 not in MOVES or value.move2 not in MOVES for value in party+[defender]):return fail(7)
    stamp=now_ms()
    # One active session per trainer/defender. A duplicate start returns that battle.
    for old in db.execute('SELECT * FROM gym_battles WHERE settled=0 AND (player_id=? OR defender_id=?)',(player,defender.id)).fetchall():
        state=json.loads(old['state'])
        advance(game,state,min(stamp,state['end']))
        if state['status']!=1:
            settle(game,db,old,state);persist(db,old['id'],state,game,old['player_id'])
        elif old['player_id']==player and old['fort_id']==fort['id'] and [mon(v).id for v in state['party']]==ids:
            return start_response(game,db,player,old['id'],state,stamp)
        else:return fail(9)
    # This client enters combat immediately after StartSession (asset loading
    # already happened). A future start rejects its first legitimate inputs.
    start_ms=stamp;end=start_ms+int(settings(game.config).round_duration_seconds*1000)
    # Motivation scales the battle copy; the owned Pokemon retains its stats.
    motivation=max(.01,min(1,row['motivation']))
    defender.cp=max(10,int(defender.cp*motivation))
    defender.cp_multiplier*=math.sqrt(motivation)
    defender.max_stamina=max(10,int(defender.max_stamina*math.sqrt(motivation)))
    # The battle copy carries the defender HP multiplier, never the owned record.
    defender.max_stamina*=2;defender.stamina=defender.max_stamina
    state={'start':start_ms,'end':end,'clock':start_ms,'status':1,'active':0,'owner':row['owner_id'],
           'party':[entry(value) for value in party],'defender':entry(defender),
           'ai_due':start_ms+1500,'pending':[],'events':[],'seen':[],'results':'',
           'defender_order':[value['pokemon_id'] for value in rows]}
    state['defender']['pokemon']=defender.SerializeToString().hex()
    state['party'][0]['ready']=start_ms
    event(state,6,start_ms,joined_player=participant(game,db,player,state))
    battle_id='gym-'+secrets.token_hex(12)
    db.execute('INSERT INTO gym_battles VALUES(?,?,?,?,?,?,?,0)',(battle_id,player,fort['id'],defender.id,stamp,end,json.dumps(state)))
    from dittobuffer.gameplay.buddy import activity
    activity(game,db,player,'battle',battle_id,ids)
    return start_response(game,db,player,battle_id,state,stamp)

def start_response(game,db,player,battle_id,state,stamp):
    return p.GymStartSessionOutProto(result=1,battle=p.BattleProto(battle_start_ms=state['start'],battle_end_ms=state['end'],
        battle_id=battle_id,attacker=participant(game,db,player,state),defender=participant(game,db,player,state,True),battle_log=log(state,stamp)))

def damage(game,source,target,move,boost=1):
    from dittobuffer.gameplay.combat import damage_value
    return damage_value(mon(source),mon(target),game.species,move,game.config,game.forms,boost)


def schedule(game,state,source,kind,at):
    attacker=state['defender'] if source==-1 else state['party'][state['active']]
    target=state['party'][state['active']] if source==-1 else state['defender']
    pokemon=mon(attacker);move=pokemon.move1 if kind==1 else pokemon.move3 if kind==11 else pokemon.move2
    if move not in MOVES:return False
    _,_,duration,energy,window=MOVES[move]
    if attacker['energy']+energy<0:return False
    attacker['energy']=min(100,attacker['energy']+energy);attacker['ready']=at+duration
    event(state,kind,at,source,0 if source==-1 else -1,duration_ms=duration,energy_delta=energy,
        active_pokemon_id=pokemon.id,target_pokemon_id=mon(target).id,
        damage_window_start_ms=at+window,damage_window_end_ms=at+DEFINITIONS[str(move)]['damageWindowEndMs'],damage_move_type=MOVES[move][0])
    boost=state.get('peer_mega_boost',{}).get(str(MOVES[move][0]),1) if source==0 else 1
    if source==0:boost*=state.get('peer_friendship_boost',1)
    pending={'at':at+window,'source':source,'source_id':pokemon.id,'target':mon(target).id,'damage':damage(game,attacker,target,move,boost)}
    if source==0 and kind in (3,11):
        from dittobuffer.gameplay.mega import combat_rules
        target_mon=mon(target);definition=game.forms.get((target_mon.pokemon_id,target_mon.pokemon_display.form),game.species[target_mon.pokemon_id])
        if effectiveness(MOVES[move][0],combat_rules(target_mon,definition)['types'])>1:
            pending['research_charge']={'event':len(state['events'])-1,'move':move}
    state['pending'].append(pending)
    return True

def advance(game,state,until):
    until=min(until,state['end'])
    if state.get('pending_swap') is not None:
        # First return canonical zero HP for the fainted active Pokemon. The
        # next poll swaps it out, after Android has received that final HP.
        state['clock']=max(state['clock'],until)
        if until>=state['end']:state['status']=4
        return
    while state['status']==1:
        pending=min(state['pending'],key=lambda x:x['at'],default=None)
        at=min(state['ai_due'],pending['at'] if pending else state['end']+1)
        if at>until:break
        if pending and pending['at']==at:
            state['pending'].remove(pending)
            source=state['defender'] if pending['source']==-1 else state['party'][state['active']]
            if source['hp']<=0 or mon(source).id!=pending.get('source_id',mon(source).id):continue
            target=state['party'][state['active']] if pending['source']==-1 else state['defender']
            if mon(target).id!=pending['target'] or target['hp']<=0:continue
            amount=pending['damage']
            if amount and at<=target['dodge_until']:amount=max(1,math.ceil(amount*(1-settings(game.config).dodge_damage_reduction_percent)))
            if amount>0 and pending.get('research_charge'):state.setdefault('research_charges',[]).append(pending['research_charge'])
            target['hp']=max(0,target['hp']-amount)
            target['energy']=min(100,target['energy']+math.ceil(amount*settings(game.config).energy_delta_per_health_lost))
            if target['hp']==0:
                event(state,5,at,-1 if pending['source']==0 else 0,active_pokemon_id=mon(target).id)
                if pending['source']==0:state['status']=2
                else:
                    replacement=next((i for i,v in enumerate(state['party']) if v['hp']>0),None)
                    if replacement is None:state['status']=3
                    else:
                        state['pending_swap']=replacement
                        break
        else:
            defender=state['defender'];charged=defender['energy']>=-MOVES[mon(defender).move2][3]
            schedule(game,state,-1,3 if charged else 1,at)
            state['ai_due']=defender['ready']+int(settings(game.config).enemy_attack_interval*1000)
    state['clock']=max(state['clock'],until)
    if state['status']==1 and until>=state['end']:state['status']=4

def settle(game,db,row,state):
    if db.execute('SELECT settled FROM gym_battles WHERE id=?',(row['id'],)).fetchone()[0]:return
    fort=gyms.gym(db,row['fort_id']);player=row['player_id']
    for value in state['party']:
        pokemon=owned(db,player,mon(value).id)
        if pokemon:
            from dittobuffer.gameplay.mega import ordinary_hp
            pokemon.stamina=min(pokemon.stamina,ordinary_hp(mon(value),value['hp']));update_owned(game,db,player,pokemon)
    result=p.BattleResultsProto(battle_duration_ms=max(0,min(now_ms(),state['end'])-state['start']))
    xp=0
    defender=db.execute('SELECT * FROM gym_defenders WHERE pokemon_id=? AND fort_id=?',(row['defender_id'],fort['id'])).fetchone()
    if state['status']==2 and defender:
        xp=gyms.rules(game)['battle_xp']
        db.execute('UPDATE players SET xp=xp+? WHERE id=?',(xp,player));game.touch(db,player)
        db.execute('UPDATE gym_defenders SET motivation=MAX(0,motivation-?),battles_lost=battles_lost+1 WHERE pokemon_id=?',
            (gyms.rules(game)['motivation_loss'],row['defender_id']))
        current=db.execute('SELECT * FROM gym_defenders WHERE pokemon_id=?',(row['defender_id'],)).fetchone()
        if current['motivation']<.00001:gyms.return_defender(game,db,current)
        result.gym_badge.add().CopyFrom(gyms.badge(game,db,player,fort,max(1,mon(state['defender']).cp//100),battles_won=1))
    elif not state.get('raid_interrupted'):
        if defender:db.execute('UPDATE gym_defenders SET battles_won=battles_won+1 WHERE pokemon_id=?',(row['defender_id'],))
        result.gym_badge.add().CopyFrom(gyms.badge(game,db,player,fort,battles_lost=1))
    db.execute('UPDATE gyms SET modified_ms=? WHERE fort_id=?',(now_ms(),fort['id']))
    result.attackers.add().CopyFrom(participant(game,db,player,state));result.player_xp_awarded.append(xp)
    result.gym_status.CopyFrom(gyms.status(game,db,fort));result.gym_state.fort_map_data.CopyFrom(gyms.map_fort(game,db,fort))
    remaining=gyms.defenders(db,fort['id'])
    if state['status']==2 and remaining:
        # A round visits each defender once in deployment order. Surviving
        # motivation belongs to the next round, rather than immediately
        # fighting the same first defender again. New deployments wait too.
        order=state.get('defender_order',[row['defender_id']])
        after=order[order.index(row['defender_id'])+1:]
        present={value['pokemon_id'] for value in remaining}
        result.next_defender_pokemon_id=next((ident for ident in after if ident in present),0)
    result.player_results.add(attacker=participant(game,db,player,state),player_xp_awarded=xp)
    state['results']=result.SerializeToString().hex()
    event(state,{2:8,3:9,4:10}[state['status']],min(now_ms(),state['end']),battle_results=result)
    db.execute('UPDATE gym_battles SET settled=1 WHERE id=?',(row['id'],))
    from dittobuffer.gameplay.trainer import event as record_event
    record_event(game,db,player,'gym:'+row['id'],'gym_battle',dict(fort_id=fort['id'],result=state['status'],xp=xp))

def project_research_charges(game,db,player,battle_id,state):
    from dittobuffer.gameplay.trainer import event as source_event
    for hit in state.get('research_charges',[]):
        source_event(game,db,player,'pve-charge:'+battle_id+':'+str(hit['event']),'super_effective_charge',{'super_effective':True,'move':hit['move'],'source':'pve'})


def persist(db,battle_id,state,game=None,player=None):
    db.execute('UPDATE gym_battles SET state=? WHERE id=?',(json.dumps(state,separators=(',',':')),battle_id))
    if game is not None:project_research_charges(game,db,player,battle_id,state)

def process_actions(game,state,actions,stamp):
    if state.get('pending_swap') is not None:
        previous=mon(state['party'][state['active']]).id
        state['active']=state.pop('pending_swap');value=state['party'][state['active']]
        value['ready']=stamp+settings(game.config).swap_duration_ms
        state['ai_due']=max(state['ai_due'],value['ready'])
        event(state,4,stamp,0,0,duration_ms=settings(game.config).swap_duration_ms,
            active_pokemon_id=previous,target_pokemon_id=mon(value).id)
    for action in sorted(actions,key=lambda a:a.action_start_ms):
        key=hashlib.sha256(action.SerializeToString()).hexdigest()
        if key in state['seen']:continue
        at=action.action_start_ms
        if at<state['start'] or at<state['clock']-5000 or at>stamp+2000 or at>state['end'] or action.attacker_index!=0:return False
        # Advance damage/AI chronologically before accepting an intention.
        advance(game,state,min(at,stamp))
        if state['status']!=1:break
        value=state['party'][state['active']]
        if action.type in (1,3,11):
            if at<value['ready'] or (action.active_pokemon_id and action.active_pokemon_id!=mon(value).id):return False
            if not schedule(game,state,0,action.type,at):return False
        elif action.type==2:
            if at<value['ready']:return False
            value['ready']=at+settings(game.config).dodge_duration_ms;value['dodge_until']=value['ready']
            event(state,2,at,duration_ms=settings(game.config).dodge_duration_ms,active_pokemon_id=mon(value).id)
        elif action.type==4:
            index=next((i for i,v in enumerate(state['party']) if mon(v).id==action.target_pokemon_id and v['hp']>0),None)
            if index is None or at<value['ready']:return False
            previous=mon(value).id
            state['active']=index;state['party'][index]['ready']=at+settings(game.config).swap_duration_ms
            event(state,4,at,0,0,duration_ms=settings(game.config).swap_duration_ms,active_pokemon_id=previous,target_pokemon_id=action.target_pokemon_id)
        elif action.type==7:
            state['status']=3;event(state,7,at)
        else:return False
        state['seen'].append(key)
    return True

def attack(game,db,player,body):
    request=p.GymBattleAttackProto.FromString(body)
    if game.config.get('profile','').startswith('m7-'):
        logging.info('GYM actions=%s',[(a.type,a.action_start_ms,a.duration_ms,a.energy_delta,a.attacker_index,a.target_index,a.active_pokemon_id) for a in request.attacker_actions])
    row=db.execute('SELECT * FROM gym_battles WHERE id=? AND player_id=? AND fort_id=?',(request.battle_id,player,request.gym_id)).fetchone()
    def fail(result=2):return p.GymBattleAttackOutProto(result=result)
    if not row:return fail()
    fort=gyms.gym(db,row['fort_id'])
    if not gyms.near(game,fort,request.player_lat_degrees,request.player_lng_degrees):return fail(3)
    if gyms.active_raid(game,db,fort):return fail(5)
    state=json.loads(row['state']);stamp=now_ms()
    if len(request.attacker_actions)>100:return fail()
    if state['status']==1:
        if not process_actions(game,state,request.attacker_actions,stamp):return fail()
        advance(game,state,stamp)
        if state['status']!=1:settle(game,db,row,state)
        else:
            # A process exit cannot heal damage from an unfinished battle.
            for value in state['party']:
                pokemon=owned(db,player,mon(value).id)
                if pokemon and pokemon.stamina!=value['hp']:
                    from dittobuffer.gameplay.mega import ordinary_hp
                    pokemon.stamina=min(pokemon.max_stamina,ordinary_hp(mon(value),value['hp']));update_owned(game,db,player,pokemon)
        persist(db,row['id'],state,game,row['player_id'])
    out=p.GymBattleAttackOutProto(result=1,battle_update=p.BattleUpdateProto(battle_id=row['id'],battle_log=log(state,stamp),
        active_defender=info(state['defender']),active_attacker=info(state['party'][state['active']])))
    if state['results']:
        result=p.BattleResultsProto.FromString(bytes.fromhex(state['results']))
        if result.gym_badge:out.gym_badge.CopyFrom(result.gym_badge[0])
    return out

def expire(game,db):
    """Recover abandoned sessions on the next request, including after restart."""
    stamp=now_ms()
    for row in db.execute('SELECT * FROM gym_battles WHERE settled=0').fetchall():
        state=json.loads(row['state'])
        raid=gyms.active_raid(game,db,gyms.gym(db,row['fort_id']))
        if raid:
            advance(game,state,min(state['end'],raid['battle_ms']))
            if state['status']==1:
                state.update(status=3,raid_interrupted=True)
                event(state,7,min(state['end'],raid['battle_ms']))
        elif row['end_ms']<=stamp:advance(game,state,state['end'])
        else:continue
        settle(game,db,row,state);persist(db,row['id'],state,game,row['player_id'])
