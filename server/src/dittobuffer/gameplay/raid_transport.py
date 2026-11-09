"""0.429.2 Nebula HTTP adapter over the existing authoritative PvE clock.

Only one real participant is admitted. Native battle state is a projection of
the persistent server engine; it never accepts client HP, damage or rewards.
"""
import hashlib,json,logging,math
from dittobuffer.gameplay.game import p,now_ms,RpcError
from dittobuffer.gameplay import raids, gym_battle as engine
from dittobuffer.gameplay.battle_master import MOVES

TURN_MS=500

def authorize(game,db,headers):
    token=headers.get('Authorization','').removeprefix('Bearer ')
    b=db.execute('SELECT b.* FROM raid_battles b JOIN raid_combat_sessions t ON t.battle_id=b.id AND t.player_id=b.player_id JOIN raid_members m ON m.seed=b.seed AND m.player_id=b.player_id WHERE t.token=? AND t.revoked_ms=0 AND m.battle_id=b.id AND t.generation=COALESCE(json_extract(b.state,\'$.generation\'),0)',(token,)).fetchone()
    if not b:raise RpcError('Invalid local raid session')
    from dittobuffer.gameplay.cooperative import native_id
    if native_id(db,b['id'])!=headers.get('nbl-battle-id',''):raise RpcError('Invalid raid room')
    if headers.get('nbl-player-id','') not in (b['player_id'],b['player_id'][:20]):raise RpcError('Raid participant mismatch')
    if game.accounts:
        from dittobuffer.core.accounts import AccountError
        row=db.execute('SELECT session_digest FROM family_battle_sessions WHERE battle_id=? AND generation=COALESCE(json_extract(?,\'$.generation\'),0)',(b['id'],b['state'])).fetchone()
        if not row:raise RpcError('Authenticated family battle session required')
        try:game.accounts.authorize_digest(db,row[0])
        except AccountError:raise RpcError('Family battle session revoked or expired') from None
    return b

def update(game,db,b,state):
    if state.get('room_id'):
        from dittobuffer.gameplay.cooperative import advance_for
        advance_for(game,db,b['id'])
        state.clear();state.update(json.loads(db.execute('SELECT state FROM raid_battles WHERE id=?',(b['id'],)).fetchone()[0]))
        return
    # Finished frames are immutable projections. Polling them after medicine
    # must never write their old injuries over newly healed owned Pokemon.
    if state['status']!=1:return
    if state['status']==1:
        if state.get('pending_swap') is not None:engine.process_actions(game,state,[],now_ms())
        engine.advance(game,state,now_ms())
    raids.checkpoint(game,db,b,state)
    if state['status']!=1:raids.finish_battle(game,db,b,state)
    raids.persist(db,b['id'],state)

def pokemon(value):
    mon=engine.mon(value)
    out=p.BattlePokemonProto(id=mon.id,pokedex_id=mon.pokemon_id,display=mon.pokemon_display,cp=mon.cp,pokeball=mon.pokeball or 1,nickname=mon.nickname)
    out.resources[1].CopyFrom(p.BattleResourceProto(type=1,quantity=value['hp'],max_quantity=mon.max_stamina))
    out.resources[2].CopyFrom(p.BattleResourceProto(type=2,quantity=value['energy'],max_quantity=100))
    for kind,move in [(1,mon.move1),(2,mon.move2)]+([(3,mon.move3)] if mon.move3 in MOVES else []):
        definition=MOVES[move]
        out.moves[kind].CopyFrom(p.MoveData(move=move,type=definition[0],duration_turns=max(1,math.ceil(definition[2]/TURN_MS)),level=1,energy_delta=definition[3]))
    return out

