"""Basic ownership and resource rules inside the existing RPC transaction."""
import math
import unicodedata
from dittobuffer.gameplay.game import p
from dittobuffer.gameplay.species import apply_stats,multiplier,rules_for

def owned(db,player,pokemon_id):
    row=db.execute('SELECT data FROM pokemon WHERE id=? AND player_id=?',(pokemon_id,player)).fetchone()
    return p.PokemonProto.FromString(row['data']) if row else None

def candy(db,player,family):
    row=db.execute('SELECT count FROM candy WHERE player_id=? AND family=?',(player,family)).fetchone()
    return row['count'] if row else 0

def add_candy(db,player,family,count):
    db.execute('INSERT INTO candy VALUES(?,?,?) ON CONFLICT(player_id,family) DO UPDATE SET count=count+excluded.count',(player,family,count))

def xl_candy(db,player,family):
    row=db.execute('SELECT count FROM mega_catch_xl WHERE player_id=? AND family=?',(player,family)).fetchone()
    return row['count'] if row else 0

def add_xl_candy(db,player,family,count):
    # SQLite evaluates the CHECK on the proposed row before UPSERT conflict
    # resolution, so deductions must be a plain UPDATE rather than a negative INSERT.
    if count<0:
        db.execute('UPDATE mega_catch_xl SET count=MAX(0,count+?) WHERE player_id=? AND family=?',(count,player,family))
    else:
        db.execute('INSERT INTO mega_catch_xl VALUES(?,?,?) ON CONFLICT(player_id,family) DO UPDATE SET count=count+excluded.count',(player,family,count))

def xl_catch_chance(config,level):
    """Catch XL candy chance rises with the wild Pokémon's level; needs trainer level 31+."""
    bands=config.get('xl_catch_rates') or [[1,0],[11,.1],[21,.25],[31,.5],[36,.75],[41,.9]]
    chance=0
    for minimum,rate in bands:
        if level>=minimum:chance=rate
    return max(0,min(1,chance))

def update_owned(game,db,player,pokemon):
    db.execute('UPDATE pokemon SET data=? WHERE id=? AND player_id=?',(pokemon.SerializeToString(),pokemon.id,player))
    game.touch(db,player)

def favorite(game,db,player,body):
    request=p.SetFavoritePokemonProto.FromString(body)
    pokemon=owned(db,player,request.pokemon_id)
    if pokemon is None:return p.SetFavoritePokemonOutProto(result=2)
    if pokemon.is_egg:return p.SetFavoritePokemonOutProto(result=3)
    if pokemon.favorite!=request.is_favorite:
        pokemon.favorite=request.is_favorite;update_owned(game,db,player,pokemon)
    return p.SetFavoritePokemonOutProto(result=1)

def nickname(game,db,player,body):
    request=p.NicknamePokemonProto.FromString(body)
    pokemon=owned(db,player,request.pokemon_id)
    if pokemon is None:return p.NicknamePokemonOutProto(result=3)
    if pokemon.is_egg:return p.NicknamePokemonOutProto(result=4)
    name=unicodedata.normalize('NFC',request.nickname.strip())
    if len(name)>12 or any(unicodedata.category(c).startswith('C') for c in name):
        return p.NicknamePokemonOutProto(result=2)
    if pokemon.nickname!=name:
        pokemon.nickname=name;update_owned(game,db,player,pokemon)
    return p.NicknamePokemonOutProto(result=1)

def level_of(db,pokemon):
    row=db.execute('SELECT level FROM pokemon_levels WHERE pokemon_id=?',(pokemon.id,)).fetchone()
    if row:return row['level']
    row=db.execute('SELECT a.level FROM pokemon o JOIN spawn_attributes a ON a.spawn_id=o.spawn_id WHERE o.id=?',(pokemon.id,)).fetchone()
    if row:return row['level']
    # Milestone-2 records predate explicit level storage.
    cp=pokemon.cp_multiplier+pokemon.additional_cp_multiplier
    return min((1+i/2 for i in range(99)),key=lambda level:abs(multiplier(level)-cp))

