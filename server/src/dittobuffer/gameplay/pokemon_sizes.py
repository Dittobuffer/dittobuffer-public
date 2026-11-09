"""Pinned height boundaries; explicitly configurable custom size distribution.

Class comparisons match supplied APK PokemonProto.Size/jef 0x88E6118.
No existing owned Pokemon are resized when enabling these rules.
"""
import math

CLASSES=('XXS','XS','M','XL','XXL')
DEFAULT={'XXS':1,'XS':15,'M':68,'XL':15,'XXL':1}


def validate(config):
    cfg=config.get('pokemon_sizes',{})
    if type(cfg.get('enabled',False)) is not bool:raise ValueError('Invalid size enabled flag')
    weights=cfg.get('weights',DEFAULT)
    if set(weights)!=set(CLASSES) or any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in weights.values()) or sum(weights.values())<=0:
        raise ValueError('Invalid custom size distribution')


def bounds(settings,strict=False):
    values=[settings[k] for k in ('xxsLowerBound','xsLowerBound','mLowerBound','mUpperBound','xlUpperBound','xxlUpperBound')]
    if any(not math.isfinite(v) or v<=0 for v in values) or values!=sorted(values) or strict and len(set(values))!=6:
        raise ValueError('Invalid reference size boundaries')
    return values


def classify(height, settings):
    if not math.isfinite(height) or height<=0:raise ValueError('Invalid Pokemon height')
    _,xs,low,high,xl,_=bounds(settings)
    if height>xl:return 'XXL'
    if height>high:return 'XL'
    if height<xs:return 'XXS'
    if height<low:return 'XS'
    return 'M'


def assign(game, pokemon, entry, rng):
    if not game.config.get('pokemon_sizes',{}).get('enabled'):return
    from dittobuffer.gameplay.species import rules_for
    rules=rules_for(game,pokemon);settings=rules.get('size_settings')
    if not settings:return
    value=entry if isinstance(entry,dict) else {}
    # Pumpkaboo/Gourgeist encode fixed sizes as separate forms, with empty
    # classes. Do not randomize those forms across nonexistent size intervals.
    if len(set(bounds(settings)))!=6:
        if value.get('size'):raise ValueError('Form has fixed size intervals')
        return
    weights=game.config['pokemon_sizes'].get('weights',DEFAULT)
    size=value.get('size') or rng.choices(CLASSES,weights=[weights[k] for k in CLASSES])[0]
    limits=bounds(settings);index=CLASSES.index(size)
    # Keep away from class edges so protobuf float rounding cannot change class.
    fraction=rng.uniform(.01,.99)
    pokemon.height_m=limits[index]+fraction*(limits[index+1]-limits[index])
    # Explicit custom positive mass relationship, not a claimed official RNG.
    pokemon.weight_kg=max(.01,rules['weight']*(pokemon.height_m/rules['height'])**2*rng.uniform(.9,1.1))


def evolve(source,target,before,after):
    if not source.get('size_settings') or not target.get('size_settings'):return
    size=classify(before.height_m,source['size_settings']);index=CLASSES.index(size)
    a,b=bounds(source['size_settings']),bounds(target['size_settings'])
    if a[index+1]<=a[index] or b[index+1]<=b[index]:return
    fraction=max(.01,min(.99,(before.height_m-a[index])/(a[index+1]-a[index])))
    after.height_m=b[index]+fraction*(b[index+1]-b[index])
    after.weight_kg=max(.01,before.weight_kg/source['weight']*target['weight'])
