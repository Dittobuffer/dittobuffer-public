"""Pinned compatible PvE balance, delivered identically to Android and combat.

The supplied prelogin snapshot omits MOVE/BATTLE/TYPE_EFFECTIVE templates.
The numeric definitions and provenance are in battle-moves.json. Only listed
moves are enabled for gym combat; unsupported parties fail before battle.
"""
from dittobuffer.gameplay.game import p
import json
from pathlib import Path

# id: (type, power, duration ms, energy delta, damage-window start ms)
PINNED=json.loads(Path(__file__).with_name('battle-moves.json').read_text(encoding='utf8'))
DEFINITIONS=PINNED['moves']
MOVES={int(ident):(m['type'],m['power'],m['durationMs'],m['energyDelta'],m['damageWindowStartMs']) for ident,m in DEFINITIONS.items()}
# Source arrays have exactly 18 entries: Normal is index 0, not 1.
def effectiveness(attack,defenses):
    import math
    return math.prod(PINNED['type_scalars'][str(attack)][kind-1] for kind in set(defenses)-{0})

# These three retained M7 timing choices are local server balance overrides.
# Other values are the pinned source, shared by server and Android.
LOCAL_BALANCE={'enemy_attack_interval':2,'attack_server_interval':.5,'round_duration_seconds':100}
OVERRIDES={'enemy_attack_interval':(.1,30),'attack_server_interval':(.1,5),'round_duration_seconds':(1,600),
           'same_type_attack_bonus_multiplier':(.1,5),'dodge_damage_reduction_percent':(0,1),'energy_delta_per_health_lost':(0,10)}

def settings(config=None):
    import math,re
    values={re.sub(r'(?<!^)(?=[A-Z])','_',key).lower():value for key,value in PINNED['battle_settings'].items()}
    values={key:value for key,value in values.items() if key in p.GymBattleSettingsProto.DESCRIPTOR.fields_by_name}
    custom=(config or {}).get('combat_balance',{})
    for key,value in custom.items():
        if key not in OVERRIDES or type(value) not in (int,float) or not math.isfinite(value) or not OVERRIDES[key][0]<=value<=OVERRIDES[key][1]:
            raise ValueError('Invalid custom combat balance: '+key)
    return p.GymBattleSettingsProto(**(values|LOCAL_BALANCE|custom))

def templates(config=None):
    result=[p.GameMasterClientTemplateProto(template_id='BATTLE_SETTINGS',battle_settings=settings(config))]
    # The client has no built-in fallback sequences. Without sys complete the
    # visual model stalls in Returning/Faint even when BattleFrame has switched.
    for name,sequence in PINNED['sequences'].items():
        result.append(p.GameMasterClientTemplateProto(template_id=name,
            move_sequence=p.MoveSequenceSettingsProto(sequence=sequence)))
    for move,(kind,power,duration,energy,window) in MOVES.items():
        m=DEFINITIONS[str(move)]
        result.append(p.GameMasterClientTemplateProto(template_id=m['template_id'],
            move=p.MoveSettingsProto(unique_id=move,animation_id=m['animationId'],type=kind,power=power,accuracy_chance=m['accuracyChance'],
                critical_chance=m['criticalChance'],trainer_level_min=m['trainerLevelMin'],trainer_level_max=m['trainerLevelMax'],stamina_loss_scalar=m['staminaLossScalar'],vfx_name=m['vfxName'],
                duration_ms=duration,damage_window_start_ms=window,damage_window_end_ms=m['damageWindowEndMs'],energy_delta=energy)))
    for kind in range(1,19):
        result.append(p.GameMasterClientTemplateProto(template_id='POKEMON_TYPE_'+p.HoloPokemonType.Name(kind).split('Type')[-1].upper(),
            type_effective=p.TypeEffectiveSettingsProto(attack_type=kind,attack_scalar=PINNED['type_scalars'][str(kind)])))
    return result
