"""Validated item mutations, called inside the existing RPC transaction."""
import math
from dittobuffer.gameplay.game import p,now_ms
from dittobuffer.core.management import owned,update_owned

POTIONS={101:(20,0),102:(50,0),103:(200,0),104:(0,1)}
REVIVES={201:0.5,202:1}

def quantity(db,player,item):
    row=db.execute('SELECT count FROM items WHERE player_id=? AND item=?',(player,item)).fetchone()
    return row['count'] if row else 0

def consume(db,player,item,count=1):
    return db.execute('UPDATE items SET count=count-? WHERE player_id=? AND item=? AND count>=?',
                      (count,player,item,count)).rowcount==1

def recycle(game,db,player,body):
    request=p.RecycleItemProto.FromString(body)
    if request.item not in {1,2,3,701,*POTIONS,*REVIVES}:
        return p.RecycleItemOutProto(result=3,new_count=quantity(db,player,request.item))
    if request.count<=0 or not consume(db,player,request.item,request.count):
        return p.RecycleItemOutProto(result=2,new_count=quantity(db,player,request.item))
    game.touch(db,player)
    return p.RecycleItemOutProto(result=1,new_count=quantity(db,player,request.item))

def heal(game,db,player,item,pokemon_id,revive):
    pokemon=owned(db,player,pokemon_id)
    if pokemon is None:return 2,0
    if pokemon.deployed_fort_id or pokemon.deployed_station_id:return 4,pokemon.stamina
    from dittobuffer.gameplay.gyms import battle_busy
    if battle_busy(db,player,[pokemon.id]):return 4,pokemon.stamina
    if pokemon.is_component or pokemon.is_fusion:return 5,pokemon.stamina
    allowed=item in (REVIVES if revive else POTIONS)
    healthy=pokemon.stamina>0 if revive else not 0<pokemon.stamina<pokemon.max_stamina
    if pokemon.is_egg or not allowed or healthy or not consume(db,player,item):return 3,pokemon.stamina
    if revive:pokemon.stamina=max(1,math.floor(pokemon.max_stamina*REVIVES[item]))
    else:
        amount,percent=POTIONS[item]
        pokemon.stamina=min(pokemon.max_stamina,pokemon.stamina+amount+math.floor(pokemon.max_stamina*percent))
    update_owned(game,db,player,pokemon)
    return 1,pokemon.stamina

def potion(game,db,player,body):
    request=p.UseItemPotionProto.FromString(body)
    result,stamina=heal(game,db,player,request.item,request.pokemon_id,False)
    return p.UseItemPotionOutProto(result=result,stamina=stamina)

def revive(game,db,player,body):
    request=p.UseItemReviveProto.FromString(body)
    result,stamina=heal(game,db,player,request.item,request.pokemon_id,True)
    return p.UseItemReviveOutProto(result=result,stamina=stamina)

def bulk_heal(game,db,player,body):
    request=p.UseItemBulkHealProto.FromString(body)
    if not request.pokemon_id or request.item not in {*POTIONS,*REVIVES}:
        return p.UseItemBulkHealOutProto(status=2,remaining_item_count=quantity(db,player,request.item))
    out=p.UseItemBulkHealOutProto(status=1)
    for pokemon_id in dict.fromkeys(request.pokemon_id):
        result,stamina=heal(game,db,player,request.item,pokemon_id,request.item in REVIVES)
        out.heal_results.add(result=result,pokemon_id=pokemon_id,stamina=stamina)
    out.remaining_item_count=quantity(db,player,request.item)
    return out

def active_berry(db,player,spawn):
    row=db.execute('SELECT item FROM encounter_items WHERE player_id=? AND spawn_id=?',(player,spawn)).fetchone()
    return row['item'] if row else 0

def probabilities(game,berry=0):
    out=p.CaptureProbabilityProto(reticle_difficulty_scale=1)
    out.pokeball_type.extend([1,2,3,4])
    out.capture_probability.extend([min(1,game.config['catch_probability']*ball*(1.5 if berry==701 else 1)) for ball in (1,1.5,2,255)])
    return out

def berry(game,db,player,body):
    request=p.UseItemEncounterProto.FromString(body)
    from dittobuffer.gameplay.special_encounters import capture_rules
    if capture_rules(db,player,request.encounter_id):return p.UseItemEncounterOutProto(status=4)
    row=db.execute('SELECT s.expires_ms,s.spawnpoint,e.state,d.deadline_ms,r.client_quest_id FROM encounters e JOIN spawns s ON s.id=e.spawn_id '
        'LEFT JOIN encounter_deadlines d ON d.player_id=e.player_id AND d.spawn_id=e.spawn_id '
        'LEFT JOIN research_encounters r ON r.player_id=e.player_id AND r.spawn_id=e.spawn_id '
        'WHERE e.player_id=? AND e.spawn_id=?',(player,request.encounter_id)).fetchone()
    # Native ordinary Research sends its visible parent quest ID, whereas wild
    # encounters send the canonical spawn point. Both identities remain bound
    # to this trainer and this exact encounter; arbitrary quest IDs are rejected.
    points={row['spawnpoint'],row['client_quest_id']} if row else set()
    if not row or request.spawn_point_guid not in points or row['state']!='active' or max(row['expires_ms'],row['deadline_ms'] or 0)<=now_ms():
        return p.UseItemEncounterOutProto(status=1)
    if request.item!=701:return p.UseItemEncounterOutProto(status=4)
    active=active_berry(db,player,request.encounter_id)
    if active:return p.UseItemEncounterOutProto(status=2,active_item=active,capture_probability=probabilities(game,active))
    if not consume(db,player,request.item):return p.UseItemEncounterOutProto(status=3)
    db.execute('INSERT INTO encounter_items VALUES(?,?,?) ON CONFLICT(player_id,spawn_id) DO UPDATE SET item=excluded.item',
               (player,request.encounter_id,request.item))
    game.touch(db,player)
    return p.UseItemEncounterOutProto(status=0,active_item=request.item,capture_probability=probabilities(game,request.item))
