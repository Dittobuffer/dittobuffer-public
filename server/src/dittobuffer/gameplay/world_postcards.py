"""Reviewed virtual city origins; independent of world forts and real senders."""
import json,math,random,secrets
from functools import lru_cache
from dittobuffer.gameplay.game import ROOT,p,now_ms

REGIONS=tuple(v for v in p.VivillonRegion.keys() if v not in ('VivillonRegion_VIVILLON_REGION_UNKNOWN','VivillonRegion_VIVILLON_REGION_FANCY','VivillonRegion_VIVILLON_REGION_POKEBALL'))
PATTERNS={name.removeprefix('VivillonRegion_VIVILLON_REGION_'):p.VivillonRegion.Value(name) for name in REGIONS}
SCHEMA='''CREATE TABLE IF NOT EXISTS world_postcard_gifts(gift INTEGER PRIMARY KEY REFERENCES social_gifts(id),source_id TEXT NOT NULL,origin TEXT NOT NULL);'''

@lru_cache(maxsize=1)
def registry():
    document=json.loads((ROOT/'src/dittobuffer/gameplay/world-postcards.json').read_text(encoding='utf8'))
    rows=document['origins'];seen=set()
    for row in rows:
        if row['id'] in seen or not row['id'].startswith('world-postcard:ne-'):raise ValueError('Duplicate/invalid virtual origin ID')
        seen.add(row['id'])
        if row['origin_type']!='VIRTUAL_WORLD_POSTCARD' or type(row['enabled']) is not bool:raise ValueError('Invalid origin kind/enable flag')
        if not all(type(row[k]) in (int,float) and math.isfinite(row[k]) for k in ('lat','lng')) or not -85<=row['lat']<=85 or not -180<=row['lng']<=180:raise ValueError('Invalid virtual origin coordinate')
        if row['region'] not in PATTERNS or not row['name'] or not row['country'] or len(row['country_code'])!=3:raise ValueError('Invalid reviewed virtual origin')
        if row['classification']['status']!='REVIEWED_CITY_ANCHOR' or row['provenance']['license']!='Public domain':raise ValueError('Origin needs reviewed classification and licensed provenance')
    if {v['region'] for v in rows if v['enabled']}!=set(PATTERNS):raise ValueError('All 18 reviewed patterns must be available')
    return document

def settings(game):
    cfg=game.routes.get('mateo',{}).get('world_postcards',{})
    if type(cfg.get('enabled',False)) is not bool:raise ValueError('Invalid worldwide Postcard enabled flag')
    if cfg.get('enabled'):
        registry()
        if not game.config.get('vivillon',{}).get('enabled'):raise ValueError('Worldwide regional Postcards require enabled Vivillon progression')
        if cfg.get('selection_mode','RANDOM_WORLD') not in ('RANDOM_WORLD','MISSING_REGION_BIAS','UNCOLLECTED_PATTERN_BIAS','STRICT_RANDOM'):raise ValueError('Invalid worldwide selection mode')
        for pattern,value in cfg.get('region_weights',{}).items():
            if pattern not in PATTERNS or type(value) not in (int,float) or not math.isfinite(value) or not 0<value<=10000:raise ValueError('Invalid region weight')
        for key in ('missing_multiplier','uncollected_multiplier'):
            value=cfg.get(key,1.25)
            if type(value) not in (int,float) or not math.isfinite(value) or not 1<=value<=10:raise ValueError('Invalid collection bias')
    return cfg

def origin(db,gift):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='world_postcard_gifts'").fetchone():return None
    row=db.execute('SELECT origin FROM world_postcard_gifts WHERE gift=?',(gift,)).fetchone()
    return json.loads(row[0]) if row else None

def distribution(game,db,player):
    cfg=settings(game);mode=cfg.get('selection_mode','RANDOM_WORLD')
    weights={key:float(cfg.get('region_weights',{}).get(key,1)) for key in PATTERNS}
    if mode in ('MISSING_REGION_BIAS','RANDOM_WORLD') and (mode=='MISSING_REGION_BIAS' or cfg.get('prefer_missing',False)):
        unlocked={row[0] for row in db.execute('SELECT region FROM vivillon_regions WHERE player=? AND rank>0',(player,))}
        for key in weights:
            if PATTERNS[key] not in unlocked:weights[key]*=cfg.get('missing_multiplier',1.25)
    if mode=='UNCOLLECTED_PATTERN_BIAS':
        from dittobuffer.gameplay.vivillon import form_for
        # Pokedex observations store exact appearance columns, not Pokemon blobs.
        forms={row[0] for row in db.execute('SELECT form FROM pokedex_observations WHERE player_id=? AND species=666 AND caught=1',(player,))}
        for key in weights:
            if form_for(PATTERNS[key],666) not in forms:weights[key]*=cfg.get('uncollected_multiplier',1.25)
    return weights

def choose(game,db,player,rng=None):
    if not settings(game).get('enabled'):raise ValueError('Worldwide Postcards disabled')
    rng=rng or random.SystemRandom();weights=distribution(game,db,player)
    pattern=rng.choices(list(weights),weights=list(weights.values()),k=1)[0]
    return dict(rng.choice([v for v in registry()['origins'] if v['enabled'] and v['region']==pattern]))

def create(game,db,player,rng=None):
    selected=choose(game,db,player,rng);gift=secrets.randbits(62) or 1
    while db.execute('SELECT 1 FROM social_gifts WHERE id=?',(gift,)).fetchone():gift=secrets.randbits(62) or 1
    # Owner denotes the receiving account's private record, never an invented
    # participant. Public Gift/Postcard serializers explicitly omit sender IDs.
    name=f'Virtual Postcard: {selected["name"]}, {selected["country"]}'
    db.execute('INSERT INTO social_gifts(id,owner,fort_id,name,image,lat,lng,created_ms) VALUES(?,?,?,?,?,?,?,?)',(gift,player,selected['id'],name,'http://127.0.0.1:8080/assets/world-postcard.png',selected['lat'],selected['lng'],now_ms()))
    db.execute('INSERT INTO world_postcard_gifts VALUES(?,?,?)',(gift,selected['id'],json.dumps(selected,sort_keys=True)))
    return db.execute('SELECT *,? AS source_route FROM social_gifts WHERE id=?',('',gift)).fetchone()

def anonymize(value):
    for name in ('sender_id','sender_nia_id','sender_nia_account_id','sender_codename'):
        if name in value.DESCRIPTOR.fields_by_name:value.ClearField(name)
    return value
