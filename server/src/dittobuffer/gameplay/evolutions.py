"""Evaluate verified permanent evolution conditions before consuming anything."""
from dittobuffer.gameplay.items import quantity

def select(game,db,player,pokemon,rules,request):
    candidates=[];item_failure=False
    for branch in rules['evolution_branches']:
        # ARM64 0x89E7EDC. Unknown conditions, temporary evolution, lure,
        # buddy identity/tasks, orientation and astronomical windows fail shut.
        # No local checkbox is accepted as evidence of those gameplay systems.
        supported={1,2,3,4,5,6,12,13,19,22,23,25}
        if any(value for tag,value in branch.items() if tag not in supported):continue
        if branch.get(25):continue
        if branch.get(6) and branch[6]!=pokemon.pokemon_display.gender:continue
        if branch.get(4,0)>pokemon.buddy_km_walked:continue
        if branch.get(23) and branch[23] not in (pokemon.move1,pokemon.move2,pokemon.move3):continue
        species=branch.get(1,0);form=branch.get(5,0)
        if request.target_pokemon_id and request.target_pokemon_id!=species:continue
        if request.target_pokemon_form and request.target_pokemon_form!=form:continue
        item=branch.get(2,0)
        if item!=request.evolution_item_requirement:
            item_failure|=bool(item);continue
        # Supplied APK writer 0x89E7EDC uses field 19 for CandyCostPurified
        # (the old exporter labels it dusk). Normal Pokemon keep field 3.
        cost=branch.get(19,branch.get(3,0)) if pokemon.pokemon_display.alignment==2 else branch.get(3,0)
        if branch.get(13) and pokemon.traded_time_ms:cost=0
        candidates.append({'species':species,'form':form,'candy':cost,'item':item,
            'item_count':branch.get(22,1) or 1})
    # Ambiguous/random/stat-selected families require a reviewed selector;
    # choosing the first GM branch would silently implement wrong mechanics.
    if len(candidates)!=1:return None,6 if item_failure else 4
    selected=candidates[0]
    if selected['item'] and not request.preview and quantity(db,player,selected['item'])<selected['item_count']:return None,6
    return selected,1
