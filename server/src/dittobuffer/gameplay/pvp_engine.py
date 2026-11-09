"""Durable two-sided 500 ms Trainer battle simulation and native projection.

Both participants act on the same state inside Game.rpc's SQLite transaction.
Inventory HP is never changed by Trainer battles. No client snapshot is trusted.
"""
import json
import math
import secrets
from dittobuffer.gameplay.game import p, now_ms
from dittobuffer.gameplay import pvp_rules as r

TURN_MS = 500


def row(db, ident, player):
    return db.execute('SELECT b.*,m.side,m.epoch FROM pvp_battles b JOIN pvp_members m ON m.battle_id=b.id WHERE b.id=? AND m.player_id=?', (ident, player)).fetchone()


def save(db, state):
    db.execute('UPDATE pvp_battles SET state=?,finished=?,end_ms=? WHERE id=?',
               (json.dumps(state, separators=(',', ':')), int(state['phase'] in (6, 7, 8)), state['end'], state['id']))


def create(game, db, players, parties, league, kind='friend', bot=None, *, ident=None, persist=True):
    stamp = now_ms()
    ident = ident or 'pvp-'+secrets.token_hex(16)
    state = dict(id=ident, league=league, kind=kind, bot=bot, phase=2 if kind=='npc' else 1,
                 season=game.pvp['season'],rank_rules=game.pvp['ranks'],rank_requirements=game.pvp['rank_requirements'],elo_k=game.pvp['elo_k'],
                 start=stamp+3000, end=stamp+3000+int(game.pvp_settings.round_duration_seconds*1000),
                 opened=[kind=='npc',players[1] is None],ready=[False,players[1] is None],armed=False,lobby_deadline=stamp+120000,started=False,
                 clock=-1, pending=[], charge=None, actions={}, minigame_actions={}, history=[], event=0,
                 winner=None, members=[dict(player=player, party=[r.snapshot(v) for v in party],
                     active=0, shields=2, swap=stamp+3000, change_end=0, last_active=None)
                     for player, party in zip(players, parties)])
    if not persist:return state
    db.execute('INSERT INTO pvp_battles(id,kind,league,state,end_ms) VALUES(?,?,?,?,?)',
               (ident, kind, league, json.dumps(state), state['end']))
    for side, player in enumerate(players):
        if player:
            db.execute('INSERT INTO pvp_members(battle_id,player_id,side) VALUES(?,?,?)', (ident, player, side))
    return state


def join(game,state,side):
    """Do not spend either player's battle clock while selecting/loading teams."""
    if state['phase']==1 and now_ms()>=state['lobby_deadline']:
        finish(state,None,'lobby_timeout')
        return
    state.setdefault('opened',[True,True])[side]=True
    if state['phase']==1 and all(state['opened']):
        state['start']=now_ms()+3000
        state['end']=state['start']+int(game.pvp_settings.round_duration_seconds*1000)
        state['phase']=2
        for member in state['members']:member['swap']=state['start']


def ready(game,state,side):
    """The first native combat poll confirms the arena finished loading."""
    if state['phase'] not in (1,2) or state.get('armed',True):return
    state.setdefault('ready',[False,False])[side]=True
    if state['phase']==2 and all(state['ready']) and now_ms()<state['lobby_deadline']:
        state['start']=now_ms()+3000
        state['end']=state['start']+int(game.pvp_settings.round_duration_seconds*1000)
        state['armed']=True;state['started']=True
        for member in state['members']:member['swap']=state['start']


def action(state, side, kind, turn, move=0, target=0, duration=1, score=0):
    value = p.CombatActionProto(type=kind, action_start_turn=turn, duration_turns=duration,
        attacker_index=side, target_index=1-side, active_pokemon_id=r.source(r.active(state, side)).id,
        target_pokemon_id=target, move=move, minigame_score=score)
    state['actions'][str(side)] = value.SerializeToString().hex()
    return value