def save(game,db,player,pokemon,level):
    db.execute('UPDATE pokemon SET species=?,data=? WHERE id=? AND player_id=?',
        (pokemon.pokemon_id,pokemon.SerializeToString(),pokemon.id,player))
    db.execute('INSERT INTO pokemon_levels VALUES(?,?) ON CONFLICT(pokemon_id) DO UPDATE SET level=excluded.level',(pokemon.id,level))
    game.touch(db,player)

def release(game,db,player,body):
    request=p.ReleasePokemonProto.FromString(body)
    ids=list(dict.fromkeys(([request.pokemon_id] if request.pokemon_id else [])+list(request.pokemon_ids)))
    if not ids:return p.ReleasePokemonOutProto(status=3)
    values=[owned(db,player,pokemon_id) for pokemon_id in ids]
    if any(value is None or value.favorite for value in values):return p.ReleasePokemonOutProto(status=3)
    if any(value.deployed_fort_id or value.deployed_station_id for value in values):return p.ReleasePokemonOutProto(status=2)
    from dittobuffer.gameplay.mega import active as active_mega
    if any(active_mega(db,value.id) for value in values):return p.ReleasePokemonOutProto(status=2)
    if db.execute('SELECT 1 FROM buddy_active WHERE player_id=? AND pokemon_id IN ('+','.join('?' for _ in ids)+')',(player,*ids)).fetchone():return p.ReleasePokemonOutProto(status=2)
    from dittobuffer.gameplay.gyms import battle_busy
    if battle_busy(db,player,ids):return p.ReleasePokemonOutProto(status=2)
    if any(value.is_egg for value in values):return p.ReleasePokemonOutProto(status=4)
    from dittobuffer.gameplay.trading import locked
    if any(locked(game,db,value.id) for value in values):return p.ReleasePokemonOutProto(status=2)
    from dittobuffer.gameplay.pvp import locked as pvp_locked
    if any(pvp_locked(game,db,player,value.id) for value in values):return p.ReleasePokemonOutProto(status=2)
    if any(value.is_fusion or value.is_component for value in values):return p.ReleasePokemonOutProto(status=6)
    if len(ids)>1 and any(value.pokemon_display.shiny or value.pokemon_display.costume or
        value.pokemon_display.location_card.location_card or rules_for(game,value)['is_costume'] or
        rules_for(game,value)['class'] in (1,2,3) for value in values):return p.ReleasePokemonOutProto(status=3)
    game.touch(db,player);revision=game.player(db,player)['revision']
    transfer_amount=math.floor(getattr(game,'liveops_effective',{}).get('bonuses',{}).get('transfer_candy',1))
    for value in values:
        add_candy(db,player,game.species[value.pokemon_id]['family'],transfer_amount)
        db.execute('INSERT INTO pokemon_tombstones VALUES(?,?,?)',(player,value.id,revision))
        db.execute('DELETE FROM pokemon WHERE id=? AND player_id=?',(value.id,player))
    from dittobuffer.gameplay.research import progress
    progress(game,db,player,'transfer',len(ids))
    return p.ReleasePokemonOutProto(status=1,candy_awarded=len(ids)*transfer_amount)

