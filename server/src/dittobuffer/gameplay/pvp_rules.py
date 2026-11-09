"""Source-backed Trainer damage and independent, non-injuring battle snapshots.

No raid damage, Rocket AI pauses, client-provided HP or result flags are used.
"""
import hashlib
import math
from dittobuffer.gameplay.game import p
from dittobuffer.gameplay.species import REFERENCE, rules_for
from dittobuffer.gameplay.battle_master import effectiveness

MOVES = {int(k): v for k, v in REFERENCE['pvp_moves'].items()}
STAGES = [.5, 4/7, 2/3, .8, 1, 1.25, 1.5, 1.75, 2]


def source(value):
    return p.PokemonProto.FromString(bytes.fromhex(value['pokemon']))


def snapshot(value):
    return dict(pokemon=value.SerializeToString().hex(), hp=value.max_stamina,
                energy=0, ready=0, attack_stage=0, defense_stage=0)


def active(state, side):
    member = state['members'][side]
    return member['party'][member['active']]


def chance(state, key):
    return int.from_bytes(hashlib.sha256((state['id']+':'+key).encode()).digest()[:8], 'big') / 2**64


def attack_stat(game, value):
    mon = source(value)
    return (rules_for(game, mon)['attack'] + mon.individual_attack) * mon.cp_multiplier * STAGES[value['attack_stage']+4]


def damage(game, settings, attacker, defender, move, score=1):
    a, d = source(attacker), source(defender)
    ar, dr = rules_for(game, a), rules_for(game, d)
    definition = MOVES[move]
    attack = attack_stat(game, attacker)
    defense = (dr['defense']+d.individual_defense)*d.cp_multiplier*STAGES[defender['defense_stage']+4]
    if a.pokemon_display.alignment == 1:
        attack *= settings.shadow_pokemon_attack_bonus_multiplier
    if d.pokemon_display.alignment == 1:
        defense *= settings.shadow_pokemon_defense_bonus_multiplier
    stab = settings.same_type_attack_bonus_multiplier if definition['type'] in ar['types'] else 1
    bonus = settings.fast_attack_bonus_multiplier if definition['energy_delta'] > 0 else settings.charge_attack_bonus_multiplier
    if definition['energy_delta'] < 0:
        score = settings.minigame_bonus_base_multiplier + settings.minigame_bonus_variable_multiplier * score
    return max(1, math.floor(.5*definition['power']*attack/max(1, defense)*stab*
                            effectiveness(definition['type'], dr['types'])*bonus*score)+1)


def buffs(state, side, move, event):
    effects = MOVES[move].get('secondary_effects', {})
    if chance(state, 'buff:'+str(event)) >= effects.get('buffActivationChance', 0):
        return
    for who, prefix in [(active(state, side), 'attacker'), (active(state, 1-side), 'target')]:
        for stat in ('attack', 'defense'):
            delta = effects.get(prefix+stat.title()+'StatStageChange', 0)
            who[stat+'_stage'] = max(-4, min(4, who[stat+'_stage']+delta))


def native_pokemon(value):
    mon = source(value)
    return p.RocketCombatPokemonProto(pokemon_id=mon.id, pokedex_id=mon.pokemon_id,
        cp=mon.cp, cp_multiplier=mon.cp_multiplier, stamina=value['hp'], max_stamina=mon.max_stamina,
        move1=mon.move1, move2=mon.move2, move3=mon.move3, energy=value['energy'],
        pokemon_display=mon.pokemon_display, individual_attack=mon.individual_attack,
        individual_defense=mon.individual_defense, individual_stamina=mon.individual_stamina,
        attack_stat_stage=value['attack_stage'], defense_stat_stage=value['defense_stage'],
        nickname=mon.nickname, pokeball=mon.pokeball or 1, height_m=mon.height_m, weight_kg=mon.weight_kg)
