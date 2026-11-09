"""Native raid summaries derived from authoritative state, not extra rewards.

Numeric bonus schedules are explicit local policy. The supplied client schema
and official help establish the categories, not the current numeric tables.
"""
import json
import math
from dittobuffer.gameplay.game import p


def validate(config):
    value = config.get('ball_bonuses', {})
    if not isinstance(value, dict) or set(value) - {'provenance', 'gym_ownership', 'damage', 'speed'}:
        raise ValueError('Invalid raid ball bonus policy')
    if value and (not isinstance(value.get('provenance'), str) or not value['provenance'].strip()):
        raise ValueError('Raid ball bonus policy needs explicit provenance')
    if type(value.get('gym_ownership', 0)) is not int or not 0 <= value.get('gym_ownership', 0) <= 20:
        raise ValueError('Invalid gym control ball bonus')
    for key, fraction in [('damage', 'minimum_fraction'), ('speed', 'maximum_fraction')]:
        previous = -1
        previous_balls = -1 if key == 'damage' else 21
        rows = value.get(key, [])
        if not isinstance(rows, list):
            raise ValueError('Invalid raid ball bonus thresholds')
        for row in rows:
            if not isinstance(row, dict) or set(row) != {fraction, 'balls'}:
                raise ValueError('Invalid raid ball bonus threshold')
            threshold = row[fraction]
            if (type(threshold) not in (float, int) or not math.isfinite(threshold)
                    or not previous < threshold <= 1 or threshold < 0
                    or type(row['balls']) is not int or not 0 <= row['balls'] <= 20):
                raise ValueError('Invalid raid ball bonus threshold')
            if ((key == 'damage' and row['balls'] < previous_balls)
                    or (key == 'speed' and row['balls'] > previous_balls)):
                raise ValueError('Raid ball bonuses must follow contribution and speed')
            previous, previous_balls = threshold, row['balls']


def damage(game, db, battle, state):
    from dittobuffer.gameplay.cooperative import room_for
    room = room_for(db, battle['id'])
    if room:
        row = db.execute('SELECT damage FROM raid_room_members WHERE attempt_id=?', (battle['id'],)).fetchone()
        return max(0, row[0] if row else 0)
    # Solo victories include damage across every party/relobby in this attempt.
    pokemon = p.PokemonProto.FromString(bytes.fromhex(state['defender']['pokemon']))
    return max(0, pokemon.max_stamina - state['defender']['hp'])


def participation(game, db, battle, state, base, friend_tier, friend_balls):
    from dittobuffer.gameplay import gyms
    raid = db.execute('SELECT * FROM raids WHERE seed=?', (battle['seed'],)).fetchone()
    policy = json.loads(raid['definition']).get('_rules', {}).get('ball_bonuses', game.raids.get('ball_bonuses', {}))
    out = p.ParticipationProto(base_pokeballs=base,
        highest_friendship_milestone=friend_tier, highest_friendship_pokeballs=friend_balls)
    maximum = p.PokemonProto.FromString(bytes.fromhex(state['defender']['pokemon'])).max_stamina
    fraction = min(1, damage(game, db, battle, state) / max(1, maximum))
    for row in policy.get('damage', []):
        if fraction >= row['minimum_fraction']:
            out.individual_damage_pokeballs = row['balls']
    team = gyms.team(db, battle['player_id'])['team']
    gyms.gym(db, raid['fort_id'])
    owner = db.execute('SELECT team FROM gyms WHERE fort_id=?', (raid['fort_id'],)).fetchone()[0]
    if team and owner == team:
        out.gym_ownership_pokeballs = policy.get('gym_ownership', 0)
    elapsed = max(0, state['clock'] - state['start'])
    duration = max(1, state['end'] - state['start'])
    if json.loads(raid['definition'])['tier'] != 6:
        for row in policy.get('speed', []):
            if elapsed / duration <= row['maximum_fraction']:
                out.speed_completion_pokeballs = row['balls']
                break
    return out


def total_balls(value):
    return sum(getattr(value, name) for name in ('base_pokeballs', 'individual_damage_pokeballs',
        'team_damage_pokeballs', 'gym_ownership_pokeballs', 'highest_friendship_pokeballs',
        'speed_completion_pokeballs', 'fort_powerup_pokeballs', 'rsvp_follow_through_pokeballs'))


def project(game, db, battle, state, result):
    """Populate the actual vNext per-player schema without crediting anything."""
    own_damage = damage(game, db, battle, state)
    result.attackers[0].damage_dealt = own_damage
    target = result.player_results[0]
    target.attacker.CopyFrom(result.attackers[0])
    # The vNext summary reads PlayerResultsProto (tag 19), while the existing
    # backend populated only legacy parallel arrays. Both must carry the same
    # persisted entitlement, not independent awards or encounters.
    for name in ('participation', 'raid_item_rewards', 'post_raid_encounter', 'gym_badge'):
        values = getattr(result, name)
        if len(values) != 1:
            raise ValueError('Expected one owned raid result')
        getattr(target, name).CopyFrom(values[0])
    target.player_xp_awarded = result.player_xp_awarded[0]
    target.stats.CopyFrom(p.RaidPlayerStatProto(stat_id=2,
        player_profile=target.attacker.trainer_public_profile, stat_value=own_damage, attacker_index=0))
    from dittobuffer.gameplay.cooperative import room_for
    room = room_for(db, battle['id'])
    winner = battle['player_id']
    best = own_damage
    if room:
        top = db.execute('SELECT player_id,damage FROM raid_room_members WHERE room_id=? ORDER BY damage DESC,player_id LIMIT 1', (room['id'],)).fetchone()
        if top:
            winner, best = top
    if best > 0:
        from dittobuffer.gameplay.gyms import public
        result.raid_player_stats.stats.add(stat_id=2, player_profile=public(game, db, winner),
            stat_value=best, featured=True, attacker_index=0 if winner == battle['player_id'] else -1)