def upgrade(game,db,player,body):
    request=p.UpgradePokemonProto.FromString(body)
    pokemon=owned(db,player,request.pokemon_id)
    if pokemon is None:return p.UpgradePokemonOutProto(result=2)
    if pokemon.deployed_fort_id or pokemon.deployed_station_id:return p.UpgradePokemonOutProto(result=5)
    from dittobuffer.gameplay.mega import active as active_mega
    if active_mega(db,pokemon.id):return p.UpgradePokemonOutProto(result=5)
    from dittobuffer.gameplay.gyms import battle_busy
    if battle_busy(db,player,[pokemon.id]):return p.UpgradePokemonOutProto(result=5)
    if pokemon.is_egg or pokemon.is_component or pokemon.is_fusion:return p.UpgradePokemonOutProto(result=4)
    current=level_of(db,pokemon);steps=request.number_of_upgrades or 1
    rules=rules_for(game,pokemon);family=rules['family']
    # Databases written by the former level formula can carry a CP multiplier
    # (and therefore a CP) that does not match their stored level. Inventory
    # serialisation already projects the corrected value, so the client sends
    # the repaired CP in pokemon_current_cp. Reconcile the stored record here
    # too; otherwise an otherwise-valid level-40 upgrade is rejected as a
    # duplicate request and surfaces natively as error 71.
    expected=multiplier(current,rules.get('cp_multipliers'))
    if abs(pokemon.cp_multiplier-expected)>.0005:
        apply_stats(pokemon,rules,current)
    if request.pokemon_current_cp and request.pokemon_current_cp!=pokemon.cp:return p.UpgradePokemonOutProto(result=6)
    from dittobuffer.gameplay.progression import bounds
    player_level=bounds(game.config,game.player(db,player)['xp'])[0]
    cap=min(50,player_level+game.config.get('power_up_levels_above_player',10))
    if not 1<=steps<=100 or current+steps/2>cap:return p.UpgradePokemonOutProto(result=4)
    dust=game.config.get('power_up_stardust_cost',100);cost=game.config.get('power_up_candy_cost',1)
    xl_cost=game.config.get('power_up_xl_candy_cost',10)
    if pokemon.is_lucky:dust=math.ceil(dust/2)
    from dittobuffer.gameplay.rocket_shadow import costs as shadow_costs
    dust,cost=shadow_costs(game,pokemon,dust,cost)
    target=current+steps/2
    # Levels 1-40 consume regular candy; every half level above 40 consumes XL candy.
    normal_steps=max(0,math.ceil((min(target,40)-current)*2))
    xl_steps=max(0,steps-normal_steps)
    if not request.preview and (game.player(db,player)['stardust']<dust*steps or candy(db,player,family)<cost*normal_steps
        or xl_candy(db,player,family)<xl_cost*xl_steps):
        return p.UpgradePokemonOutProto(result=3)
    out=p.UpgradePokemonOutProto(result=1)
    upgraded=p.PokemonProto();upgraded.CopyFrom(pokemon)
    apply_stats(upgraded,rules,target)
    upgraded.num_upgrades+=steps
    out.upgraded_pokemon.CopyFrom(upgraded)
    if target<cap:
        out.next_upgraded_pokemon.CopyFrom(upgraded)
        apply_stats(out.next_upgraded_pokemon,rules,target+0.5)
    for count in range(1,math.floor((cap-current)*2)+1):
        preview=p.PokemonProto();preview.CopyFrom(pokemon)
        level=current+count/2
        apply_stats(preview,rules,level)
        normal=max(0,math.ceil((min(level,40)-current)*2))
        out.bulk_upgrades_cost_table.add(number_of_upgrades=count,pokemon_level=int(level),
            pokemon_cp=preview.cp,total_stardust_cost=dust*count,total_candy_cost=cost*normal,
            total_xl_candy_cost=xl_cost*max(0,count-normal),total_cp_multiplier=preview.cp_multiplier)
    if not request.preview:
        add_candy(db,player,family,-cost*normal_steps)
        if xl_steps:add_xl_candy(db,player,family,-xl_cost*xl_steps)
        db.execute('UPDATE players SET stardust=stardust-? WHERE id=?',(dust*steps,player))
        save(game,db,player,upgraded,target)
        from dittobuffer.gameplay.research import progress
        progress(game,db,player,'power_up',steps,context={'species':upgraded.pokemon_id,'pokemon_id':upgraded.id,'level':target,'form':upgraded.pokemon_display.form})
    return out