def snapshot(b,state):
    stamp=now_ms();turn=max(0,(stamp-state['start'])//TURN_MS)
    # PbsClient marks its first frame as full itself. Subsequent frames must use
    # the update callback: all-full delivery leaves HasReceivedStateForBattle
    # false, permanently blocking native gestures despite rendering the models.
    out=p.BattleStateOutProto(turn=turn,time_ms=stamp,full_state=False,serial=stamp)
    s=out.battle_state;s.turn_start_ms=state['start']+turn*TURN_MS;s.turn=turn;s.ms_per_turn=TURN_MS
    s.current_actor_id=b['player_id'][:20];s.state=3 if state['status']==1 else 5;s.active_actor_count=1
    s.battle_start_turn=0;s.battle_end_turn=max(1,(state['end']-state['start'])//TURN_MS);s.allied_pokemon_remaining=sum(v['hp']>0 for v in state['party'])
    s.team_actor_count[1]=1;s.team_actor_count[2]=1
    for actor,team,typ,values,active in [(b['player_id'][:20],1,1,state['party'],state['active']),('raid-boss',2,5,[state['defender']],0)]:
        a=s.actors[actor];a.id=actor;a.type=typ;a.team=team;a.party_id=actor;a.active_pokemon_id=engine.mon(values[active]).id
        a.pokemon_roster.extend(engine.mon(v).id for v in values);s.party_member_count[actor]=1
        for v in values:s.pokemon[engine.mon(v).id].CopyFrom(pokemon(v))
    # Events retain stable serials across full-state polls. The native director
    # uses those cursors to avoid replaying animations on network retries.
    s.events.add(type=1,actor_id=b['player_id'][:20],turn=0,serial=1,start_battle=p.RaidPbsStart())
    for serial,raw in enumerate(state['events'],2):
        event=p.BattleActionProto.FromString(bytes.fromhex(raw));kind={1:4,3:4,11:4,2:5,4:7}.get(event.type)
        if kind:
            e=s.events.add(type=kind,actor_id='raid-boss' if event.attacker_index==-1 else b['player_id'][:20],turn=max(0,(event.action_start_ms-state['start'])//TURN_MS),serial=serial)
            if kind==4:
                mon=engine.mon(state['defender']) if event.attacker_index==-1 else next(engine.mon(v) for v in state['party'] if engine.mon(v).id==event.active_pokemon_id)
                move=mon.move1 if event.type==1 else mon.move3 if event.type==11 else mon.move2
                e.attack.CopyFrom(p.RaidPbsAttack(attack_type=1 if event.type==1 else 3 if event.type==11 else 2,move=move,type=MOVES[move][0],source_pokemon_id=mon.id,
                    target_id=b['player_id'][:20] if event.attacker_index==-1 else 'raid-boss'))
            elif kind==5:e.dodge.direction=1
            else:e.swap_pokemon.CopyFrom(p.RaidPbsSwap(outgoing_pokemon_id=event.active_pokemon_id,incoming_pokemon_id=event.target_pokemon_id))
    if state['status']!=1:
        e=s.events.add(type=18,actor_id=b['player_id'][:20],turn=turn,serial=len(state['events'])+2)
        # ARM64 BattleEndEventHandler (0x7617D8C) treats ELIMINATION as a
        # victory regardless of Result. SIDELINE_ELIMINATION is party defeat.
        e.battle_end.CopyFrom(p.RaidPbsEnd(reason=5 if state.get('surrendered') else {2:1,3:3,4:2}[state['status']],result=1 if state['status']==2 else 2))
    return out

def handle(game,path,headers,body=b''):
    with game.db.transaction() as db:
        b=authorize(game,db,headers);state=json.loads(b['state'])
        if path.endswith('/get-time'):
            return p.BattleClockSyncOutProto(result=1,server_time_ms=now_ms())
        if path.endswith('/get-state'):
            update(game,db,b,state)
            if state.get('room_id'):
                from dittobuffer.gameplay.cooperative import projection
                return projection(game,db,b,state)
            return snapshot(b,state)
        req=p.SendBattleEventProto.FromString(body)
        from dittobuffer.gameplay.cooperative import native_id
        if req.battle_id!=native_id(db,b['id']) or req.player_id not in (b['player_id'],b['player_id'][:20]):raise RpcError('Raid event ownership mismatch')
        if req.sequence_id<0:raise RpcError('Invalid raid sequence')
        sequence=req.sequence_id;generation=state.get('generation',0)
        digest=hashlib.sha256(body).hexdigest()
        old=db.execute('SELECT * FROM raid_event_receipts WHERE battle_id=? AND generation=? AND sequence=?',(b['id'],generation,sequence)).fetchone()
        if old:
            if old['request_hash']!=digest:raise RpcError('Raid sequence reused with different data')
            return p.SendBattleEventOutProto.FromString(old['response'])
        if state.get('room_id'):update(game,db,b,state)
        event=req.battle_event;stamp=now_ms();turn=max(0,(stamp-state['start'])//TURN_MS);accepted=False
        if event.actor_id and event.actor_id!=b['player_id'][:20]:raise RpcError('Invalid raid actor')
        action=p.BattleActionProto(action_start_ms=stamp,attacker_index=0,active_pokemon_id=engine.mon(state['party'][state['active']]).id)
        if event.type==4:
            # Both NonCombatMoveType and BattlePokemonProto.MoveType explicitly
            # define Charge2=3 in this APK. Never trust a supplied damage/move.
            slot=event.attack.attack_type;action.type={1:1,2:3,3:11}.get(slot,0)
            mon=engine.mon(state['party'][state['active']])
            move={1:mon.move1,2:mon.move2,3:mon.move3}.get(slot,0)
            if not action.type or move not in MOVES:raise RpcError('Unsupported or locked raid move slot')
            if event.attack.move and event.attack.move!=move:raise RpcError('Unlearned raid move')
            if event.attack.source_pokemon_id and event.attack.source_pokemon_id!=mon.id:raise RpcError('Raid Pokemon mismatch')
        elif event.type==5:action.type=2
        elif event.type==7:action.type=4;action.target_pokemon_id=event.swap_pokemon.incoming_pokemon_id
        elif event.type==3:action.type=7
        elif event.type in (1,2,41,45,47):accepted=True
        else:raise RpcError('Unsupported raid event '+str(event.type))
        if not accepted and state['status']==1:
            accepted=engine.process_actions(game,state,[action],stamp)
            if accepted and action.type==7 and state['status']==3:state['surrendered']=True
            if accepted:
                if state.get('room_id'):raids.persist(db,b['id'],state)
                if state['status']!=1:raids.finish_battle(game,db,b,state)
                else:update(game,db,b,state)
        response=p.SendBattleEventOutProto(result=1 if accepted else 2,sequence_id=req.sequence_id,accepted_turn=turn)
        db.execute('INSERT INTO raid_event_receipts(battle_id,generation,sequence,request_hash,response) VALUES(?,?,?,?,?)',(b['id'],generation,sequence,digest,response.SerializeToString()))
        logging.info('RAID PBS event=%d sequence=%d accepted=%s hp=%d',event.type,req.sequence_id,accepted,state['defender']['hp'])
        return response
