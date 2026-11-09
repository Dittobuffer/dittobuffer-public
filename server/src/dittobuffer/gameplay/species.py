"""Read verified gameplay fields without reserializing opaque model templates."""
from functools import lru_cache
import gzip
import hashlib
import math
import json
from pathlib import Path
import re
import struct
from protocol.opaque import fields,read_varint

REFERENCE=json.loads(Path(__file__).with_name('pokemon-reference.json').read_text(encoding='utf8'))

def packed(data):
    values=[];offset=0
    while offset<len(data):
        value,offset=read_varint(data,offset);values.append(value)
    return values

@lru_cache(maxsize=2)
def catalog(path):
    """Stat-bearing defaults, including evidenced explicit visual identities."""
    variants=form_catalog(path);metadata=form_metadata(path)
    result={}
    for (species,form),rules in variants.items():
        normal=rules['template_id'].endswith('_NORMAL')
        simple=re.fullmatch(r'V\d{4}_POKEMON_[A-Z0-9]+',rules['template_id'])
        preferred=next((f for s,f in metadata if s==species),None)
        old=result.get(species)
        if old is None or normal or simple and not old['template_id'].endswith('_NORMAL') or form==preferred and not old['template_id'].endswith('_NORMAL'):
            result[species]=rules
    # M11.1 established exact default model/form identities. Legacy stat-only
    # templates sometimes use form zero even though the client default is an
    # explicit form. Apply the accepted choices only to those unset defaults;
    # do not infer arbitrary forms or rewrite persisted Pokemon records.
    defaults=json.loads(Path(__file__).with_name('pokemon-defaults.json').read_text(encoding='utf8'))
    if defaults['game_master_sha256']!=hashlib.sha256(path.read_bytes()).hexdigest():
        raise ValueError('Pokemon default mappings require the matching supplied Game Master')
    for entry in defaults['mappings']:
        species,form=entry['species'],entry['form']
        rules=variants.get((species,form))
        if not rules or rules['is_costume'] or result[species]['form'] not in (0,form):
            raise ValueError('Invalid evidenced gameplay default')
        result[species]=rules
    return result

@lru_cache(maxsize=2)
def form_metadata(path):
    result={}
    for number,kind,_,raw in fields(gzip.decompress(path.read_bytes())):
        if number!=2 or kind!=2:continue
        template={n:v for n,k,_,v in fields(raw)}
        if not template.get(1,b'').startswith(b'FORMS_') or 22 not in template:continue
        data={n:v for n,k,_,v in fields(template[22])};species=data.get(1,0)
        for n,k,_,value in fields(template[22]):
            if n!=2 or k!=2:continue
            form={a:b for a,c,_,b in fields(value)}
            result[species,form.get(1,0)]={'asset_index':form.get(2,0),
                'asset_suffix':form.get(3,b'').decode(),'is_costume':bool(form.get(4,0))}
    return result

@lru_cache(maxsize=2)
def form_catalog(path):
    """Every stat-bearing supplied form; model availability is audited separately.

    Tags are from this APK's PokemonSettings serializer, not the inaccurate
    exported schema. Unknown fields remain opaque in the delivered template.
    """
    result={};metadata=form_metadata(path)
    for number,kind,_,raw in fields(gzip.decompress(path.read_bytes())):
        if number!=2 or kind!=2:continue
        template={n:v for n,k,_,v in fields(raw)}
        name=template.get(1,b'').decode()
        match=re.fullmatch(r'V(\d{4})_POKEMON_(.+)',name)
        if not match or 2 not in template:continue
        data={n:v for n,k,_,v in fields(template[2])}
        if data.get(1)!=int(match[1]):continue
        if any(n not in data for n in (8,9,10,15,16)) or not data[9] or not data[10]:continue
        species=int(match[1])
        # PokemonSettings serializer 0x8905550: stats=8, moves=9/10,
        # height/weight=15/16, family=21, evolution branches=26, form=28.
        stats={n:v for n,k,_,v in fields(data[8])}
        evolutions=[];branches=[]
        for n,k,_,value in fields(template[2]):
            if n==26 and k==2:
                branch={a:b for a,c,_,b in fields(value)}
                branches.append({tag:(struct.unpack('<f',data)[0] if tag==4 and isinstance(data,bytes) else data.hex() if isinstance(data,bytes) else data) for tag,data in branch.items()})
                # Only ordinary candy evolutions are supported here. Reject
                # unknown/nonzero conditions too, rather than bypassing them.
                # Priority (12) and the alternate purified price (19) do not
                # constrain the normal, non-purified Pokemon generated here.
                if not any(value for tag,value in branch.items() if tag not in (1,3,5,12,19)):
                    evolutions.append({'species':branch[1],'candy':branch.get(3,data.get(22,0)),
                                       'form':branch.get(5,0)})
        form=data.get(28,0)
        encounter={n:v for n,k,_,v in fields(data.get(7,b''))}
        third={n:v for n,k,_,v in fields(data.get(36,b''))}
        f32=lambda value:struct.unpack('<f',value)[0] if isinstance(value,bytes) and len(value)==4 else 0
        result[species,form]={'name':match[2].removesuffix('_NORMAL'),'template_id':name,
            'species':species,'form':form,'family':data.get(21,species),
            'stamina':stats[1],'attack':stats[2],'defense':stats[3],
            'types':[data.get(4,0),data.get(5,0)],'class':data.get(14,0),
            'moves':(packed(data[9])[0],packed(data[10])[0]),
            'fast_moves':packed(data[9]),'charged_moves':packed(data[10]),
            'elite_fast_moves':packed(data.get(49,b'')), 'elite_charged_moves':packed(data.get(50,b'')),
            'third_move':{'stardust':third.get(1,0),'candy':third.get(2,0)},
            'capture_bonus_candy':encounter.get(10,0),'capture_bonus_dust':encounter.get(11,0),
            'base_capture_rate':f32(encounter.get(1)),'base_flee_rate':f32(encounter.get(2)),
            'buddy_distance_km':f32(data.get(23)),'egg_distance_km':f32(data.get(20)),
            'height_stddev':f32(data.get(18)),'weight_stddev':f32(data.get(19)),
            'height':struct.unpack('<f',data[15])[0],'weight':struct.unpack('<f',data[16])[0],
            'evolutions':evolutions,'evolution_branches':branches,**metadata.get((species,form),{'asset_index':0,'asset_suffix':'','is_costume':False})}
    return result