def _fixed64_field(data,number):
    from protocol.opaque import fields
    for tag,kind,_,value in fields(data):
        if tag==number and kind==1:return int.from_bytes(value,'little')
    return None


def purify(game,db,player,body):
    request=p.PurifyPokemonProto.FromString(body)
    # The client writes PurifyPokemonProto.pokemon_id as fixed64 even though the
    # supplied schema declares uint64. The generated parser therefore leaves
    # the field unset (and ignores the unknown wire type); read the wire field
    # directly so an existing shadow Pokémon is found.
    pokemon_id=request.pokemon_id
    if not pokemon_id:
        pokemon_id=_fixed64_field(body,1)
    fail=lambda value:p.PurifyPokemonOutProto(status=p.PurifyPokemonOutProto.Status.Value(value))
    pokemon=owned(db,player,pokemon_id)
    if pokemon is None:return fail('Status_ErrorPokemonNotFound')
    if pokemon.pokemon_display.alignment!=1:return fail('Status_ErrorPokemonNotShadow')
    if pokemon.deployed_fort_id or pokemon.deployed_station_id:return fail('Status_ErrorPokemonDeployed')
    from dittobuffer.gameplay.mega import active as active_mega
    if active_mega(db,pokemon.id):return fail('Status_ErrorPokemonDeployed')
    from dittobuffer.gameplay.gyms import battle_busy
    if battle_busy(db,player,[pokemon.id]):return fail('Status_ErrorPokemonDeployed')
    dust=max(0,int(game.config.get('purify_stardust_cost',1000)))
    candy_cost=max(0,int(game.config.get('purify_candy_cost',25)))
    rules=rules_for(game,pokemon);family=rules['family']
    if game.player(db,player)['stardust']<dust or candy(db,player,family)<candy_cost:
        return fail('Status_ErrorInsufficientFunds')
    level=level_of(db,pokemon)
    purified=p.PokemonProto();purified.CopyFrom(pokemon)
    purified.pokemon_display.alignment=2
    # Purification raises each IV by two, capped at the legal maximum.
    purified.individual_attack=min(15,purified.individual_attack+2)
    purified.individual_defense=min(15,purified.individual_defense+2)
    purified.individual_stamina=min(15,purified.individual_stamina+2)
    # Frustration becomes the purified-exclusive Return.  Shadow records made
    # by this server can carry a normal charged move instead, so only replace
    # the actual shadow move and leave a legitimate second attack untouched.
    if purified.move2==322:
        purified.move2=323
        purified.move2_is_purified_exclusive=True
    else:
        purified.move2_is_purified_exclusive=False
    apply_stats(purified,rules,level,heal=True)
    db.execute('UPDATE players SET stardust=stardust-? WHERE id=?',(dust,player))
    add_candy(db,player,family,-candy_cost)
    save(game,db,player,purified,level)
    from dittobuffer.gameplay.pokedex import record
    record(game,db,player,'purify:'+str(purified.id)+':'+str(purified.pokemon_id)+':'+str(purified.pokemon_display.form),purified,True)
    from dittobuffer.gameplay.trainer import event
    event(game,db,player,'purify:'+str(purified.id),'purify',dict(pokemon=purified.SerializeToString().hex()))
    return p.PurifyPokemonOutProto(status=p.PurifyPokemonOutProto.Status.Value('Status_Success'),purified_pokemon=purified)

