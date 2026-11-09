"""Local, admin-editable Pokéstop loot table.

The table lives in ``data/admin/stop-loot.json`` so the published world manifest
is never rewritten. It has three parts: items per spin, guaranteed drops,
weighted drops (with per-row minimum trainer level and count range), and
independent Premium/Remote raid-pass chances.
"""
from collections import Counter
import hashlib
import json
import os
import random
from dittobuffer.world.world_store import ROOT

RULES = json.loads((ROOT / 'src/dittobuffer/world/world-item-rules.json').read_text())
LOOT_PATH = ROOT / 'data/admin/stop-loot.json'
PASS_ITEMS = {'premium': 1402, 'remote': 1408}
_cache = {'key': None, 'table': None}


def default(config):
    weights = config.get('world', {}).get('spin_weights', {})
    drops = [{'item': row['id'], 'weight': weights.get(str(row['id']), row['weight']),
              'min_level': row['minimum_level'], 'min_count': 1, 'max_count': 1}
             for row in RULES['items']]
    # Any extra weight in config (for example the Master Ball) becomes a default row.
    known = {row['id'] for row in RULES['items']}
    for key, weight in sorted(weights.items(), key=lambda pair: int(pair[0])):
        item = int(key)
        if item not in known:
            drops.append({'item': item, 'weight': weight, 'min_level': 1, 'min_count': 1, 'max_count': 1})
    return {'count': 2, 'guaranteed': [{'item': 1, 'count': 1}], 'drops': drops,
            'passes': {'premium': 0.025, 'remote': 0.025}}


def _bounded(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} must be an integer between {low} and {high}')
    return value


def normalise(config, table):
    if not isinstance(table, dict):
        raise ValueError('Loot table must be an object')
    base = default(config)
    count = _bounded(table.get('count', base['count']), 0, 20, 'Items per spin')
    guaranteed = table.get('guaranteed', base['guaranteed'])
    drops = table.get('drops', base['drops'])
    passes = table.get('passes', base['passes'])
    if not isinstance(guaranteed, list) or len(guaranteed) > 20:
        raise ValueError('Guaranteed drops must be a list of at most 20 rows')
    if not isinstance(drops, list) or not 1 <= len(drops) <= 200:
        raise ValueError('Weighted drops must have 1-200 rows')
    if not isinstance(passes, dict):
        raise ValueError('Pass chances must be an object')
    clean_guaranteed = []
    for row in guaranteed:
        if not isinstance(row, dict):
            raise ValueError('Invalid guaranteed drop row')
        clean_guaranteed.append({'item': _bounded(row.get('item'), 1, 100000, 'Guaranteed item'),
                                 'count': _bounded(row.get('count', 1), 1, 999, 'Guaranteed count')})
    clean_drops = []
    total = 0
    for row in drops:
        if not isinstance(row, dict):
            raise ValueError('Invalid weighted drop row')
        weight = row.get('weight')
        if type(weight) not in (int, float) or not 0 <= weight <= 100000:
            raise ValueError('Weight must be between 0 and 100000')
        minimum = _bounded(row.get('min_count', 1), 1, 999, 'Minimum count')
        maximum = _bounded(row.get('max_count', minimum), minimum, 999, 'Maximum count')
        clean_drops.append({'item': _bounded(row.get('item'), 1, 100000, 'Drop item'),
                            'weight': weight,
                            'min_level': _bounded(row.get('min_level', 1), 1, 50, 'Minimum level'),
                            'min_count': minimum, 'max_count': maximum})
        total += weight
    if not total:
        raise ValueError('Weighted drops need at least one positive weight')
    clean_passes = {}
    for key in PASS_ITEMS:
        rate = passes.get(key, 0)
        if type(rate) not in (int, float) or not 0 <= rate <= 1:
            raise ValueError(f'{key.title()} pass chance must be between 0 and 1')
        clean_passes[key] = rate
    return {'count': count, 'guaranteed': clean_guaranteed, 'drops': clean_drops, 'passes': clean_passes}


def load(config):
    if not LOOT_PATH.is_file():
        return default(config)
    key = LOOT_PATH.stat().st_mtime_ns
    if _cache['key'] != key:
        try:
            _cache['table'] = normalise(config, json.loads(LOOT_PATH.read_text(encoding='utf8')))
        except (OSError, ValueError) as error:
            raise ValueError(f'Invalid stop loot table: {error}') from error
        _cache['key'] = key
    return _cache['table']


def save(config, table):
    clean = normalise(config, table)
    LOOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = LOOT_PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(clean, indent=2), encoding='utf8')
    os.replace(temporary, LOOT_PATH)
    _cache.update(key=LOOT_PATH.stat().st_mtime_ns, table=clean)
    return clean


def max_awards(config, level):
    table = load(config)
    guaranteed = sum(row['count'] for row in table['guaranteed'])
    pool = [row for row in table['drops'] if row['min_level'] <= level and row['weight'] > 0]
    weighted = table['count'] * (max((row['max_count'] for row in pool), default=0))
    passes = sum(1 for rate in table['passes'].values() if rate > 0)
    return guaranteed + weighted + passes


def awards(game, player, fort, stamp, level):
    table = load(game.config)
    pool = [row for row in table['drops'] if row['min_level'] <= level and row['weight'] > 0]
    rng = random.Random(hashlib.sha256(f'{player}:{fort}:{stamp}'.encode()).digest())
    result = Counter()
    for row in table['guaranteed']:
        result[row['item']] += row['count']
    if pool and table['count']:
        for row in rng.choices(pool, weights=[row['weight'] for row in pool], k=table['count']):
            result[row['item']] += rng.randint(row['min_count'], row['max_count'])
    for key, item in PASS_ITEMS.items():
        if rng.random() < table['passes'][key]:
            result[item] += 1
    return dict(result)


def augment_templates(values, config):
    if not config.get('world', {}).get('enabled'):
        return values
    known = {v.item.unique_id: v for v in values if v.HasField('item')}
    for definition in RULES['items']:
        if definition['id'] in known:
            known[definition['id']].item.drop_trainer_level = definition['minimum_level']
        elif definition['id'] in (703, 705):
            from dittobuffer.gameplay.game import p
            item = p.GameMasterClientTemplateProto(template_id=definition['source_template'])
            item.item.unique_id = definition['id']
            item.item.item_type = 6
            item.item.category = 2
            item.item.drop_trainer_level = definition['minimum_level']
            food = definition['food']
            target = item.item.food
            for source, name in [('growthPercent', 'growth_percent'), ('berryMultiplier', 'berry_multiplier'),
                                 ('remoteBerryMultiplier', 'remote_berry_multiplier'),
                                 ('numBuddyAffectionPoints', 'num_buddy_affection_points'),
                                 ('numBuddyHungerPoints', 'num_buddy_hunger_points'),
                                 ('mapDurationMs', 'map_duration_ms'), ('timeFullDurationMs', 'active_duration_ms')]:
                if source in food:
                    setattr(target, name, int(food[source]) if source.endswith('Ms') else food[source])
            target.item_effect.extend([{'ITEM_EFFECT_CANDY_AWARD': 1015, 'ITEM_EFFECT_CAP_TARGET_SLOW': 1005}[v]
                                       for v in food['itemEffect']])
            target.item_effect_percent.extend(food['itemEffectPercent'])
            values.append(item)
    return values