def rules_for(game,pokemon):
    key=pokemon.pokemon_id,pokemon.pokemon_display.form
    if key in game.forms:return game.forms[key]
    # Legacy records may predate an explicit display form. Do not rewrite them.
    if not key[1]:return game.species[key[0]]
    raise ValueError('Unknown species/form: '+str(key))

def choose_gender(game,species,rng):
    proportions=game.pokemon_genders.get(str(species),{})
    allowed=[(gender,proportions.get(key,0)) for key,gender in
        [('malePercent',1),('femalePercent',2),('genderlessPercent',3)] if proportions.get(key,0)>0]
    return rng.choices([v[0] for v in allowed],weights=[v[1] for v in allowed])[0] if allowed else 3

def pool_rules(game,entry):
    """Common explicit variant identity for wild/egg/raid/research definitions."""
    value={'species':entry} if type(entry) is int else entry
    if not isinstance(value,dict):raise ValueError('Pool entry must be a species ID or variant object')
    species=value.get('species',value.get('id'))
    if type(species) is not int or species not in game.species:raise ValueError('Unknown pool species')
    key=species,value.get('form',game.species[species]['form'])
    if key not in game.forms:raise ValueError('Unknown pool form')
    from dittobuffer.gameplay.costumes import validate as validate_costume
    validate_costume(game,species,key[1],value.get('costume',0))
    if 'size' in value:
        if value['size'] not in ('XXS','XS','M','XL','XXL') or not game.config.get('pokemon_sizes',{}).get('enabled'):
            raise ValueError('Explicit size requires enabled size rules and a valid class')
        if 'size_settings' not in game.forms[key]:raise ValueError('No reference size bounds for variant')
        from dittobuffer.gameplay.pokemon_sizes import bounds
        bounds(game.forms[key]['size_settings'],strict=True)
    if 'shiny_rate' in value and (type(value['shiny_rate']) not in (int,float) or not math.isfinite(value['shiny_rate']) or not 0<=value['shiny_rate']<=1):raise ValueError('Invalid pool shiny rate')
    if value.get('background'):
        from dittobuffer.gameplay.backgrounds import definitions
        if value['background'] not in {v['id'] for v in definitions(game.config)}:raise ValueError('Unknown pool background')
    if 'gender' in value:
        if type(value['gender']) is not int:raise ValueError('Gender must be an integer enum')
        name={1:'malePercent',2:'femalePercent',3:'genderlessPercent'}.get(value['gender'])
        if not name or not game.pokemon_genders.get(str(species),{}).get(name,0):raise ValueError('Gender incompatible with species')
    return game.forms[key]

def cp_multiplier(level):
    """Exact reference CP multipliers through level 50; hybrids interpolate."""
    table=REFERENCE['cp_multipliers']
    lower=max(1,min(50,int(level)))
    a=table[lower-1]
    b=table[min(49,lower)]
    return math.sqrt(a*a+(b*b-a*a)*(level-lower))

def multiplier(level,table=None):
    # The server's own level table, also delivered to the client in Game Master.
    lower=max(1,min(50,int(level)));fraction=level-lower
    a=table[lower-1] if table and lower<=len(table) else cp_multiplier(lower)
    b=table[lower] if table and lower<len(table) else cp_multiplier(lower+1)
    return math.sqrt(a*a+(b*b-a*a)*fraction)

def apply_stats(pokemon,rules,level,heal=False):
    old_max=pokemon.max_stamina
    cp=multiplier(level,rules.get('cp_multipliers'))
    pokemon.cp=max(10,math.floor((rules['attack']+pokemon.individual_attack)*
        math.sqrt(rules['defense']+pokemon.individual_defense)*
        math.sqrt(rules['stamina']+pokemon.individual_stamina)*cp*cp/10))
    pokemon.max_stamina=max(10,math.floor((rules['stamina']+pokemon.individual_stamina)*cp))
    pokemon.stamina=pokemon.max_stamina if heal else min(pokemon.max_stamina,pokemon.stamina+max(0,pokemon.max_stamina-old_max))
    pokemon.cp_multiplier=cp;pokemon.additional_cp_multiplier=0
    return pokemon

def make_pokemon(p,rules,species,level,ivs,shiny=False,gender=1,costume=0):
    out=p.PokemonProto(pokemon_id=species,individual_attack=ivs[0],individual_defense=ivs[1],
        individual_stamina=ivs[2],move1=rules['moves'][0],move2=rules['moves'][1],
        height_m=rules['height'],weight_kg=rules['weight'])
    out.pokemon_display.gender=gender
    out.pokemon_display.form=rules['form'];out.pokemon_display.shiny=shiny
    out.pokemon_display.costume=costume
    return apply_stats(out,rules,level,heal=True)