def evolve(game,db,player,body):
    request=p.EvolvePokemonProto.FromString(body)
    pokemon=owned(db,player,request.pokemon_id)
    if pokemon is None:return p.EvolvePokemonOutProto(result=2)
    if pokemon.deployed_fort_id or pokemon.deployed_station_id:return p.EvolvePokemonOutProto(result=5)
    from dittobuffer.gameplay.mega import active as active_mega
    if active_mega(db,pokemon.id):return p.EvolvePokemonOutProto(result=5)
    from dittobuffer.gameplay.gyms import battle_busy
    if battle_busy(db,player,[pokemon.id]):return p.EvolvePokemonOutProto(result=5)
    if pokemon.is_egg or pokemon.is_component or pokemon.is_fusion or request.use_special:return p.EvolvePokemonOutProto(result=4)
    rules=rules_for(game,pokemon)
    # Renderable costume mappings do not establish an evolution mapping.
    # The available final stages have no ordinary branch; bow Jigglypuff's
    # Wigglytuff attachment is absent. Never strip a costume into a normal target.
    if pokemon.pokemon_display.costume or rules['is_costume']:
        return p.EvolvePokemonOutProto(result=4)
    from dittobuffer.gameplay.evolutions import select
    branch,status=select(game,db,player,pokemon,rules,request)
    if not branch:return p.EvolvePokemonOutProto(result=status)
    if not branch or branch['species'] not in game.species:return p.EvolvePokemonOutProto(result=4)
    if not request.preview and candy(db,player,rules['family'])<branch['candy']:return p.EvolvePokemonOutProto(result=3)
    level=level_of(db,pokemon);target=game.forms.get((branch['species'],branch['form']))
    if target is None and not branch['form']:target=game.species[branch['species']]
    if target is None:return p.EvolvePokemonOutProto(result=4)
    evolved=p.PokemonProto();evolved.CopyFrom(pokemon)
    evolved.pokemon_id=branch['species'];evolved.pokemon_display.form=target['form']
    if pokemon.pokemon_display.alignment==1 and getattr(game,'rocket_reference',None):
        from dittobuffer.gameplay.rocket_shadow import rule
        if rule(game,evolved) is None:return p.EvolvePokemonOutProto(result=4)
    evolved.move1,evolved.move2=target['moves'];evolved.move3=0
    if pokemon.pokemon_display.alignment and pokemon.move2 in (322,323):evolved.move2=pokemon.move2
    if pokemon.move3:
        from dittobuffer.gameplay.battle_master import MOVES
        choices=sorted(set(target['charged_moves']).intersection(MOVES)-{evolved.move2})
        if not choices:return p.EvolvePokemonOutProto(result=4)
        evolved.move3=choices[0]
    evolved.height_m=target['height'];evolved.weight_kg=target['weight']
    from dittobuffer.gameplay.pokemon_sizes import evolve as evolve_size
    evolve_size(rules,target,pokemon,evolved)
    apply_stats(evolved,target,level)
    out=p.EvolvePokemonOutProto(result=1,evolved_pokemon=evolved)
    out.preview.default_cp_range.min_cp=evolved.cp
    out.preview.default_cp_range.max_cp=evolved.cp
    out.preview.buddy_boosted_cp_range.CopyFrom(out.preview.default_cp_range)
    out.preview.evolving_pokemon_default_cp=pokemon.cp
    out.preview.evolving_pokemon_buddy_boosted_cp=pokemon.cp
    if not request.preview:
        if branch.get('item'):
            from dittobuffer.gameplay.items import consume
            if not consume(db,player,branch['item'],branch['item_count']):return p.EvolvePokemonOutProto(result=6)
        add_candy(db,player,rules['family'],1-branch['candy'])
        out.candy_awarded=1;out.exp_awarded=game.config.get('evolution_xp',500)
        db.execute('UPDATE players SET xp=xp+? WHERE id=?',(out.exp_awarded,player))
        save(game,db,player,evolved,level)
        from dittobuffer.gameplay.pokedex import record
        record(game,db,player,'evolve:'+str(evolved.id)+':'+str(evolved.pokemon_id)+':'+str(evolved.pokemon_display.form),evolved,True)
        from dittobuffer.gameplay.trainer import event
        event(game,db,player,'evolve:'+str(evolved.id)+':'+str(evolved.pokemon_id)+':'+str(evolved.pokemon_display.form),'evolve',dict(pokemon=evolved.SerializeToString().hex(),xp=out.exp_awarded))
    return out
