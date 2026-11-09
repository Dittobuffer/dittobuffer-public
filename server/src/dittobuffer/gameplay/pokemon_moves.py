"""Form-aware TMs and second Charged Attacks in the existing RPC transaction."""
import secrets
from dittobuffer.gameplay.game import p
from dittobuffer.gameplay.species import rules_for
from dittobuffer.core.management import owned,update_owned,candy,add_candy
from dittobuffer.gameplay.items import consume
from dittobuffer.gameplay.battle_master import MOVES

def mutable(db,player,pokemon):
    from dittobuffer.gameplay.gyms import battle_busy
    # Shadow and purified Pokemon are still valid party members; only
    # explicitly unavailable states block TMs and second Charged Attacks.
    return not (pokemon.is_egg or pokemon.is_bad or pokemon.is_component or pokemon.is_fusion or
        pokemon.deployed_fort_id or pokemon.deployed_station_id or
        battle_busy(db,player,[pokemon.id]))

def reroll(game,db,player,body):
    request=p.UseItemMoveRerollProto.FromString(body)
    fail=lambda value:p.UseItemMoveRerollOutProto(result=value)
    if request.item not in (1201,1202,1203,1204):return fail(5)
    pokemon=owned(db,player,request.pokemon_id)
    if pokemon is None:return fail(2)
    if not mutable(db,player,pokemon):return fail(7)
    fast=request.item in (1201,1203);elite=request.item in (1203,1204)
    if request.target_special_move:return fail(9) # Unsupported special-item mechanic.
    if request.reroll_unlocked_move and (fast or not pokemon.move3):return fail(8)
    slot='move1' if fast else 'move3' if request.reroll_unlocked_move else 'move2'
    if pokemon.pokemon_display.alignment==1 and pokemon.move2==322 and slot=='move2' and getattr(game,'rocket_reference',None):
        from dittobuffer.gameplay.game import now_ms
        if not game.rocket['frustration_event_start_ms']<=now_ms()<game.rocket['frustration_event_end_ms']:return fail(7)
    rules=rules_for(game,pokemon)
    pool=set(rules['fast_moves' if fast else 'charged_moves'])
    if elite:pool.update(rules['elite_fast_moves' if fast else 'elite_charged_moves'])
    # Do not teach a move the shared PvE engine cannot actually process.
    pool.intersection_update(MOVES)
    existing={pokemon.move1} if fast else {pokemon.move2,pokemon.move3}
    choices=sorted(pool-existing)
    if elite:
        if not request.target_elite_move or request.target_elite_move not in choices:return fail(10)
        target=request.target_elite_move
    else:
        if request.target_elite_move:return fail(10)
        if not choices:return fail(3)
        target=secrets.choice(choices)
    if not consume(db,player,request.item):return fail(6)
    setattr(pokemon,slot,target);update_owned(game,db,player,pokemon)
    return p.UseItemMoveRerollOutProto(result=1,updated_pokemon=pokemon)

def unlock(game,db,player,body):
    import math
    request=p.UnlockPokemonMoveProto.FromString(body)
    fail=lambda value:p.UnlockPokemonMoveOutProto(result=value)
    pokemon=owned(db,player,request.pokemon_id)
    if pokemon is None:return fail(2)
    if not mutable(db,player,pokemon):return fail(3)
    if pokemon.move3:return fail(4)
    rules=rules_for(game,pokemon);base=rules['third_move']
    from dittobuffer.gameplay.shadow import shared_move_multipliers
    shadow_dust,shadow_candy,purified_dust,purified_candy=shared_move_multipliers(game.config)
    dust_mult,candy_mult=1.0,1.0
    if pokemon.pokemon_display.alignment==1:dust_mult,candy_mult=shadow_dust,shadow_candy
    elif pokemon.pokemon_display.alignment==2:dust_mult,candy_mult=purified_dust,purified_candy
    cost={'stardust':math.ceil(base['stardust']*dust_mult),'candy':math.ceil(base['candy']*candy_mult)}
    if getattr(game,'rocket_reference',None):
        from dittobuffer.gameplay.rocket_shadow import costs
        cost['stardust'],cost['candy']=costs(game,pokemon,base['stardust'],base['candy'],third=True)
    pool=sorted(set(rules['charged_moves']).intersection(MOVES)-{pokemon.move2})
    if not pool or cost['candy']<=0 or cost['stardust']<=0:return fail(3)
    if candy(db,player,rules['family'])<cost['candy'] or game.player(db,player)['stardust']<cost['stardust']:return fail(5)
    add_candy(db,player,rules['family'],-cost['candy'])
    db.execute('UPDATE players SET stardust=stardust-? WHERE id=?',(cost['stardust'],player))
    pokemon.move3=secrets.choice(pool);update_owned(game,db,player,pokemon)
    return p.UnlockPokemonMoveOutProto(result=1,unlocked_pokemon=pokemon)
