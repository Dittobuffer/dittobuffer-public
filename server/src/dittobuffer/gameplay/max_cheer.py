"""Native sideline protocol with explicit, provenance-labelled private tuning.

The official help establishes fainting/three-star eligibility and meter support.
25 taps / 500ms / 20 units are a published two-trainer measurement, NOT a
verified 0.429.2 server table. No effect is enabled without explicit configuration.
"""
import math

from dittobuffer.gameplay.game import p, RpcError


def validate(cfg):
    if not isinstance(cfg, dict) or type(cfg.get('enabled', False)) is not bool:
        raise ValueError('Invalid Max cheering configuration')
    if not cfg.get('enabled'):
        return
    if not isinstance(cfg.get('provenance'), str) or len(cfg['provenance'].strip()) < 20:
        raise ValueError('Explicit Max cheering provenance required')
    for key, low, high in [('taps_required', 1, 1000), ('tap_interval_ms', 500, 10000), ('meter_gain', 1, 1000)]:
        if type(cfg.get(key)) is not int or not low <= cfg[key] <= high:
            raise ValueError('Invalid Max cheering ' + key)
    if cfg.get('scope') not in ('group', 'lobby_when_group_eliminated'):
        raise ValueError('Explicit Max cheering scope required')


def config(state):
    return state.get('rules', {}).get('cheering', {})


def targets(state, player):
    from dittobuffer.gameplay.max_battle import group_players
    own = [(key, ps) for key, ps in group_players(state, player)
           if not ps['quit'] and any(v['hp'] > 0 for v in ps['party'])]
    if own or config(state).get('scope') == 'group':
        return own
    return [(key, ps) for key, ps in state['players'].items()
            if not ps['quit'] and any(v['hp'] > 0 for v in ps['party'])]


def eligible(state, player):
    ps = state['players'][player]
    if not config(state).get('enabled') or state['status'] != 1 or ps['quit'] or any(v['hp'] > 0 for v in ps['party']):
        return False
    # Source enums: ordinary three-star=3; G-Max six-star is DOUGH1=7.
    station = p.StationProto.FromString(bytes.fromhex(state['station_snapshot']))
    return station.battle_details.battle_level in (3, 4, 5, 6, 7) and bool(targets(state, player))


def apply(game, state, player, event, stamp, emit):
    from dittobuffer.gameplay.max_battle import group_for, begin_max
    cfg = config(state)
    if not cfg.get('enabled'):
        raise RpcError('Native Max cheering requires verified or explicit private tuning')
    if not eligible(state, player) or not event.HasField('sideline_action') or event.sideline_action.dispatch_globally:
        return False
    ps = state['players'][player]
    charge = ps.setdefault('cheer', dict(power=0, ready_ms=0, charges=0))
    if stamp < charge['ready_ms']:
        return False
    kind = event.sideline_action.type
    if kind == 1:
        if charge['power'] >= cfg['taps_required']:
            return False
        charge['power'] += 1
    elif kind == 2:
        if charge['power'] < cfg['taps_required']:
            return False
        groups = {state['player_groups'][key] for key, _ in targets(state, player)}
        groups = [key for key in groups if state['groups'][key]['phase'] == 0]
        if not groups:
            return False  # Do not consume a cheer during transformation/Max.
        for key in groups:
            group = state['groups'][key]
            group['meter'] = min(state['rules']['meter_goal'], group['meter'] + cfg['meter_gain'])
            representative = next(key2 for key2, _ in targets(state, player) if state['player_groups'][key2] == key)
            begin_max(state, representative, stamp)
        charge['power'] = 0
        charge['charges'] += 1
        ps['support_actions'] = ps.get('support_actions', 0) + 1
    else:
        return False
    charge['ready_ms'] = stamp + cfg['tap_interval_ms']
    emit(state, 25, player, stamp, sideline_action=p.MaxPbsSideline(type=kind,
         dispatch_globally=not any(state['player_groups'][key] == state['player_groups'][player] for key, _ in targets(state, player))))
    return True