def switch(state, side, index, turn, voluntary):
    member = state['members'][side]
    member['last_active'] = dict(r.active(state, side))
    old_id = r.source(r.active(state, side)).id
    r.active(state, side)['attack_stage'] = r.active(state, side)['defense_stage'] = 0
    member['active'] = index
    value = r.active(state, side)
    value['attack_stage'] = value['defense_stage'] = 0
    value['ready'] = turn+int(voluntary)
    a = action(state, side, 9 if voluntary else 8, turn, target=r.source(value).id)
    a.active_pokemon_id = old_id
    state['actions'][str(side)] = a.SerializeToString().hex()
    member['change_end'] = 0
    state['history'].append(dict(type='switch', side=side, turn=turn, voluntary=voluntary))


def finish(state, winner, reason):
    state['winner'] = winner
    state['phase'] = 6
    state['reason'] = reason
    state['finished_ms'] = now_ms()


def terminal(state):
    alive = [sum(v['hp'] > 0 for v in m['party']) for m in state['members']]
    if not all(alive):
        finish(state, None if alive == [0, 0] else int(alive[1] > 0), 'fainted')
        return True
    return False


def begin_charge(game, state, side, slot, turn):
    value = r.active(state, side)
    mon = r.source(value)
    move = mon.move2 if slot == 2 else mon.move3
    value['energy'] += r.MOVES[move]['energy_delta']
    state['phase'] = 4
    end = now_ms()+int(game.pvp_settings.minigame_duration_seconds*1000)
    state['charge'] = dict(side=side, move=move, turn=turn, end_ms=end, score=None,
                           defense=None, shield=False)
    a = action(state, side, slot, turn, move, duration=max(1, (end-now_ms())//500))
    state['minigame_actions'] = {str(side): a.SerializeToString().hex()}
    if state['bot']:
        policy = game.pvp['ai'][state['bot']['difficulty']]
        if side == 1:
            state['charge']['score'] = policy['score']
        else:
            other = state['members'][1]
            use = other['shields'] > 0 and r.chance(state, 'shield:'+str(state['event'])) < policy['shield_probability']
            if state['bot']['difficulty'] in ('advanced','expert'):
                own=r.active(state,1)
                # Sacrifice a nearly fainted active Pokemon when a reserve is
                # available. Uses visible HP and the AI's own party, never the
                # human's queued input, energy or future minigame score.
                if own['hp']/r.source(own).max_stamina<.15 and sum(v['hp']>0 for v in other['party'])>1:use=False
            if use:
                other['shields'] -= 1
            state['charge'].update(defense=True, shield=use)


def queue_charge(state,side,slot,turn):
    """Arbitrate one turn's intents together instead of rewarding HTTP order."""
    intents=state.setdefault('charge_intents',[])
    if not any(v['side']==side for v in intents):
        intents.append(dict(side=side,slot=slot,turn=turn,pokemon=r.source(r.active(state,side)).id))


def next_charge(game,state,turn):
    valid=[]
    for intent in state.get('charge_intents',[]):
        actor=r.active(state,intent['side']);mon=r.source(actor)
        move=mon.move2 if intent['slot']==2 else mon.move3
        if actor['hp']>0 and mon.id==intent['pokemon'] and move in r.MOVES and actor['energy']>=-r.MOVES[move]['energy_delta']:
            valid.append(intent)
    state['charge_intents']=valid
    ready=[v for v in valid if v['turn']<turn and r.active(state,v['side'])['ready']<=turn]
    if not ready:return False
    # Earliest turn first; same-turn priority uses actual Attack including IV,
    # multiplier and stages. Equal Attack uses a reproducible fair battle seed.
    winner=min(ready,key=lambda v:(v['turn'],-r.attack_stat(game,r.active(state,v['side'])),r.chance(state,'cmp:'+str(v['turn'])+':'+str(v['side']))))
    valid.remove(winner)
    begin_charge(game,state,winner['side'],winner['slot'],turn)
    return True


def resolve_charge(game, state, turn):
    c = state['charge']
    side = c['side']
    defender = r.active(state, 1-side)
    before = defender['hp']
    score = c['score'] if c['score'] is not None else 0
    defender['hp'] = max(0, before-(1 if c['shield'] else r.damage(game, game.pvp_settings,
                        r.active(state, side), defender, c['move'], score)))
    r.buffs(state, side, c['move'], state['event'])
    state['event'] += 1
    state['history'].append(dict(type='charged', side=side, move=c['move'], score=score,
        shield=c['shield'], before_hp=before, after_hp=defender['hp'], turn=turn,
        super_effective=r.effectiveness(r.MOVES[c['move']]['type'],r.rules_for(game,r.source(defender))['types'])>1))
    for s, kind, value in [(side, 4, score), (1-side, 6, int(c['shield']))]:
        a = action(state, s, kind, turn, score=value)
        state['minigame_actions'][str(s)] = a.SerializeToString().hex()
    c['fly_out_turn'] = turn+3
    state['last_charge'] = c
    state['charge'] = None
    state['phase'] = 3
    # Preserve queued fast moves across the native charged sequence. Damage is
    # resolved after fly-out against the active target, including a caught swap.
    for event in state['pending']:
        event['turn'] = max(event['turn'], turn+3)
    for s in (0, 1):
        r.active(state, s)['ready'] = max(r.active(state, s)['ready'], turn+3)
    state['clock'] = turn
    terminal(state)


def ai(game, state, turn):
    if not state['bot'] or state['phase'] != 3:
        return
    member = state['members'][1]
    value = r.active(state, 1)
    if value['hp'] <= 0 or value['ready'] > turn or any(v['side']==1 for v in state.get('charge_intents',[])):
        return
    mon = r.source(value)
    policy = game.pvp['ai'][state['bot']['difficulty']]
    # Type-aware voluntary switching is deliberately explicit private policy.
    if policy['switch'] and state['start']+turn*500 >= member['swap']:
        from dittobuffer.gameplay.battle_master import effectiveness
        from dittobuffer.gameplay.species import rules_for
        types = rules_for(game, r.source(r.active(state, 0)))['types']
        scores = [(effectiveness(r.MOVES[r.source(v).move1]['type'], types), i)
                  for i, v in enumerate(member['party']) if v['hp'] > 0]
        best, index = max(scores)
        current = next(score for score, i in scores if i == member['active'])
        weakened=value['attack_stage']<=-2 or value['defense_stage']<=-2
        if index!=member['active'] and (best > current*1.5 or weakened and best>=current):
            switch(state, 1, index, turn, True)
            member['swap'] = now_ms()+int(game.pvp_settings.quick_swap_cooldown_duration_seconds*1000)
            return
    affordable = [(slot, move) for slot, move in [(2, mon.move2), (3, mon.move3)]
                  if move in r.MOVES and r.MOVES[move]['energy_delta'] < 0 and value['energy'] >= -r.MOVES[move]['energy_delta']]
    smart=state['bot']['difficulty'] in ('advanced','expert')
    target=r.active(state,0)
    save_charge=smart and target['hp']/r.source(target).max_stamina<.1 and value['hp']/mon.max_stamina>.25
    overflow=smart and value['energy']+r.MOVES[mon.move1]['energy_delta']>game.pvp_settings.max_energy
    if affordable and not save_charge and (overflow or r.chance(state, 'charge:'+str(turn)) < policy['charge_probability']):
        slot, move = max(affordable, key=lambda pair: r.damage(game, game.pvp_settings, value, r.active(state, 0), pair[1]))
        queue_charge(state,1,slot,turn)
    else:
        fast(state, 1, turn)


def fast(state, side, turn):
    value = r.active(state, side)
    mon = r.source(value)
    move = mon.move1
    duration = max(1, r.MOVES[move]['duration_turns'])
    value['ready'] = turn+duration
    action(state, side, 1, turn, move, duration=duration)
    state['pending'].append(dict(side=side, source=mon.id, move=move, turn=turn+duration))


def advance(game, state):
    stamp = now_ms()
    if state['phase']==1 or (state['phase']==2 and not state.get('armed',True)):
        if stamp>=state['lobby_deadline']:finish(state,None,'lobby_timeout')
        return
    target = max(0, (min(stamp, state['end'])-state['start'])//500)
    if state['phase'] in (6, 7, 8) or stamp < state['start']:
        return
    if stamp>=state['end']:
        scores=[(sum(v['hp']>0 for v in m['party']),sum(v['hp']/r.source(v).max_stamina for v in m['party'])) for m in state['members']]
        finish(state,None if scores[0]==scores[1] else int(scores[1]>scores[0]),'timer')
        return
    if state['phase'] == 2:
        state['phase'] = 3
    if state['charge']:
        c = state['charge']
        deadline = c['end_ms']+int(game.pvp_settings.minigame_submit_score_duration_seconds*1000)
        if (c['score'] is None or c['defense'] is None) and stamp < deadline:
            return
        resolve_charge(game, state, target)
    while state['clock'] < target and state['phase'] not in (4, 6, 7, 8):
        turn = state['clock']+1
        state['clock'] = turn
        if terminal(state):
            break
        for side, member in enumerate(state['members']):
            if r.active(state, side)['hp'] <= 0:
                if not member['change_end']:
                    member['change_end'] = state['start']+turn*500+int(game.pvp_settings.change_pokemon_duration_seconds*1000)
                if (side == 1 and state['bot']) or stamp >= member['change_end']:
                    index = next(i for i, v in enumerate(member['party']) if v['hp'] > 0)
                    switch(state, side, index, turn, False)
        # Collect all damage before applying it, so a simultaneous KO does not
        # erase the slower-iterated participant's already completed Fast Attack.
        due = [e for e in state['pending'] if e['turn'] <= turn]
        state['pending'] = [e for e in state['pending'] if e['turn'] > turn]
        hits = []
        for event in due:
            a, d = r.active(state, event['side']), r.active(state, 1-event['side'])
            if a['hp'] > 0 and d['hp'] > 0 and r.source(a).id == event['source']:
                hits.append((event, d, r.damage(game, game.pvp_settings, a, d, event['move'])))
        for event, defender, amount in hits:
            defender['hp'] = max(0, defender['hp']-amount)
            attacker = r.active(state, event['side'])
            attacker['energy'] = min(game.pvp_settings.max_energy, attacker['energy']+r.MOVES[event['move']]['energy_delta'])
            r.buffs(state, event['side'], event['move'], state['event'])
            state['event'] += 1
        if terminal(state):
            break
        if next_charge(game,state,turn):break
        ai(game, state, turn)
    if stamp >= state['end'] and state['phase'] not in (6, 7, 8):
        # Alive Pokemon count, then sum of remaining HP fractions; no client
        # completion flag can decide a timeout winner.
        scores = [(sum(v['hp'] > 0 for v in m['party']),
                   sum(v['hp']/r.source(v).max_stamina for v in m['party'])) for m in state['members']]
        finish(state, None if scores[0] == scores[1] else int(scores[1] > scores[0]), 'timer')


def submit(game, state, side, value):
    stamp = now_ms()
    turn = max(0, (stamp-state['start'])//500)
    actor = r.active(state, side)
    member = state['members'][side]
    mon = r.source(actor)
    if state['phase'] in (6, 7, 8) or stamp < state['start']:
        return 5
    if value.active_pokemon_id not in (0, mon.id) or abs(value.action_start_turn-turn) > 8:
        return 5
    if value.type in (1, 2, 3):
        move = {1:mon.move1, 2:mon.move2, 3:mon.move3}[value.type]
        if state['phase'] != 3 or actor['hp'] <= 0 or actor['ready'] > turn or move not in r.MOVES or value.move not in (0, move):
            return 5
        queued=any(v['side']==side for v in state.get('charge_intents',[]))
        if queued:return 1 if value.type in (2,3) else 5
        if value.type == 1:
            fast(state, side, turn)
        elif actor['energy'] < -r.MOVES[move]['energy_delta']:
            return 10
        else:
            queue_charge(state,side,value.type,turn)
    elif value.type in (4, 5, 6):
        c = state['charge']
        if not c or not math.isfinite(value.minigame_score) or not 0 <= value.minigame_score <= 1:
            return 13
        if value.type == 4 and c['side'] == side and c['score'] is None:
            c['score'] = value.minigame_score
        elif value.type == 5 and c['side'] != side:
            pass
        elif value.type == 6 and c['side'] != side and c['defense'] is None:
            use = value.minigame_score > 0 and member['shields'] > 0
            if use:
                member['shields'] -= 1
            c.update(defense=True, shield=use)
        else:
            return 13
        state['minigame_actions'][str(side)] = value.SerializeToString().hex()
    elif value.type in (8, 9):
        index = next((i for i, v in enumerate(member['party']) if r.source(v).id == value.target_pokemon_id and v['hp'] > 0), None)
        voluntary = value.type == 9
        if index is None or index == member['active'] or state['charge'] or (not voluntary and actor['hp'] > 0) or (voluntary and (stamp < member['swap'] or actor['hp'] <= 0 or actor['ready'] > turn)):
            return 14
        switch(state, side, index, turn, voluntary)
        if voluntary:
            member['swap'] = stamp+int(game.pvp_settings.quick_swap_cooldown_duration_seconds*1000)
    elif value.type == 7:
        return 1 if actor['hp'] == 0 else 5
    else:
        return 5
    return 1


def projection(game, db, state, side, counter=0):
    stamp = now_ms()
    turn = max(0, (min(stamp, state['end'])-state['start'])//500)
    phase = state['phase']
    if phase == 3 and r.active(state, side)['hp'] <= 0:
        phase = 5
    out = p.CombatProto(combat_state=phase, combat_id=state['id'], combat_start_ms=state['start'],
        combat_end_ms=state['end'], server_ms=stamp, current_turn=turn, turn_start_ms=state['start']+turn*500,
        quick_swap_cooldown_duration_ms=int(game.pvp_settings.quick_swap_cooldown_duration_seconds*1000),
        combat_request_counter=counter, change_pokemon_end_ms=state['members'][side]['change_end'])
    from dittobuffer.gameplay.gyms import public
    for canonical, target in [(side, out.player), (1-side, out.opponent)]:
        member = state['members'][canonical]
        if member['player']:
            target.public_profile.CopyFrom(public(game, db, member['player']))
            target.public_profile.player_id=member['player'][:20]
            # Direct rematches construct ChallengePlayer from this public
            # profile, and CombatStage.Initialize dereferences its legacy
            # PlayerAvatar even when appearance comes from NeutralAvatar.
            target.public_profile.avatar.SetInParent()
        else:
            from dittobuffer.gameplay.avatar import default_avatar
            from dittobuffer.gameplay.pvp import bot_identity,wire_player
            target.public_profile.CopyFrom(p.PlayerPublicProfileProto(player_id=wire_player(bot_identity(state['bot'])),
                name=state['bot']['name'], level=20, team=1))
            target.public_profile.neutral_avatar.CopyFrom(default_avatar(p))
            if state['kind']=='npc':target.combat_npc_personality_id = state['bot'].get('personality','LIBRE_AI_'+state['bot']['difficulty'].upper())
        current = r.active(state, canonical)
        target.active_pokemon.CopyFrom(r.native_pokemon(current))
        target.last_active_pokemon.CopyFrom(r.native_pokemon(member['last_active'] or current))
        target.minigame_defense_chances_left = member['shields']
        target.lockstep_ack = True
        target.last_updated_turn = turn
        target.quick_swap_available_ms = member['swap']
        for index, v in enumerate(member['party']):
            if index != member['active']:
                getattr(target, 'reserve_pokemon' if v['hp'] else 'fainted_pokemon').add().CopyFrom(r.native_pokemon(v))
        for field, store in [('current_action', 'actions'), ('minigame_action', 'minigame_actions')]:
            if str(canonical) in state[store]:
                a = getattr(target, field)
                a.ParseFromString(bytes.fromhex(state[store][str(canonical)]))
                a.attacker_index, a.target_index = int(canonical != side), int(canonical == side)
        target.last_snapshot_action_type = target.current_action.type
    c = state['charge'] or state.get('last_charge')
    if c:
        out.opponent_triggered = c['side'] != side
        out.minigame_end_ms = c['end_ms']
        out.minigame_submit_score_end_ms = c['end_ms']+1000
        out.minigame_data.CopyFrom(p.MinigameProto(minigame_end_ms=c['end_ms'], minigame_submit_score_end_ms=c['end_ms']+1000,
            fly_in_completion_turn=c['turn']+3, fly_out_completion_turn=c.get('fly_out_turn', c['turn']+20)))
    return out
