"""Shared authoritative PvE damage, independent of gym/raid persistence."""
import math
from dittobuffer.gameplay.battle_master import MOVES,effectiveness,settings

def damage_value(attacker,defender,species,move,config=None,forms=None,attack_multiplier=1):
    kind,power,*_=MOVES[move]
    if power<=0:return 0
    from dittobuffer.gameplay.mega import combat_rules
    resolve=lambda pokemon:combat_rules(pokemon,(forms or {}).get((pokemon.pokemon_id,pokemon.pokemon_display.form),species[pokemon.pokemon_id]))
    a=resolve(attacker);d=resolve(defender)
    attack=(a['attack']+attacker.individual_attack)*attacker.cp_multiplier
    defense=(d['defense']+defender.individual_defense)*defender.cp_multiplier
    stab=settings(config).same_type_attack_bonus_multiplier if kind in a['types'] else 1
    return max(1,math.floor(.5*power*attack/max(1,defense)*stab*effectiveness(kind,d['types'])*attack_multiplier)+1)
